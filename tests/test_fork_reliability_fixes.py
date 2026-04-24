"""
Regression tests for fork-specific reliability fixes documented in
`dotfiles/docs/graphiti_upstream_review_2026_03_15.md` Phase 1-7.

These fixes earned numbered phase docstrings but never received unit test
coverage. This module fills the gap:

  - Phase 5 (`8fb9934`): RediSearch reserved-word filtering + special-char escaping
  - Phase 7 (`9331410`): TruncationError detection (exception class shape)
  - f49c982: sanitize_extracted_attributes (#1164) — LLM field-name collisions
  - ae9dfe5: semaphore_gather max_coroutines BEHAVIOR (not just signature)

Tests are self-contained: no DB, no LLM, no MCP. Run fast.
"""

from __future__ import annotations

import asyncio
import time

import pytest


# ---------------------------------------------------------------------------
# Phase 5: RediSearch reserved words + special-char escaping
# (Regression for commit 8fb9934 — LLM infers reserved words like 'MAP' from
# episodes even when they're not in the body text. Prior `lucene_sanitize`
# used str.maketrans() to escape individual letters O/R/N/T/A/D, mangling
# all queries AND failing to filter the actual reserved words.)
# ---------------------------------------------------------------------------


class TestLuceneSanitizeReservedWords:
    """The sanitizer must strip standalone RediSearch reserved words (case-insensitive)."""

    def test_strips_aggregation_keywords_map_reduce_filter(self):
        """'route MAP' — LLM extracts 'MAP' from 'route mapping' text.
        Prior RediSearch query would crash with 'Syntax error near map'.
        """
        from graphiti_core.helpers import lucene_sanitize

        sanitized = lucene_sanitize("Rack route MAP entity")
        assert 'map' not in sanitized.lower().split()
        assert 'Rack' in sanitized
        assert 'route' in sanitized
        assert 'entity' in sanitized

    def test_strips_all_documented_reserved_words(self):
        """All RediSearch reserved words must be filtered."""
        from graphiti_core.helpers import lucene_sanitize

        # Include every documented reserved word
        for word in ['MAP', 'REDUCE', 'FILTER', 'GROUPBY', 'SORTBY',
                     'APPLY', 'LIMIT', 'LOAD', 'AS',
                     'OR', 'AND', 'NOT']:
            sanitized = lucene_sanitize(f"keep {word} keep")
            tokens = sanitized.split()
            assert word.lower() not in [t.lower() for t in tokens], \
                f'Reserved word {word} not stripped: {sanitized!r}'
            assert 'keep' in sanitized

    def test_reserved_words_case_insensitive(self):
        """Filter must be case-insensitive."""
        from graphiti_core.helpers import lucene_sanitize
        for variant in ['map', 'MAP', 'Map', 'mAp']:
            sanitized = lucene_sanitize(f"hello {variant} world")
            tokens = [t.lower() for t in sanitized.split()]
            assert 'map' not in tokens, f'case variant {variant!r} not stripped: {sanitized!r}'

    def test_reserved_substring_NOT_stripped(self):
        """A word CONTAINING a reserved word must NOT be stripped — only standalone matches."""
        from graphiti_core.helpers import lucene_sanitize
        # 'mapping' contains 'map' but isn't the reserved word 'map'
        sanitized = lucene_sanitize("rack mapping service")
        assert 'mapping' in sanitized, f'mapping stripped from: {sanitized!r}'

    def test_empty_query_returns_empty(self):
        from graphiti_core.helpers import lucene_sanitize
        assert lucene_sanitize('') == ''

    def test_only_reserved_words_returns_empty(self):
        from graphiti_core.helpers import lucene_sanitize
        # All words reserved — result should be empty or whitespace-only
        result = lucene_sanitize("MAP REDUCE FILTER")
        assert result.strip() == ''


