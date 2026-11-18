"""
Tests for dotted.translators (sqlize) — dotted path → SQL clause components.

sqlize(path, driver=...) returns a driver-specific Resolver with SQL
fragments. `r.build(sql, **bindings)` uses the driver's paramstyle;
`Resolver.build(sql, paramstyle=..., **bindings)` is the low-level
classmethod this file exercises to cover every paramstyle emitter.

A local `sqlize` wrapper defaults `driver='asyncpg'` because these
tests don't care which driver picks the class as long as translation
runs. Every concrete render in this file goes through the classmethod
`Resolver.build(paramstyle=...)` which ignores the driver's paramstyle.
"""
import re

import pytest
from dotted.translators import (
    sqlize as _sqlize, Resolver, SQLFragment, ParamStyle, ParamPool,
    Raw, Col, TranslationError,
)


def sqlize(path, *, driver='asyncpg', bindings=None, pool=None, root='column'):
    """
    Test-file shim: fills in a default driver so existing test calls
    `sqlize('path')` continue to work without touching every call site.
    """
    return _sqlize(path, driver=driver, bindings=bindings, pool=pool, root=root)


# All supported paramstyles as fixture values. Used to parametrize
# scenario tests so each runs under every paramstyle.
PARAMSTYLES = ['named', 'pyformat', 'qmark', 'format', 'numeric', 'dollar-numeric']

# Styles that share bindings by back-reference (repeated markers collapse
# to one entry). qmark/format have no back-reference and repeat values.
BACKREF_STYLES = ['named', 'pyformat', 'numeric', 'dollar-numeric']

# Styles that return a dict vs a list.
DICT_STYLES = ['named', 'pyformat']


# ---------------- scalar columns ----------------

def test_bare_column():
    r = sqlize('status')
    sql, params = Resolver.build(r.select)
    assert sql == 'status'
    assert params == {}
    assert r.where is None


def test_column_eq_string():
    r = sqlize('status="active"')
    assert Resolver.build(r.where) == ('status = :_p1', {'_p1': 'active'})
    assert Resolver.build(r.where, paramstyle='dollar-numeric') == (
        'status = $1', ['active'])


def test_column_eq_numeric():
    r = sqlize('age=30')
    assert Resolver.build(r.where) == ('age = :_p1', {'_p1': 30})


def test_column_ordering():
    r = sqlize('age>=30')
    assert Resolver.build(r.where) == ('age >= :_p1', {'_p1': 30})
    r = sqlize('age<18')
    assert Resolver.build(r.where) == ('age < :_p1', {'_p1': 18})


def test_column_ne():
    """
    `!=` holds for a NULL, as Python's `None != x` does.
    """
    r = sqlize('status!="banned"')
    assert Resolver.build(r.where) == (
        'status IS DISTINCT FROM :_p1', {'_p1': 'banned'})


def test_column_null():
    r = sqlize('deleted_at=None')
    assert Resolver.build(r.where) == ('deleted_at IS NULL', {})


def test_column_not_null():
    r = sqlize('deleted_at!=None')
    assert Resolver.build(r.where) == ('deleted_at IS NOT NULL', {})


def test_column_regex():
    """
    A regex fullmatches, as dotted's does: anchored at both ends.
    """
    r = sqlize('name=/^alice/')
    assert Resolver.build(r.where) == ('name ~ :_p1', {'_p1': '^(?:^alice)$'})


def test_column_regex_ne():
    """
    `!=` a regex holds where it doesn't fullmatch, a NULL included.
    """
    r = sqlize('name!=/^alice/')
    assert Resolver.build(r.where) == (
        '(name ~ :_p1) IS NOT TRUE', {'_p1': '^(?:^alice)$'})


# ---------------- jsonb traversal ----------------

def test_jsonb_select_only():
    r = sqlize('data.user.name')
    assert Resolver.build(r.select) == ("data #>> '{user,name}'", {})
    assert r.where is None


def test_jsonb_single_segment():
    r = sqlize('data.status')
    assert Resolver.build(r.select) == ("data #>> '{status}'", {})


def test_jsonb_string_guard():
    """
    A guard inside a column compares the value there, if any, by its
    own type: a string with a string.
    """
    r = sqlize('data.user.role="admin"')
    assert Resolver.build(r.where) == (
        "(SELECT (_pat1.n IS NOT NULL AND (CASE WHEN jsonb_typeof(_pat1.n) = 'string' THEN (_pat1.n #>> '{}') = :_p1 ELSE FALSE END)) FROM (SELECT data #> '{user,role}' AS n) AS _pat1)",
        {'_p1': 'admin'},
    )


def test_jsonb_numeric_guard():
    """
    A number compares with a number, and a string there is no match,
    not a failed cast.
    """
    r = sqlize('data.user.age>=30')
    assert Resolver.build(r.where) == (
        "(SELECT (_pat1.n IS NOT NULL AND (CASE jsonb_typeof(_pat1.n) WHEN 'number' THEN (_pat1.n)::numeric >= (30) ELSE FALSE END)) FROM (SELECT data #> '{user,age}' AS n) AS _pat1)",
        {},
    )


def test_jsonb_boolean_guard():
    """
    True compares as 1, as Python's does: a boolean or the number 1.
    """
    r = sqlize('data.user.active=True')
    assert Resolver.build(r.where) == (
        "(SELECT (_pat1.n IS NOT NULL AND (CASE jsonb_typeof(_pat1.n) WHEN 'number' THEN (_pat1.n)::numeric = (1) WHEN 'boolean' THEN _pat1.n = 'true'::jsonb ELSE FALSE END)) FROM (SELECT data #> '{user,active}' AS n) AS _pat1)",
        {},
    )


def test_jsonb_boolean_ne():
    """
    `!=` is `=`'s negation, on a value that is there.
    """
    r = sqlize('data.user.active!=True')
    sql, _ = Resolver.build(r.where)
    assert sql.startswith('(SELECT (_pat1.n IS NOT NULL AND NOT (CASE ')


def test_jsonb_null_guard():
    """
    `= None` matches a JSON null that is there, not a missing key.
    """
    r = sqlize('data.score=None')
    assert Resolver.build(r.where) == (
        "(SELECT (_pat1.n IS NOT NULL AND (jsonb_typeof(_pat1.n) = 'null')) FROM (SELECT data #> '{score}' AS n) AS _pat1)", {})


def test_jsonb_not_null_guard():
    """
    `!= None` matches any value that is there but a JSON null.
    """
    r = sqlize('data.score!=None')
    assert Resolver.build(r.where) == (
        "(SELECT (_pat1.n IS NOT NULL AND NOT (jsonb_typeof(_pat1.n) = 'null')) FROM (SELECT data #> '{score}' AS n) AS _pat1)", {})


def test_jsonb_regex():
    """
    A regex fullmatches a string there, and a number as Python writes
    it; it matches no boolean or null whose str() it doesn't.
    """
    r = sqlize('data.user.name=/alice/')
    sql, params = Resolver.build(r.where)
    assert params == {'_p1': '^(?:alice)$'}
    assert "WHEN 'string' THEN (_pat1.n #>> '{}') ~ :_p1" in sql
    assert "WHEN 'boolean' THEN FALSE WHEN 'null' THEN FALSE" in sql


# ---------------- guard transforms (casts) ----------------

def test_guard_transform_int():
    """
    `|int` is dotted's int(): a number truncated, a boolean 0 or 1, a
    string of an int that int; the comparison then reads its type.
    """
    sql, _ = Resolver.build(sqlize('data.user.age|int>=30').where)
    assert 'to_jsonb(trunc((_pat1.n)::numeric))' in sql
    assert "(_pat2.n)::numeric >= (30)" in sql


def test_guard_transform_float():
    """
    `|float` is dotted's float(), a whole number with its `.0`.
    """
    sql, _ = Resolver.build(sqlize('data.user.score|float>=#"0.5"').where)
    assert '::float8::numeric + 0.0' in sql
    assert "(_pat2.n)::numeric >= (0.5)" in sql


def test_guard_transform_str():
    """
    `|str` is str(): a boolean `True` / `False`, a null `None`.
    """
    r = sqlize('data.user.id|str="42"')
    sql, params = Resolver.build(r.where)
    assert params == {'_p1': '42'}
    assert """'"True"'::jsonb""" in sql and """'"None"'::jsonb""" in sql


def test_guard_transform_unknown():
    with pytest.raises(TranslationError):
        sqlize('data.user.name|uppercase="ALICE"')


# ---------------- boolean groups ----------------

def test_group_and():
    """
    A group of guards keeps its guards' own predicates, joined.
    """
    r = sqlize('data.(user.age>=30 & status="active")')
    sql, params = Resolver.build(r.where)
    assert sql.startswith('((SELECT (_pat1.n IS NOT NULL')
    assert ') AND ((SELECT (_pat2.n IS NOT NULL' in sql
    assert params == {'_p1': 'active'}


def test_group_or():
    """
    `,` joins its guards' predicates with OR.
    """
    r = sqlize('data.(user.age=18, user.age=21)')
    sql, params = Resolver.build(r.where)
    assert ') OR ((SELECT (_pat2.n IS NOT NULL' in sql
    assert '= (18)' in sql and '= (21)' in sql
    assert params == {}


