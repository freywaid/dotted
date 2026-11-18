"""
End-to-end tests: build sparkize Columns and run them against a local
SparkSession over the sample DataFrame in conftest.py, asserting the
rows (or values) that come back.
"""
import pytest

pytest.importorskip('pyspark')

from pyspark.sql import functions as F

from dotted import sparkize as _sparkize


def sparkize(path, *, bindings=None):
    """
    Test-file shim: fills in the only flavor.
    """
    return _sparkize(path, flavor='pyspark', bindings=bindings)


def ids(df, column):
    """
    Sorted ids of the rows `column` keeps.
    """
    return sorted(row.id for row in df.where(column).select('id').collect())


def selected(df, column):
    """
    id → the value `column` selects, per row.
    """
    return {row.id: row.v
            for row in df.select('id', column.alias('v')).collect()}


def where_ids(df, path, **bindings):
    """
    Sorted ids of the rows a path's `where` keeps.
    """
    r = sparkize(path)
    return ids(df, r.build(r.where, schema=df.schema, **bindings))


# ---- column-level guards --------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('status = "active"', [1, 2, 4, 5, 6]),
    ('status != "active"', [3, 7]),
    ('age >= 30', [2, 3, 5, 6]),
    ('age < 18', [4]),
    ('deleted_at = None', [1, 2, 3, 4, 5, 7]),
    ('deleted_at != None', [6]),
    ('status = /act.*/', [1, 2, 4, 5, 6]),
    ('status != /act.*/', [3, 7]),
    ('status = /^act/', []),
    ('status != /^act/', [1, 2, 3, 4, 5, 6, 7]),
])
def test_column_guard(df, path, expected):
    """
    A guard on the column itself. A regex fullmatches, as dotted's does:
    `/^act/` matches no whole status.
    """
    assert where_ids(df, path) == expected


# ---- boolean groups -------------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('(age >= 18 & status = "active")', [1, 2, 5, 6]),
    ('(status = "banned", status = "pending")', [3, 7]),
])
def test_top_level_group(df, path, expected):
    """
    `&` and `,` map onto `&` and `|`.
    """
    assert where_ids(df, path) == expected


# ---- groups in a path -----------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('data.(user.age, users[*].age)', [1, 2, 3, 4, 5, 6, 7]),
    ('data.users[*].(age >= 30 & active = True)', [2, 5]),
    ('data.user.(age >= 30, age < 18)', [2, 3, 4, 5, 6]),
    ('data.(users[*], user)?.age >= 30', [2, 3, 5, 6]),
    ('data.users[*].(age >= 30#, age >= 18)', [1, 2, 3, 5]),
    ('tags(!.color)', [1, 2, 3]),
])
def test_group_where(df, path, expected):
    """
    A group's `where` keeps rows where it matches. An `&` after `[*]`
    holds per element: one user both 30+ and active. A first match is
    the first user, or `user` where there are none: id=1's first user
    is 25, so its 32 doesn't count. id=7's user.age is null, a None
    that is there, as a struct's field always is.
    """
    assert where_ids(df, path) == expected


_USER_AND_USERS_AGES = {
    1: [25, 25, 32], 2: [35, 35], 3: [40, 40], 4: [17, 17],
    5: [50, 50, 18], 6: [30], 7: [None],
}


