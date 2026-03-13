# TODO

## Upstream PRs

- [ ] **PR: Fix RediSearch syntax errors from entity names with boolean operators or special chars**
  - `helpers.py`: Added 6 missing RediSearch-specific chars (`@`, `.`, `#`, `$`, `%`, `'`) to `lucene_sanitize()` escape map
  - `search_utils.py`: Added empty query guard in `fulltext_query()` — entity names that are pure boolean operators (AND, OR, NOT) get fully stripped by sanitizer, producing empty parens `()` which is invalid RediSearch syntax
  - Reproducible: any episode with an LLM-extracted entity named "NOT", "And", "Or", etc. crashes the dedup search
  - Files: `graphiti_core/helpers.py`, `graphiti_core/search/search_utils.py`