def test_guard_group_selects_matches():
    """
    A group of guards keeps its plain predicates for `where`; its
    `select` is its matches, as get gives them: a lateral of each
    branch's rows in turn.
    """
    r = sqlize('data.user.(age>=30, age<18)')
    where, _ = Resolver.build(r.where)
    assert ') OR (' in where
    alias = Resolver.build(r.select)[0].split('.')[0]
    lateral, _ = Resolver.build(r.lateral)
    assert lateral.startswith('LATERAL (SELECT ')
    assert lateral.endswith(f') AS {alias}(value)')
    assert ' UNION ALL ' in lateral


def test_and_group_lateral_needs_every_branch():
    """
    An `&` group's matches are there only where every branch's way
    reaches something from the node: an EXISTS per way.
    """
    lateral, params = Resolver.build(
        sqlize('data.(user.age>=30 & status="active")').lateral)
    assert ') AND (EXISTS (' in lateral
    assert list(params.values()) == ['active']


def test_group_bare_traversal():
    """
    A branch without a guard is a traversal: the group's matches are
    each branch's, read a step at a time.
    """
    r = sqlize('data.(x=1, y)')
    where, _ = Resolver.build(r.where)
    assert where.startswith('EXISTS (')
    lateral, _ = Resolver.build(r.lateral)
    assert "-> 'x'" in lateral and "-> 'y'" in lateral
    assert ' UNION ALL ' in lateral


def test_group_continues_after():
    """
    The path after a group runs on from each branch: its steps read
    under each way through the groups.
    """
    r = sqlize('event.(data, data.components[*]).(amount, reporting_amount)')
    lateral, _ = Resolver.build(r.lateral)
    for key in ('data', 'components', 'amount', 'reporting_amount'):
        assert f"-> '{key}'" in lateral
    assert Resolver.build(r.where)[0].startswith('EXISTS (')


def test_and_group_in_pattern_is_per_node():
    """
    An `&` group after `[*]` holds per element: the EXISTS of each way
    reads from the element.
    """
    lateral, _ = Resolver.build(sqlize('data.users[*].(age & active)').lateral)
    assert ') AND (EXISTS (' in lateral
    assert 'jsonb_array_elements' in lateral


def test_top_level_group_continues_after():
    """
    The path after a top-level group runs on from each branch's column.
    """
    sql, params = Resolver.build(sqlize('(data, meta).user.age>=30').where)
    assert "(SELECT data #> '{user,age}' AS n)" in sql
    assert ') OR (' in sql
    assert "(SELECT meta #> '{user,age}' AS n)" in sql
    assert params == {}


def test_first_match_group():
    """
    A first-match group is the first match of the first branch to have
    one, in dotted's order, the path after it run on from it.
    """
    r = sqlize('data.(a, b)?.c')
    for sql in (Resolver.build(r.where)[0], Resolver.build(r.lateral)[0]):
        assert 'ORDER BY' in sql and 'LIMIT 1' in sql
        assert "-> 'c'" in sql


def test_first_match_group_per_node():
    """
    After `[*]`, the first match is each element's: a lateral at each.
    """
    lateral, _ = Resolver.build(sqlize('data.users[*].(a, b)?').lateral)
    assert 'jsonb_array_elements' in lateral
    assert 'CROSS JOIN LATERAL' in lateral and 'LIMIT 1' in lateral


def test_guard_after_first_match_group():
    """
    A guard after a first-match group tests the first match; it does
    not go on to the next branch.
    """
    sql, _ = Resolver.build(sqlize('data.(a, b)?=1').where)
    first = sql.index('LIMIT 1')
    assert sql.index('= (1)') > first


def test_first_match_of_a_branch_through_a_group():
    """
    A branch through a group gives its first match in the order dotted
    reaches them: the first row by ord.
    """
    lateral, _ = Resolver.build(sqlize('data.(a[*].(q, p))?').lateral)
    assert 'ORDER BY' in lateral and 'LIMIT 1' in lateral
    assert "-> 'q'" in lateral and "-> 'p'" in lateral


def test_top_level_first_match_group():
    """
    At the top of a path, a first match is there where some branch
    matches.
    """
    r = sqlize('(age=18, status="a")?')
    assert Resolver.build(r.where) == (
        '(age = :_p1) OR (status = :_p2)', {'_p1': 18, '_p2': 'a'})


def test_cut_group():
    """
    (a#, b): b's way yields only where a's reaches nothing, a NOT EXISTS
    at the node; a cut changes which branches yield, not whether the
    group matches, so `where` reads the group without it.
    """
    r = sqlize('data.(a#, b)')
    assert 'NOT EXISTS' in Resolver.build(r.lateral)[0]
    assert Resolver.build(r.where) == Resolver.build(sqlize('data.(a, b)').where)


def test_cut_group_per_element():
    """
    After [*], the cut holds at each element: b where it has no a.
    """
    lateral, _ = Resolver.build(sqlize('data.x[*].(a#, b)').lateral)
    assert 'NOT EXISTS' in lateral and 'jsonb_array_elements' in lateral


def test_cut_in_conjunction():
    """
    (a#&b): the conjunction holds only where a matches, and there the
    cut ends it, so b is never read.
    """
    r = sqlize('data.(a#&b)')
    for sql in (Resolver.build(r.where)[0], Resolver.build(r.lateral)[0]):
        assert "-> 'a'" in sql
        assert "-> 'b'" not in sql


def test_cut_in_first_match_group():
    """
    A cut changes nothing in a first-match group, whose first match
    ends it anyway.
    """
    assert (Resolver.build(sqlize('data.(a#, b)?').lateral)
            == Resolver.build(sqlize('data.(a, b)?').lateral))


def test_soft_cut_group_where_is_the_groups():
    """
    A soft cut changes which matches a group yields, not whether it
    yields any: the where is the plain group's.
    """
    assert (Resolver.build(sqlize('data.(a##, *)').where)
            == Resolver.build(sqlize('data.(a, *)').where))


def test_soft_cut_group_lateral_tracks_paths():
    """
    JSONPath gives no match's path, so a soft cut's lateral walks the
    ways with their paths and folds them in order, a recursive query
    keeping what no soft-cut branch's match overlaps.
    """
    sql, _ = Resolver.build(sqlize('data.(a##, *)').lateral)
    assert 'jsonb_path_query' not in sql
    assert "to_jsonb('a'::text)::text || ','" in sql
    assert 'jsonb_each(CASE WHEN jsonb_typeof(' in sql
    assert 'WITH RECURSIVE' in sql
    assert 'starts_with(' in sql


def test_negation_is_a_complement():
    """
    data.(!(status="banned")) is the complement of status="banned":
    the largest subtrees with no match of it, found by a recursive
    query over the children its matches run through; where is whether
    there is one, not NOT (status = 'banned').
    """
    r = sqlize('data.(!(status="banned"))')
    where, _ = Resolver.build(r.where)
    assert where.startswith('EXISTS (')
    assert 'NOT ((data' not in where
    lateral, _ = Resolver.build(r.lateral)
    assert 'WITH RECURSIVE' in lateral
    assert 'jsonb_each(CASE WHEN jsonb_typeof(' in lateral


def test_negation_by_key_reads_no_array_at_its_node():
    """
    A key reads an object's members only, so (!x) splits no array at
    its node, its own path; a slot reads an array's elements.
    """
    by_key, _ = Resolver.build(sqlize('data.(!x)').lateral)
    by_slot, _ = Resolver.build(sqlize('data.(![0])').lateral)
    node = r"= 'array' AND _pat\d+\.path <> _pat\d+\.path THEN"
    assert re.search(node, by_key)
    assert not re.search(node, by_slot)


@pytest.mark.parametrize('path', [
    'data.(**:2##, y)',
    'data.**:1',
    'data.*(*, [*])',
])
def test_group_not_supported_yet_raises(path):
    """
    A ** with depths or accessors raises rather than translate to
    anything else.
    """
    with pytest.raises(TranslationError, match='not supported yet'):
        sqlize(path)


def test_top_level_and_across_columns():
    r = sqlize('(age>=18 & status="active")')
    assert Resolver.build(r.where) == (
        '(age >= :_p1) AND (status = :_p2)',
        {'_p1': 18, '_p2': 'active'},
    )


def test_top_level_or_across_columns():
    r = sqlize('(age=18, age=21, age=25)')
    assert Resolver.build(r.where) == (
        '(age = :_p1) OR (age = :_p2) OR (age = :_p3)',
        {'_p1': 18, '_p2': 21, '_p3': 25},
    )


def test_top_level_mixed_columns_and_jsonb():
    """
    A top-level group joins a JSONB guard and a column's guard.
    """
    sql, params = Resolver.build(sqlize('(data.user.age>=30 & status="active")').where)
    assert sql.endswith(') AND (status = :_p1)')
    assert "(SELECT data #> '{user,age}' AS n)" in sql
    assert params == {'_p1': 'active'}


# ---------------- groups over the row's columns ----------------

_ROW_GROUPS = [
    '(title, author)',
    '(title & author)',
    '(title, author)?',
    '(title, author)?.x',
    '(title#, author)',
    '(title#&author)',
    '(title##, author)',
    '(title##&author)',
    '(data, meta).user.age>=30',
    '(age>=18 & status="active")',
]


