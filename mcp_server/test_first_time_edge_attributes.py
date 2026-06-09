"""Prevention test for the first-time-edge attribute fix (edge_operations.py).

Pins the behaviour that a brand-new edge (no related/existing edges) STILL gets
its type classified + attributes extracted when an edge ontology applies — i.e.
it does NOT take the dedup-skip early-return. Guards against a future
graphiti-core change re-introducing the upstream #1111 early-return bug, which
would silently empty the `attributes` of the *first* edge of every new
relationship (e.g. the first debt of a new creditor).
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from pydantic import BaseModel, Field

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EpisodicNode, EpisodeType
from graphiti_core.utils.maintenance.edge_operations import resolve_extracted_edge


class _Money(BaseModel):
    """A test edge type carrying a numeric attribute."""

    amount: float | None = Field(default=None, description='A sum of money, as a number.')


def _episode() -> EpisodicNode:
    return EpisodicNode(
        name='ep',
        group_id='test',
        source=EpisodeType.text,
        source_description='test',
        content='Acme owes Bob 32377 SEK.',
        valid_at=datetime.now(timezone.utc),
    )


def _edge() -> EntityEdge:
    return EntityEdge(
        group_id='test',
        source_node_uuid='src',
        target_node_uuid='tgt',
        created_at=datetime.now(timezone.utc),
        name='OWES',
        fact='Acme owes Bob 32377 SEK.',
    )


@pytest.mark.asyncio
async def test_first_time_edge_extracts_attributes_when_edge_types_apply():
    """With an applicable edge ontology, a first-time edge is classified +
    attributed (no early-return). RED on the pre-fix code, GREEN with it."""
    llm = AsyncMock()
    llm.generate_response = AsyncMock(
        side_effect=[
            # resolve_edge (dedup/classify): nothing to dedup, classify the type
            {'duplicate_facts': [], 'contradicted_facts': [], 'fact_type': 'Money'},
            # extract_attributes: the structured edge attributes
            {'amount': 32377.0},
        ]
    )

    resolved, invalidated, duplicates = await resolve_extracted_edge(
        llm, _edge(), [], [], _episode(), edge_types={'Money': _Money}
    )

    assert resolved.attributes == {'amount': 32377.0}
    assert resolved.name == 'Money'
    assert llm.generate_response.await_count == 2
    assert invalidated == [] and duplicates == []


@pytest.mark.asyncio
async def test_first_time_edge_keeps_fast_path_without_edge_types():
    """No applicable edge ontology -> dedup-skip fast path preserved (no LLM
    call, empty attributes). Ensures the fix doesn't change existing behaviour
    for the non-custom-edge case."""
    llm = AsyncMock()
    llm.generate_response = AsyncMock()

    resolved, invalidated, duplicates = await resolve_extracted_edge(
        llm, _edge(), [], [], _episode(), edge_types={}
    )

    assert not resolved.attributes
    llm.generate_response.assert_not_awaited()
