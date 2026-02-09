# Enhancement: Path mapping (unpack → match → patch → pack)

## Summary

Add support for mapping from one path (or path pattern) to another, using a pipeline: **unpack** structure to dotted normal form, **match** paths against a pattern (with optional capture groups), **patch** by rewriting paths (and optionally values) via a template with backreferences (`\1`, `\2`, …), then **pack** the result back into a nested structure.

## Dependency

**Requires [#34 — Recursive wildcard `**` for deep traversal](https://github.com/freywaid/dotted/issues/34) first.** The unpack step depends on `**` (and depth/type semantics) to produce a flat set of (path, value) pairs from arbitrary nested structures.

## Pipeline

| Step   | Purpose |
|--------|--------|
| **Unpack** | Structure → flat (path, value) pairs. Recurse into dicts (and attrs); do *not* expand list elements (no “unpack leaf sequences”). Candidate pattern: `**:-1(.*, @*, [])` — recursive, all segment types (key, attr, slot). |
| **Match**  | For each path, `match(pattern, path)` (and `groups=True` when captures are needed) to select candidates. |
| **Patch**  | Rewrite path (and optionally value) using a template. Backreferences `\1`, `\2`, … substitute captured groups from the match (regex-style). Escaping: `\\` → `\`, `\\1` = literal `\1`. |
| **Pack**   | Inverse of unpack: set of (path, value) pairs → nested structure. |

## Backreferences

- **Capture:** Already supported via `match(pattern, key, groups=True)` → `(matched_key, (g1, g2, …))`.
- **Substitution:** New primitive, e.g. `rewrite_path(pattern, path, template)` → rewritten path string or `None` if path doesn’t match. Template uses `\1`, `\2`, … (1-based) for groups. Group values may contain dots (multi-segment); substituted as-is.
- **Design choices to settle:** `\1`–`\9` only vs `\10`+; optional `\0` for full match; exact escaping rules.

## Use cases

- Rename or relocate paths at scale: “map `user.*` → `profile.*`” by matching and rewriting with a template.
- Shape data for APIs or storage: select paths by pattern, rewrite to target schema, pack.
- Normalize or transform nested structures using pattern-based path mapping instead of ad-hoc traversal.

## Open design (can be decided later)

- **Unpack:** Exact semantics for root path representation, list-only root (`pluck([1,2,3], '**:-1(.*, @*, [])')`), and depth vs type rules (e.g. “don’t unpack leaf sequences” vs depth slice `**:-1`).
- **Pack:** Algorithm and edge cases for building nested dict/list/attr structures from (path, value) pairs.
- **Patch:** Whether patch applies only to path names or also to values (e.g. transforms in the template).

## Related

- [#34 — Recursive wildcard `**` for deep traversal](https://github.com/freywaid/dotted/issues/34) (dependency)
- Existing: `match(..., groups=True)`, `expand`, `pluck`