@pytest.mark.parametrize('path', _ROW_GROUPS)
def test_row_group_reads_the_same_under_either_root(path):
    """
    A group at the top of a path reads the row's columns as one value,
    the same in a table search as in a column path: its matches are
    the lateral's, its where whether there is one.
    """
    column = sqlize(path)
    table = sqlize(f'hello[*].{path}', root='table')
    assert table.table == 'hello'
    assert sorted(column) == ['lateral', 'select', 'where']
    assert sorted(table) == ['from_', 'lateral', 'select', 'where']
    for name in ('select', 'where', 'lateral'):
        assert Resolver.build(table[name]) == Resolver.build(column[name])


def test_row_group_reads_its_columns():
    """
    The row is an object of the columns the branches read, and the
    group's lateral each branch's matches in turn.
    """
    r = sqlize('hello[*](.title, .author)', root='table')
    lateral, _ = Resolver.build(r.lateral)
    assert "jsonb_build_object('title', title, 'author', author) AS value" in lateral
    assert ' UNION ALL ' in lateral
    assert lateral.endswith(f') AS {Resolver.build(r.select)[0].split(".")[0]}(value)')


def test_row_group_of_guards_keeps_plain_where():
    """
    A group of guards over the row keeps its plain predicates, after
    the row filters; its lateral reads the row's columns.
    """
    r = sqlize('hello[*&a=1].(age>=18 & status="active")', root='table')
    assert Resolver.build(r.where) == (
        '(a = :_p1) AND ((age >= :_p2) AND (status = :_p3))',
        {'_p1': 1, '_p2': 18, '_p3': 'active'})
    lateral, _ = Resolver.build(r.lateral)
    assert "jsonb_build_object('age', age, 'status', status)" in lateral


def test_row_cut_group():
    """
    (title#, author) at the row: author's way yields only where
    title's reaches nothing.
    """
    lateral, _ = Resolver.build(sqlize('hello[*].(title#, author)', root='table').lateral)
    assert "jsonb_build_object('title', title, 'author', author)" in lateral
    assert 'NOT EXISTS' in lateral


def test_row_cut_in_conjunction():
    """
    (title#&author) at the row: the cut ends the conjunction, so the
    row reads title alone.
    """
    where, _ = Resolver.build(sqlize('hello[*].(title#&author)', root='table').where)
    assert "jsonb_build_object('title', title) AS value" in where
    assert 'author' not in where


def test_row_soft_cut_group_lateral_tracks_paths():
    """
    (title##, author) at the row: the lateral walks the row's columns
    with their paths and folds them in order.
    """
    lateral, _ = Resolver.build(
        sqlize('hello[*].(title##, author)', root='table').lateral)
    assert "jsonb_build_object('title', title, 'author', author) AS value" in lateral
    assert 'WITH RECURSIVE' in lateral


def test_row_first_match_group_continues_after():
    """
    The path after a first-match group over the row runs on from the
    match: the first user, or user where there are none.
    """
    where, _ = Resolver.build(
        sqlize('hello[*].(data.users[*], data.user)?.age', root='table').where)
    assert "jsonb_build_object('data', data) AS value" in where
    assert where.index('LIMIT 1') < where.rindex("-> 'age'")


@pytest.mark.parametrize('path', [
    'hello[*](!.title)',
    'public.hello[*](!.title)',
    'hello[*].(!(status="banned"))',
    'hello[*].(!(status="banned"), title)',
])
def test_row_negation_reads_the_whole_row(path):
    """
    A negation over the row's columns is the complement among all of
    them, so it reads the table's whole row, named without its schema.
    """
    r = sqlize(path, root='table')
    for name in ('where', 'lateral'):
        sql, _ = Resolver.build(r[name])
        assert 'to_jsonb(hello)' in sql
        assert 'WITH RECURSIVE' in sql


@pytest.mark.parametrize('path, root', [
    ('!(status="banned")', 'column'),
    ('(!(status="banned"), title)', 'column'),
    ('[*](!.title)', 'table'),
])
def test_row_negation_needs_a_named_table(path, root):
    """
    A negation at the top of a path is the complement of the row's
    columns: a column path, or a search of an unnamed table, has no
    row to read them all from.
    """
    with pytest.raises(TranslationError, match="row's columns"):
        sqlize(path, root=root)


# ---------------- absolute root references ----------------

def test_ref_select():
    r = sqlize('data.$$(data.config.field)')
    assert Resolver.build(r.select) == (
        "data ->> (data #>> '{config,field}')", {}
    )


def test_ref_with_guard():
    """
    A guard on a member a reference names compares the value there.
    """
    sql, params = Resolver.build(sqlize('data.$$(data.config.field)="Alice"').where)
    assert sql.endswith("FROM (SELECT data -> (data #>> '{config,field}') AS n) AS _pat1)")
    assert params == {'_p1': 'Alice'}


def test_ref_mid_path():
    r = sqlize('data.users.$$(data.config.key).name')
    assert Resolver.build(r.select) == (
        "data -> 'users' -> (data #>> '{config,key}') ->> 'name'", {}
    )


def test_ref_numeric_guard():
    """
    A number there compares as a number.
    """
    sql, params = Resolver.build(sqlize('data.$$(data.idx)=30').where)
    assert "(_pat1.n)::numeric = (30)" in sql
    assert "(SELECT data -> (data #>> '{idx}') AS n)" in sql
    assert params == {}


def test_ref_relative_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$$(^field)')


def test_ref_parent_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$$(^^field)')


# ---------------- pattern paths (jsonb_path_exists) ----------------

def test_pattern_slot_wildcard_guard_numeric():
    """
    A pattern path's where is whether its walk reaches a match: each
    element, its age, the guard on that.
    """
    sql, params = Resolver.build(sqlize('data.users[*].age >= 30').where)
    assert sql.startswith('EXISTS (')
    assert "-> 'users'" in sql
    assert 'jsonb_array_elements' in sql
    assert "-> 'age'" in sql
    assert '.value)::numeric >= (30)' in sql
    assert params == {}


def test_pattern_slot_wildcard_guard_boolean():
    """
    True compares as 1, a boolean or the number 1.
    """
    sql, params = Resolver.build(sqlize('data.users[*].active = True').where)
    assert sql.startswith('EXISTS (')
    assert ".value = 'true'::jsonb ELSE FALSE END" in sql
    assert '= (1)' in sql
    assert params == {}


def test_pattern_key_wildcard_guard_string():
    """
    A key wildcard reads an object's members; a string compares with
    a string, hoisted.
    """
    sql, params = Resolver.build(sqlize('data.*.status = "on"').where)
    assert sql.startswith('EXISTS (')
    assert 'jsonb_each' in sql
    assert ".value #>> '{}') = :_p1" in sql
    assert params == {'_p1': 'on'}


def test_pattern_recursive_guard():
    """
    ** reads every value under the node by keys, a recursive query.
    """
    sql, params = Resolver.build(sqlize('data.**.flag = True').where)
    assert sql.startswith('EXISTS (')
    assert 'WITH RECURSIVE' in sql
    assert "-> 'flag'" in sql
    assert params == {}


def test_pattern_slice_filter():
    """
    A slice filter reads the filtered list as one value, as dotted
    does: its where holds for every array, the list kept or empty.
    """
    sql, _ = Resolver.build(sqlize('data.users[age>=30]').where)
    assert 'jsonb_agg(' in sql
    assert "FILTER (WHERE (jsonb_typeof(" in sql


def test_pattern_filter_wrap():
    """
    A filter keeps the elements that are objects and whose key's value
    compares.
    """
    sql, params = Resolver.build(sqlize('data.users[*&age>=30]').where)
    assert sql.startswith('EXISTS (')
    assert re.search(r"\(jsonb_typeof\(_pat\d+\.value\) = 'object' AND EXISTS \(SELECT 1 FROM", sql)
    assert '>= (30)' in sql
    assert params == {}


def test_pattern_filter_and_trailing_guard():
    """
    A filter, then the path on from what it keeps, then a guard.
    """
    sql, params = Resolver.build(sqlize('data.users[*&age>=30].name = "alice"').where)
    assert sql.startswith('EXISTS (')
    assert '>= (30)' in sql
    assert "-> 'name'" in sql
    assert "#>> '{}') = :_p1" in sql
    assert params == {'_p1': 'alice'}


def test_pattern_with_substitution():
    """
    A substitution compares as its binding's JSON, read by its type at
    run time: the binding goes as JSON text, cast to jsonb.
    """
    r = sqlize('data.users[*].age >= $(min_age)')
    sql, params = Resolver.build(r.where, min_age=30)
    assert '(SELECT :min_age__json::jsonb AS n)' in sql
    assert params == {'min_age__json': '30'}


def test_pattern_with_substitution_dollar():
    """
    Positionally, the JSON text is one argument, a jsonb placeholder.
    """
    r = sqlize('data.users[*].age >= $(min_age)')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric', min_age=30)
    assert '(SELECT $1::jsonb AS n)' in sql
    assert args == ['30']


