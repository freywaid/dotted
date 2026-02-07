"""
Tests for update_if, update_if_multi, remove_if, remove_if_multi.
"""
import copy
import dotted
import pytest


# update_if

def test_update_if_missing():
    """Always updates when path is missing."""
    r = dotted.update_if({}, 'a.b', 7)
    assert r == {'a': {'b': 7}}


def test_update_if_present_pred_true_default():
    """Default pred is lambda val: val is None; updates when value is None."""
    r = dotted.update_if({'a': {'b': None}}, 'a.b', 7)
    assert r == {'a': {'b': 7}}


def test_update_if_present_pred_false_default():
    """Does not update when value is not None (default pred)."""
    r = dotted.update_if({'a': {'b': 5}}, 'a.b', 7)
    assert r == {'a': {'b': 5}}


def test_update_if_present_pred_true_explicit():
    """Explicit pred: update when pred(current) is true."""
    r = dotted.update_if({'a': {'b': 2}}, 'a.b', 99, pred=lambda v: v < 5)
    assert r == {'a': {'b': 99}}


def test_update_if_present_pred_false_explicit():
    """Explicit pred: no update when pred(current) is false."""
    r = dotted.update_if({'a': {'b': 10}}, 'a.b', 99, pred=lambda v: v < 5)
    assert r == {'a': {'b': 10}}


def test_update_if_pattern_per_match():
    """Per-match predicate: only update where pred holds."""
    d = {'a': [{'x': 1}, {'x': 2}, {'x': 3}]}
    r = dotted.update_if(d, 'a[*].x', 0, pred=lambda v: v < 3)
    assert r == {'a': [{'x': 0}, {'x': 0}, {'x': 3}]}


def test_update_if_mutable_false():
    """mutable=False returns a copy and does not mutate original."""
    orig = {'a': None}
    r = dotted.update_if(orig, 'a', 1, mutable=False)
    assert orig == {'a': None}
    assert r == {'a': 1}


def test_update_if_equivalent_to_path_expression():
    """update_if with default pred matches update with path ( (name&first=None).first, name.~first, name.first )?."""
    path = '( (name&first=None).first, name.~first, name.first )?'
    assert dotted.update_if({'name': {}}, 'name.first', 'hello') == dotted.update({'name': {}}, path, 'hello')
    assert dotted.update_if({'name': {'first': 'Alice'}}, 'name.first', 'hello') == dotted.update({'name': {'first': 'Alice'}}, path, 'hello')
    assert dotted.update_if({'name': {'first': None}}, 'name.first', 'hello') == dotted.update({'name': {'first': None}}, path, 'hello')


# remove_if

def test_remove_if_missing():
    """Remove when path missing: no-op (nothing to remove)."""
    r = dotted.remove_if({'a': 1}, 'b')
    assert r == {'a': 1}


def test_remove_if_present_pred_true_default():
    """Default pred: remove when value is None."""
    r = dotted.remove_if({'a': 1, 'b': None}, 'b')
    assert r == {'a': 1}


def test_remove_if_present_pred_false_default():
    """Default pred: do not remove when value is not None."""
    r = dotted.remove_if({'a': 1, 'b': 2}, 'b')
    assert r == {'a': 1, 'b': 2}


def test_remove_if_pattern_per_match():
    """Per-match predicate: only remove where pred holds."""
    d = {'a': [{'x': 1}, {'x': 2}, {'x': 3}]}
    r = dotted.remove_if(d, 'a[*].x', pred=lambda v: v < 3)
    assert dotted.get(r, 'a[*].x') == (3,)


# update_if_multi

def test_update_if_multi():
    """Multiple (key, val, pred); pred None uses default."""
    r = dotted.update_if_multi({'a': 1}, [('a', 99, lambda v: v == 1), ('b', 2, None)])
    assert r == {'a': 99, 'b': 2}


def test_update_if_multi_partial():
    """Only some keys updated when pred blocks others."""
    r = dotted.update_if_multi(
        {'x': 1, 'y': 2},
        [('x', 10, lambda v: v == 1), ('y', 20, lambda v: v == 99)]
    )
    assert r == {'x': 10, 'y': 2}


# remove_if_multi

def test_remove_if_multi_keys_only():
    """keys_only=True: remove listed keys (pred always true)."""
    r = dotted.remove_if_multi({'a': 1, 'b': None, 'c': 2}, ['b'])
    assert r == {'a': 1, 'c': 2}


def test_remove_if_multi_with_pred():
    """keys_only=False: (key, val, pred) like update_if_multi."""
    d = {'a': 1, 'b': None, 'c': 2}
    r = dotted.remove_if_multi(d, [('b', dotted.ANY, lambda v: v is None), ('c', dotted.ANY, lambda v: v == 2)], keys_only=False)
    assert r == {'a': 1}


def test_remove_if_multi_keys_only_custom_pred():
    """keys_only=True with explicit pred: only remove where pred(value) is true."""
    d = {'a': 0, 'b': 1, 'c': 0}
    r = dotted.remove_if_multi(d, ['a', 'b', 'c'], keys_only=True, pred=lambda v: v == 0)
    assert r == {'b': 1}


def test_remove_if_pred_none_equals_always_true():
    """pred=None and pred=lambda _: True both mean unconditional remove."""
    d = {'a': 1, 'b': 2, 'c': 3}
    r_none = dotted.remove_if(copy.deepcopy(d), 'b', pred=None)
    r_true = dotted.remove_if(copy.deepcopy(d), 'b', pred=lambda _: True)
    assert r_none == r_true == {'a': 1, 'c': 3}


def test_remove_if_multi_pred_none_equals_always_true():
    """pred=None and pred=lambda _: True both mean unconditional remove (remove_multi behavior)."""
    d = {'a': 1, 'b': 2, 'c': 3}
    r_none = dotted.remove_if_multi(copy.deepcopy(d), ['a', 'c'], keys_only=True, pred=None)
    r_true = dotted.remove_if_multi(copy.deepcopy(d), ['a', 'c'], keys_only=True, pred=lambda _: True)
    assert r_none == r_true == {'b': 2}
