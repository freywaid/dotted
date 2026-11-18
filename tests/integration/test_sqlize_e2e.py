"""
End-to-end integration tests: run sqlize output against the
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

Each test runs twice — once per driver (asyncpg / psycopg2) — via the
parametrized `query` fixture. The driver determines the paramstyle
`Resolver.build` emits (`dollar-numeric` for asyncpg, `pyformat` for
psycopg2) so both SQL shapes get exercised against real Postgres.
"""
import pytest
from dotted import sqlize


def _ids(query, where_sql, args=()):
    """
    Run SELECT over dotted_test.items using the given WHERE SQL and
    args, return the matching ids sorted.
    """
    sql = f'SELECT id FROM dotted_test.items WHERE {where_sql}'
    rows = query(sql, args)
    return sorted(r['id'] for r in rows)


def _rooted(query, path, root='column'):
    """
    Translate `path` for the active driver: as a column path over
    dotted_test.items, or under root='table' as a search of it.
    Returns the resolver and what it reads FROM.
    """
    if root == 'column':
        return sqlize(path, driver=query.driver), 'dotted_test.items'
    r = sqlize(f'dotted_test.items[*].{path}', driver=query.driver, root='table')
    return r, r.from_


def _run_where(query, path, root='column', **bindings):
    """
    Translate `path` for the active driver under `root`, render the
    WHERE clause using the driver's paramstyle/cast, run it against
    the seed data, return sorted matching ids.
    """
    r, _ = _rooted(query, path, root)
    sql, args = r.build(r.where, **bindings)
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
    # a regex fullmatches, as dotted's does: /a.*/ is 'active', and /^a/
    # matches no whole status
    assert _run_where(query, 'status = /a.*/') == [1, 2, 4, 5, 6]
    assert _run_where(query, 'status = /^a/') == []


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
    # a guard tests a value that's there: id=7 has no user.age, so no
    # row has a null one
    assert _run_where(query, 'data.user.age = None') == []


# ---------------- boolean grouping ----------------

def test_group_and(query):
    assert _run_where(query, '(status = "active" & age >= 30)') == [2, 5, 6]


def test_group_or(query):
    # status='banned' OR status='pending'
    assert _run_where(query,
                      '(status = "banned", status = "pending")') == [3, 7]


def test_group_of_traversals(query):
    # id=7 has no user.age and no users
    assert _run_where(query, 'data.(user.age, users[*].age)') \
        == [1, 2, 3, 4, 5, 6]


def test_and_group_per_element(query):
    # one user must be both 30+ and active: id=1's 32 is inactive, its
    # 25 active, so it is out
    assert _run_where(query,
                      'data.users[*].(age >= 30 & active = True)') == [2, 5]


def test_first_match_group(query):
    # the first user's age, or user's where there are no users: id=1's
    # first user is 25, so its 32 doesn't count
    assert _run_where(query, 'data.(users[*], user)?.age >= 30') \
        == [2, 3, 5, 6]


def test_guard_after_first_match_group(query):
    # only id=7 has no user.age, so only there is role the first match;
    # elsewhere the guard fails on age and doesn't go on to role
    assert _run_where(query, 'data.user.(age, role)? = "guest"') == [7]


def test_lateral_first_match_group(query):
    """
    One row per row with a first match: the first user's age, or
    user's where there are no users; id=7's user has no age.
    """
    r = sqlize('data.(users[*], user)?.age', driver=query.driver)
    sel_sql, _ = r.build(r.select)
    lat_sql, _ = r.build(r.lateral)
    sql = (f'SELECT id, {sel_sql} AS age '
           f'FROM dotted_test.items, {lat_sql}')
    rows = query(sql, ())
    pairs = sorted((r['id'], int(r['age'])) for r in rows)
    assert pairs == [(1, 25), (2, 35), (3, 40), (4, 17), (5, 50), (6, 30)]


def test_lateral_cut_group(query):
    """
    (age >= 30#, age >= 18) at each user: a user 30+ once, by the
    first branch; one 18 to 29 by the second; one under 18 not at all.
    """
    r = sqlize('data.users[*].(age >= 30#, age >= 18)', driver=query.driver)
    sel_sql, _ = r.build(r.select)
    lat_sql, _ = r.build(r.lateral)
    sql = (f'SELECT id, {sel_sql} AS age '
           f'FROM dotted_test.items, {lat_sql}')
    rows = query(sql, ())
    pairs = sorted((r['id'], int(r['age'])) for r in rows)
    assert pairs == [(1, 25), (1, 32), (2, 35), (3, 40), (5, 18), (5, 50)]


