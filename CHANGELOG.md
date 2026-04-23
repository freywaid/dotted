# Changelog

All notable changes to `dotted` are recorded here. Versions prior to
the ones listed are omitted — browse git history for earlier entries.

## [0.44.0]

### Changed (breaking)
- Public-API parameter renamed from `key` to `path` across `get`,
  `update`, `update_if`, `remove`, `remove_if`, `has`, `setdefault`,
  `build`, `mutable`, `match`, `parse`, and all `is_*` predicates.
  Positional calls are unaffected; keyword callers must update
  (e.g. `update(obj, key=...)` → `update(obj, path=...)`).
- `build_multi(obj, keys=...)` → `build_multi(obj, paths=...)`.
- `setdefault_multi`, `update_multi`, `pack` rename `keyvalues=` to
  `pathvalues=`.
- `remove_multi` / `remove_if_multi` rename `keys_only=` to
  `paths_only=`.
- `assemble` / `assemble_multi` rename `keys`/`keys_list` to
  `segments`/`segments_list`.
- `remove_if` default pred signature: `lambda key: key is not None` →
  `lambda path: path is not None`.

### Added
- `is_mutable` — preferred alias for `mutable`, parallels
  `is_pattern`, `is_template`, etc. `mutable` remains as an alias.

### Rationale
- The library distinguishes a *path* (the whole dotted expression)
  from a *path segment* or *field* (one component), with *key field*
  referring specifically to the dot-notation kind (vs bracket or
  attr fields). Using bare `key` as a parameter name for the whole
  path conflated those. Docs and API now use precise terminology.

## [0.43.16]

### Added
- PyPI metadata: `keywords`, expanded `classifiers` (license,
  audience, topic, per-Python-version 3.6–3.13). Package now surfaces
  under PyPI's Python-version filter.
- `MANIFEST.in` — includes `CHANGELOG.md` in sdist, excludes `tests/`
  (previously pulled in incidentally via auto-discovery).

### Changed
- Packaging migrated from `setup.py` to `pyproject.toml` ([PEP 621]
  `[project]` table with setuptools as the build backend).
  Install-time behavior unchanged; source builds now require pip >=19.

[PEP 621]: https://peps.python.org/pep-0621/

## [0.43.15]

### Changed
- `enum.StrEnum` replaced with plain `enum.Enum` for `ParamStyle`,
  `Attrs`, `GroupMode`. Entry-point normalization preserves the
  "member or string" API — `Resolver.build(paramstyle=...)`,
  `match(groups=...)`, `unpack(attrs=...)` all accept either form.
- `dataclasses` usage routed through `utils.is_dataclass` /
  `utils.dataclass_replace` wrappers that degrade gracefully on
  interpreters without the module.

### Fixed
- `python_requires='>=3.6'` is now actually honest. Previously the
  declaration promised 3.6+ but the code used `StrEnum` (3.11+) and
  unconditional `import dataclasses` (3.7+), so installs on older
  Python succeeded but failed at import time. Both sources of
  breakage removed.

## [0.43.14]

### Added
- `psycopg3` driver alias — dispatches to the same `PsycopgResolver`
  as `psycopg`. Use whichever reads better.

### Fixed
- Casts under `named` / `pyformat` / `qmark` / `format` paramstyles.
  The `:cast` marker spec was only honored by numeric / dollar-numeric
  renderers; every paramstyle now respects it when the driver's
  `cast_fn` is active. The `psycopg` driver (cast=True) now emits
  `::bigint` / `::text` casts inside `jsonb_build_object(...)`
  polymorphic contexts as intended — previously was indistinguishable
  from `psycopg2` output.

## [0.43.13]

### Added
- `Resolver.lateral` — new fragment: `LATERAL jsonb_path_query(...) AS
  _patN(value)` for pattern paths. Compose `r.select` + `r.lateral`
  into a FROM for row-per-match extraction; `r.where` still filters
  rows via `jsonb_path_exists`. Same Resolver carries both shapes.
