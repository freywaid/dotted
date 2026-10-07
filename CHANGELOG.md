# Changelog

All notable changes to `dotted` are recorded here. Versions prior to
the ones listed are omitted — browse git history for earlier entries.

## [0.46.1]

### Changed
- Each compiled wheel is built and tested in its own CI job, so a
  release takes minutes rather than the better part of an hour. The CLI
  tests write their input files under pytest's `tmp_path`, which
  Windows accepts.

## [0.46.0]

### Added
- The engine compiles to C. Wheels for CPython 3.11-3.14 on Linux
  (glibc and musl, x86_64 and aarch64), macOS (Apple Silicon) and
  Windows carry the compiled engine; every other Python installs the
  same code as pure Python from the universal wheel. `DOTTED_NATIVE=0`
  in the environment runs the Python engine regardless;
  `dotted.native.active()` says which is loaded. Wildcard reads are
  about 2.9x faster than 0.45.1 compiled and writes about 3.5x; pure
  Python is faster too, writes by about a third.
- For development: `make test` and `make bench` run both engines,
  `test.python`/`test.native` and `bench.python`/`bench.native` one;
  `make test.wheel` runs the suite against a wheel built from the
  source distribution.

### Changed
- The `all` extra now means all of them: `formats` and `copium`. It was
  an alias for `formats`, deprecated since 0.44.9.
- A concrete key present in a node is no longer looked for among the
  node's keys to return the node's own key object: it matches as
  written. Concrete writes and `expand`, `pluck` and `unpack` on a
  concrete key cost the same wherever the key sits in a large dict
  (`update` of the 996th key of 1000: 11.5us to 3.3us compiled). The
  one visible difference: a rendered path shows the key as written
  where the node spells it differently but equal, `users.1` for a key
  `1.0`, `RED` for a `StrEnum` member.

## [0.45.1]

### Changed
- The extras no longer carry environment markers of their own. A marker
  ahead of the extra condition made pypistats list `copium` and `tomli`
  as hard requirements; only `pyparsing` is one. Two consequences:
  `pip install dotted-notation[copium]` now fails where copium cannot be
  installed (below CPython 3.10) instead of skipping it, and the `toml`,
  `formats` and `all` extras install `tomli` on Python 3.11+ as well,
  where it goes unused in favor of the standard library's `tomllib`.

## [0.45.0]

A performance release. Figures compare against 0.44.11 on a document of
1000 records, using `python -m benchmarks`.

### Performance
- `unpack()` no longer grows with the square of the leaf count. A soft
  cut compared every new path against every path already yielded; those
  paths are now indexed by their leading keys. A recursive op with a
  negative depth also recomputed each node's depth-to-leaf for every
  ancestor; a traversal now remembers it. 16,000 leaves: 382s to 0.21s.
- `unpack(project=...)` matches projections against the paths the walk
  already has instead of parsing each leaf path it generated.
- A concrete key is no longer found by comparing it against every key
  of the node: an absent key matches without a scan and a present one
  stops at the first match. `update()` looks each level up once, not
  twice. Concrete `update` and `remove` into a 1000-key dict take about
  70% less time.
- Writing paths out is cheaper: quoting no longer imports per call,
  tests for an integer by catching an exception, or scans characters in
  a Python loop. `pluck()` and `expand()` take about 40% less time.
- `setdefault`, `update` and `remove` with `AUTO`, `pluck`, `translate`,
  `match_multi` and `unpack` compile each path once per call.
- Pattern reads shed per-match layers that did nothing without filters
  or transforms: wildcard and recursive `get` take 15-21% less time,
  pattern `update` 30-40%.
- `build()` takes about 60% less time, `match_multi` 40%.
- Imports that ran inside hot functions are now at module level.

### Added
- `compile()`, an alias for `parse()`. Every API that takes a path also
  takes the compiled result.
- Benchmark cases for `unpack`, `translate`, `match_multi`,
  `setdefault`, filters, a late key and an absent key.

### Fixed
- A top-level key starting with `-` was written out bare, so it read
  back as an inverted path and `pack(unpack(obj))` lost it. Such a key
  is now quoted when it leads a path: `'-n'` with quotes, `#'-1'` for a
  negative number.

### Changed
- Paths returned by `pluck`, `expand`, `unpack` and `walk` quote a
  leading `-` key as above, and so do `match()` captures that begin
  with one. Keys elsewhere in a path are unchanged.
