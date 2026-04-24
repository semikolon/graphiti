"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import logging
from time import time
from typing import Any

from pydantic import BaseModel

from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.helpers import MAX_REFLEXION_ITERATIONS, semaphore_gather
from graphiti_core.llm_client import LLMClient
from graphiti_core.llm_client.config import ModelSize
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode, create_entity_node_embeddings
from graphiti_core.prompts import prompt_library
from graphiti_core.prompts.dedupe_nodes import NodeDuplicate, NodeResolutions
from graphiti_core.prompts.extract_nodes import (
    EntitySummary,
    ExtractedEntities,
    ExtractedEntity,
    MissedEntities,
)
from graphiti_core.search.search import search
from graphiti_core.search.search_config import SearchResults
from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.utils.content_chunking import (
    chunk_json_content,
    chunk_message_content,
    chunk_text_content,
    should_chunk,
)
from graphiti_core.utils.datetime_utils import utc_now
from graphiti_core.utils.maintenance.edge_operations import filter_existing_duplicate_of_edges

logger = logging.getLogger(__name__)

# Protected field names from EntityNode and EntityEdge core models.
# LLM-extracted attributes with these names are stripped to prevent
# silent data corruption (Graphiti #1164). The LLM sees episode text
# containing words like "attributes", "name", "summary" and may extract
# them as entity properties — which would overwrite core model fields.
_PROTECTED_NODE_FIELDS = frozenset(EntityNode.model_fields.keys())
_PROTECTED_EDGE_FIELDS: frozenset[str] = frozenset()  # populated lazily to avoid circular import


def _get_protected_edge_fields() -> frozenset[str]:
    global _PROTECTED_EDGE_FIELDS
    if not _PROTECTED_EDGE_FIELDS:
        from graphiti_core.edges import EntityEdge
        _PROTECTED_EDGE_FIELDS = frozenset(EntityEdge.model_fields.keys())
    return _PROTECTED_EDGE_FIELDS


def sanitize_extracted_attributes(
    extracted: dict[str, Any],
    protected_fields: frozenset[str],
    context_label: str = '',
) -> dict[str, Any]:
    """Strip keys that collide with core model fields.

    Prevents silent data corruption when LLM extracts property names
    like 'attributes', 'name', 'summary' from episode text (Graphiti #1164).
    """
    collisions = set(extracted.keys()) & protected_fields
    if collisions:
        logger.warning(
            f'Stripped protected field names from LLM-extracted attributes '
            f'({context_label}): {collisions}'
        )
        return {k: v for k, v in extracted.items() if k not in protected_fields}
    return extracted


async def _extract_entities_chunked(
    llm_client: LLMClient,
    episode: EpisodicNode,
    previous_episodes: list[EpisodicNode],
    entity_types_context: list[dict],
    ensure_ascii: bool,
) -> list[ExtractedEntity]:
    """Chunk-aware entity extraction for dense content (PR #1129 wire-in).

    Splits the episode content via the source-appropriate chunker, extracts
    entities from each chunk in parallel, then deduplicates case-insensitively
    by name. Reflexion is intentionally skipped — each chunk is small enough
    that single-pass extraction is reliable.
    """
    # Select the source-appropriate chunker
    if episode.source == EpisodeType.message:
        chunks = chunk_message_content(episode.content)
        extract_prompt_fn = prompt_library.extract_nodes.extract_message
    elif episode.source == EpisodeType.json:
        chunks = chunk_json_content(episode.content)
        extract_prompt_fn = prompt_library.extract_nodes.extract_json
    else:
        chunks = chunk_text_content(episode.content)
        extract_prompt_fn = prompt_library.extract_nodes.extract_text

    if not chunks:
        # Degenerate: chunker returned nothing. Fall back to treating
        # the whole content as one chunk to preserve semantics.
        chunks = [episode.content]

    logger.debug(
        f'Chunked episode into {len(chunks)} chunks ({episode.source.name}); '
        f'extracting entities in parallel'
    )

    async def extract_one_chunk(chunk_text: str) -> list[ExtractedEntity]:
        context = {
            'episode_content': chunk_text,
            'episode_timestamp': episode.valid_at.isoformat(),
            'previous_episodes': [ep.content for ep in previous_episodes],
            'custom_prompt': '',
            'entity_types': entity_types_context,
            'source_description': episode.source_description,
            'ensure_ascii': ensure_ascii,
        }
        llm_response = await llm_client.generate_response(
            extract_prompt_fn(context),
            response_model=ExtractedEntities,
        )
        response_obj = ExtractedEntities(**llm_response)
        return response_obj.extracted_entities

    # Parallel extraction, bounded by semaphore_gather's default concurrency.
    # Individual chunk failures propagate — worth improving with per-chunk
    # error recovery in a follow-up, but matching upstream #1129 semantics.
    per_chunk_entities: list[list[ExtractedEntity]] = await semaphore_gather(
        *[extract_one_chunk(c) for c in chunks]
    )

    # Merge + case-insensitive dedup by name. First occurrence wins on
    # entity_type_id (consistent with upstream #1129's cross-chunk dedup).
    seen_names: set[str] = set()
    merged: list[ExtractedEntity] = []
    for chunk_entities in per_chunk_entities:
        for entity in chunk_entities:
            name_key = (entity.name or '').strip().lower()
            if not name_key or name_key in seen_names:
                continue
            seen_names.add(name_key)
            merged.append(entity)

    logger.debug(
        f'Chunked extraction: {sum(len(c) for c in per_chunk_entities)} raw entities → '
        f'{len(merged)} after cross-chunk dedup'
    )
    return merged