@pytest.mark.parametrize('path, expected', [
    ('data.(user.age, users[*].age)', _USER_AND_USERS_AGES),
    ('data.(user, users[*]).age', _USER_AND_USERS_AGES),
    ('data.(user.age & users[*].age)',
     {1: [25, 25, 32], 2: [35, 35], 3: [40, 40], 4: [17, 17],
      5: [50, 50, 18], 6: [], 7: []}),
    ('data.user.(age >= 30, age < 18)',
     {1: [], 2: [35], 3: [40], 4: [17], 5: [50], 6: [30], 7: []}),
    ('data.(users[*], user)?.age',
     {1: [25], 2: [35], 3: [40], 4: [17], 5: [50], 6: [30], 7: [None]}),
    ('data.users[*].(age >= 30, age >= 18)',
     {1: [25, 32, 32], 2: [35, 35], 3: [40, 40], 4: [], 5: [50, 50, 18],
      6: [], 7: []}),
    ('data.users[*].(age >= 30#, age >= 18)',
     {1: [25, 32], 2: [35], 3: [40], 4: [], 5: [50, 18], 6: [], 7: []}),
    ('data.users[*].(age >= 30##, age)',
     {1: [25, 32], 2: [35], 3: [40], 4: [17], 5: [50, 18], 6: [], 7: []}),
    ('data.users[*]((.age >= 30, .age)##, .age)',
     {1: [25, 32], 2: [35], 3: [40], 4: [17], 5: [50, 18], 6: [], 7: []}),
    ('tags(!.color)',
     {1: ['L'], 2: ['red'], 3: ['red'], 4: [], 5: [], 6: [], 7: []}),
    ('data.users(![0]).age',
     {1: [32], 2: [], 3: [], 4: [], 5: [18], 6: [], 7: []}),
])
def test_group_select(df, path, expected):
    """
    A group's `select` is each branch's matches in turn, the path after
    the group run on from each; for `&`, none unless every branch
    matches. A first match is one node, the path after it run from it:
    the first user, or `user` where there are none. After a cut, a
    branch runs only where the cut branch found nothing: a user 30+
    isn't read again as 18+.
    """
    r = sparkize(path)
    assert selected(df, r.build(r.select, schema=df.schema)) == expected


# ---- groups over the row's columns ----------------------------------

ROOTS = ['column', 'table']


def _rooted(spark, df, path, root):
    """
    `path`'s resolver and the DataFrame it reads: a column path over
    `df`, or under root='table' a search of it as the view `items`.
    """
    if root == 'column':
        return sparkize(path), df
    df.createOrReplaceTempView('items')
    r = _sparkize(f'items[*].{path}', flavor='pyspark', root='table')
    return r, spark.table(r.table)


@pytest.mark.parametrize('root', ROOTS)
@pytest.mark.parametrize('path, expected', [
    ('(age >= 18 & status = "active")', [1, 2, 5, 6]),
    ('(age >= 30#, status = "active")', [1, 2, 3, 4, 5, 6]),
    ('(age >= 30##, status = "active")', [1, 2, 3, 4, 5, 6]),
    ('(data.users[*], data.user)?.age >= 30', [2, 3, 5, 6]),
    ('(status = "banned", data.user.role = "guest")', [3, 7]),
])
def test_row_group_where(spark, df, root, path, expected):
    """
    A group over the row's columns keeps the rows where it matches, in
    a table search as in a column path.
    """
    r, rows = _rooted(spark, df, path, root)
    assert ids(rows, r.build(r.where, schema=rows.schema)) == expected


@pytest.mark.parametrize('root', ROOTS)
@pytest.mark.parametrize('path, expected', [
    ('(age, data.user.age)',
     {1: [25, 25], 2: [35, 35], 3: [40, 40], 4: [17, 17], 5: [50, 50],
      6: [30, 30], 7: [None, None]}),
    ('(age >= 30#, data.user.age)',
     {1: [25], 2: [35], 3: [40], 4: [17], 5: [50], 6: [30], 7: [None]}),
    ('(age >= 30##, age)',
     {1: [25], 2: [35], 3: [40], 4: [17], 5: [50], 6: [30], 7: [None]}),
    ('(age >= 30 & data.user.age >= 30)',
     {1: [], 2: [35, 35], 3: [40, 40], 4: [], 5: [50, 50], 6: [30, 30],
      7: []}),
    ('(data.users[*], data.user)?.age',
     {1: [25], 2: [35], 3: [40], 4: [17], 5: [50], 6: [30], 7: [None]}),
])
def test_row_group_select(spark, df, root, path, expected):
    """
    A group over the row's columns selects its matches as `get` does,
    in a table search as in a column path: after a cut, user.age only
    where age isn't 30+; after a soft cut, age once. A null is no match
    for a cut, as for `where`.
    """
    r, rows = _rooted(spark, df, path, root)
    assert selected(rows, r.build(r.select, schema=rows.schema)) == expected


