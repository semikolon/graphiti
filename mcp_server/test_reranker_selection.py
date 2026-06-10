"""Unit tests for the search-tool reranker selection helpers.

Pins the recipe each (reranker, center_node_uuid) combination resolves to, plus
the validation errors. Pure logic — no DB, no network.
"""

from graphiti_mcp_server import _resolve_node_search_config, _resolve_edge_search_config
from graphiti_core.search.search_config_recipes import (
    NODE_HYBRID_SEARCH_RRF,
    NODE_HYBRID_SEARCH_MMR,
    NODE_HYBRID_SEARCH_CROSS_ENCODER,
    NODE_HYBRID_SEARCH_NODE_DISTANCE,
    EDGE_HYBRID_SEARCH_MMR,
    EDGE_HYBRID_SEARCH_CROSS_ENCODER,
)


def _dump(recipe, limit):
    c = recipe.model_copy(deep=True)
    c.limit = limit
    return c.model_dump()


# ---- node ----

def test_node_default_no_center_is_rrf():
    cfg, err = _resolve_node_search_config('', '', 10)
    assert err is None
    assert cfg.model_dump() == _dump(NODE_HYBRID_SEARCH_RRF, 10)


def test_node_default_centered_is_node_distance():
    cfg, err = _resolve_node_search_config('', 'uuid-123', 10)
    assert err is None
    assert cfg.model_dump() == _dump(NODE_HYBRID_SEARCH_NODE_DISTANCE, 10)


def test_node_mmr():
    cfg, err = _resolve_node_search_config('mmr', '', 5)
    assert err is None
    expected = NODE_HYBRID_SEARCH_MMR.model_copy(deep=True)
    expected.limit = 5
    expected.reranker_min_score = -2.0  # MMR keeps + reorders, doesn't drop (see helper)
    assert cfg.model_dump() == expected.model_dump()


def test_node_cross_encoder_hyphen_alias():
    cfg, err = _resolve_node_search_config('cross-encoder', '', 5)
    assert err is None
    assert cfg.model_dump() == _dump(NODE_HYBRID_SEARCH_CROSS_ENCODER, 5)


def test_node_node_distance_requires_center():
    cfg, err = _resolve_node_search_config('node_distance', '', 5)
    assert cfg is None
    assert 'center_node_uuid' in err


def test_node_node_distance_with_center_ok():
    cfg, err = _resolve_node_search_config('node_distance', 'uuid-123', 5)
    assert err is None
    assert cfg.model_dump() == _dump(NODE_HYBRID_SEARCH_NODE_DISTANCE, 5)


def test_node_unknown_reranker():
    cfg, err = _resolve_node_search_config('bogus', '', 5)
    assert cfg is None
    assert 'Unknown reranker' in err


# ---- edge / facts ----

def test_edge_default_falls_through_to_client_search():
    cfg, err = _resolve_edge_search_config('', '', 10)
    assert cfg is None and err is None  # signals: use the default client.search() path


def test_edge_mmr():
    cfg, err = _resolve_edge_search_config('mmr', '', 5)
    assert err is None
    expected = EDGE_HYBRID_SEARCH_MMR.model_copy(deep=True)
    expected.limit = 5
    expected.reranker_min_score = -2.0  # MMR keeps + reorders, doesn't drop (see helper)
    assert cfg.model_dump() == expected.model_dump()


def test_edge_cross_encoder():
    cfg, err = _resolve_edge_search_config('cross_encoder', '', 5)
    assert err is None
    assert cfg.model_dump() == _dump(EDGE_HYBRID_SEARCH_CROSS_ENCODER, 5)


def test_edge_node_distance_requires_center():
    cfg, err = _resolve_edge_search_config('node_distance', '', 5)
    assert cfg is None
    assert 'center_node_uuid' in err


def test_edge_unknown_reranker():
    cfg, err = _resolve_edge_search_config('bogus', '', 5)
    assert cfg is None
    assert 'Unknown reranker' in err
