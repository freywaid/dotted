# Changelog

All notable changes to `dotted` are recorded here. Versions prior to
the ones listed are omitted — browse git history for earlier entries.

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
