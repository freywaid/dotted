"""
Fixtures and collection rules for integration tests.

Every test collected under `tests/integration/` is auto-marked as
`integration`. Root `conftest.py` handles the default skip + `--all`
override.

A session-scoped fixture creates a dedicated `dotted_test` schema and
seeds a sample table; per-test transaction rollback keeps tests
isolated. Tests receive a sync `query` fixture parametrized over the
installed drivers (`asyncpg`, `psycopg2`). The callable exposes a
`.driver` attribute so tests can pick the matching paramstyle —
`PARAMSTYLE_BY_DRIVER` carries that mapping. asyncpg is async; its
branch wraps calls in `asyncio.run()` so tests stay sync and don't
need pytest-asyncio.
"""
import asyncio
import datetime
import json
import os

import pytest

try:
    import asyncpg
except ImportError:
    asyncpg = None

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


# Driver names the `query` fixture parametrizes over. Each must be
# registered in the dotted.sql driver registry — tests pass the
# name straight into `sqlize(path, driver=...)`, and paramstyle/cast
# come from the Resolver subclass.
_DRIVERS = ('asyncpg', 'psycopg2')


SCHEMA = 'dotted_test'
TABLE = 'items'

DEFAULT_DSN = 'postgresql://postgres:postgres@localhost:5432/postgres'


_HERE = os.path.dirname(os.path.abspath(__file__))


def pytest_collection_modifyitems(config, items):
    """
    Auto-mark every test collected from this directory as `integration`.
    pytest passes all collected items to every conftest's hook, so we
    filter to items whose file actually lives under this directory.
    Root conftest handles the default skip; no per-test decoration
    required.
    """
    for item in items:
        try:
            item_path = str(item.path)
        except AttributeError:
            item_path = str(item.fspath)
        if item_path.startswith(_HERE):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope='session')
def dsn():
    return os.environ.get('DOTTED_TEST_DSN', DEFAULT_DSN)


def _run(coro):
    """
    Run an awaitable to completion on a fresh event loop. Keeps each
    test self-contained without requiring pytest-asyncio.
    """
    return asyncio.run(coro)


def _seed_rows():
    """
    Sample rows covering the scenarios our sqlize tests exercise.
    Keys: id, status, age, deleted_at, data (JSONB).
    """
    return [
        (1, 'active', 25, None,
         {'user': {'age': 25, 'role': 'admin', 'active': True},
          'users': [{'age': 25, 'active': True},
                    {'age': 32, 'active': False}]}),
        (2, 'active', 35, None,
         {'user': {'age': 35, 'role': 'user', 'active': True},
          'users': [{'age': 35, 'active': True}]}),
        (3, 'banned', 40, None,
         {'user': {'age': 40, 'role': 'user', 'active': False},
          'users': [{'age': 40, 'active': False}]}),
        (4, 'active', 17, None,
         {'user': {'age': 17, 'role': 'user', 'active': True},
          'users': [{'age': 17, 'active': True}]}),
        (5, 'active', 50, None,
         {'user': {'age': 50, 'role': 'admin', 'active': True},
          'users': [{'age': 50, 'active': True},
                    {'age': 18, 'active': True}]}),
        (6, 'active', 30, datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
         {'user': {'age': 30, 'role': 'user', 'active': False},
          'users': []}),
        (7, 'pending', None, None,
         {'user': {'role': 'guest'},
          'users': []}),
    ]


@pytest.fixture(scope='session', autouse=True)
def _schema(dsn):
    """
    Session-scoped schema lifecycle: drop-if-exists, create, seed, then
    drop on teardown. All tests in this directory share the seed.
    """
    if asyncpg is None:
        pytest.skip('asyncpg not installed; run `make install`')

    async def setup():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')
            await conn.execute(f'CREATE SCHEMA {SCHEMA}')
            await conn.execute(f'''
                CREATE TABLE {SCHEMA}.{TABLE} (
                    id          INTEGER PRIMARY KEY,
                    status      TEXT,
                    age         INTEGER,
                    deleted_at  TIMESTAMPTZ,
                    data        JSONB
                )
            ''')
            rows = [(rid, s, a, d, json.dumps(js))
                    for rid, s, a, d, js in _seed_rows()]
            await conn.executemany(
                f'INSERT INTO {SCHEMA}.{TABLE} '
                f'(id, status, age, deleted_at, data) '
                f'VALUES ($1, $2, $3, $4, $5::jsonb)',
                rows,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')
        finally:
            await conn.close()

    try:
        _run(setup())
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f'cannot reach Postgres at {dsn}: {e}')

    yield

    _run(teardown())


@pytest.fixture(params=_DRIVERS)
def query(request, dsn):
    """
    Return a sync callable `(sql, args) -> list[dict]` that runs the
    query inside a transaction and rolls back on exit. Read-only
    tests don't strictly need rollback, but it's cheap insurance.

    Parametrized over the supported drivers. The callable exposes a
    `.driver` attribute so tests can pass it directly into
    `sqlize(path, driver=query.driver)`. If a driver isn't installed,
    that parametrization skips individually — the other driver still
    runs.
    """
    driver = request.param
    if driver == 'asyncpg':
        if asyncpg is None:
            pytest.skip('asyncpg not installed')
        q = _make_asyncpg_query(dsn)
    elif driver == 'psycopg2':
        if psycopg2 is None:
            pytest.skip('psycopg2 not installed')
        q = _make_psycopg2_query(dsn)
    else:
        raise RuntimeError(f'unknown driver: {driver!r}')
    q.driver = driver
    return q


def _make_asyncpg_query(dsn):
    """
    asyncpg-backed sync `query(sql, args)` — runs the async call on a
    fresh event loop per invocation. Transaction is aborted via an
    internal sentinel exception so rows are rolled back after the
    fetch.
    """
    def _q(sql, args=()):
        async def go():
            conn = await asyncpg.connect(dsn)
            try:
                async with conn.transaction():
                    rows = await conn.fetch(sql, *args)
                    result = [dict(r) for r in rows]
                    # Raising here aborts the transaction → ROLLBACK.
                    raise _RollbackAndReturn(result)
            except _RollbackAndReturn as done:
                return done.result
            finally:
                await conn.close()
        return _run(go())
    return _q


def _make_psycopg2_query(dsn):
    """
    psycopg2-backed sync `query(sql, args)`. Uses `RealDictCursor` so
    rows come back as dicts, matching the asyncpg branch. `args` is
    passed as a tuple for positional pyformat (`%s`); psycopg2 also
    accepts a mapping for named pyformat (`%(name)s`) — either shape
    works since Resolver.build under `pyformat` returns a dict.

    Rolls back explicitly after fetch so write-side tests stay
    isolated even on the happy path. `with conn:` is deliberately
    avoided — psycopg2's context manager commits on successful exit.
    """
    def _q(sql, args=()):
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # psycopg2 accepts tuple for %s and dict for %(name)s.
                params = args if isinstance(args, dict) else tuple(args)
                cur.execute(sql, params)
                result = [dict(r) for r in cur.fetchall()]
            conn.rollback()
            return result
        finally:
            conn.close()
    return _q


class _RollbackAndReturn(Exception):
    """
    Sentinel to force transaction rollback while preserving the
    fetch result for the caller.
    """
    def __init__(self, result):
        self.result = result


def table(name=TABLE):
    """
    Qualified table name for composing SQL in tests:
    `f"SELECT id FROM {table()} WHERE {where}"`.
    """
    return f'{SCHEMA}.{name}'