def _lateral_counts(query, path, root='column'):
    """
    id → how many rows the path's lateral yields for it, under `root`.
    """
    r, table = _rooted(query, path, root)
    sql, args = r.build('SELECT id, count(' + r.select + ') AS n FROM '
                        + table + ', ' + r.lateral + ' GROUP BY id')
    return {row['id']: row['n'] for row in query(sql, args)}


def _lateral_values(query, path, root='column'):
    """
    Sorted (id, JSON text) per row the path's lateral yields, under
    `root`.
    """
    r, table = _rooted(query, path, root)
    sql, args = r.build('SELECT id, (' + r.select + ')::text AS v FROM '
                        + table + ', ' + r.lateral)
    return sorted((row['id'], row['v']) for row in query(sql, args))


def test_lateral_soft_cut_group(query):
    """
    (age##, *) at user: * skips age, the soft-cut branch's match, so
    a user with an age yields three rows, not four; id=7's has none.
    """
    assert _lateral_counts(query, 'data.user.(age, *)') == {
        1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 1}
    assert _lateral_counts(query, 'data.user.(age##, *)') == {
        1: 3, 2: 3, 3: 3, 4: 3, 5: 3, 6: 3, 7: 1}


def test_lateral_soft_cut_per_element(query):
    """
    (age >= 30##, age) at each user: the age a soft cut kept isn't read
    again, so each user's age comes once.
    """
    r = sqlize('data.users[*].(age >= 30##, age)', driver=query.driver)
    sel_sql, _ = r.build(r.select)
    lat_sql, _ = r.build(r.lateral)
    rows = query(f'SELECT id, {sel_sql} AS age FROM dotted_test.items, {lat_sql}', ())
    pairs = sorted((r['id'], int(r['age'])) for r in rows)
    assert pairs == [(1, 25), (1, 32), (2, 35), (3, 40), (4, 17), (5, 18), (5, 50)]


def test_lateral_soft_cut_keeps_in_order(query):
    """
    ((.user.age, .user, .user.role)##, .*): user overlaps the age kept
    before it, so it is dropped, and role, overlapping only user, is
    kept; * then adds users. id=7's user has no age, so user is kept
    and role dropped.
    """
    assert _lateral_counts(query, 'data((.user.age, .user, .user.role)##, .*)') == {
        1: 3, 2: 3, 3: 3, 4: 3, 5: 3, 6: 3, 7: 2}


def test_lateral_negation(query):
    """
    user(!.age) is user's keys but age; id=7's user has only role.
    """
    assert _lateral_counts(query, 'data.user(!.age)') == {
        1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 1}


def test_lateral_negation_splits_a_node_a_match_runs_through(query):
    """
    data(!.user.age): user holds a match, so it is split, role and
    active kept; users is kept whole. id=7's user has no age, so it
    is kept whole.
    """
    assert _lateral_counts(query, 'data(!.user.age)') == {
        1: 3, 2: 3, 3: 3, 4: 3, 5: 3, 6: 3, 7: 2}


def test_lateral_negation_through_elements(query):
    """
    users(![*].active): each user is split, its age kept; ids 6 and 7
    have no users.
    """
    assert _lateral_counts(query, 'data.users(![*].active)') == {
        1: 2, 2: 1, 3: 1, 4: 1, 5: 2}


def test_negation_where(query):
    # id=7's user has only role, so nothing is left of it but role
    assert _run_where(query, 'data.user(!.role)') == [1, 2, 3, 4, 5, 6]


def test_lateral_group_continues_after(query):
    """
    The path after a group runs on from each branch: user.age, then
    every users[*].age, one row each.
    """
    r = sqlize('data.(user, users[*]).age', driver=query.driver)
    sel_sql, _ = r.build(r.select)
    lat_sql, _ = r.build(r.lateral)
    sql = (f'SELECT id, {sel_sql} AS age '
           f'FROM dotted_test.items, {lat_sql}')
    rows = query(sql, ())
    pairs = sorted((r['id'], int(r['age'])) for r in rows)
    assert pairs == [(1, 25), (1, 25), (1, 32), (2, 35), (2, 35),
                     (3, 40), (3, 40), (4, 17), (4, 17),
                     (5, 18), (5, 50), (5, 50), (6, 30)]