async def extract_nodes_reflexion(
    llm_client: LLMClient,
    episode: EpisodicNode,
    previous_episodes: list[EpisodicNode],
    node_names: list[str],
    ensure_ascii: bool = False,
) -> list[str]:
    # Prepare context for LLM
    context = {
        'episode_content': episode.content,
        'previous_episodes': [ep.content for ep in previous_episodes],
        'extracted_entities': node_names,
        'ensure_ascii': ensure_ascii,
    }

    llm_response = await llm_client.generate_response(
        prompt_library.extract_nodes.reflexion(context), MissedEntities
    )
    missed_entities = llm_response.get('missed_entities', [])

    return missed_entities


async def extract_nodes(
    clients: GraphitiClients,
    episode: EpisodicNode,
    previous_episodes: list[EpisodicNode],
    entity_types: dict[str, type[BaseModel]] | None = None,
    excluded_entity_types: list[str] | None = None,
) -> list[EntityNode]:
    start = time()
    llm_client = clients.llm_client
    llm_response = {}
    custom_prompt = ''
    entities_missed = True
    reflexion_iterations = 0

    entity_types_context = [
        {
            'entity_type_id': 0,
            'entity_type_name': 'Entity',
            'entity_type_description': 'Default entity classification. Use this entity type if the entity is not one of the other listed types.',
        }
    ]

    entity_types_context += (
        [
            {
                'entity_type_id': i + 1,
                'entity_type_name': type_name,
                'entity_type_description': type_model.__doc__,
            }
            for i, (type_name, type_model) in enumerate(entity_types.items())
        ]
        if entity_types is not None
        else []
    )

    # Adaptive chunking wire-in (upstream PR #1129, ported Apr 24).
    # should_chunk() only returns True for high-density content >= CHUNK_MIN_TOKENS.
    # 95%+ of our target ingestion (ChatGPT/Claude prose conversations) flows the
    # existing reflexion path unchanged — no regression for typical content.
    # Dense structured inputs (bulk-data JSON, AWS cost logs, entity-rich text)
    # take the chunked path which bypasses reflexion in favor of parallel
    # per-chunk extraction + case-insensitive cross-chunk dedup.
    if should_chunk(episode.content, episode.source):
        extracted_entities = await _extract_entities_chunked(
            llm_client=llm_client,
            episode=episode,
            previous_episodes=previous_episodes,
            entity_types_context=entity_types_context,
            ensure_ascii=clients.ensure_ascii,
        )
    else:
        context = {
            'episode_content': episode.content,
            'episode_timestamp': episode.valid_at.isoformat(),
            'previous_episodes': [ep.content for ep in previous_episodes],
            'custom_prompt': custom_prompt,
            'entity_types': entity_types_context,
            'source_description': episode.source_description,
            'ensure_ascii': clients.ensure_ascii,
        }

        while entities_missed and reflexion_iterations <= MAX_REFLEXION_ITERATIONS:
            if episode.source == EpisodeType.message:
                llm_response = await llm_client.generate_response(
                    prompt_library.extract_nodes.extract_message(context),
                    response_model=ExtractedEntities,
                )
            elif episode.source == EpisodeType.text:
                llm_response = await llm_client.generate_response(
                    prompt_library.extract_nodes.extract_text(context),
                    response_model=ExtractedEntities,
                )
            elif episode.source == EpisodeType.json:
                llm_response = await llm_client.generate_response(
                    prompt_library.extract_nodes.extract_json(context),
                    response_model=ExtractedEntities,
                )

            response_object = ExtractedEntities(**llm_response)

            extracted_entities = response_object.extracted_entities

            reflexion_iterations += 1
            if reflexion_iterations < MAX_REFLEXION_ITERATIONS:
                missing_entities = await extract_nodes_reflexion(
                    llm_client,
                    episode,
                    previous_episodes,
                    [entity.name for entity in extracted_entities],
                    clients.ensure_ascii,
                )

                entities_missed = len(missing_entities) != 0

                custom_prompt = 'Make sure that the following entities are extracted: '
                for entity in missing_entities:
                    custom_prompt += f'\n{entity},'

    filtered_extracted_entities = [entity for entity in extracted_entities if entity.name.strip()]
    end = time()
    logger.debug(f'Extracted new nodes: {filtered_extracted_entities} in {(end - start) * 1000} ms')
    # Convert the extracted data into EntityNode objects
    extracted_nodes = []
    for extracted_entity in filtered_extracted_entities:
        type_id = extracted_entity.entity_type_id
        if 0 <= type_id < len(entity_types_context):
            entity_type_name = entity_types_context[extracted_entity.entity_type_id].get(
                'entity_type_name'
            )
        else:
            entity_type_name = 'Entity'

        # Check if this entity type should be excluded
        if excluded_entity_types and entity_type_name in excluded_entity_types:
            logger.debug(f'Excluding entity "{extracted_entity.name}" of type "{entity_type_name}"')
            continue

        labels: list[str] = list({'Entity', str(entity_type_name)})

        new_node = EntityNode(
            name=extracted_entity.name,
            group_id=episode.group_id,
            labels=labels,
            summary='',
            created_at=utc_now(),
        )
        extracted_nodes.append(new_node)
        logger.debug(f'Created new node: {new_node.name} (UUID: {new_node.uuid})')

    logger.debug(f'Extracted nodes: {[(n.name, n.uuid) for n in extracted_nodes]}')
    return extracted_nodes


