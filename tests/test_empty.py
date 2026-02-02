"""
Tests for empty path operations (root access).
"""
import dotted


def test_get_empty_returns_root():
    data = {'a': 1, 'b': 2}
    assert dotted.get(data, '') == data


def test_get_empty_list():
    data = [1, 2, 3]
    assert dotted.get(data, '') == data


def test_get_empty_primitive():
    assert dotted.get(42, '') == 42
    assert dotted.get('hello', '') == 'hello'
    assert dotted.get(None, '') is None


def test_update_empty_replaces_root():
    data = {'a': 1}
    result = dotted.update(data, '', {'b': 2})
    assert result == {'b': 2}


def test_update_empty_with_primitive():
    data = {'a': 1}
    result = dotted.update(data, '', 42)
    assert result == 42


def test_update_empty_list():
    data = [1, 2, 3]
    result = dotted.update(data, '', [4, 5])
    assert result == [4, 5]


def test_remove_empty_returns_none():
    data = {'a': 1}
    result = dotted.remove(data, '')
    assert result is None


def test_remove_empty_with_matching_value():
    data = {'a': 1}
    result = dotted.remove(data, '', {'a': 1})
    assert result is None


def test_remove_empty_with_non_matching_value():
    data = {'a': 1}
    result = dotted.remove(data, '', {'b': 2})
    assert result == {'a': 1}


def test_has_empty_returns_true():
    assert dotted.has({'a': 1}, '') is True
    assert dotted.has([], '') is True
    assert dotted.has(None, '') is True


def test_expand_empty():
    data = {'a': 1}
    result = dotted.expand(data, '')
    assert result == ('',)


def test_pluck_empty():
    data = {'a': 1, 'b': 2}
    result = dotted.pluck(data, '')
    assert result == ('', {'a': 1, 'b': 2})


def test_build_empty():
    # Building empty path returns the object's default representation
    # For a dict with empty path, we get None (leaf default)
    result = dotted.build({}, '')
    assert result is None


def test_match_empty():
    # Empty pattern matches empty key
    assert dotted.match('', '') == ''
    # Empty pattern does NOT match non-empty key (pattern is Empty, key is not)
    assert dotted.match('', 'a') is None
    # Non-empty pattern does NOT match empty key
    assert dotted.match('a', '') is None


def test_setdefault_empty_existing():
    data = {'a': 1}
    result = dotted.setdefault(data, '', {'b': 2})
    # Root exists, so no change
    assert result == {'a': 1}


# mutable tests

def test_mutable_dict():
    assert dotted.mutable({'a': 1}, 'a') is True


def test_mutable_empty_path():
    # Empty path can never mutate
    assert dotted.mutable({'a': 1}, '') is False


def test_mutable_tuple():
    # Tuples are immutable
    assert dotted.mutable((1, 2), '[0]') is False


def test_mutable_list():
    assert dotted.mutable([1, 2], '[0]') is True


def test_mutable_nested_immutable():
    # Dict contains tuple - dict is still mutable (tuple replaced, not mutated)
    assert dotted.mutable({'a': (1, 2)}, 'a[0]') is True


def test_mutable_nested_mutable():
    # Dict contains dict - can mutate
    assert dotted.mutable({'a': {'b': 1}}, 'a.b') is True


def test_mutable_string():
    # Strings are immutable
    assert dotted.mutable('hello', '[0]') is False


def test_mutable_frozenset():
    assert dotted.mutable(frozenset([1, 2]), '') is False


def test_mutable_missing_path():
    # Path doesn't exist yet, but parent is mutable
    assert dotted.mutable({'a': 1}, 'b') is True


def test_mutable_deep_nested():
    data = {'a': {'b': {'c': 1}}}
    assert dotted.mutable(data, 'a.b.c') is True


def test_mutable_namedtuple():
    from collections import namedtuple
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    assert dotted.mutable(p, 'x') is False


def test_mutable_tuple_containing_dict():
    # Tuple contains a dict - dict IS mutable
    assert dotted.mutable(({'a': 1},), '[0].a') is True


def test_mutable_nested_tuples():
    # All tuples - nothing is mutable
    assert dotted.mutable(((1, 2),), '[0][0]') is False


# mutable=False parameter tests

def test_update_mutable_false_prevents_mutation():
    data = {'a': 1, 'b': 2}
    result = dotted.update(data, 'a', 99, mutable=False)
    assert data == {'a': 1, 'b': 2}  # Original unchanged
    assert result == {'a': 99, 'b': 2}  # Result has update


def test_update_mutable_true_allows_mutation():
    data = {'a': 1, 'b': 2}
    result = dotted.update(data, 'a', 99, mutable=True)
    assert data == {'a': 99, 'b': 2}  # Original mutated
    assert result == {'a': 99, 'b': 2}


def test_update_mutable_false_nested():
    data = {'a': {'b': 1}}
    result = dotted.update(data, 'a.b', 99, mutable=False)
    assert data == {'a': {'b': 1}}  # Original unchanged
    assert result == {'a': {'b': 99}}


def test_remove_mutable_false_prevents_mutation():
    data = {'a': 1, 'b': 2}
    result = dotted.remove(data, 'a', mutable=False)
    assert data == {'a': 1, 'b': 2}  # Original unchanged
    assert result == {'b': 2}


def test_remove_mutable_true_allows_mutation():
    data = {'a': 1, 'b': 2}
    result = dotted.remove(data, 'a', mutable=True)
    assert data == {'b': 2}  # Original mutated
    assert result == {'b': 2}


def test_update_multi_mutable_false():
    data = {'a': 1, 'b': 2}
    result = dotted.update_multi(data, [('a', 99), ('b', 88)], mutable=False)
    assert data == {'a': 1, 'b': 2}  # Original unchanged
    assert result == {'a': 99, 'b': 88}


def test_remove_multi_mutable_false():
    data = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.remove_multi(data, ['a', 'b'], mutable=False)
    assert data == {'a': 1, 'b': 2, 'c': 3}  # Original unchanged
    assert result == {'c': 3}


def test_update_mutable_false_on_immutable_no_copy_needed():
    # Tuple is already immutable, mutable=False shouldn't break anything
    data = (1, 2, 3)
    result = dotted.update(data, '[0]', 99, mutable=False)
    assert data == (1, 2, 3)  # Original unchanged (it's immutable anyway)
    assert result == (99, 2, 3)


def test_update_mutable_false_with_nested_immutable():
    # Dict contains tuple - mutable=False should still prevent dict mutation
    data = {'a': (1, 2, 3)}
    result = dotted.update(data, 'a[0]', 99, mutable=False)
    assert data == {'a': (1, 2, 3)}  # Original dict unchanged
    assert result == {'a': (99, 2, 3)}  # Result has new tuple


def test_remove_mutable_false_with_nested_immutable():
    # Dict contains tuple - mutable=False should still prevent dict mutation
    data = {'a': (1, 2, 3), 'b': 2}
    result = dotted.remove(data, 'b', mutable=False)
    assert data == {'a': (1, 2, 3), 'b': 2}  # Original unchanged
    assert result == {'a': (1, 2, 3)}
