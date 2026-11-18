"""
Tests for sparkize that need no pyspark: translation, its errors, and
the fragment / binding bookkeeping. Nothing here builds a Column — the
tests that do run against a SparkSession under tests/spark/.
"""
import pytest

import dotted
from dotted.translators import (
    sparkize as _sparkize, SparkResolver, ColumnFragment, TranslationError,
    flavors,
)


def sparkize(path, *, flavor='pyspark', bindings=None, root='column'):
    """
    Test-file shim: fills in the only flavor.
    """
    return _sparkize(path, flavor=flavor, bindings=bindings, root=root)


def test_sparkize_is_public():
    """
    `sparkize` is re-exported at the top level.
    """
    assert dotted.sparkize is _sparkize


def test_pyspark_is_a_registered_flavor():
    """
    The built-in flavor is registered.
    """
    assert 'pyspark' in flavors()


def test_flavor_is_required():
    """
    `flavor=` has no default, like sqlize's `driver=`.
    """
    with pytest.raises(TypeError):
        _sparkize('age >= 30')


def test_unknown_flavor_raises():
    """
    An unregistered flavor names the registered ones.
    """
    with pytest.raises(TranslationError, match='unknown flavor'):
        sparkize('age >= 30', flavor='snowpark')


def test_resolver_type():
    """
    sparkize returns the flavor's SparkResolver subclass.
    """
    r = sparkize('age >= 30')
    assert isinstance(r, SparkResolver)
    assert type(r).flavor == 'pyspark'


def test_guard_has_select_and_where():
    """
    A guard yields both fragments; the Mapping interface lists them.
    """
    r = sparkize('data.user.age >= 30')
    assert list(r) == ['select', 'where']
    assert isinstance(r['where'], ColumnFragment)


def test_traversal_has_select_only():
    """
    A path with no guard has nothing to filter on.
    """
    r = sparkize('data.user.name')
    assert list(r) == ['select']
    assert r.where is None


def test_pattern_has_select_and_where():
    """
    A pattern path yields both: `where` for existence, `select` for the
    array of matches.
    """
    r = sparkize('data.users[*].age >= 30')
    assert list(r) == ['select', 'where']


@pytest.mark.parametrize('path', [
    'event.(data, data.components[*]).(amount, reporting_amount)',
    'event.data.(amount, reporting_amount)',
    'data.users[*].(age & active)',
    'data.(x=1, y)',
    'data.(users[*], user)?.age',
    'data.users[*].(a, b)?',
    'data.(a, b)?=1',
    'data.(a#, b)',
    'data.users[*].(a#, b)',
    'data.(a#&b)',
    'data.(a##, b)',
    'data.users[*].(a##, b)',
    'data.(a##&b)',
    'data.(!(status="banned"))',
    'tags(!.color)',
    'data.users(![0]).age',
])
def test_group_has_select_and_where(path):
    """
    A group in a path, the path going on after it or not, is a pattern
    path: both fragments.
    """
    r = sparkize(path)
    assert list(r) == ['select', 'where']


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
def test_row_group_has_select_and_where(path):
    """
    A group at the top of a path reads the row's columns as one
    struct, in a column path and in a table search alike: both
    fragments.
    """
    assert list(sparkize(path)) == ['select', 'where']
    r = sparkize(f'hello[*].{path}', root='table')
    assert (r.table, list(r)) == ('hello', ['select', 'where'])


@pytest.mark.parametrize('path, root', [
    ('!(status="banned")', 'column'),
    ('(!(status="banned"), title)', 'column'),
    ('[*](!.title)', 'table'),
])
def test_row_negation_raises(path, root):
    """
    A negation at the top of a path is the complement of the row's
    columns, which a column path or an unnamed table has no row to read
    from, as in sqlize.
    """
    with pytest.raises(TranslationError, match="row's columns"):
        sparkize(path, root=root)


@pytest.mark.parametrize('path', [
    'hello[*](!.title)',
    'hello[*].(!(status="banned"), title)',
])
def test_row_negation_of_a_table(path):
    """
    A named table's search reads its whole row, every column the schema
    has, for a negation to take the complement of.
    """
    assert 'select' in sparkize(path, root='table')


def test_negation_reaching_any_depth_raises():
    """
    A negation reaching any depth isn't built yet.
    """
    with pytest.raises(TranslationError, match='any depth'):
        sparkize('data.(!**.x)')