# ---- struct traversal -----------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('data.user.age >= 30', [2, 3, 5, 6]),
    ('data.user.role = "admin"', [1, 5]),
    ('data.user.active = True', [1, 2, 4, 5]),
    ('data.user.active = False', [3, 6]),
    ('data.user.age = None', [7]),
    ('data.user.age|str = "25"', [1]),
])
def test_struct_guard(df, path, expected):
    """
    A guard inside a struct column.
    """
    assert where_ids(df, path) == expected


def test_struct_select(df):
    """
    A plain traversal selects the nested value.
    """
    r = sparkize('data.user.role')
    assert selected(df, r.build(r.select, schema=df.schema)) == {
        1: 'admin', 2: 'user', 3: 'user', 4: 'user',
        5: 'admin', 6: 'user', 7: 'guest',
    }


# ---- array indexes --------------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('data.users[0].age = 17', [4]),
    ('data.users[-1].age = 18', [5]),
    ('data.users[5].age = 1', []),
])
def test_array_index(df, path, expected):
    """
    `[n]` indexes from 0, `[-n]` from the end; out of range is null,
    not an error.
    """
    assert where_ids(df, path) == expected


# ---- maps -----------------------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('tags.color = "red"', [1, 6]),
    ('tags.missing = "red"', []),
    ('tags.* = "red"', [1, 2, 3, 6]),
    ('tags.$$(pick) = "red"', [1, 3, 6]),
])
def test_map(df, path, expected):
    """
    A map key, a missing key (null), every value (`*`), and a key
    taken from another column (`$$(...)`).
    """
    assert where_ids(df, path) == expected


def test_map_wildcard_select(df):
    """
    `*` over a map selects its values.
    """
    r = sparkize('tags.*')
    got = {k: sorted(v) for k, v in selected(df, r.build(r.select, schema=df.schema)).items()}
    assert got == {
        1: ['L', 'red'], 2: ['blue', 'red'], 3: ['red'], 4: [],
        5: ['green'], 6: ['red'], 7: [],
    }


# ---- pattern paths --------------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('data.users[*].age >= 40', [3, 5]),
    ('data.users[*].active = False', [1, 3]),
    ('data.users[*&active = False]', [1, 3]),
    ('data.users[*&active = True].age >= 50', [5]),
    ('data.users[*&age >= 30, active = False]', [1, 2, 3, 5]),
    ('data.users[active = False]', [1, 2, 3, 4, 5, 6, 7]),
    ('data.users[active = False].age', []),
    ('data.users[*]', [1, 2, 3, 4, 5]),
    ('data.users[*+].age >= 40', [1, 2, 3, 4, 5]),
    ('data.users[*+&active=True].age', [1, 2, 4, 5]),
])
def test_pattern_where(df, path, expected):
    """
    A pattern path's `where` keeps rows where some element matches. A
    slice filter, `users[active = False]`, is the filtered list as one
    value, there even when empty, and a list has no `age`.
    """
    assert where_ids(df, path) == expected


@pytest.mark.parametrize('path, expected', [
    ('data.users[*].age',
     {1: [25, 32], 2: [35], 3: [40], 4: [17], 5: [50, 18], 6: [], 7: []}),
    ('data.users[*&active = True].age',
     {1: [25], 2: [35], 3: [], 4: [17], 5: [50, 18], 6: [], 7: []}),
    ('data.users[*].age >= 30',
     {1: [32], 2: [35], 3: [40], 4: [], 5: [50], 6: [], 7: []}),
    ('data.users[*+].age >= 30',
     {1: [None, 32], 2: [35], 3: [40], 4: [None], 5: [50, None], 6: [], 7: []}),
    ('data.users[*+&active=True].age',
     {1: [25], 2: [35], 3: [], 4: [17], 5: [50, 18], 6: [], 7: []}),
])
def test_pattern_select(df, path, expected):
    """
    A pattern path's `select` is the array of matches, in order.
    """
    r = sparkize(path)
    assert selected(df, r.build(r.select, schema=df.schema)) == expected


