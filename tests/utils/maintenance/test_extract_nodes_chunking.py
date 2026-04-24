"""
Tests for the chunking integration in extract_nodes (RED-GREEN-REFACTOR).

The adaptive chunking utility (`graphiti_core/utils/content_chunking.py`) was
ported Apr 24 alongside PR #1129. This module tests the WIRE-IN of that utility
into fork's `extract_nodes` function, replacing the reflexion loop for dense
content while preserving the reflexion path for prose/narrative content.

These tests run in RED first (describing desired behavior), then the wire-in
makes them GREEN. No DB, no real LLM — pure unit with AsyncMock.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.utils.maintenance.node_operations import extract_nodes


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_clients(mock_responses: list[dict]):
    """Build a GraphitiClients mock that returns the given responses in order.

    Each response is a dict that will be wrapped with {'extracted_entities': [...]}
    if it looks like a bare list. Otherwise returned as-is.
    """
    from graphiti_core.graphiti_types import GraphitiClients

    clients = MagicMock(spec=GraphitiClients)
    clients.ensure_ascii = False

    # Normalize mock_responses: support both raw dicts + list-of-entities shortcut
    normalized = []
    for r in mock_responses:
        if isinstance(r, list):
            normalized.append({'extracted_entities': r})
        else:
            normalized.append(r)

    mock_llm = AsyncMock()
    mock_llm.generate_response = AsyncMock(side_effect=normalized)
    clients.llm_client = mock_llm
    clients.driver = MagicMock()
    return clients


def _make_episode(content: str, source: EpisodeType = EpisodeType.text) -> EpisodicNode:
    return EpisodicNode(
        name='test',
        source=source,
        source_description='test',
        group_id='test',
        content=content,
        created_at=datetime.now(timezone.utc),
        valid_at=datetime.now(timezone.utc),
    )


def _entity(name: str, type_id: int = 0) -> dict:
    return {'name': name, 'entity_type_id': type_id}


# ---------------------------------------------------------------------------
# Behavior: chunking routing
# ---------------------------------------------------------------------------


class TestChunkingRouting:
    """Dense content → chunked path. Prose → single-call reflexion path."""

    @pytest.mark.asyncio
    async def test_short_prose_uses_single_call(self):
        """Short prose must stay on the existing reflexion path (single LLM call).

        Regression test: non-chunked content must keep behaving exactly as before.
        """
        clients = _make_clients([[_entity('Alice'), _entity('Bob')]])
        episode = _make_episode('Alice and Bob went to the park. They talked about Paris.')

        nodes = await extract_nodes(clients, episode, [])

        # Exactly one LLM call (reflexion defaults to single-pass)
        assert clients.llm_client.generate_response.await_count == 1, \
            f'Expected 1 LLM call for prose, got {clients.llm_client.generate_response.await_count}'
        assert len(nodes) == 2
        assert {n.name for n in nodes} == {'Alice', 'Bob'}

    @pytest.mark.asyncio
    async def test_dense_json_triggers_chunking(self):
        """Large entity-dense JSON must chunk → multiple LLM calls."""
        from graphiti_core.utils.content_chunking import should_chunk

        # Dense JSON: 1000 small objects triggers the density threshold.
        # Empirically validated — see session doc for fixture calibration.
        dense = json.dumps([{'x': i, 'y': i + 1} for i in range(1000)])
        assert should_chunk(dense, EpisodeType.json), \
            'Test fixture failed: should_chunk returned False. Adjust fixture.'

        # Make the mock return one entity per call
        # (we don't know the exact chunk count a priori, but it'll be > 1)
        responses = [[_entity(f'entity-from-chunk-{i}')] for i in range(50)]
        clients = _make_clients(responses)
        episode = _make_episode(dense, source=EpisodeType.json)

        nodes = await extract_nodes(clients, episode, [])

        assert clients.llm_client.generate_response.await_count > 1, \
            f'Expected >1 LLM call for dense JSON, got {clients.llm_client.generate_response.await_count}'
        # Entities from all chunks should be collected
        assert len(nodes) >= clients.llm_client.generate_response.await_count

    @pytest.mark.asyncio
    async def test_dense_text_triggers_chunking(self):
        """Large entity-rich text (lots of proper nouns) must chunk."""
        from graphiti_core.utils.content_chunking import should_chunk

        # Entity-rich text: many capitalized words, large enough.
        dense = ' '.join(
            f'Stockholm Paris Berlin London Tokyo Acme Corp Widget Ltd Delta Inc Gamma LLC'
            for _ in range(200)
        )
        assert should_chunk(dense, EpisodeType.text), \
            'Test fixture failed: should_chunk returned False for entity-rich text'

        responses = [[_entity(f'e-{i}')] for i in range(50)]
        clients = _make_clients(responses)
        episode = _make_episode(dense, source=EpisodeType.text)

        nodes = await extract_nodes(clients, episode, [])
        assert clients.llm_client.generate_response.await_count > 1, \
            f'Expected >1 LLM call for entity-rich text, got {clients.llm_client.generate_response.await_count}'


# ---------------------------------------------------------------------------
# Behavior: cross-chunk deduplication
# ---------------------------------------------------------------------------


class TestCrossChunkDedup:
    """Entities extracted from multiple chunks must be merged case-insensitively."""

    @pytest.mark.asyncio
    async def test_same_entity_across_chunks_deduplicated(self):
        """If 'Alice' appears in chunks 1, 2, 3 → final list has 1 'Alice'."""
        from graphiti_core.utils.content_chunking import should_chunk

        dense = json.dumps([{'x': i, 'y': i + 1} for i in range(1000)])
        assert should_chunk(dense, EpisodeType.json)

        # Every chunk returns 'Alice' plus a unique other entity
        responses = [
            [_entity('Alice'), _entity(f'event-{i}')]
            for i in range(50)
        ]
        clients = _make_clients(responses)
        episode = _make_episode(dense, source=EpisodeType.json)

        nodes = await extract_nodes(clients, episode, [])
        alice_nodes = [n for n in nodes if n.name == 'Alice']
        assert len(alice_nodes) == 1, \
            f'Expected 1 Alice after cross-chunk dedup, got {len(alice_nodes)}'

    @pytest.mark.asyncio
    async def test_case_insensitive_dedup(self):
        """'alice' vs 'Alice' vs 'ALICE' must dedupe to one entity."""
        from graphiti_core.utils.content_chunking import should_chunk

        dense = json.dumps([{'x': i, 'y': i + 1} for i in range(1000)])
        assert should_chunk(dense, EpisodeType.json)

        responses = [
            [_entity('alice')],
            [_entity('Alice')],
            [_entity('ALICE')],
            [_entity('aLiCe')],
        ] + [[] for _ in range(50)]
        clients = _make_clients(responses)
        episode = _make_episode(dense, source=EpisodeType.json)

        nodes = await extract_nodes(clients, episode, [])
        alice_nodes = [n for n in nodes if n.name.lower() == 'alice']
        assert len(alice_nodes) == 1, \
            f'Case-insensitive dedup failed; got {[n.name for n in alice_nodes]}'


# ---------------------------------------------------------------------------
# Behavior: excluded_entity_types flows through chunked path
# ---------------------------------------------------------------------------


class TestChunkedExcludedEntityTypes:

    @pytest.mark.asyncio
    async def test_chunked_respects_excluded_entity_types(self):
        """excluded_entity_types=['Location'] must filter location entities
        from chunked output, same as the non-chunked path does."""
        from graphiti_core.utils.content_chunking import should_chunk
        from pydantic import BaseModel

        class Location(BaseModel):
            """A geographical place."""
            pass

        class Person(BaseModel):
            """An individual human."""
            pass

        entity_types = {'Location': Location, 'Person': Person}

        dense = json.dumps([{'x': i, 'y': i + 1} for i in range(1000)])
        assert should_chunk(dense, EpisodeType.json)

        # Per-chunk: return one Person (type_id=2) + one Location (type_id=1).
        # Location should be excluded; Person retained.
        responses = [
            [_entity(f'person-{i}', type_id=2), _entity(f'loc-{i}', type_id=1)]
            for i in range(50)
        ]
        clients = _make_clients(responses)
        episode = _make_episode(dense, source=EpisodeType.json)

        nodes = await extract_nodes(
            clients, episode, [],
            entity_types=entity_types,
            excluded_entity_types=['Location'],
        )
        # No Location entities
        assert all('Location' not in n.labels for n in nodes), \
            f'Location entities not excluded: {[(n.name, n.labels) for n in nodes]}'
        # Some Person entities
        assert any('Person' in n.labels for n in nodes)


# ---------------------------------------------------------------------------
# Behavior: per-source chunker selection
# ---------------------------------------------------------------------------


class TestSourceSpecificChunker:
    """Message episodes → chunk_message_content; JSON → chunk_json_content;
    Text → chunk_text_content. Each preserves the appropriate structural
    boundary (speaker turns / JSON elements / sentence boundaries)."""

    @pytest.mark.asyncio
    async def test_message_source_preserves_speaker_boundaries(self):
        """A conversation export chunked shouldn't split mid-turn.

        Strictly: we can only test that chunker functions get called correctly
        (structural fidelity is tested in test_content_chunking.py). Here we
        ensure the message path routes to the message chunker.
        """
        from graphiti_core.utils import content_chunking
        from graphiti_core.utils.content_chunking import should_chunk

        # Build a message-shaped content that will trigger chunking
        turns = [{'role': 'user', 'content': f'Hello Stockholm-{i} from Alice-{i}'}
                 for i in range(200)]
        dense = json.dumps(turns)
        assert should_chunk(dense, EpisodeType.message)

        # Spy on chunk_message_content
        original_fn = content_chunking.chunk_message_content
        calls = []

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original_fn(*args, **kwargs)

        # monkeypatch
        import graphiti_core.utils.maintenance.node_operations as node_ops
        monkeypatch_attr = None
        if hasattr(node_ops, 'chunk_message_content'):
            monkeypatch_attr = node_ops.chunk_message_content
            node_ops.chunk_message_content = spy

        try:
            responses = [[_entity(f'e-{i}')] for i in range(50)]
            clients = _make_clients(responses)
            episode = _make_episode(dense, source=EpisodeType.message)
            await extract_nodes(clients, episode, [])
            assert len(calls) >= 1, \
                'chunk_message_content was not called for EpisodeType.message'
        finally:
            if monkeypatch_attr is not None:
                node_ops.chunk_message_content = monkeypatch_attr