def test_pattern_mixed_in_group():
    """
    A top-level group joins a pattern path's where and a column's guard.
    """
    sql, params = Resolver.build(sqlize('(data.users[*].age >= 30 & status = "active")').where)
    assert sql.startswith('(EXISTS (')
    assert sql.endswith(') AND (status = :_p1)')
    assert params == {'_p1': 'active'}


# ---------------- substitutions ----------------

def test_value_sub_with_binding():
    # Bound substitution resolves at sqlize time and becomes a generated
    # literal name.
    r = sqlize('age>=$(min_age)', bindings={'min_age': 30})
    sql, params = Resolver.build(r.where)
    assert sql == 'age >= :_p1'
    assert params == {'_p1': 30}


def test_value_sub_without_binding():
    # Unbound — build() requires binding
    r = sqlize('age>=$(min_age)')
    assert 'min_age' in r.where.unbound.values()
    sql, params = Resolver.build(r.where, min_age=30)
    assert sql == 'age >= :min_age'
    assert params == {'min_age': 30}


def test_value_sub_missing_binding_errors():
    r = sqlize('age>=$(min_age)')
    with pytest.raises(TranslationError, match='missing binding'):
        Resolver.build(r.where)


def test_value_sub_unknown_binding_errors():
    r = sqlize('age>=$(min_age)')
    with pytest.raises(TranslationError, match='unknown binding'):
        Resolver.build(r.where, min_age=30, max_age=65)


def test_value_sub_repeated_dedupes():
    r = sqlize('(age>=$(x) & weight=$(x))')
    # Same name in two positions → single marker shared
    sql, params = Resolver.build(r.where, x=42)
    assert sql == '(age >= :x) AND (weight = :x)'
    assert params == {'x': 42}


def test_value_sub_repeated_dollar_dedupes():
    r = sqlize('(age>=$(x) & weight=$(x))')
    # Dollar-numeric also shares one $N slot for repeated name
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric', x=42)
    assert sql == '(age >= $1) AND (weight = $1)'
    assert args == [42]


def test_mixed_subst_and_literal():
    r = sqlize('age>=$(min_age) & weight>200')
    sql, params = Resolver.build(r.where, min_age=30)
    assert sql == '(age >= :min_age) AND (weight > :_p1)'
    assert params == {'min_age': 30, '_p1': 200}


def test_value_sub_dotted_name_with_bindings():
    # bindings= resolves via nested lookup
    r = sqlize('age>=$(user.min_age)',
               bindings={'user': {'min_age': 30}})
    sql, params = Resolver.build(r.where)
    assert sql == 'age >= :_p1'
    assert params == {'_p1': 30}


def test_value_sub_dotted_name_hashed():
    # Deferred dotted name hashes to _s_<hex> marker; original name
    # recoverable via unbound mapping.
    r = sqlize('age>=$(user.min_age)')
    [(marker, orig)] = r.where.unbound.items()
    assert marker.startswith('_s_')
    assert orig == 'user.min_age'


def test_value_sub_hash_is_deterministic():
    a = next(iter(sqlize('age>=$(user.min_age)').where.unbound))
    b = next(iter(sqlize('weight>=$(user.min_age)').where.unbound))
    assert a == b


def test_value_sub_hash_names_are_distinct():
    a = next(iter(sqlize('age>=$(user.min_age)').where.unbound))
    b = next(iter(sqlize('age>=$(user.max_age)').where.unbound))
    assert a != b


def test_value_sub_special_char_hashed():
    r = sqlize("age>=$('dr pepper')")
    [(marker, orig)] = r.where.unbound.items()
    assert marker.startswith('_s_')
    assert orig == "'dr pepper'"


def test_path_sub_with_binding():
    r = sqlize('data.$(field)', bindings={'field': 'name'})
    assert Resolver.build(r.select) == ("data #>> '{name}'", {})


def test_path_sub_dotted_name_with_bindings():
    r = sqlize('data.$(cfg.field)',
               bindings={'cfg': {'field': 'name'}})
    assert Resolver.build(r.select) == ("data #>> '{name}'", {})


def test_path_sub_without_binding_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$(field)')


def test_positional_sub():
    r = sqlize('data.$0', bindings=['name'])
    assert Resolver.build(r.select) == ("data #>> '{name}'", {})


# ---------------- SQL composition / concatenation ----------------

def test_sql_concat_preserves_metadata():
    r = sqlize('age >= $(min_age)')
    combined = "WHERE " + r.where
    assert isinstance(combined, SQLFragment)
    sql, params = Resolver.build(combined, min_age=30)
    assert sql == 'WHERE age >= :min_age'
    assert params == {'min_age': 30}


def test_sql_concat_two_fragments_merges_metadata():
    r = sqlize('(age>=$(min_age) & weight<200)')
    # compose the where with plain text
    combined = r.where + ' -- comment'
    sql, params = Resolver.build(combined, min_age=30)
    assert sql.startswith('(age >= :min_age) AND (weight < :_p1) -- comment')
    assert params == {'min_age': 30, '_p1': 200}


def test_sql_concat_both_directions():
    r = sqlize('age >= $(min_age)')
    s1 = "LEFT " + r.where
    s2 = r.where + " RIGHT"
    assert isinstance(s1, SQLFragment)
    assert isinstance(s2, SQLFragment)
    assert s1.text == "LEFT age >= {min_age}"
    assert s2.text == "age >= {min_age} RIGHT"


# ---------------- error cases ----------------

def test_empty_path_errors():
    with pytest.raises(TranslationError):
        sqlize('')


def test_wildcard_first_segment_still_errors():
    with pytest.raises(TranslationError):
        sqlize('*.age=30')


def test_unknown_driver_errors():
    """
    sqlize raises TranslationError for a driver name that isn't
    registered in the driver registry.
    """
    with pytest.raises(TranslationError):
        _sqlize('age=30', driver='mysql')


def test_unsupported_paramstyle_errors():
    r = sqlize('age=30')
    with pytest.raises(TranslationError, match='unsupported paramstyle'):
        Resolver.build(r.where, paramstyle='bogus')


# ---------------- identifier quoting ----------------

def test_quoted_column_with_special_chars():
    r = sqlize("'user data'.name")
    assert Resolver.build(r.select) == ('"user data" #>> \'{name}\'', {})


# ---------------- dollar-numeric output shape ----------------

def test_dollar_numeric_simple():
    r = sqlize('age >= 30')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric')
    assert sql == 'age >= $1'
    assert args == [30]


def test_dollar_numeric_multiple_params():
    r = sqlize('(age >= 18 & status = "active")')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric')
    assert sql == '(age >= $1) AND (status = $2)'
    assert args == [18, 'active']


def test_dollar_numeric_with_subst():
    r = sqlize('(age >= $(min_age) & weight < 200)')
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               min_age=30)
    assert sql == '(age >= $1) AND (weight < $2)'
    assert args == [30, 200]


# ---------------- paramstyle matrix ----------------
#
# Core scenarios exercised against each supported paramstyle. Each
# parametrize invokes both ('named' → `:p1`-style + params dict) and
# ('dollar-numeric' → `$N`-style + args list) to catch asymmetries.


def _placeholder(paramstyle, name, position):
    """
    Return the expected placeholder string for a given paramstyle.
    name: the bind name (for name-keyed styles) — ignored for positional.
    position: 1-based position in the final SQL.
    """
    if paramstyle == 'named':
        return f':{name}'
    if paramstyle == 'pyformat':
        return f'%({name})s'
    if paramstyle == 'numeric':
        return f':{position}'
    if paramstyle == 'dollar-numeric':
        return f'${position}'
    if paramstyle == 'qmark':
        return '?'
    if paramstyle == 'format':
        return '%s'
    raise ValueError(paramstyle)