# ---------------- groups over the row's columns ----------------

ROOTS = ['column', 'table']


@pytest.mark.parametrize('root', ROOTS)
@pytest.mark.parametrize('path, expected', [
    ('(age >= 30#, status = "active")', [1, 2, 3, 4, 5, 6]),
    ('(age >= 30##, status = "active")', [1, 2, 3, 4, 5, 6]),
    ('(age >= 30 & status = "active")', [2, 5, 6]),
    ('(data.users[*], data.user)?.age >= 30', [2, 3, 5, 6]),
    ('(status = "banned", data.user.role = "guest")', [3, 7]),
])
def test_row_group_where(query, root, path, expected):
    """
    A group over the row's columns keeps the rows where it matches, in
    a table search as in a column path. A first match is the first
    user, or user where there are none: id=1's first user is 25.
    """
    assert _run_where(query, path, root) == expected


@pytest.mark.parametrize('root', ROOTS)
@pytest.mark.parametrize('path, expected', [
    ('(status, age)',
     [(1, '"active"'), (1, '25'), (2, '"active"'), (2, '35'),
      (3, '"banned"'), (3, '40'), (4, '"active"'), (4, '17'),
      (5, '"active"'), (5, '50'), (6, '"active"'), (6, '30'),
      (7, '"pending"'), (7, 'null')]),
    ('(age >= 30#, status = "active")',
     [(1, '"active"'), (2, '35'), (3, '40'), (4, '"active"'), (5, '50'),
      (6, '30')]),
    ('(age >= 30 & status = "active")',
     [(2, '"active"'), (2, '35'), (5, '"active"'), (5, '50'),
      (6, '"active"'), (6, '30')]),
    ('(data.user.age##, data.user)',
     [(1, '25'), (2, '35'), (3, '40'), (4, '17'), (5, '50'), (6, '30'),
      (7, '{"role": "guest"}')]),
    ('(data.users[*], data.user)?.age',
     [(1, '25'), (2, '35'), (3, '40'), (4, '17'), (5, '50'), (6, '30')]),
    ('(data.user.role = "admin", status = "banned")',
     [(1, '"admin"'), (3, '"banned"'), (5, '"admin"')]),
])
def test_row_group_lateral(query, root, path, expected):
    """
    A group over the row's columns yields its matches as `get` does,
    in a table search as in a column path: after a cut, status only
    where age isn't 30+; after a soft cut, user only where it holds no
    age; id=7's age is NULL, a JSON null.
    """
    assert _lateral_values(query, path, root) == expected


def test_row_negation(query):
    """
    In a table search, a negation over the row's columns is the
    complement among all five: (!.data) is the other four, each whole;
    (!(status = "banned")) all five but status where it is banned.
    """
    assert _lateral_counts(query, '(!.data)', 'table') == {
        1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4}
    assert _lateral_counts(query, '(!(status = "banned"))', 'table') == {
        1: 5, 2: 5, 3: 4, 4: 5, 5: 5, 6: 5, 7: 5}


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
    # a slice filter reads the filtered list as one value, there even
    # when empty, so every row has one
    assert _run_where(query, 'data.users[age>=30]') == [1, 2, 3, 4, 5, 6, 7]


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


# ---------------- pattern paths as LATERAL extract ----------------

def test_lateral_extracts_matching_values(query):
    """
    Composing r.select + r.lateral gives one row per matching user.age,
    joined back to the source row via the items table in the FROM.

    Seed ages (non-deleted rows): 1→32(deleted? no), 2→35, 3→40,
    5→50 (also 18 — excluded by guard). id=6 has users=[] so no rows
    from it.
    """
    r = sqlize('data.users[*].age >= 30', driver=query.driver)
    sel_sql, _ = r.build(r.select)
    lat_sql, lat_args = r.build(r.lateral)
    sql = (f'SELECT id, {sel_sql} AS matched_age '
           f'FROM dotted_test.items, {lat_sql} '
           f'ORDER BY id, matched_age')
    rows = query(sql, lat_args)
    # JSONB numbers come back as int in asyncpg / psycopg2 (RealDictCursor)
    pairs = sorted((r['id'], int(r['matched_age'])) for r in rows)
    assert pairs == [(1, 32), (2, 35), (3, 40), (5, 50)]