async def resolve_extracted_nodes(
    clients: GraphitiClients,
    extracted_nodes: list[EntityNode],
    episode: EpisodicNode | None = None,
    previous_episodes: list[EpisodicNode] | None = None,
    entity_types: dict[str, type[BaseModel]] | None = None,
    existing_nodes_override: list[EntityNode] | None = None,
) -> tuple[list[EntityNode], dict[str, str], list[tuple[EntityNode, EntityNode]]]:
    llm_client = clients.llm_client
    driver = clients.driver

    search_results: list[SearchResults] = await semaphore_gather(
        *[
            search(
                clients=clients,
                query=node.name,
                group_ids=[node.group_id],
                search_filter=SearchFilters(),
                config=NODE_HYBRID_SEARCH_RRF,
            )
            for node in extracted_nodes
        ]
    )

    candidate_nodes: list[EntityNode] = (
        [node for result in search_results for node in result.nodes]
        if existing_nodes_override is None
        else existing_nodes_override
    )

    existing_nodes_dict: dict[str, EntityNode] = {node.uuid: node for node in candidate_nodes}

    existing_nodes: list[EntityNode] = list(existing_nodes_dict.values())

    # Port of upstream PR #1276 (dedup scaling fix, CLA-blocked).
    # Drop candidate.attributes from the resolution context — only name + entity_types
    # are needed for identity-level dedup. Attributes (summaries, descriptions) add
    # thousands of tokens that can push the prompt past max_tokens as graph grows.
    # Cap at MAX_RESOLVE_CANDIDATES = 50 as a hard ceiling. See issue #1275.
    MAX_RESOLVE_CANDIDATES = 50
    existing_nodes_context = (
        [
            {
                'idx': i,
                'name': candidate.name,
                'entity_types': candidate.labels,
                # candidate.attributes intentionally omitted — see comment above.
            }
            for i, candidate in enumerate(existing_nodes[:MAX_RESOLVE_CANDIDATES])
        ],
    )

    entity_types_dict: dict[str, type[BaseModel]] = entity_types if entity_types is not None else {}

    # Prepare context for LLM
    extracted_nodes_context = [
        {
            'id': i,
            'name': node.name,
            'entity_type': node.labels,
            'entity_type_description': entity_types_dict.get(
                next((item for item in node.labels if item != 'Entity'), '')
            ).__doc__
            or 'Default Entity Type',
        }
        for i, node in enumerate(extracted_nodes)
    ]

    context = {
        'extracted_nodes': extracted_nodes_context,
        'existing_nodes': existing_nodes_context,
        'episode_content': episode.content if episode is not None else '',
        'previous_episodes': [ep.content for ep in previous_episodes]
        if previous_episodes is not None
        else [],
        'ensure_ascii': clients.ensure_ascii,
    }

    llm_response = await llm_client.generate_response(
        prompt_library.dedupe_nodes.nodes(context),
        response_model=NodeResolutions,
    )

    node_resolutions: list[NodeDuplicate] = NodeResolutions(**llm_response).entity_resolutions

    resolved_nodes: list[EntityNode] = []
    uuid_map: dict[str, str] = {}
    node_duplicates: list[tuple[EntityNode, EntityNode]] = []
    for resolution in node_resolutions:
        resolution_id: int = resolution.id
        duplicate_idx: int = resolution.duplicate_idx

        extracted_node = extracted_nodes[resolution_id]

        resolved_node = (
            existing_nodes[duplicate_idx]
            if 0 <= duplicate_idx < len(existing_nodes)
            else extracted_node
        )

        # resolved_node.name = resolution.get('name')

        resolved_nodes.append(resolved_node)
        uuid_map[extracted_node.uuid] = resolved_node.uuid

        duplicates: list[int] = resolution.duplicates
        if duplicate_idx not in duplicates and duplicate_idx > -1:
            duplicates.append(duplicate_idx)
        for idx in duplicates:
            existing_node = existing_nodes[idx] if idx < len(existing_nodes) else resolved_node

            node_duplicates.append((extracted_node, existing_node))

    logger.debug(f'Resolved nodes: {[(n.name, n.uuid) for n in resolved_nodes]}')

    new_node_duplicates: list[
        tuple[EntityNode, EntityNode]
    ] = await filter_existing_duplicate_of_edges(driver, node_duplicates)

    return resolved_nodes, uuid_map, new_node_duplicates