- `ParamPool.alloc_pattern_alias()` — shared `_pat1`, `_pat2`, …
  counter so pattern paths sharing a pool don't alias-collide.
- `Resolver` implements the `collections.abc.Mapping` protocol over
  its fragment attributes: `r['where']`, `list(r.keys())`, `dict(r)`,
  `**r` splat, `isinstance(r, Mapping)`.

### Changed
- Pattern paths: `r.select` is now `_patN.value` (was a bare column
  reference). The bare form was effectively meaningless; the new
  value composes with `r.lateral` for extract-style queries.

## [0.43.12]

### Added
- `Raw` and `Col` substitution-value wrappers for emitting SQL
  expressions in place of a bind parameter. `Raw('matched.customer')`
  renders verbatim (low-level escape hatch); `Col('matched.customer')`
  validates each identifier segment before wrapping. Exposes the
  CTE-composition use case: same Resolver serves N+1 orchestration
  (bind runtime value) and single-SQL composition (bind a column ref
  via `Raw` / `Col`).

## [0.43.11]

### Added
- `ParamPool` — shared bind-parameter pool passed as `pool=` to
  multiple `sqlize()` calls so composed fragments don't have marker
  collisions. Substitutions by the same original name dedup across
  Resolvers sharing a pool. Single-Resolver behavior unchanged.

## [0.43.10]

### Added
- `project_urls` in `setup.py` so PyPI shows a "Changelog" sidebar
  link pointing at `CHANGELOG.md` on GitHub.

## [0.43.9]

### Changed (breaking)
- `sqlize(path)` now requires `driver=` (`'asyncpg'`, `'psycopg2'`,
  `'psycopg'`). `flavor=` argument removed; implied by driver.
- Primary render call is `r.build(sql, **bindings)` (instance method).
  `Resolver.build(sql, paramstyle=..., **bindings)` classmethod kept
  as a low-level escape hatch.
- Package `dotted.sqlize` → `dotted.sql` (function `dotted.sqlize`
  unchanged; only the package path moves).

### Added
- Driver-class architecture: `Resolver` + per-driver subclasses via
  `@dotted.sql.driver('<name>')`, mixing in a flavor (Postgres today).
  Built-in drivers: `asyncpg`, `psycopg2`, `psycopg` (v3).
- `dotted.sql.drivers()` — list registered drivers at runtime.
- Integration test suite against live Postgres (`make test.integration`),
  parametrized over `asyncpg` + `psycopg2` drivers.

## [0.43.8]
- Add `pyformat` / `qmark` / `format` / `numeric` paramstyles to
  `Resolver.build`, covering PEP 249 styles beyond `named` / `dollar-numeric`.

## [0.43.7]
- Replace `sqlize`'s dict return with a `Resolver` carrying
  `SQLFragment` objects; paramstyle moves from sqlize time to
  `Resolver.build` time.

## [0.43.6]
- Support pattern paths (`*`, `[*]`, `**`, bracket filters) in
  `sqlize` via Postgres `jsonb_path_exists`.

## [0.43.5]
- Replace `mangle`/`demangle` with hash-based bind names in `sqlize`.
- Add `is_reference`, `is_indeterminate`, `is_simple` path classifiers.

## [0.43.4]
- Fix `is_pattern` / `is_template` classification of substitutions.

## [0.43.3]
- Parse floats on guard RHS; make value guards terminal in `op_seq`.

## [0.43.2]
- Support JSON-style sentinels (`true` / `false` / `null`) on guard RHS.

## [0.43.1]
- Support dotted notation inside substitution names (`$(user.min_age)`).

## [0.43.0]
- Add `sqlize`: translate dotted paths into SQL clause components.

## [0.42.9]
- Add concrete access fallback and `__slots__` support for attr/key
  fields.

## [0.42.8]
- Fix thread-safety in the parser with a lock around `parse_string`.

## [0.42.7]
- Mark package as Beta in classifiers.

## [0.42.6]
- Replace isinstance dispatch with polymorphism and declarative
  `_match_from`; extract shared helpers.