def test_lateral_no_guard_extracts_all_values(query):
    """
    With no guard, LATERAL produces every element — id=5 contributes
    50 and 18 (both ages).
    """
    r = sqlize('data.users[*].age', driver=query.driver)
    sel_sql, _ = r.build(r.select)
    lat_sql, _ = r.build(r.lateral)
    sql = (f'SELECT id, {sel_sql} AS age '
           f'FROM dotted_test.items, {lat_sql} '
           f'ORDER BY id, age')
    rows = query(sql, ())
    pairs = sorted((r['id'], int(r['age'])) for r in rows)
    # id=1 → 25, 32; id=2 → 35; id=3 → 40; id=4 → 17; id=5 → 18, 50;
    # id=6 and id=7 contribute nothing (users: []).
    assert pairs == [(1, 25), (1, 32), (2, 35), (3, 40),
                     (4, 17), (5, 18), (5, 50)]


# ---------------- table search ----------------

def test_table_search(query):
    """
    A table search names the table, filters and pages its rows, and
    selects a column; the caller assembles the query. Active ids are
    1, 2, 4, 5, 6 — offset 1, limit 2 keeps 2 and 4.
    """
    r = sqlize('dotted_test.items[status="active"][1:3][*].id',
               driver=query.driver, root='table')
    sql, args = r.build(
        'SELECT ' + r.select + ' FROM ' + r.from_ + ' WHERE ' + r.where
        + f' ORDER BY id OFFSET {r.offset} LIMIT {r.limit}')
    assert [row['id'] for row in query(sql, args)] == [2, 4]


def test_table_search_whole_rows(query):
    """
    With nothing after the row step, `select` is `*`.
    """
    r = sqlize('dotted_test.items[*&data.user.role="admin"]',
               driver=query.driver, root='table')
    sql, args = r.build(
        'SELECT ' + r.select + ' FROM ' + r.from_ + ' WHERE ' + r.where
        + ' ORDER BY id')
    rows = query(sql, args)
    assert [row['id'] for row in rows] == [1, 5]
    assert {'id', 'status', 'age', 'deleted_at', 'data'} <= set(rows[0])


# ---------------- groups of row steps ----------------

def _search_ids(query, path):
    """
    The ids of the rows a table search over dotted_test.items yields,
    a row as often as it comes, sorted.
    """
    r = sqlize(f'dotted_test.items{path}', driver=query.driver, root='table')
    sql = 'SELECT id FROM ' + r.from_
    if r.where is not None:
        sql = sql + ' WHERE ' + r.where
    sql, args = r.build(sql)
    return sorted(row['id'] for row in query(sql, args))


@pytest.mark.parametrize('path, expected', [
    ('[(*&status="active", *&age>=30)]', [1, 2, 2, 3, 4, 5, 5, 6, 6]),
    ('[(*&status="banned"#, *&status="active")]', [3]),
    ('[(*&status="gone"#, *&status="active")]', [1, 2, 4, 5, 6]),
    ('[(*&status="active", *&age>=30)].age >= 40', [3, 5, 5]),
    ('[(*&status="active"#, *&age>=30)].age >= 40', [5]),
    ('[(*&status="active"##, *&age>=30)]', [1, 2, 3, 4, 5, 6]),
    ('[(*&status="gone", *&status="banned")?]', [3]),
    ('([*&status="banned"] & [*&age>=40])', [3, 3, 5]),
    ('([*&status="gone"] & [*&age>=40])', []),
    ('(![*&status="active"])', [3, 7]),
    ('(![*&age>=30])', [1, 4, 7]),
])
def test_row_step_group(query, path, expected):
    """
    A group as the row step yields each branch's rows in turn, a row
    both reach twice. A cut holds over the table: banned rows exist,
    so no active one comes; with a guard after, only an active row 40+
    stops the rest. A soft cut skips the active rows the second branch
    reaches again. A first match is the first branch's first row; an
    `&` needs every branch to reach one. A negation is the rows its
    step doesn't reach, id=7's NULL age among them.
    """
    assert _search_ids(query, path) == expected