async def extract_attributes_from_nodes(
    clients: GraphitiClients,
    nodes: list[EntityNode],
    episode: EpisodicNode | None = None,
    previous_episodes: list[EpisodicNode] | None = None,
    entity_types: dict[str, type[BaseModel]] | None = None,
) -> list[EntityNode]:
    llm_client = clients.llm_client
    embedder = clients.embedder
    updated_nodes: list[EntityNode] = await semaphore_gather(
        *[
            extract_attributes_from_node(
                llm_client,
                node,
                episode,
                previous_episodes,
                entity_types.get(next((item for item in node.labels if item != 'Entity'), ''))
                if entity_types is not None
                else None,
                clients.ensure_ascii,
            )
            for node in nodes
        ]
    )

    await create_entity_node_embeddings(embedder, updated_nodes)

    return updated_nodes


async def extract_attributes_from_node(
    llm_client: LLMClient,
    node: EntityNode,
    episode: EpisodicNode | None = None,
    previous_episodes: list[EpisodicNode] | None = None,
    entity_type: type[BaseModel] | None = None,
    ensure_ascii: bool = False,
) -> EntityNode:
    node_context: dict[str, Any] = {
        'name': node.name,
        'summary': node.summary,
        'entity_types': node.labels,
        'attributes': node.attributes,
    }

    attributes_context: dict[str, Any] = {
        'node': node_context,
        'episode_content': episode.content if episode is not None else '',
        'previous_episodes': [ep.content for ep in previous_episodes]
        if previous_episodes is not None
        else [],
        'ensure_ascii': ensure_ascii,
    }

    summary_context: dict[str, Any] = {
        'node': node_context,
        'episode_content': episode.content if episode is not None else '',
        'previous_episodes': [ep.content for ep in previous_episodes]
        if previous_episodes is not None
        else [],
        'ensure_ascii': ensure_ascii,
    }

    llm_response = (
        (
            await llm_client.generate_response(
                prompt_library.extract_nodes.extract_attributes(attributes_context),
                response_model=entity_type,
                model_size=ModelSize.small,
            )
        )
        if entity_type is not None
        else {}
    )

    summary_response = await llm_client.generate_response(
        prompt_library.extract_nodes.extract_summary(summary_context),
        response_model=EntitySummary,
        model_size=ModelSize.small,
    )

    if entity_type is not None:
        try:
            entity_type(**llm_response)
        except Exception as e:
            logger.warning(
                f'Custom entity type validation failed for node "{node.name}": {e}. '
                f'Proceeding with sanitized attributes.'
            )

    node.summary = summary_response.get('summary', '')
    node_attributes = sanitize_extracted_attributes(
        {key: value for key, value in llm_response.items()},
        _PROTECTED_NODE_FIELDS,
        context_label=f'node "{node.name}"',
    )

    node.attributes.update(node_attributes)

    return node
