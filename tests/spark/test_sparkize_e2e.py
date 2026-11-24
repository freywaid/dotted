"""
End-to-end tests: build sparkize Columns and run them against a local
SparkSession over the sample DataFrame in conftest.py, asserting the
rows (or values) that come back.
"""
import pytest

pytest.importorskip('pyspark')

from pyspark.sql import functions as F

from dotted import sparkize as _sparkize
from dotted.translators import TranslationError


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


# ---- a path under a Column the caller holds -------------------------

def _user_type(df):
    """
    The Spark type of one element of `data.users`.
    """
    return df.schema['data'].dataType['users'].dataType.elementType


def test_head_substitution_reads_under_a_lambda_argument(df):
    """
    A substitution at a path's head, bound in `build` to a lambda's
    argument with its type, reads the path under each element.
    """
    r = sparkize('$(item).age')
    elem = _user_type(df)
    got = F.transform('data.users', lambda e: r.build(
        r.select, schema=df.schema, item=r.typed(e, elem)))
    assert r.unbound == {'item': 'item'}
    assert selected(df, got) == selected(
        df, F.transform('data.users', lambda e: e['age']))


def test_head_substitution_guards_under_a_lambda_argument(df):
    """
    A guard on a path under a lambda's argument is the lambda's test.
    """
    r = sparkize('$(item).age >= 30')
    elem = _user_type(df)
    got = F.exists('data.users', lambda e: r.build(
        r.where, schema=df.schema, item=r.typed(e, elem)))
    assert ids(df, got) == where_ids(df, 'data.users[*].age >= 30')


@pytest.mark.parametrize('bound', [
    lambda: F.col('data'),
    lambda: 'data',
])
def test_head_substitution_bound_in_build(df, bound):
    """
    A head substitution bound in `build` to a Column reads from it, its
    type read in the session; bound to a string, from the column it
    names.
    """
    r = sparkize('$(c).user.age')
    plain = sparkize('data.user.age')
    assert selected(df, r.build(r.select, schema=df.schema, c=bound())) == selected(
        df, plain.build(plain.select, schema=df.schema))


def test_head_substitution_bound_to_a_column_at_sparkize(df):
    """
    A Column bound at a path's head at sparkize time is read from, not
    taken for a column's name.
    """
    r = sparkize('$(c).user.age', bindings={'c': F.col('data')})
    plain = sparkize('data.user.age')
    assert selected(df, r.build(r.select, schema=df.schema)) == selected(
        df, plain.build(plain.select, schema=df.schema))


def test_head_substitution_under_a_table_search_row(spark, df):
    """
    Under `root='table'` the path after the row step starts at what its
    head substitution holds, as a column path does.
    """
    df.createOrReplaceTempView('items')
    r = _sparkize('items[*].$(c).user.age', flavor='pyspark', root='table')
    plain = _sparkize('items[*].data.user.age', flavor='pyspark', root='table')
    table = spark.table(r.table)
    got = r.build(r.select, schema=table.schema, c=F.col('data'))
    assert r.unbound == {'c': 'c'}
    assert selected(table, got) == selected(
        table, plain.build(plain.select, schema=table.schema))


def test_substitution_as_a_table_name_is_refused():
    """
    A table search's name is no place for a substitution left unbound.
    """
    with pytest.raises(TranslationError, match='unresolved substitution'):
        _sparkize('$(t)[*].id', flavor='pyspark', root='table')


def test_typed_column_binding_compares_by_its_type(df):
    """
    A typed Column bound to a guard's value compares by the type it
    carries, with no session read.
    """
    r = sparkize('age >= $(v)')
    typed = r.typed(F.col('age'), df.schema['age'].dataType)
    assert ids(df, r.build(r.where, schema=df.schema, v=typed)) == where_ids(
        df, 'age >= $(v)', v=F.col('age'))


def _analyzed(df, column):
    """
    The type Spark gives `column` over `df`, as a string.
    """
    return df.select(column).schema[0].dataType.simpleString()


