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


def sparkize(path, *, flavor='pyspark', bindings=None):
    """
    Test-file shim: fills in the only flavor.
    """
    return _sparkize(path, flavor=flavor, bindings=bindings)


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


@pytest.mark.parametrize('path', [
    'data.**',
    'data.** = 1',
    '*.age = 1',
    '$(col).age = 1',
    'data.$(key) = 1',
    'data.users[*].$(key) = 1',
    'age < None',
    'data.user.age < None',
    'name < /^a/',
    'data.users[*].name < /^a/',
    'data.user.age|upper = 1',
    'data.$$(^x) = 1',
])
def test_unsupported_paths_raise(path):
    """
    Paths sparkize can't express raise TranslationError at sparkize
    time, before any Column is built.
    """
    with pytest.raises(TranslationError):
        sparkize(path)