def test_unbound_substitution():
    """
    A value-position substitution stays unbound until build.
    """
    r = sparkize('age >= $(min_age)')
    assert r.unbound == {'min_age': 'min_age'}
    assert r.where.unbound == {'min_age': 'min_age'}
    assert r.select.unbound == {}


def test_unbound_substitution_dotted_name():
    """
    A substitution whose name isn't an identifier keeps its name.
    """
    r = sparkize('age >= $(user.min_age)')
    assert list(r.unbound.values()) == ['user.min_age']


def test_unbound_substitution_in_pattern_filter():
    """
    Substitutions inside bracket filters are collected too.
    """
    r = sparkize('data.users[age >= $(min)].active = True')
    assert r.where.unbound == {'min': 'min'}


def test_bindings_resolve_at_sparkize_time():
    """
    `bindings=` resolves substitutions up front.
    """
    r = sparkize('age >= $(min_age)', bindings={'min_age': 30})
    assert r.unbound == {}
    assert r.where.unbound == {}


def test_fragments_compose_and_merge_unbound():
    """
    `&`, `|` and `~` compose fragments, merging what they need.
    """
    a = sparkize('age >= $(min_age)').where
    b = sparkize('status = $(status)').where
    assert (a & b).unbound == {'min_age': 'min_age', 'status': 'status'}
    assert (a | b).unbound == {'min_age': 'min_age', 'status': 'status'}
    assert (~a).unbound == {'min_age': 'min_age'}


def test_fragment_composes_only_with_fragments():
    """
    A fragment doesn't combine with arbitrary objects.
    """
    a = sparkize('age >= 30').where
    with pytest.raises(TypeError):
        a & 1


def test_build_rejects_non_fragment():
    """
    build takes a ColumnFragment.
    """
    r = sparkize('age >= 30')
    with pytest.raises(TranslationError, match='ColumnFragment'):
        r.build('age >= 30')


def test_build_rejects_unknown_binding():
    """
    A binding the fragment doesn't use is an error, as in sqlize.
    """
    r = sparkize('age >= $(min_age)')
    with pytest.raises(TranslationError, match='unknown binding'):
        r.build(r.where, min_age=30, extra=1)


def test_build_rejects_missing_binding():
    """
    Every unbound substitution must be bound.
    """
    r = sparkize('age >= $(min_age)')
    with pytest.raises(TranslationError, match='missing binding'):
        r.build(r.where)


def test_build_needs_the_schema():
    """
    A path reads each node by its type, which the DataFrame's schema
    says: `build` without it raises.
    """
    r = sparkize('age >= 30')
    with pytest.raises(TranslationError, match='schema'):
        r.build(r.where)


def test_as_update_is_a_fragment():
    """
    A write translates without pyspark, to a fragment `build` makes,
    with the substitutions its filters need.
    """
    r = sparkize('data.users[*&age >= $(min)].active')
    u = r.as_update(True)
    assert isinstance(u, ColumnFragment)
    assert u.unbound == {'min': 'min'}


@pytest.mark.parametrize('path', [
    'data.(a##, b)',
    'data.(!a)',
    'data.**.a',
    'data.a[1:2]',
])
def test_unsupported_writes_raise(path):
    """
    Soft cuts, negations, `**` and slices in a write raise, as in sqlize.
    """
    with pytest.raises(TranslationError):
        sparkize(path).as_update(1)


def test_table_search_write_raises():
    """
    A table search's write isn't built yet, as in sqlize.
    """
    with pytest.raises(TranslationError, match='table search'):
        sparkize('books[*].title', root='table').as_update('x')


@pytest.mark.parametrize('path', [
    '*.age = 1',
    '$(col).age = 1',
    'data.$(key) = 1',
    'data.users[*].$(key) = 1',
    'data.user.age|upper = 1',
    'data.users[*].age|int:16 = 1',
    'data.users[*]|upper',
    'data.**:2.role',
    'data.name = /(?L)x/',
    'data.$$(^x) = 1',
])
def test_unsupported_paths_raise(path):
    """
    Paths sparkize can't express raise TranslationError at sparkize
    time, before any Column is built.
    """
    with pytest.raises(TranslationError):
        sparkize(path)


# ---- table search ---------------------------------------------------