@pytest.mark.parametrize('path', ['data.user', 'data.user.age', 'data.users[*].age'])
def test_typed_build_carries_the_type_spark_gives(df, path):
    """
    `typed=True` builds the Column with its Spark type: the one Spark
    gives the Column, a pattern's an array of its matches.
    """
    r = sparkize(path)
    column, dtype = r.build(r.select, schema=df.schema, typed=True)
    assert dtype.simpleString() == _analyzed(df, column)


def test_typed_build_of_a_test_is_boolean(df):
    """
    A `where`, and a composition of them, is boolean.
    """
    r1 = sparkize('age >= 30')
    r2 = sparkize('status = "active"')
    for fragment in (r1.where, r1.where & r2.where, r1.where | r2.where, ~r1.where):
        column, dtype = r1.build(fragment, schema=df.schema, typed=True)
        assert dtype.simpleString() == _analyzed(df, column) == 'boolean'


def test_typed_build_binds_another_build(df):
    """
    What a typed build makes is a binding another build reads by the
    type it carries.
    """
    r1 = sparkize('data.user')
    r2 = sparkize('$(u).age >= 30')
    user = r1.build(r1.select, schema=df.schema, typed=True)
    assert ids(df, r2.build(r2.where, schema=df.schema, u=user)) == where_ids(
        df, 'data.user.age >= 30')


def test_typed_build_of_a_write(df):
    """
    A write builds to a dict of each column to its new value, a
    `TypedColumn` where `typed`.
    """
    r = sparkize('data.user.role')
    plain = r.build(r.as_update('admin'), schema=df.schema)
    typed = r.build(r.as_update('admin'), schema=df.schema, typed=True)
    assert list(typed) == list(plain) == ['data']
    assert typed['data'].dtype.simpleString() == _analyzed(df, plain['data'])


@pytest.mark.parametrize('path', ['nope', 'data.user.nope', 'nope[*]', 'data.users[*].nope',
                                  'data.users[*+].nope'])
def test_typed_build_of_nothing_has_no_type(df, path):
    """
    A path the schema has nothing at, concrete or a pattern, has no
    type to tell: None.
    """
    r = sparkize(path)
    assert r.build(r.select, schema=df.schema, typed=True).dtype is None


@pytest.mark.parametrize('path, expected', [('v', 'void'), ('vs[*]', 'array<void>')])
def test_typed_build_of_a_void_column_tells_its_type(df, path, expected):
    """
    A column the schema types `void` has a type to tell, the null
    type: not None.
    """
    void = df.withColumn('v', F.lit(None)).withColumn('vs', F.array(F.lit(None)))
    r = sparkize(path)
    column, dtype = r.build(r.select, schema=void.schema, typed=True)
    assert dtype.simpleString() == _analyzed(void, column) == expected


def test_typed_build_of_every_column_has_no_type(df):
    """
    A select of `*` is every column of the row: no one type, None.
    """
    r = _sparkize('items[*]', flavor='pyspark', root='table')
    assert r.build(r.select, schema=df.schema, typed=True).dtype is None


def test_typed_build_of_nothing_binds_as_nulls(df):
    """
    A typed build of None type, bound to another build, reads as nulls:
    nothing under it.
    """
    r1 = sparkize('data.user.nope')
    r2 = sparkize('$(u).age >= 30')
    nothing = r1.build(r1.select, schema=df.schema, typed=True)
    assert ids(df, r2.build(r2.where, schema=df.schema, u=nothing)) == []


def test_build_binds_a_reserved_name_by_its_alias(df):
    """
    `schema_` binds `$(schema)`, beside the schema itself.
    """
    r = sparkize('age >= $(schema)')
    assert ids(df, r.build(r.where, schema=df.schema, schema_=30)) == where_ids(
        df, 'age >= 30')


def test_write_under_a_held_column_is_refused(df):
    """
    A write names the column it replaces: a Column at its head raises.
    """
    r = sparkize('$(c).user.age', bindings={'c': F.col('data')})
    with pytest.raises(TranslationError, match='a Column in column position'):
        r.as_update(1)