def _values(paramstyle, items):
    """
    Build the expected params (dict) or args (list) from an ordered
    list of (bind_name, value) tuples. Each test passes items in the
    canonical named-paramstyle shape; this helper converts for
    positional styles.
    """
    if paramstyle in DICT_STYLES:
        return dict(items)
    return [v for _, v in items]


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_null(paramstyle):
    r = sqlize('deleted_at=None')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    assert sql == 'deleted_at IS NULL'
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_not_null(paramstyle):
    r = sqlize('deleted_at!=None')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    assert sql == 'deleted_at IS NOT NULL'
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_boolean_guard(paramstyle):
    """
    A boolean compares inline: no parameter in any paramstyle.
    """
    sql, values = Resolver.build(sqlize('data.user.active=True').where, paramstyle=paramstyle)
    assert "WHEN 'boolean' THEN _pat1.n = 'true'::jsonb" in sql
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_jsonb_null_guard(paramstyle):
    """
    `= None` takes no parameter in any paramstyle.
    """
    sql, values = Resolver.build(sqlize('data.score=None').where, paramstyle=paramstyle)
    assert "(jsonb_typeof(_pat1.n) = 'null')" in sql
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_regex(paramstyle):
    """
    A regex is hoisted, anchored to fullmatch.
    """
    sql, values = Resolver.build(sqlize('name=/^alice/').where, paramstyle=paramstyle)
    assert sql == f'name ~ {_placeholder(paramstyle, "_p1", 1)}'
    assert values == _values(paramstyle, [('_p1', '^(?:^alice)$')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_ref_with_guard(paramstyle):
    """
    A string compared through a reference is hoisted.
    """
    sql, values = Resolver.build(
        sqlize('data.$$(data.config.field)="Alice"').where, paramstyle=paramstyle)
    assert f"(_pat1.n #>> '{{}}') = {_placeholder(paramstyle, '_p1', 1)}" in sql
    assert values == _values(paramstyle, [('_p1', 'Alice')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_jsonb_numeric_guard(paramstyle):
    """
    A number compares inline: no parameter in any paramstyle.
    """
    sql, values = Resolver.build(sqlize('data.user.age>=30').where, paramstyle=paramstyle)
    assert '(_pat1.n)::numeric >= (30)' in sql
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_guard_transform(paramstyle):
    """
    A transform runs in SQL; the number it's compared to is inline.
    """
    sql, values = Resolver.build(sqlize('data.user.age|int>=30').where, paramstyle=paramstyle)
    assert 'to_jsonb(trunc(' in sql
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_group_and(paramstyle):
    r = sqlize('(age >= 18 & status = "active")')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    p1 = _placeholder(paramstyle, '_p1', 1)
    p2 = _placeholder(paramstyle, '_p2', 2)
    assert sql == f'(age >= {p1}) AND (status = {p2})'
    assert values == _values(paramstyle,
                             [('_p1', 18), ('_p2', 'active')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_subst(paramstyle):
    r = sqlize('age >= $(min_age)')
    sql, values = Resolver.build(r.where,
                                 paramstyle=paramstyle,
                                 min_age=30)
    ph = _placeholder(paramstyle, 'min_age', 1)
    assert sql == f'age >= {ph}'
    assert values == _values(paramstyle, [('min_age', 30)])


@pytest.mark.parametrize('paramstyle', BACKREF_STYLES)
def test_ps_subst_repeated_backref(paramstyle):
    """Styles with back-reference share one slot across occurrences."""
    r = sqlize('(age >= $(x) & weight = $(x))')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle, x=42)
    ph = _placeholder(paramstyle, 'x', 1)
    assert sql == f'(age >= {ph}) AND (weight = {ph})'
    assert values == _values(paramstyle, [('x', 42)])


@pytest.mark.parametrize('paramstyle', ['qmark', 'format'])
def test_ps_subst_repeated_no_backref(paramstyle):
    """qmark/format repeat the value (no back-reference)."""
    r = sqlize('(age >= $(x) & weight = $(x))')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle, x=42)
    ph = _placeholder(paramstyle, 'x', 1)
    assert sql == f'(age >= {ph}) AND (weight = {ph})'
    assert values == [42, 42]


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_pattern_with_literal_inline(paramstyle):
    """
    A number in a pattern path's guard is inline: no parameter.
    """
    sql, values = Resolver.build(sqlize('data.users[*].age >= 30').where, paramstyle=paramstyle)
    assert '>= (30)' in sql
    assert values == _values(paramstyle, [])


def _cast_suffix(paramstyle, sql_cast):
    """
    Dollar-numeric adds explicit casts to placeholders inside
    `jsonb_build_object(...)` so asyncpg can infer parameter types.
    Other paramstyles emit the placeholder bare.
    """
    return f'::{sql_cast}' if paramstyle == 'dollar-numeric' else ''


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_pattern_with_string_hoisted(paramstyle):
    """
    A string in a pattern path's guard is hoisted, its placeholder
    typed by the text it compares with.
    """
    sql, values = Resolver.build(sqlize('data.*.status = "on"').where, paramstyle=paramstyle)
    assert f"#>> '{{}}') = {_placeholder(paramstyle, '_p1', 1)} " in sql
    assert values == _values(paramstyle, [('_p1', 'on')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_pattern_with_substitution(paramstyle):
    """
    A substitution goes as its binding's JSON text, once, cast to jsonb.
    """
    sql, values = Resolver.build(sqlize('data.users[*].age >= $(min_age)').where,
                                 paramstyle=paramstyle, min_age=30)
    ph = _placeholder(paramstyle, 'min_age__json', 1)
    assert f'(SELECT {ph}::jsonb AS n)' in sql
    assert values == _values(paramstyle, [('min_age__json', '30')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_mixed_pattern_and_scalar(paramstyle):
    """
    A pattern path's where and a column's guard in one group.
    """
    sql, values = Resolver.build(
        sqlize('(data.users[*].age >= 30 & status = "active")').where, paramstyle=paramstyle)
    assert sql.endswith(f'AND (status = {_placeholder(paramstyle, "_p1", 1)})')
    assert values == _values(paramstyle, [('_p1', 'active')])


# ---------------- ParamStyle enum accepted ----------------

@pytest.mark.parametrize('style', list(ParamStyle))
def test_paramstyle_enum_member_accepted(style):
    """
    Enum members normalize to their string value at `Resolver.build`
    entry — passing `ParamStyle.named` produces the same output as
    passing `'named'`.
    """
    r = sqlize('age = 30')
    sql_enum, args_enum = Resolver.build(r.where, paramstyle=style)
    sql_str, args_str = Resolver.build(r.where, paramstyle=style.value)
    assert sql_enum == sql_str
    assert args_enum == args_str


# ---------------- shared ParamPool ----------------

def test_pool_none_gives_independent_marker_spaces():
    """
    Without a shared pool each Resolver allocates from its own counter,
    so two independent sqlize calls can both emit `_p1` — composing
    them blind would collide (this test just documents the unshared
    behavior; the next tests cover the shared fix).
    """
    r1 = sqlize('status = "active"')
    r2 = sqlize('age = 30')
    assert '_p1' in r1.where.params
    assert '_p1' in r2.where.params
    # Same marker name, different values — real collision if merged naively.
    assert r1.where.params['_p1'] != r2.where.params['_p1']


def test_pool_shared_avoids_marker_collision():
    """
    Two Resolvers sharing a pool allocate through one counter, so their
    markers are distinct and fragment composition merges cleanly.
    """
    pool = ParamPool()
    r1 = sqlize('status = "active"', pool=pool)
    r2 = sqlize('age = 30', pool=pool)
    assert r1.where.params == {'_p1': 'active'}
    assert r2.where.params == {'_p2': 30}
    assert pool.params == {'_p1': 'active', '_p2': 30}


def test_pool_shared_fragment_composition_renders_both_params():
    """
    When the markers across two Resolvers don't collide, composing
    their fragments produces a single SQLFragment whose render picks
    up both params in order.
    """
    pool = ParamPool()
    r1 = sqlize('status = "active"', pool=pool)
    r2 = sqlize('age = 30', pool=pool)
    combined = '(' + r1.where + ') AND (' + r2.where + ')'
    sql, args = Resolver.build(combined, paramstyle='dollar-numeric')
    assert sql == '(status = $1) AND (age = $2)'
    assert args == ['active', 30]


def test_pool_shared_dedups_substitutions_across_resolvers():
    """
    A substitution by the same original name resolves to the same
    marker across Resolvers that share a pool — one bind, one value,
    back-referenced at render time.
    """
    pool = ParamPool()
    r1 = sqlize('age >= $(threshold)', pool=pool)
    r2 = sqlize('score >= $(threshold)', pool=pool)
    combined = '(' + r1.where + ') AND (' + r2.where + ')'
    sql, args = Resolver.build(combined,
                               paramstyle='dollar-numeric',
                               threshold=100)
    assert sql == '(age >= $1) AND (score >= $1)'
    assert args == [100]


def test_pool_shared_named_paramstyle_unique_keys():
    """
    Under `named`/`pyformat` the rendered placeholder carries the
    marker name directly; shared pool ensures those names are
    unique across Resolvers so the output dict has one entry per
    real value.
    """
    pool = ParamPool()
    r1 = sqlize('status = "active"', pool=pool)
    r2 = sqlize('age = 30', pool=pool)
    combined = '(' + r1.where + ') AND (' + r2.where + ')'
    sql, params = Resolver.build(combined, paramstyle='named')
    assert sql == '(status = :_p1) AND (age = :_p2)'
    assert params == {'_p1': 'active', '_p2': 30}


def test_pool_mixed_literal_and_substitution_across_resolvers():
    """
    A pool spanning a literal hoist in r1 and a substitution in r2
    generates distinct markers for each and renders with the right
    mix of pre-hoisted and supplied values.
    """
    pool = ParamPool()
    r1 = sqlize('status = "active"', pool=pool)
    r2 = sqlize('age >= $(min_age)', pool=pool)
    combined = '(' + r1.where + ') AND (' + r2.where + ')'
    sql, args = Resolver.build(combined,
                               paramstyle='dollar-numeric',
                               min_age=18)
    assert sql == '(status = $1) AND (age >= $2)'
    assert args == ['active', 18]


def test_pool_is_public():
    """
    `ParamPool` is re-exported from both `dotted.translators` and `dotted`.
    """
    import dotted
    import dotted.translators
    assert ParamPool is dotted.ParamPool
    assert ParamPool is dotted.translators.ParamPool


# ---------------- Raw / Col substitution values ----------------

def test_raw_emits_literal_sql_not_a_bind_param():
    """
    Binding a substitution to Raw emits the SQL verbatim and adds
    nothing to the args list.
    """
    r = sqlize('customer = $(matched.customer)')
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               **{'matched.customer': Raw('matched.customer')})
    assert sql == 'customer = matched.customer'
    assert args == []


def test_raw_mixed_with_value_bindings():
    """
    Raw and regular value bindings coexist in one build call. The args
    list receives only the value-bound substitutions, in occurrence
    order.
    """
    r = sqlize('(status = $(s) & customer = $(c))')
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               s='active', c=Raw('matched.customer'))
    assert sql == '(status = $1) AND (customer = matched.customer)'
    assert args == ['active']


def test_raw_across_paramstyles():
    """
    Raw emits the same literal SQL regardless of paramstyle — bypasses
    placeholder formatting entirely.
    """
    r = sqlize('customer = $(c)')
    for paramstyle in ['named', 'pyformat', 'qmark', 'format',
                       'numeric', 'dollar-numeric']:
        sql, args = Resolver.build(r.where,
                                   paramstyle=paramstyle,
                                   c=Raw('matched.customer'))
        assert sql == 'customer = matched.customer', paramstyle
        # Dict paramstyles produce {}; list paramstyles produce [].
        assert not args, paramstyle


def test_raw_repeated_emits_each_occurrence():
    """
    Same Raw value used in two places emits the SQL at each occurrence
    — no back-reference reserved (unlike value bindings under numeric /
    dollar-numeric paramstyles which back-ref to one slot).
    """
    r = sqlize('(a = $(x) & b = $(x))')
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               x=Raw('T.x'))
    assert sql == '(a = T.x) AND (b = T.x)'
    assert args == []


def test_raw_drops_cast_inside_jsonb_build_object():
    """
    A Raw binding where a substitution compares as JSON is its SQL's
    jsonb: no placeholder, no argument.
    """
    r = sqlize('data.users[*].customer = $(c)')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric', c=Raw('matched.customer'))
    assert '(SELECT to_jsonb(matched.customer) AS n)' in sql
    assert args == []


def test_raw_with_shared_pool():
    """
    Raw bindings work inside a pool-shared composition. The Raw slot
    doesn't participate in args numbering; non-Raw bindings get
    sequential `$N` as usual.
    """
    pool = ParamPool()
    r1 = sqlize('status = "active"', pool=pool)
    r2 = sqlize('customer = $(matched.customer)', pool=pool)
    combined = '(' + r1.where + ') AND (' + r2.where + ')'
    sql, args = Resolver.build(combined,
                               paramstyle='dollar-numeric',
                               **{'matched.customer': Raw('matched.customer')})
    assert sql == '(status = $1) AND (customer = matched.customer)'
    assert args == ['active']


def test_col_single_dotted_string():
    """
    `Col('matched.customer')` splits on `.` and joins back with `.` —
    same net SQL as Raw('matched.customer') but validated.
    """
    c = Col('matched.customer')
    assert isinstance(c, Raw)
    assert c.sql == 'matched.customer'


def test_col_multi_arg():
    """
    `Col('matched', 'customer')` takes separate segments.
    """
    assert Col('matched', 'customer').sql == 'matched.customer'
    assert Col('schema', 'tbl', 'col').sql == 'schema.tbl.col'


def test_col_rejects_non_identifier_segments():
    """
    Each segment must be a plain SQL identifier — no punctuation,
    no SQL meta-characters.
    """
    with pytest.raises(TranslationError):
        Col('bad; DROP TABLE x')
    with pytest.raises(TranslationError):
        Col('matched', 'bad col')
    with pytest.raises(TranslationError):
        Col('')


def test_col_in_build():
    """
    Col passes through to the renderer as a Raw — emits the validated
    dotted identifier verbatim.
    """
    r = sqlize('customer = $(c)')
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               c=Col('matched.customer'))
    assert sql == 'customer = matched.customer'
    assert args == []


def test_raw_col_are_public():
    """
    Raw and Col are re-exported from both `dotted.translators` and the top-
    level namespace is submodule-only by design.
    """
    import dotted.translators
    assert Raw is dotted.translators.Raw
    assert Col is dotted.translators.Col


def test_raw_rejects_non_string():
    """
    Raw requires a str — construction errors for other types.
    """
    with pytest.raises(TranslationError):
        Raw(42)


# ---------------- Pattern-path LATERAL form ----------------

def test_pattern_lateral_simple_wildcard():
    """
    A pattern path gives a LATERAL of its matches (extract-style) and
    a where of whether there is one (filter-style).
    """
    r = sqlize('data.users[*].age')
    assert str(r.select) == '_pat1.value'
    assert str(r.where).startswith('EXISTS (')
    assert str(r.lateral).startswith('LATERAL (SELECT _pat2.value FROM (')
    assert str(r.lateral).endswith(') AS _pat2) AS _pat1(value)')


def test_pattern_lateral_with_guard():
    """
    The guard tests each match in both the where and the lateral.
    """
    r = sqlize('data.users[*].age >= 30')
    assert '>= (30)' in str(r.where)
    assert '>= (30)' in str(r.lateral)


def test_pattern_lateral_builds_with_driver_paramstyle():
    """
    Each fragment renders its own hoisted values under the driver's
    paramstyle.
    """
    r = sqlize('data.*.status = "on"')
    where_sql, where_args = Resolver.build(r.where, paramstyle='dollar-numeric')
    lat_sql, lat_args = Resolver.build(r.lateral, paramstyle='dollar-numeric')
    assert "#>> '{}') = $1 " in where_sql
    assert "#>> '{}') = $1 " in lat_sql
    assert where_args == ['on']
    assert lat_args == ['on']


def test_pattern_lateral_alias_from_shared_pool():
    """
    Pool-shared translation of two pattern paths allocates unique
    aliases from the pool's counter so their lateral clauses don't
    collide in a composed query.
    """
    pool = ParamPool()
    r1 = sqlize('data.users[*].age', pool=pool)
    r2 = sqlize('data.orders[*].amount', pool=pool)
    a1 = str(r1.select).split('.')[0]
    a2 = str(r2.select).split('.')[0]
    assert a1 != a2
    assert str(r1.lateral).endswith(f' AS {a1}(value)')
    assert str(r2.lateral).endswith(f' AS {a2}(value)')


def test_pattern_lateral_alias_per_resolver_when_no_pool():
    """
    Without a shared pool each Resolver's own ParamPool resets the
    counter, so two independent pattern Resolvers both emit `_pat1`.
    (Composing them blind collides — use a shared pool.)
    """
    r1 = sqlize('data.users[*].age')
    r2 = sqlize('data.orders[*].amount')
    assert '_pat1' in str(r1.lateral)
    assert '_pat1' in str(r2.lateral)


def test_scalar_path_has_no_lateral():
    """
    Scalar (non-pattern) paths don't produce a lateral.
    """
    r = sqlize('status = "active"')
    assert r.lateral is None
    r = sqlize('data.user.age >= 30')
    assert r.lateral is None
    r = sqlize('data.user.name')
    assert r.lateral is None


def test_pattern_extract_composition():
    """
    End-to-end: compose the extract-style query, pulling id plus each
    matched value by joining on the lateral. The engine's job — sqlize
    supplies the pieces.
    """
    r = sqlize('data.users[*].age >= 30')
    select_sql, select_args = Resolver.build(r.select, paramstyle='dollar-numeric')
    lat_sql, _ = Resolver.build(r.lateral, paramstyle='dollar-numeric')
    query = f"SELECT id, {select_sql} FROM items, {lat_sql}"
    assert query.startswith('SELECT id, _pat1.value FROM items, LATERAL (SELECT')
    assert select_args == []


# ---------------- Resolver as Mapping ----------------

def test_resolver_mapping_keys_only_present_fragments():
    """
    `keys()` lists only the fragment attributes that are not None.
    """
    r = sqlize('status = "active"')
    assert list(r.keys()) == ['select', 'where']
    r = sqlize('data.users[*].age')
    assert list(r.keys()) == ['select', 'where', 'lateral']
    r = sqlize('data.user.name')   # pure traversal, no predicate
    assert list(r.keys()) == ['select']


def test_resolver_mapping_getitem():
    """
    Indexing returns the fragment; absent / unknown keys raise.
    """
    r = sqlize('status = "active"')
    assert isinstance(r['where'], SQLFragment)
    assert r['where'] is r.where
    with pytest.raises(KeyError):
        r['lateral']     # no pattern → no lateral
    with pytest.raises(KeyError):
        r['nonsense']


def test_resolver_mapping_contains_and_len():
    """
    `in` tests for a present non-None fragment. `len()` counts them.
    """
    r = sqlize('data.users[*].age')
    assert 'where' in r
    assert 'lateral' in r
    assert 'from_' not in r
    assert len(r) == 3

    r = sqlize('data.user.name')
    assert len(r) == 1
    assert 'select' in r


def test_resolver_mapping_dict_cast():
    """
    `dict(r)` materializes present fragments; values() / items() work
    via the Mapping ABC.
    """
    r = sqlize('status = "active"')
    d = dict(r)
    assert set(d.keys()) == {'select', 'where'}
    assert all(isinstance(v, SQLFragment) for v in d.values())
    for k, v in r.items():
        assert r[k] is v


def test_resolver_is_a_mapping():
    """
    Resolver registers as a collections.abc.Mapping for isinstance
    checks (useful for duck-typing).
    """
    import collections.abc
    r = sqlize('status = "active"')
    assert isinstance(r, collections.abc.Mapping)


def test_resolver_splat_as_kwargs_works():
    """
    `**r` unpacking relies on keys() + __getitem__ — each key becomes
    a kwarg bound to its fragment.
    """
    r = sqlize('data.users[*].age')
    kwargs = {**r}
    assert set(kwargs) == {'select', 'where', 'lateral'}


# ---------------- psycopg3 alias + pyformat cast emission ----------------

def test_psycopg3_alias_resolves_to_psycopg_driver():
    """
    `psycopg3` and `psycopg` both dispatch to `PsycopgResolver`; the
    canonical driver name on the class is `'psycopg'`.
    """
    r1 = sqlize('age = 30', driver='psycopg')
    r2 = sqlize('age = 30', driver='psycopg3')
    assert type(r1) is type(r2)
    assert type(r1).driver == 'psycopg'


def test_psycopg3_appears_in_drivers_list():
    """
    Both names are registered.
    """
    from dotted.translators import drivers
    names = drivers()
    assert 'psycopg' in names
    assert 'psycopg3' in names


def test_psycopg_emits_cast_inside_jsonb_build_object():
    """
    Psycopg v3 sends a compared substitution as JSON text cast to
    jsonb, so the server types it.
    """
    r = sqlize('data.users[*].age >= $(min)', driver='psycopg')
    sql, params = r.build(r.where, min=30)
    assert '(SELECT %(min__json)s::jsonb AS n)' in sql
    assert params == {'min__json': '30'}


def test_psycopg2_still_emits_no_cast():
    """
    Psycopg2 sends it the same way: JSON text cast to jsonb.
    """
    r = sqlize('data.users[*].age >= $(min)', driver='psycopg2')
    sql, params = r.build(r.where, min=30)
    assert '(SELECT %(min__json)s::jsonb AS n)' in sql
    assert params == {'min__json': '30'}


def test_cast_under_named_paramstyle_with_driver_cast_true():
    """
    Under the named paramstyle a compared substitution is its own
    parameter, its JSON text, apart from the plain one of the same
    name.
    """
    from dotted.translators import Resolver
    from dotted.translators.sql_pg import PostgresMixin

    class _NamedCastResolver(PostgresMixin, Resolver):
        paramstyle = 'named'
        cast       = True

    r = _NamedCastResolver()
    r.translate(__import__('dotted').parse(
        'data.users[*].age >= $(min)', partial=True).ops)
    sql, params = r.build(r.where, min=30)
    assert '(SELECT :min__json::jsonb AS n)' in sql
    assert params == {'min__json': '30'}


# ---------------- table search ----------------

@pytest.mark.parametrize('path, table, select, where, offset, limit', [
    ('hello', 'hello', '*', None, None, None),
    ('public.hello', 'public.hello', '*', None, None, None),
    ('public.hello[*]', 'public.hello', '*', None, None, None),
    ('hello[*&book_id="b1"]', 'hello', '*',
     ('book_id = :_p1', {'_p1': 'b1'}), None, None),
    ('hello[*&book_id="b1"].title', 'hello', 'title',
     ('book_id = :_p1', {'_p1': 'b1'}), None, None),
    ('hello[*&data.user.age>30]', 'hello', '*',
     ("(SELECT (_pat1.n IS NOT NULL AND (CASE jsonb_typeof(_pat1.n) WHEN 'number' THEN (_pat1.n)::numeric > (30) ELSE FALSE END)) FROM (SELECT data #> '{user,age}' AS n) AS _pat1)", {}), None, None),
    ('hello[*].book_id = "b1"', 'hello', 'book_id',
     ('book_id = :_p1', {'_p1': 'b1'}), None, None),
    ('hello[*].data.user.age >= 30', 'hello', "data #>> '{user,age}'",
     ("(SELECT (_pat1.n IS NOT NULL AND (CASE jsonb_typeof(_pat1.n) WHEN 'number' THEN (_pat1.n)::numeric >= (30) ELSE FALSE END)) FROM (SELECT data #> '{user,age}' AS n) AS _pat1)", {}), None, None),
    ('hello[0].title', 'hello', 'title', None, 0, 1),
    ('hello[10:20]', 'hello', '*', None, 10, 10),
    ('hello[10:]', 'hello', '*', None, 10, None),
    ('hello[:20]', 'hello', '*', None, None, 20),
    ('hello[10:20][*].title', 'hello', 'title', None, 10, 10),
    ('hello[book_id="b1"]', 'hello', '*',
     ('book_id = :_p1', {'_p1': 'b1'}), None, None),
    ('hello[book_id="b1"][0]', 'hello', '*',
     ('book_id = :_p1', {'_p1': 'b1'}), 0, 1),
    ('hello[book_id="b1"][10:20]', 'hello', '*',
     ('book_id = :_p1', {'_p1': 'b1'}), 10, 10),
    ('hello[book_id="b1"][*].title', 'hello', 'title',
     ('book_id = :_p1', {'_p1': 'b1'}), None, None),
    ('hello[book_id="b1"][0:10][*].title', 'hello', 'title',
     ('book_id = :_p1', {'_p1': 'b1'}), 0, 10),
])
def test_table_search(path, table, select, where, offset, limit):
    """
    Under `root='table'` the leading keys name the table, the row step
    becomes WHERE / offset / limit, and the rest reads as a column
    path under each row.
    """
    r = sqlize(path, root='table')
    assert r.table == table
    assert Resolver.build(r.select) == (select, {})
    assert (Resolver.build(r.where) if r.where else None) == where
    assert (r.offset, r.limit) == (offset, limit)


@pytest.mark.parametrize('path, where', [
    ('hello[*&a=1&b!=2]', '(a = :_p1) AND (b IS DISTINCT FROM :_p2)'),
    ('hello[*&(a=1,b=2)]', '(a = :_p1) OR (b = :_p2)'),
    ('hello[*&!a=1]', '(a = :_p1) IS NOT TRUE'),
    ('hello[*&name=/^a/]', 'name ~ :_p1'),
    ('hello[*&deleted_at=None]', 'deleted_at IS NULL'),
    ('hello[*&tags[0]="x"]', "(SELECT (_pat1.n IS NOT NULL AND (CASE WHEN jsonb_typeof(_pat1.n) = 'string' THEN (_pat1.n #>> '{}') = :_p1 ELSE FALSE END)) FROM (SELECT (CASE jsonb_typeof(tags) WHEN 'array' THEN tags -> (0) WHEN 'string' THEN (CASE WHEN 0 < char_length((tags #>> '{}')) THEN to_jsonb(substr((tags #>> '{}'), 0 + 1, 1)) END) END) AS n) AS _pat1)"),
    ('hello[a=1][*&b=2]', '(a = :_p1) AND (b = :_p2)'),
    ('hello[*&a=1].b = 2', '(a = :_p1) AND (b = :_p2)'),
])
def test_table_search_filters(path, where):
    """
    Row filters combine with AND / OR / NOT, and AND with the `where`
    of the path under each row. `!=` and a negation hold for a NULL, as
    Python's `None != x` and `not (None == x)` do.
    """
    r = sqlize(path, root='table')
    assert Resolver.build(r.where)[0] == where


def test_table_search_unnamed():
    """
    A path that starts with a row step searches an unnamed table, with
    no `root=` needed.
    """
    r = sqlize('[*&book_id="b1"]')
    assert r.table is None
    assert Resolver.build(r.select) == ('*', {})
    assert Resolver.build(r.where) == ('book_id = :_p1', {'_p1': 'b1'})


def test_leading_index_is_a_row_not_a_column():
    """
    `[0]` used to slip through as the column `"0"`; it is the first
    row of an unnamed table.
    """
    r = sqlize('[0]')
    assert Resolver.build(r.select) == ('*', {})
    assert (r.table, r.offset, r.limit) == (None, 0, 1)


def test_column_root_is_the_default():
    """
    Without `root='table'`, the first segment is still a JSONB column.
    """
    r = sqlize('hello[*&book_id="b1"]')
    assert r.table is None
    where = Resolver.build(r.where)[0]
    assert where.startswith('EXISTS (')
    assert 'hello AS value' in where


def test_column_path_has_no_table_values():
    """
    A column path leaves `table`, `offset` and `limit` None.
    """
    r = sqlize('status = "active"')
    assert (r.table, r.offset, r.limit) == (None, None, None)


def test_unknown_root_raises():
    """
    `root` is `'column'` or `'table'`.
    """
    with pytest.raises(TranslationError, match='unknown root'):
        sqlize('hello', root='rows')


def test_table_search_bindings_resolve_index_and_slice():
    """
    `bindings=` resolves substitutions in a row index or slice bounds
    at sqlize time.
    """
    r = sqlize('hello[$(n)].title', root='table', bindings={'n': 3})
    assert (r.offset, r.limit) == (3, 1)
    r = sqlize('hello[$(a):$(b)]', root='table', bindings={'a': 2, 'b': 7})
    assert (r.offset, r.limit) == (2, 5)


def test_table_search_unbound_substitution_in_filter():
    """
    A substitution in a row filter's value binds late, as in a column
    path.
    """
    r = sqlize('hello[*&age >= $(min_age)]', root='table')
    assert list(r.where.unbound.values()) == ['min_age']
    assert r.build(r.where, min_age=30) == ('age >= $1', [30])


def test_table_values_stay_out_of_mapping():
    """
    `table`, `offset` and `limit` are plain attributes, not fragments:
    the mapping yields fragments only.
    """
    r = sqlize('hello[book_id="b1"][0:5]', root='table')
    assert sorted(r) == ['from_', 'select', 'where']
    assert all(isinstance(v, SQLFragment) for v in dict(r).values())


def test_table_search_repr_shows_plain_values():
    """
    `repr` shows the table, offset and limit when set.
    """
    r = sqlize('hello[0:5]', root='table')
    assert repr(r) == (
        "AsyncPGResolver(driver='asyncpg', select='*', from_='hello', "
        "table='hello', offset=0, limit=5)")


def test_table_search_composes_into_a_query():
    """
    The caller assembles the query from the fragments and plain values.
    """
    r = sqlize('hello[*&book_id="b1"].title', root='table')
    sql = 'SELECT ' + r.select + ' FROM ' + r.from_ + ' WHERE ' + r.where
    assert r.build(sql) == (
        'SELECT title FROM hello WHERE book_id = $1', ['b1'])


@pytest.mark.parametrize('path, from_', [
    ('hello[*&a=1].title', 'hello'),
    ('public.hello[0]', 'public.hello'),
    ('"my table"[*]', '"my table"'),
])
def test_table_search_from_is_the_table(path, from_):
    """
    `from_` is the table a search reads, each part of its name quoted
    where it needs it.
    """
    assert Resolver.build(sqlize(path, root='table').from_) == (from_, {})


def test_table_search_unnamed_has_no_from():
    """
    An unnamed table's FROM is the caller's.
    """
    assert sqlize('[*&a=1]').from_ is None


# ---------------- groups of row steps ----------------

_BOOKS_A = 'SELECT * FROM books WHERE a = :_p1'
_BOOKS_B = 'SELECT * FROM books WHERE b = :_p2'


def test_row_step_group_reads_each_branchs_rows():
    """
    A group as the row step yields each branch's rows in turn, a row
    both reach twice, as dotted does, so `from_` is their UNION ALL
    under the table's name; the path after it reads each row.
    """
    r = sqlize('books[(*&a=1, *&b=2)].title', root='table')
    assert Resolver.build(r.from_) == (
        f'(({_BOOKS_A}) UNION ALL ({_BOOKS_B})) AS books', {'_p1': 1, '_p2': 2})
    assert Resolver.build(r.select) == ('title', {})
    assert r.where is None
    assert (r.table, r.offset, r.limit) == ('books', None, None)


def test_row_step_group_composes_into_a_query():
    """
    The caller selects from `from_`, as for any table search.
    """
    r = sqlize('books[(*&a=1, *&b=2)].title', root='table')
    assert r.build('SELECT ' + r.select + ' FROM ' + r.from_) == (
        'SELECT title FROM ((SELECT * FROM books WHERE a = $1) UNION ALL '
        '(SELECT * FROM books WHERE b = $2)) AS books', [1, 2])


def test_row_step_group_cut():
    """
    A cut holds over the table: b's rows come only where no row of a's
    has the path after the group match.
    """
    r = sqlize('books[(*&a=1#, *&b=2)].title = "x"', root='table')
    a = 'SELECT * FROM books WHERE a = :_p2'
    b = 'SELECT * FROM books WHERE b = :_p3'
    assert Resolver.build(r.from_)[0] == (
        f'(({a}) UNION ALL (SELECT * FROM ({b}) AS books WHERE NOT EXISTS '
        f'(SELECT 1 FROM ({a}) AS books WHERE title = :_p1))) AS books')
    assert Resolver.build(r.where) == ('title = :_p1', {'_p1': 'x'})


def test_row_step_group_soft_cut():
    """
    A soft cut skips, in the branches after it, the rows its branch
    yielded: those its predicates hold for, null being no match.
    """
    r = sqlize('books[(*&a=1##, *&b=2)].title', root='table')
    assert Resolver.build(r.from_)[0] == (
        f'(({_BOOKS_A}) UNION ALL (SELECT * FROM ({_BOOKS_B}) AS books '
        f'WHERE (a = :_p1) IS NOT TRUE)) AS books')


def test_row_step_first_match_group():
    """
    A first match is the first row of the first branch to reach one.
    """
    r = sqlize('books[(*&a=1, *&b=2)?].title', root='table')
    assert Resolver.build(r.from_)[0] == (
        f'((SELECT * FROM ({_BOOKS_A}) AS books LIMIT 1) UNION ALL '
        f'(SELECT * FROM (SELECT * FROM ({_BOOKS_B}) AS books WHERE NOT EXISTS '
        f'(SELECT 1 FROM ({_BOOKS_A}) AS books)) AS books LIMIT 1)) AS books')


def test_row_step_conjunction():
    """
    An `&` of row steps yields each branch's rows only where every
    branch reaches one.
    """
    r = sqlize('books([*&a=1] & [*&b=2])', root='table')
    held = (f'(EXISTS (SELECT 1 FROM ({_BOOKS_A}) AS books)) AND '
            f'(EXISTS (SELECT 1 FROM ({_BOOKS_B}) AS books))')
    assert Resolver.build(r.from_)[0] == (
        f'((SELECT * FROM ({_BOOKS_A}) AS books WHERE {held}) UNION ALL '
        f'(SELECT * FROM ({_BOOKS_B}) AS books WHERE {held})) AS books')


@pytest.mark.parametrize('path, from_', [
    ('books(![*&a=1]).title',
     '(SELECT * FROM books WHERE (a = :_p1) IS NOT TRUE) AS books'),
    ('books(![*])', '(SELECT * FROM books LIMIT 0) AS books'),
])
def test_row_step_negation(path, from_):
    """
    A negation of a row step is the rows it doesn't reach, each whole;
    a row its filter is null for is not reached. `[*]` reaches them
    all.
    """
    assert Resolver.build(sqlize(path, root='table').from_)[0] == from_


@pytest.mark.parametrize('path, from_', [
    ('books[(0, 2)]',
     '((SELECT * FROM books OFFSET 0 LIMIT 1) UNION ALL '
     '(SELECT * FROM books OFFSET 2 LIMIT 1)) AS books'),
    ('books[x=1][(*&a=1, *&b=2)]',
     '((SELECT * FROM books WHERE (x = :_p1) AND (a = :_p2)) UNION ALL '
     '(SELECT * FROM books WHERE (x = :_p1) AND (b = :_p3))) AS books'),
    ('books[(*&a=1)]', '(SELECT * FROM books WHERE a = :_p1) AS books'),
])
def test_row_step_group_rows(path, from_):
    """
    A branch may pick a row by index; a slice filter before the group
    filters every branch's rows; a one-branch group is its branch's.
    """
    assert Resolver.build(sqlize(path, root='table').from_)[0] == from_


@pytest.mark.parametrize('path', [
    '[(*&a=1, *&b=2)]',
    'books[0:5][(*&a=1, *&b=2)]',
    'books[(0##, *&b=2)]',
    'books(![0])',
    'books([*&a=1].title, [*&b=2].author)',
    'books([a=1], [b=2])',
    'books[(*&a=1##, *&b=2)].tags[*]',
    'books[(*&a=1, *&b=2)][0]',
])
def test_row_step_group_unsupported_raises(path):
    """
    A group of row steps raises where its rows can't be read: an
    unnamed table has none to select from; rows a slice or an index
    picks no predicate tells apart, for a soft cut or a negation; a
    branch that goes on past its row step, or a slice filter that
    reads the list; a soft cut whose path after may match a row more
    than once; and a row step after it.
    """
    with pytest.raises(TranslationError):
        sqlize(path, root='table')


@pytest.mark.parametrize('path', [
    'hello[book_id="b1"].title',
    'hello[10:20].title',
    'hello[*][0]',
    'hello[*&a=1][0]',
    'hello[*](.title,.author;)',
    'hello[::2]',
    'hello[-1]',
    'hello[-2:]',
    'hello[0:5][0]',
    'hello[0:5][a=1]',
    'hello[0:5][*&a=1]',
    'hello[0:5][0:2]',
    'hello = 1',
    'hello.title = 1',
    '*[*]',
    '$(t)[*]',
    'hello[$(n)]',
    'hello[$(a):5]',
    'hello[*&a=1?]',
    'hello[*&tags[*]="x"]',
    'hello[+]',
])
def test_table_search_unsupported_paths_raise(path):
    """
    Table searches v1 can't express raise TranslationError: a packed
    group, a key after a slice or slice filter (dotted reads the list
    there), a row step after a row step, slice steps and negative
    bounds, unresolved index / slice substitutions, and the rest.
    """
    with pytest.raises(TranslationError):
        sqlize(path, root='table')