class TestLuceneSanitizeSpecialChars:
    """Lucene + RediSearch special chars must be escaped, not stripped."""

    def test_basic_lucene_specials_escaped(self):
        """+ - && || ! ( ) { } [ ] ^ " ~ * ? : \\ /"""
        from graphiti_core.helpers import lucene_sanitize
        for char in ['+', '-', '!', '(', ')', '{', '}', '[', ']',
                     '^', '"', '~', '*', '?', ':']:
            sanitized = lucene_sanitize(f"hello {char} world")
            assert f'\\{char}' in sanitized, \
                f'Char {char} not escaped: {sanitized!r}'

    def test_redisearch_specific_chars_escaped(self):
        """@ (field prefix), . (separator), # $ % ' — FalkorDB-only concerns."""
        from graphiti_core.helpers import lucene_sanitize
        for char in ['@', '.', '#', '$', '%', "'"]:
            sanitized = lucene_sanitize(f"hello {char} world")
            assert f'\\{char}' in sanitized, \
                f'RediSearch char {char} not escaped: {sanitized!r}'

    def test_no_corruption_of_letters(self):
        """Critical: the sanitizer must NOT escape individual letters.

        Prior bug: str.maketrans() was used to ESCAPE individual letters
        O/R/N/T/A/D (from 'OR', 'NOT', 'AND'), mangling every query.
        After the fix, plain text passes through unchanged.
        """
        from graphiti_core.helpers import lucene_sanitize
        sanitized = lucene_sanitize("Notifications are good")
        # Individual letters must NOT be escaped with backslash
        for letter in 'ORNTADornstad':
            assert f'\\{letter}' not in sanitized, \
                f'Individual letter {letter} was escaped: {sanitized!r}'

    def test_combined_escape_and_strip(self):
        """Realistic query: 'Rack route map!' should keep Rack/route, strip map, escape !."""
        from graphiti_core.helpers import lucene_sanitize
        sanitized = lucene_sanitize("Rack route MAP!")
        assert 'Rack' in sanitized
        assert 'route' in sanitized
        assert '\\!' in sanitized
        assert 'map' not in sanitized.lower().split()


# ---------------------------------------------------------------------------
# Phase 7: TruncationError detection shape
# (9331410 — detect both openai.LengthFinishReasonError and Responses API
# status='incomplete', doubling max_tokens on retry up to 65536 cap.)
# ---------------------------------------------------------------------------


class TestTruncationError:
    """Exception class must be importable + of correct shape."""

    def test_truncation_error_importable(self):
        from graphiti_core.llm_client import TruncationError
        assert TruncationError is not None
        assert issubclass(TruncationError, Exception)

    def test_truncation_error_has_standard_exception_interface(self):
        """Must be catchable + have a message."""
        from graphiti_core.llm_client import TruncationError
        try:
            raise TruncationError('output truncated at 16384 tokens')
        except TruncationError as e:
            assert 'truncated' in str(e).lower()
        except Exception:
            pytest.fail('TruncationError not raising/catching as expected')


# ---------------------------------------------------------------------------
# #1164 port (f49c982): sanitize_extracted_attributes
# LLM extracts property names like 'attributes', 'name', 'summary' from
# episode body text and tries to set them as entity attributes — which
# would overwrite core model fields (silent corruption).
# ---------------------------------------------------------------------------


class TestAttributeSanitization:
    """LLM-extracted attributes must be stripped of keys that collide with core model fields."""

    def test_sanitize_function_exported(self):
        from graphiti_core.utils.maintenance.node_operations import sanitize_extracted_attributes
        assert callable(sanitize_extracted_attributes)

    def test_strips_protected_node_fields(self):
        """If LLM extracted `name` or `summary` as attributes, they must be stripped."""
        from graphiti_core.utils.maintenance.node_operations import (
            sanitize_extracted_attributes,
            _PROTECTED_NODE_FIELDS,
        )

        extracted = {
            'name': 'ATTACKER_OVERWRITE',
            'summary': 'ATTACKER_OVERWRITE',
            'description': 'safe custom attribute',
            'color': 'blue',
        }
        result = sanitize_extracted_attributes(
            extracted, _PROTECTED_NODE_FIELDS, context_label='test'
        )
        # Protected fields stripped
        assert 'name' not in result
        assert 'summary' not in result
        # Non-protected fields preserved
        assert result.get('description') == 'safe custom attribute'
        assert result.get('color') == 'blue'

    def test_no_protected_collisions_passthrough(self):
        """If nothing collides, the dict passes through unchanged."""
        from graphiti_core.utils.maintenance.node_operations import (
            sanitize_extracted_attributes,
            _PROTECTED_NODE_FIELDS,
        )

        extracted = {'color': 'blue', 'size': 'large', 'priority': 3}
        result = sanitize_extracted_attributes(
            extracted, _PROTECTED_NODE_FIELDS, context_label='test'
        )
        assert result == extracted

    def test_empty_dict_returns_empty(self):
        from graphiti_core.utils.maintenance.node_operations import (
            sanitize_extracted_attributes,
            _PROTECTED_NODE_FIELDS,
        )
        assert sanitize_extracted_attributes({}, _PROTECTED_NODE_FIELDS) == {}

    def test_all_fields_protected_returns_empty(self):
        """If every key collides, result is empty."""
        from graphiti_core.utils.maintenance.node_operations import (
            sanitize_extracted_attributes,
            _PROTECTED_NODE_FIELDS,
        )
        extracted = {f: 'anything' for f in list(_PROTECTED_NODE_FIELDS)[:3]}
        result = sanitize_extracted_attributes(
            extracted, _PROTECTED_NODE_FIELDS, context_label='test'
        )
        assert result == {}


