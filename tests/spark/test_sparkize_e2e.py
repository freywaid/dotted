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
    return ids(df, r.build(r.where, **bindings))


# ---- column-level guards --------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('status = "active"', [1, 2, 4, 5, 6]),
    ('status != "active"', [3, 7]),
    ('age >= 30', [2, 3, 5, 6]),
    ('age < 18', [4]),
    ('deleted_at = None', [1, 2, 3, 4, 5, 7]),
    ('deleted_at != None', [6]),
    ('status = /^act/', [1, 2, 4, 5, 6]),
    ('status != /^act/', [3, 7]),
])
def test_column_guard(df, path, expected):
    """
    A guard on the column itself.
    """
    assert where_ids(df, path) == expected


# ---- boolean groups -------------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('(age >= 18 & status = "active")', [1, 2, 5, 6]),
    ('(status = "banned", status = "pending")', [3, 7]),
    ('!(status = "active")', [3, 7]),
])
def test_top_level_group(df, path, expected):
    """
    `&`, `,` and `!` map onto `&`, `|` and `~`.
    """
    assert where_ids(df, path) == expected


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
    assert selected(df, r.build(r.select)) == {
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
    got = {k: sorted(v) for k, v in selected(df, r.build(r.select)).items()}
    assert got == {
        1: ['L', 'red'], 2: ['blue', 'red'], 3: ['red'], 4: [],
        5: ['green'], 6: ['red'], 7: [],
    }


# ---- pattern paths --------------------------------------------------

@pytest.mark.parametrize('path, expected', [
    ('data.users[*].age >= 40', [3, 5]),
    ('data.users[*].active = False', [1, 3]),
    ('data.users[active = False]', [1, 3]),
    ('data.users[active = True].age >= 50', [5]),
    ('data.users[age >= 30, active = False]', [1, 2, 3, 5]),
    ('data.users[*]', [1, 2, 3, 4, 5]),
    ('data.users[*+].age >= 40', [1, 2, 3, 4, 5]),
    ('data.users[*+&active=True].age', [1, 2, 4, 5]),
])
def test_pattern_where(df, path, expected):
    """
    A pattern path's `where` keeps rows where some element matches.
    """
    assert where_ids(df, path) == expected


@pytest.mark.parametrize('path, expected', [
    ('data.users[*].age',
     {1: [25, 32], 2: [35], 3: [40], 4: [17], 5: [50, 18], 6: [], 7: []}),
    ('data.users[active = True].age',
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
    assert selected(df, r.build(r.select)) == expected


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
    assert ids(df, r.build(r.where)) == [2, 3, 5, 6]


def test_substitution_in_pattern_filter(df):
    """
    Substitutions inside a bracket filter.
    """
    assert where_ids(df, 'data.users[age >= $(min)].active = True',
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
    assert ids(df, a.build(a.where & b.where, min_age=30)) == [2, 5, 6]
    assert ids(df, a.build(~b.where)) == [3, 7]