## [0.42.5]
- Fix `unpack` dropping leaves in mixed-depth trees.

## [0.42.4]
- Fix default generation for `FilterWrap`-ed slot groups.

## [0.42.3]
- Support multi-doc Python literals in `py` / `pyl` input formats.

## [0.42.2]
- `dq` CLI: add `py` / `pyl` input and output formats.

## [0.42.1]
- Fix type-erasing `str()` in `Subst.resolve()`; add var support in
  containers and globs.

## [0.42.0]
- Add template bindings and a resolution guard to `parse()` and the
  traversal APIs.
- Add `+` concat operator for key construction.

## [0.41.2]
- Unify substitution classes with transform support.

## [0.41.1]
- Move `most_inner` to the `TraversalOp` base; add it to `Wrap` for
  deep unwrapping; delegate `is_reference()` through `Wrap`.

## [0.41.0]
- Add relative references: `$$(^path)`, `$$(^^path)`,
  `$$(^^^path)`.

## [0.40.0]
- **Breaking**: `unpack()` now returns a dict instead of a tuple of
  pairs.
- Add `$$(path)` internal references (absolute-root) with pattern
  support.

## [0.39.1]
- Add `$(name)` named substitutions.

## [0.39.0]
- Make `quote()` idempotent; rename `match.py` → `matchers.py`.
- Add `\$` escaping for literal dollar-sign keys; add `is_template`
  API.

## [0.38.1]
- Enable pyparsing packrat mode (~40% parse speedup).

## [0.38.0]
- Add comparison operators (`<`, `>`, `<=`, `>=`) for filters and
  value guards.
- Add `translate_multi()` yielding `(original, translated)` tuples.

## [0.37.0]
- Add `GroupMode.patterns` and `translate()`.
- Fix mid-path `**` parsing.

## [0.36.1]
- Add universal `$N` resolution via tree-level `resolve()`.
- Extract filters from `AccessOp` into a dedicated `FilterWrap`
  wrapper.

## [0.36.0]
- Add `$N` template substitution grammar, `replace()`, and `pack()`.

## [0.35.6]
- Fix recursive `remove` ignoring `val` parameter; add cycle
  detection tests.

## [0.35.5]
- Add `keys()`, `values()`, `items()` APIs returning dict-view types.

## [0.35.4]
- Add transform support for value guards and filters.

## [0.35.3]
- Add `walk` / `walk_multi` API for lazy `(path, value)` iteration.

## [0.35.2]
- Extract `Dotted` / `assemble` into `results.py`; move transform
  decorator to `transforms.py`; split `elements.py` into `access`,
  `filters`, `recursive`, `wrappers`, `engine`, `groups` modules.

## [0.35.1]
- Add type restrictions on `OpGroup`s and recursive operators.

## [0.35.0]
- Add path segment type restrictions; remove `_RECURSIVE_TERMINALS`.

## [0.34.4]
- Cache concrete op construction for ~20% `pluck` speedup.

## [0.34.3]
- Rename `--attrs` to `--unpack-attrs`; document the `Attrs` enum.

## [0.34.2]
- Optimize stack-based traversal by eliminating `Frame` kwargs
  copying.

## [0.34.1]
- Add `Attrs` enum for `unpack` attr filtering and `--attrs` CLI
  flag.

## [0.34.0]
- Add `strict=True` mode for type-separated accessor matching.
- Refactor the recursive operator: `**` is dict-only; add `*(expr)`
  accessor groups.

## [0.33.0]
- Stack-based traversal, unified grammar, single `AccessOp`.

## [0.32.2]
- Split Projection and Unpack into separate README sections.

## [0.32.1]
- Improve `dq` intro phrasing; add explicit anchors for PyPI README
  navigation.

## [0.32.0]
- Add key quoting, `normalize()`, and extended numeric literals.

## [0.31.1]
- Unify string and bytes glob grammar; README updates.

## [0.31.0]
- Add math, comparison, and membership transforms.
- Add string glob, bytes glob, bytes literal, and value group
  patterns; add container filter values.