@pytest.mark.parametrize('path, table, keys, offset, limit', [
    ('hello', 'hello', ['select'], None, None),
    ('public.hello[*]', 'public.hello', ['select'], None, None),
    ('hello[*&book_id="b1"].title', 'hello', ['select', 'where'], None, None),
    ('hello[0].title', 'hello', ['select'], 0, 1),
    ('hello[10:20]', 'hello', ['select'], 10, 10),
    ('hello[book_id="b1"][10:20][*].title', 'hello', ['select', 'where'], 10, 10),
    ('[*&book_id="b1"]', None, ['select', 'where'], None, None),
])
def test_table_search(path, table, keys, offset, limit):
    """
    Under `root='table'` the plain values match sqlize's; `select` and
    `where` stay Column fragments.
    """
    r = sparkize(path, root='table')
    assert r.table == table
    assert list(r) == keys
    assert (r.offset, r.limit) == (offset, limit)


def test_table_search_unnamed():
    """
    A path that starts with a row step searches an unnamed DataFrame,
    with no `root=` needed.
    """
    r = sparkize('[0]')
    assert (r.table, r.offset, r.limit) == (None, 0, 1)
    assert isinstance(r.select, ColumnFragment)


def test_column_path_has_no_table_values():
    """
    A column path leaves `table`, `offset` and `limit` None.
    """
    r = sparkize('status = "active"')
    assert (r.table, r.offset, r.limit) == (None, None, None)


def test_unknown_root_raises():
    """
    `root` is `'column'` or `'table'`.
    """
    with pytest.raises(TranslationError, match='unknown root'):
        sparkize('hello', root='rows')


def test_table_search_unbound_substitution_in_filter():
    """
    A substitution in a row filter's value binds late.
    """
    r = sparkize('hello[*&age >= $(min_age)]', root='table')
    assert r.unbound == {'min_age': 'min_age'}


def test_table_search_repr_shows_plain_values():
    """
    `repr` shows the table, offset and limit when set.
    """
    r = sparkize('hello[0:5]', root='table')
    assert repr(r) == (
        "PySparkResolver(flavor='pyspark', select, table='hello', "
        "offset=0, limit=5)")


_ROW_STEP_GROUPS = [
    'books[(*&a=1, *&b=2)].title',
    'books[(*&a=1#, *&b=2)].title = "x"',
    'books[(*&a=1##, *&b=2)].title',
    'books[(*&a=1, *&b=2)?].title',
    'books([*&a=1] & [*&b=2])',
    'books(![*&a=1]).title',
    'books[(0, 2)]',
    '[(*&a=1, *&b=2)]',
]


@pytest.mark.parametrize('path', _ROW_STEP_GROUPS)
def test_row_step_group(path):
    """
    A group as the row step reads its rows through `r.rows(df)`, no
    window of them in `offset` / `limit`; an unnamed table's too, as
    `rows` takes the DataFrame.
    """
    r = sparkize(path, root='table')
    assert (r.offset, r.limit) == (None, None)
    assert 'select' in r


def test_rows_of_a_plain_search_is_the_dataframe():
    """
    Without a group as the row step, `r.rows(df)` is `df` itself.
    """
    df = object()
    assert sparkize('hello[*&a=1].title', root='table').rows(df) is df


def test_rows_checks_bindings():
    """
    `rows` binds the substitutions its rows need, as `build` does.
    """
    r = sparkize('books[(*&a=$(a), *&b=2)]', root='table')
    with pytest.raises(TranslationError, match='missing binding'):
        r.rows(object())
    with pytest.raises(TranslationError, match='unknown binding'):
        sparkize('hello[*]', root='table').rows(object(), a=1)


@pytest.mark.parametrize('path', [
    'books[0:5][(*&a=1, *&b=2)]',
    'books[(0##, *&b=2)]',
    'books(![0])',
    'books([*&a=1].title, [*&b=2].author)',
    'books([a=1], [b=2])',
    'books[(*&a=1##, *&b=2)].tags[*]',
])
def test_row_step_group_unsupported_raises(path):
    """
    The groups of row steps sqlize can't read raise here too, but for
    an unnamed table's.
    """
    with pytest.raises(TranslationError):
        sparkize(path, root='table')


@pytest.mark.parametrize('path', [
    'hello[book_id="b1"].title',
    'hello[10:20].title',
    'hello[*][0]',
    'hello[*](.title,.author;)',
    'hello[::2]',
    'hello[-1]',
    'hello[0:5][0]',
    'hello = 1',
])
def test_table_search_unsupported_paths_raise(path):
    """
    The same table searches sqlize rejects raise here too.
    """
    with pytest.raises(TranslationError):
        sparkize(path, root='table')