# ---- substitutions --------------------------------------------------

def test_substitution_bound_at_build(df):
    """
    `$(name)` is bound by `build`.
    """
    assert where_ids(df, 'age >= $(min_age)', min_age=30) == [2, 3, 5, 6]


def test_substitution_bound_at_sparkize(df):
    """
    `bindings=` binds up front.
    """
    r = sparkize('age >= $(min_age)', bindings={'min_age': 30})
    assert ids(df, r.build(r.where, schema=df.schema)) == [2, 3, 5, 6]


def test_substitution_in_pattern_filter(df):
    """
    Substitutions inside a bracket filter.
    """
    assert where_ids(df, 'data.users[*&age >= $(min)].active = True',
                     min=50) == [5]


def test_substitution_bound_to_none(df):
    """
    A substitution bound to None is a null test, like a literal None.
    """
    assert where_ids(df, 'deleted_at = $(when)', when=None) == [1, 2, 3, 4, 5, 7]


def test_substitution_bound_to_column(df):
    """
    A binding may be a Column, used as is.
    """
    assert where_ids(df, 'age >= $(floor)',
                     floor=F.col('id') * 10) == [1, 2, 3, 5]


# ---- composition ----------------------------------------------------

def test_fragments_compose(df):
    """
    Fragments from separate sparkize calls compose before build.
    """
    a = sparkize('age >= $(min_age)')
    b = sparkize('status = "active"')
    assert ids(df, a.build(a.where & b.where, schema=df.schema, min_age=30)) == [2, 5, 6]
    assert ids(df, a.build(~b.where, schema=df.schema)) == [3, 7]


# ---- table search ---------------------------------------------------

def test_table_search(spark, df):
    """
    A table search names the DataFrame, filters and pages its rows,
    and selects a column; the caller chains the plain values. Active
    ids are 1, 2, 4, 5, 6 — offset 1, limit 2 keeps 2 and 4.
    """
    df.createOrReplaceTempView('items')
    r = _sparkize('items[status="active"][1:3][*].id',
                  flavor='pyspark', root='table')
    table = spark.table(r.table)
    rows = (table.where(r.build(r.where, schema=table.schema)).orderBy('id')
            .offset(r.offset).limit(r.limit).select(r.build(r.select, schema=table.schema))
            .collect())
    assert [row.id for row in rows] == [2, 4]


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
def test_row_step_group(spark, df, path, expected):
    """
    A group as the row step reads its rows with `r.rows`: each
    branch's in turn, a row both reach twice; a cut over the table; a
    soft cut skipping what an earlier branch yielded; a first match; an
    `&`; and a negation, id=7's null age not reached by `age>=30`.
    """
    df.createOrReplaceTempView('items')
    r = _sparkize(f'items{path}', flavor='pyspark', root='table')
    rows = r.rows(spark.table(r.table))
    if r.where is not None:
        rows = rows.where(r.build(r.where, schema=rows.schema))
    assert sorted(row.id for row in rows.select('id').collect()) == expected


def test_table_search_whole_rows(spark, df):
    """
    With nothing after the row step, `select` is every column.
    """
    df.createOrReplaceTempView('items')
    r = _sparkize('items[*&data.user.role="admin"]',
                  flavor='pyspark', root='table')
    table = spark.table(r.table)
    out = table.where(r.build(r.where, schema=table.schema)).select(
        r.build(r.select, schema=table.schema))
    assert out.columns == df.columns
    assert sorted(row.id for row in out.collect()) == [1, 5]