- A dict-like whose `in` disagrees with its `keys()` can see a present
  key reported as absent, since a concrete key is now tested for
  membership before the keys are scanned.
- `match()` without `groups` no longer assembles the unmatched tail of
  a partial match, so a hand-built path containing an op that cannot be
  written as text no longer raises there.

## [0.44.11]

### Changed
- `build()` copies its leaves with the standard library's
  `copy.deepcopy` even when copium is installed. copium 0.1.0 gets slow
  at small copies once it has copied something large in the same
  process ([copium#54](https://github.com/percolab/copium/issues/54)),
  which made `build` slower with the extra than without it. copium is
  still used for the whole-object copy made by `mutable=False`.

## [0.44.10]

### Performance
- `build()` and `build_multi()` keep the parsed paths they expand
  instead of assembling each one to a string and parsing it again. A
  pattern matching more paths than the parse cache holds (300) used to
  miss on every one; `build` over 1000 matches is about 35x faster.

### Changed
- Three edge cases of `build()` change with the string round trip gone:
  with `strict=True`, a trailing `[]` or `[:]` now creates the empty
  list where it was a no-op, and raises `TypeError` on a list root; a
  wildcard over keys that cannot be written as a path, such as `None` or
  `True`, now succeeds where it raised `AttributeError`.

### Added
- Benchmark suite: `python -m benchmarks` or `make bench`. Times reads
  and writes at two data sizes, and the copying cases under both
  `copy.deepcopy` and copium. Results can be saved and compared.

### Fixed
- `make` targets failed because the install rule depended on
  `setup.py`, removed in the move to `pyproject.toml`.

### Documentation
- README caveat for the `copium` extra: copium 0.1.0 gets slower at
  copying small objects once it has copied a large one in the same
  process. Reported upstream as
  [copium#54](https://github.com/percolab/copium/issues/54).

## [0.44.9]

### Performance
- Optional [copium](https://github.com/Bobronium/copium) support: a C
  implementation of `copy.deepcopy` with the same semantics. When
  installed, the copies made by `mutable=False` updates and removes and
  by `build()` use it (roughly 5-20x faster on plain nested data);
  otherwise dotted falls back to `copy.deepcopy`. Install with
  `pip install dotted-notation[copium]` (CPython 3.10+).

### Added
- `copium` extra, and a `formats` extra bundling YAML and TOML support.

### Deprecated
- The `all` extra. It is an alias for `formats` and will be removed in
  a future release.

### Changed
- When copium is installed the test suite runs every test function
  under both deepcopy implementations.

## [0.44.8]

### Added
- `match()` supports variadic ops on the *path* side. A group or
  recursive op in the path used to fall through to `None` (or match by
  accident, as `**` did); the path is now treated as the set of paths it
  denotes, and the pattern must *subsume* it — cover every expansion.
  Every branch of a path-side group must match, so
  `match('references.*', 'references.(a,b)')` matches while
  `match('*.*', '(a.b,c)')` does not (branch `c` is a segment short).
  Groups on both sides compose the two rules: some pattern branch must
  cover every path branch. A path-side recursive is covered only by a
  recursive pattern that subsumes it — `match('**', '*b')` matches,
  `match('*', '**')` does not. Conjunctions match on any branch (their
  expansions are the intersection); negations, denoting an open set,
  only by an identical negation or a bare wildcard.

### Changed
- A path-side group captures as one segment: itself, e.g.
  `match('references.*', 'references.(a,b)', groups=True)` gives
  `('references.(a,b)', ('references', '(a,b)'))`. When a variadic
  pattern consumes across the group boundary the group folds into that
  segment's capture, as nested patterns already do.
- Match dispatch is now per-op on both sides: variadic path ops
  implement `do_match_path` (the mirror of `do_match`), and segment
  coverage moved onto ops as `covered_by`, replacing the ad-hoc value
  extraction `Recursive.do_match` used to do.

## [0.44.7]

### Fixed
- `groups='patterns'` capture positions for variadic patterns: literals
  are now correctly excluded when the pattern contains a group or
  recursive op, so `translate`'s `$N` numbering counts pattern segments
  only, as documented. Previously `a.(x,y).*` numbered `$0='a'` (a
  literal), and `**.c` captured the literal `c`.

### Changed
- A group is one pattern segment: it captures the path segments its
  matching branch consumed as a single group, keeping `$N` positions
  stable across branches of different lengths. `x.(a.b,c)` matching
  `x.a.b` now captures `('x', 'a.b')` instead of `('x', 'a', 'b')`.
  Parentheses thereby act as a regex-like capture group: `x.(a.b)`
  captures `'a.b'` as one group where `x.a.b` captures `'a', 'b'`.
  Nested patterns inside a branch fold into the group's capture, as
  with `**`.

## [0.44.6]

### Added
- `match()` supports op groups: disjunction `(a,b)`, first-match `(a,b)?`,
  conjunction `(a&b)`, and negation `(!a)` now match paths instead of
  raising `AttributeError`. A path matches a group if it matches any
  branch followed by the rest of the pattern (a concrete path is only
  ever one branch's output, so Or/First/And all reduce to this);
  negation matches one segment its inner pattern does not.
  Multi-segment branches, nested groups, recursive ops inside branches,
  cut markers, and `groups=True` captures all supported.

### Fixed
- Wrapped variadic patterns in `match()`: `~(a,b)` (nop-wrapped group)
  silently matched nothing and `**=7` (value-guarded recursive) raised
  `AttributeError`; both now match.

### Changed
- Path matching is polymorphic: `base.match_ops()` dispatches to each
  op's `do_match`, which decides how many path segments it consumes
  (single-segment default; backtracking on `Recursive`; branch
  expansion on groups; `Wrap` delegates to variadic inners). The api
  layer no longer special-cases op types.

## [0.44.5]

### Performance
- Transforms no longer disqualify the get() fast path: `a.b|int`
  resolves via the direct-lookup chain and applies transforms on the
  hit (~7x faster than walk). Plain paths are unaffected; `is_simple()`
  semantics unchanged (still requires no transforms).

## [0.44.4]

### Added
- Module-level escape hatches: `dotted.set_simple_fastpath(False)` disables
  the get() fast path (everything goes through walk()), and
  `dotted.set_parse_cache(size)` resizes the LRU cache behind parse()
  (0 disables, None unbounded). Both return the previous setting.

### Performance
- `get()` fast path for simple paths (#58): a plain chain of literal
  Key/Attr/Slot accesses (`a.b`, `a[0].b`, `a@x`) resolves with direct
  dict/list/attr lookups, skipping the walk() machinery entirely. The
  chain is computed once at parse time and cached on the `Dotted`
  (`simple_chain`); unusual containers (dict subclasses, custom
  mappings) fall back to the full traversal.
- `Const.value`/`Numeric.value` are now computed once and cached on the
  instance instead of recomputed per property access.
- `Dotted.__hash__` is cached, making repeated cache lookups keyed on a
  pre-parsed `Dotted` (e.g. `get(obj, parsed)`) much cheaper.

## [0.44.3]

### Fixed
- Transforms with a dict (or other nested-container) argument, e.g.
  `code|lookup:{"a": 1}`, no longer raise `TypeError: unhashable type: 'dict'`
  when the path is parsed. `Transform.__hash__` now freezes container params
  recursively.

## [0.44.2]

### Added
- `unpack(obj, project=...)` keeps only the leaf paths selected by one or
  more dotted patterns. Selection is directional (a leaf survives if it
  `match`es a pattern), so projecting `a.b` never pulls in a shallower scalar
  `a`. Matching defaults to `partial=True` (trailing segment is greedy); pass
  `partial=False` for exact-depth matching, or override per field with a
  `(pattern, partial)` tuple. `project=`/`partial=` also flow through `keys`,
  `values`, and `items`.

## [0.44.1]

### Added
- GitHub Actions: `tests.yml` runs pytest on push/PR; `publish.yml`
  uses PyPI Trusted Publishing (OIDC) on GitHub Release, gated on
  tests passing. Releases now show "Verified details" on PyPI.

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
- `expand_multi`, `pluck_multi`, `assemble_multi` now return a **generator**
  instead of a tuple, aligning with `get_multi`, `match_multi`, `walk_multi`,
  `translate_multi`, `setdefault_multi`. Callers that consumed the return as a
  tuple (indexing, `len()`, equality) must wrap with `tuple(...)` /
  `list(...)`. Singular forms (`expand`, `pluck`, `assemble`) unchanged.

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
