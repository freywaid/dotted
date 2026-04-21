"""
End-to-end integration tests: run dotted.sqlize output against the
seeded Postgres schema and verify row-level semantics.

The seeded table `dotted_test.items` has columns:
    id, status, age, deleted_at, data (JSONB)

Seed (from tests/integration/conftest.py):
    id=1: status=active age=25                 user.age=25 role=admin active=True
          users[25(True), 32(False)]
    id=2: status=active age=35                 user.age=35 role=user  active=True
          users[35(True)]
    id=3: status=banned age=40                 user.age=40 role=user  active=False
          users[40(False)]
    id=4: status=active age=17                 user.age=17 role=user  active=True
          users[17(True)]
    id=5: status=active age=50                 user.age=50 role=admin active=True
          users[50(True), 18(True)]
    id=6: status=active age=30 deleted_at=…   user.age=30 role=user  active=False
          users[]
    id=7: status=pending age=NULL              user.role=guest (no age/active)
          users[]

Integration tests use asyncpg, which requires `dollar-numeric`
paramstyle. Named / pyformat / etc. are covered by the unit test
matrix.
"""
import pytest
from dotted import sqlize, Resolver


def _ids(query, where_sql, args=()):
    """
    Run SELECT over dotted_test.items using the given WHERE SQL and
    args, return the matching ids sorted.
    """
    sql = f'SELECT id FROM dotted_test.items WHERE {where_sql}'
    rows = query(sql, args)
    return sorted(r['id'] for r in rows)


def _run_where(query, path, **bindings):
    """
    Translate `path`, build dollar-numeric, run the predicate against
    the seed data, return sorted matching ids.
    """
    r = sqlize(path)
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               **bindings)
    return _ids(query, sql, args)


# ---------------- scalar columns ----------------

def test_status_equality(query):
    assert _run_where(query, 'status = "active"') == [1, 2, 4, 5, 6]


def test_numeric_ge(query):
    assert _run_where(query, 'age >= 30') == [2, 3, 5, 6]


def test_numeric_lt(query):
    assert _run_where(query, 'age < 30') == [1, 4]  # id=7 has NULL, excluded


def test_not_equal(query):
    # id=7 has NULL status? no, status='pending'. NULL-safe: != excludes NULLs.
    assert _run_where(query, 'status != "banned"') == [1, 2, 4, 5, 6, 7]


def test_is_null(query):
    assert _run_where(query, 'deleted_at = None') == [1, 2, 3, 4, 5, 7]


def test_is_not_null(query):
    assert _run_where(query, 'deleted_at != None') == [6]


def test_regex_match(query):
    # status matching /^a/ — only 'active' starts with 'a'
    assert _run_where(query, 'status = /^a/') == [1, 2, 4, 5, 6]


# ---------------- jsonb nested access ----------------

def test_jsonb_numeric_guard(query):
    # user.age >= 30: values are 25,35,40,17,50,30,(missing)
    # Missing key yields NULL, excluded.
    assert _run_where(query, 'data.user.age >= 30') == [2, 3, 5, 6]


def test_jsonb_string_guard(query):
    assert _run_where(query, 'data.user.role = "admin"') == [1, 5]


def test_jsonb_boolean_guard(query):
    assert _run_where(query, 'data.user.active = True') == [1, 2, 4, 5]


def test_jsonb_null_guard(query):
    # data.user.age missing for id 7
    assert _run_where(query, 'data.user.age = None') == [7]


# ---------------- boolean grouping ----------------

def test_group_and(query):
    assert _run_where(query, '(status = "active" & age >= 30)') == [2, 5, 6]


def test_group_or(query):
    # status='banned' OR status='pending'
    assert _run_where(query,
                      '(status = "banned", status = "pending")') == [3, 7]


def test_group_not(query):
    # NOT (status = 'banned')
    assert _run_where(query, '(!status = "banned")') == [1, 2, 4, 5, 6, 7]


# ---------------- guard transforms (casts) ----------------

def test_guard_transform_int(query):
    assert _run_where(query, 'data.user.age|int >= 30') == [2, 3, 5, 6]


# ---------------- pattern paths ----------------

def test_pattern_slot_wildcard(query):
    # Any user in data.users[*] with age >= 30
    # id=1 has 32; id=2 has 35; id=3 has 40; id=5 has 50
    assert _run_where(query, 'data.users[*].age >= 30') == [1, 2, 3, 5]


def test_pattern_slot_wildcard_boolean(query):
    # Any user with active=True
    assert _run_where(query, 'data.users[*].active = True') == [1, 2, 4, 5]


def test_pattern_slice_filter(query):
    # Same as wildcard test, different syntax
    assert _run_where(query, 'data.users[age>=30]') == [1, 2, 3, 5]


def test_pattern_filter_wrap(query):
    assert _run_where(query, 'data.users[*&age>=30]') == [1, 2, 3, 5]


def test_pattern_filter_and_trailing_guard(query):
    # Filter users (age>=30) and look for one whose active is True
    # id=1 has 32/False → filtered out
    # id=2 has 35/True → match
    # id=3 has 40/False → filtered out
    # id=5 has 50/True → match
    assert _run_where(query,
                      'data.users[*&age>=30].active = True') == [2, 5]


def test_pattern_recursive(query):
    # Any nested field 'role' == 'admin'
    assert _run_where(query, 'data.**.role = "admin"') == [1, 5]


# ---------------- substitutions ----------------

def test_substitution(query):
    # Unbound substitution, supplied at build time
    assert _run_where(query,
                      'age >= $(min_age)',
                      min_age=30) == [2, 3, 5, 6]


def test_substitution_pattern(query):
    # Substitution inside jsonb_path_exists vars
    assert _run_where(query,
                      'data.users[*].age >= $(min)',
                      min=30) == [1, 2, 3, 5]


# ---------------- mixed pattern + scalar in group ----------------

def test_mixed_pattern_and_scalar(query):
    # Any user >= 30 AND outer status active
    # id=1 (active, has 32) match; id=2 (active, has 35) match;
    # id=3 (banned) excluded; id=5 (active, has 50) match
    assert _run_where(query,
                      '(data.users[*].age >= 30 & status = "active")') \
        == [1, 2, 5]