# ---------------------------------------------------------------------------
# semaphore_gather max_coroutines BEHAVIOR (not just signature)
# issue #1398 addresses the signature; this test proves it actually bounds
# concurrency at runtime.
# ---------------------------------------------------------------------------


class TestSemaphoreGatherBehavior:
    """The max_coroutines parameter must actually bound runtime concurrency."""

    @pytest.mark.asyncio
    async def test_max_coroutines_bounds_concurrency(self):
        """Launch 20 coroutines with max_coroutines=3; observed peak in-flight ≤ 3."""
        from graphiti_core.helpers import semaphore_gather

        in_flight = 0
        peak_in_flight = 0
        lock = asyncio.Lock()

        async def work():
            nonlocal in_flight, peak_in_flight
            async with lock:
                in_flight += 1
                if in_flight > peak_in_flight:
                    peak_in_flight = in_flight
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return True

        results = await semaphore_gather(
            *[work() for _ in range(20)],
            max_coroutines=3,
        )
        assert len(results) == 20
        assert all(results)
        assert peak_in_flight <= 3, \
            f'max_coroutines=3 did not throttle: peak was {peak_in_flight}'

    @pytest.mark.asyncio
    async def test_max_coroutines_none_uses_default(self):
        """max_coroutines=None should fall back to SEMAPHORE_LIMIT env default."""
        from graphiti_core.helpers import semaphore_gather, SEMAPHORE_LIMIT

        # Just verify it runs without error when limit is None (default path)
        async def trivial():
            return 42

        results = await semaphore_gather(
            *[trivial() for _ in range(5)],
            max_coroutines=None,
        )
        assert results == [42] * 5
        # SEMAPHORE_LIMIT should be a positive integer from env
        assert isinstance(SEMAPHORE_LIMIT, int) and SEMAPHORE_LIMIT > 0

    @pytest.mark.asyncio
    async def test_max_coroutines_1_serializes(self):
        """max_coroutines=1 forces serial execution."""
        from graphiti_core.helpers import semaphore_gather

        order: list[int] = []

        async def work(i):
            order.append(f'start-{i}')
            await asyncio.sleep(0.01)
            order.append(f'end-{i}')
            return i

        results = await semaphore_gather(
            *[work(i) for i in range(5)],
            max_coroutines=1,
        )
        # With concurrency=1, every coroutine must fully finish before the next starts.
        # Pattern: [start-0, end-0, start-1, end-1, start-2, end-2, ...]
        assert results == [0, 1, 2, 3, 4]
        for i in range(5):
            start_idx = order.index(f'start-{i}')
            end_idx = order.index(f'end-{i}')
            # No other start should interleave between this start and end
            for other_i in range(5):
                if other_i == i:
                    continue
                other_start = order.index(f'start-{other_i}')
                assert not (start_idx < other_start < end_idx), \
                    f'coroutine {other_i} started between {i} start and end'


# ---------------------------------------------------------------------------
# MAX_RESOLVE_CANDIDATES enforcement (PR #1276 port, integration-ish)
# ---------------------------------------------------------------------------


class TestMaxResolveCandidatesIntegration:
    """The cap must actually fire at runtime when called on large candidate lists.

    We can't test the full resolve_extracted_nodes without DB + LLM, but we can
    prove the slicing logic is in place by inspecting and simulating.
    """

    def test_max_resolve_candidates_constant_is_reasonable(self):
        """The 50 cap is a load-bearing magic number; if someone changes it
        without thinking, this test fails and forces them to reason about the
        token budget implications."""
        src = open(
            '/Users/fredrikbranstrom/Projects/graphiti-official/'
            'graphiti_core/utils/maintenance/node_operations.py'
        ).read()
        assert 'MAX_RESOLVE_CANDIDATES = 50' in src
        # At 50 × ~80 chars each = ~4 KB context, well under 8-16 KB output cap
        # If someone pushes this to 200+, they should explicitly update this test
        # to document the new reasoning.

    def test_slicing_applied_at_call_site(self):
        """The slice MUST be on existing_nodes[:MAX_RESOLVE_CANDIDATES], not
        on the full list."""
        src = open(
            '/Users/fredrikbranstrom/Projects/graphiti-official/'
            'graphiti_core/utils/maintenance/node_operations.py'
        ).read()
        assert 'existing_nodes[:MAX_RESOLVE_CANDIDATES]' in src
