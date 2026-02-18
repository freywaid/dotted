"""
Tests for update_if, update_if_multi, remove_if, remove_if_multi.

update_if: pred gates on the incoming val (default: skip None).
remove_if: pred gates on the key (default: skip None keys).
"""
import copy
import dotted


# update_if

def test_update_if_non_none():
    """Default pred: updates when val is not None."""
    r = dotted.update_if({}, 'a', 1)
    assert r == {'a': 1}


def test_update_if_none_skipped():
    """Default pred: skips when val is None."""
    r = dotted.update_if({}, 'a', None)
    assert r == {}


def test_update_if_zero_not_skipped():
    """Default pred: 0 is not None, so update proceeds."""
    r = dotted.update_if({}, 'a', 0)
    assert r == {'a': 0}


def test_update_if_empty_string_not_skipped():
    """Default pred: '' is not None, so update proceeds."""
    r = dotted.update_if({}, 'a', '')
    assert r == {'a': ''}


def test_update_if_pred_bool():
    """Custom pred=bool: skips falsy values."""
    r = dotted.update_if({}, 'a', '', pred=bool)
    assert r == {}
    r = dotted.update_if({}, 'a', 0, pred=bool)
    assert r == {}
    r = dotted.update_if({}, 'a', 'hello', pred=bool)
    assert r == {'a': 'hello'}


def test_update_if_pred_none_unconditional():
    """pred=None: always updates, same as update."""
    r = dotted.update_if({}, 'a', None, pred=None)
    assert r == {'a': None}


def test_update_if_existing_value():
    """Updates existing value when pred passes."""
    r = dotted.update_if({'a': 1}, 'a', 2)
    assert r == {'a': 2}


def test_update_if_mutable_false():
    """mutable=False returns a copy and does not mutate original."""
    orig = {'a': 1}
    r = dotted.update_if(orig, 'a', 2, mutable=False)
    assert orig == {'a': 1}
    assert r == {'a': 2}


def test_update_if_nested():
    """Works with nested paths."""
    r = dotted.update_if({}, 'a.b.c', 7)
    assert r == {'a': {'b': {'c': 7}}}


# update_if_multi

def test_update_if_multi_skips_none():
    """Default pred: skips None values."""
    r = dotted.update_if_multi({}, [('a', 1), ('b', None), ('c', 3)])
    assert r == {'a': 1, 'c': 3}


def test_update_if_multi_custom_pred():
    """Per-item pred overrides default."""
    r = dotted.update_if_multi({}, [
        ('a', 1),
        ('b', 0, bool),      # 0 is falsy, skip
        ('c', 'hi', bool),   # truthy, proceed
    ])
    assert r == {'a': 1, 'c': 'hi'}


def test_update_if_multi_pred_none_unconditional():
    """pred=None in tuple: unconditional update."""
    r = dotted.update_if_multi({}, [('a', None, None)])
    assert r == {'a': None}


# remove_if

def test_remove_if_non_none_key():
    """Default pred: removes when key is not None."""
    r = dotted.remove_if({'a': 1, 'b': 2}, 'a')
    assert r == {'b': 2}


def test_remove_if_none_key_skipped():
    """Default pred: skips when key is None."""
    r = dotted.remove_if({'a': 1}, None)
    assert r == {'a': 1}


def test_remove_if_pred_none_unconditional():
    """pred=None: always removes, same as remove."""
    r = dotted.remove_if({'a': 1}, 'a', pred=None)
    assert r == {}


def test_remove_if_custom_pred():
    """Custom pred on key."""
    # Only remove keys starting with underscore
    r = dotted.remove_if({'_private': 1, 'public': 2}, '_private', pred=lambda k: k.startswith('_'))
    assert r == {'public': 2}
    r = dotted.remove_if({'_private': 1, 'public': 2}, 'public', pred=lambda k: k.startswith('_'))
    assert r == {'_private': 1, 'public': 2}


# remove_if_multi

def test_remove_if_multi_skips_none():
    """Default pred: skips None keys."""
    r = dotted.remove_if_multi({'a': 1, 'b': 2}, ['a', None, 'b'])
    assert r == {}


def test_remove_if_multi_all_none():
    """All None keys: nothing removed."""
    r = dotted.remove_if_multi({'a': 1}, [None, None])
    assert r == {'a': 1}


def test_remove_if_multi_pred_none():
    """pred=None: unconditional remove."""
    r = dotted.remove_if_multi({'a': 1, 'b': 2}, ['a'], pred=None)
    assert r == {'b': 2}
