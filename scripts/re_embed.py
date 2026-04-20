#!/usr/bin/env python3
"""Re-embed all Graphiti entities/edges with a new embedding model.

Iterates over all FalkorDB graphs, fetches entity names and edge facts,
embeds them via an OpenAI-compatible API, and updates the stored vectors.

Usage:
    # Dry run — count items per graph without re-embedding:
    python3 scripts/re_embed.py --dry-run

    # Re-embed using local llama.cpp on Darwin:
    python3 scripts/re_embed.py --base-url http://darwin.home:8080/v1

    # Re-embed using OpenAI API (for comparison/rollback):
    python3 scripts/re_embed.py --base-url https://api.openai.com/v1 \\
        --api-key $OPENAI_API_KEY --model text-embedding-3-small

Prerequisites:
    pip install falkordb openai  (or run from the MCP server venv)
"""

import argparse
import asyncio
import sys
import time

from falkordb import FalkorDB
from openai import AsyncOpenAI

EMBEDDING_DIM = 1024
BATCH_SIZE = 32


def format_vec(vec: list[float]) -> str:
    """Format a float vector as a FalkorDB vecf32 literal."""
    return '[' + ','.join(f'{v:.8f}' for v in vec) + ']'


async def embed_batch(
    embedder: AsyncOpenAI, texts: list[str], model: str, dim: int
) -> list[list[float]]:
    """Embed a batch of texts via OpenAI-compatible API."""
    response = await embedder.embeddings.create(input=texts, model=model)
    return [item.embedding[:dim] for item in response.data]


async def re_embed_label(
    graph,
    embedder: AsyncOpenAI,
    model: str,
    dim: int,
    label: str,
    match_pattern: str,
    id_field: str,
    text_field: str,
    embedding_field: str,
    dry_run: bool,
) -> int:
    """Re-embed all items matching a Cypher pattern."""
    query = f'MATCH {match_pattern} RETURN {id_field}, {text_field}'
    result = graph.query(query)
    items = [(row[0], row[1]) for row in result.result_set if row[1]]

    print(f'  {label}: {len(items)} items')
    if dry_run or not items:
        return len(items)

    embedded = 0
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i : i + BATCH_SIZE]
        texts = [text for _, text in batch]

        vecs = await embed_batch(embedder, texts, model, dim)

        for (item_id, _), vec in zip(batch, vecs):
            vec_literal = format_vec(vec)
            # UUID is parameterized to prevent injection; vector is floats only
            update_query = (
                f'MATCH {match_pattern} '
                f'WHERE {id_field} = $uuid '
                f'SET {embedding_field} = vecf32({vec_literal})'
            )
            graph.query(update_query, params={'uuid': item_id})

        embedded += len(batch)
        print(f'    {embedded}/{len(items)}')

    return len(items)


async def re_embed_graph(
    graph, graph_name: str, embedder: AsyncOpenAI, model: str, dim: int, dry_run: bool
) -> dict:
    """Re-embed all entities, edges, and communities in a single graph."""
    print(f'\nGraph: {graph_name}')
    stats = {}

    # Entity nodes
    stats['entities'] = await re_embed_label(
        graph,
        embedder,
        model,
        dim,
        label='Entity nodes',
        match_pattern='(n:Entity)',
        id_field='n.uuid',
        text_field='n.name',
        embedding_field='n.name_embedding',
        dry_run=dry_run,
    )

    # Entity edges (RELATES_TO)
    stats['edges'] = await re_embed_label(
        graph,
        embedder,
        model,
        dim,
        label='Entity edges',
        match_pattern='()-[r:RELATES_TO]->()',
        id_field='r.uuid',
        text_field='r.fact',
        embedding_field='r.fact_embedding',
        dry_run=dry_run,
    )

    # Community nodes
    stats['communities'] = await re_embed_label(
        graph,
        embedder,
        model,
        dim,
        label='Community nodes',
        match_pattern='(n:Community)',
        id_field='n.uuid',
        text_field='n.name',
        embedding_field='n.name_embedding',
        dry_run=dry_run,
    )

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description='Re-embed all Graphiti knowledge graph entities with a new model'
    )
    parser.add_argument(
        '--base-url',
        default='http://darwin.home:8080/v1',
        help='OpenAI-compatible embedding API base URL',
    )
    parser.add_argument(
        '--model',
        default='qwen3-embedding-4b',
        help='Model name to pass to the API (llama.cpp ignores this)',
    )
    parser.add_argument(
        '--api-key',
        default='not-needed',
        help='API key (not needed for llama.cpp)',
    )
    parser.add_argument('--host', default='127.0.0.1', help='FalkorDB host')
    parser.add_argument('--port', type=int, default=6380, help='FalkorDB port')
    parser.add_argument('--password', default='falkordb', help='FalkorDB password')
    parser.add_argument(
        '--embedding-dim', type=int, default=EMBEDDING_DIM, help='Output embedding dimensions'
    )
    parser.add_argument(
        '--graphs',
        nargs='*',
        help='Specific graph names to re-embed (default: all)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Count items without re-embedding',
    )
    args = parser.parse_args()

    print(f'Connecting to FalkorDB at {args.host}:{args.port}')
    db = FalkorDB(host=args.host, port=args.port, password=args.password)

    print(f'Embedding API: {args.base_url} (model: {args.model})')
    embedder = AsyncOpenAI(base_url=args.base_url, api_key=args.api_key)

    # Discover graphs
    raw_graphs = db.execute_command('GRAPH.LIST')
    all_graphs = [g.decode() if isinstance(g, bytes) else g for g in raw_graphs]
    print(f'Available graphs: {all_graphs}')

    graphs = args.graphs if args.graphs else all_graphs
    if not graphs:
        print('No graphs found.')
        sys.exit(0)

    if args.dry_run:
        print('\n--- DRY RUN (counting items only) ---')

    start = time.time()
    total_stats = {'entities': 0, 'edges': 0, 'communities': 0}

    for graph_name in graphs:
        if graph_name not in all_graphs:
            print(f'\nWarning: graph "{graph_name}" not found, skipping')
            continue

        graph = db.select_graph(graph_name)
        stats = await re_embed_graph(graph, graph_name, embedder, args.model, args.embedding_dim, args.dry_run)

        for k, v in stats.items():
            total_stats[k] += v

    elapsed = time.time() - start
    total_items = sum(total_stats.values())

    print(f'\n{"DRY RUN " if args.dry_run else ""}Summary:')
    print(f'  Entities: {total_stats["entities"]}')
    print(f'  Edges: {total_stats["edges"]}')
    print(f'  Communities: {total_stats["communities"]}')
    print(f'  Total: {total_items} items in {elapsed:.1f}s')

    if not args.dry_run and total_items > 0:
        print(f'  Rate: {total_items / elapsed:.1f} items/s')


if __name__ == '__main__':
    asyncio.run(main())
