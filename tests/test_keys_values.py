"""
Tests for dotted.keys, dotted.values, and dotted.items.
"""
import dotted


def test_keys_flat():
    d = {'a': 1, 'b': 2}
    assert list(dotted.keys(d)) == ['a', 'b']


def test_keys_nested():
    d = {'a': {'b': 1}, 'x': 2}
    assert list(dotted.keys(d)) == ['a.b', 'x']


def test_keys_deep():
    d = {'a': {'b': {'c': 1}}}
    assert list(dotted.keys(d)) == ['a.b.c']


def test_keys_empty():
    assert list(dotted.keys({})) == []


def test_keys_with_lists():
    d = {'a': {'b': [1, 2, 3]}, 'x': 'y'}
    assert list(dotted.keys(d)) == ['a.b', 'x']


def test_keys_attrs():
    class Pt:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    d = {'point': Pt(3, 4)}
    r = dotted.keys(d, attrs=[dotted.Attrs.standard])
    assert 'point@x' in r
    assert 'point@y' in r


def test_keys_set_operations():
    """
    keys() returns a dict_keys view supporting set operations.
    """
    a = dotted.keys({'x': {'y': 1}, 'shared': 2})
    b = dotted.keys({'shared': 3, 'other': 4})
    assert a & b == {'shared'}
    assert a | b == {'x.y', 'shared', 'other'}
    assert a - b == {'x.y'}


def test_values_flat():
    d = {'a': 1, 'b': 2}
    assert list(dotted.values(d)) == [1, 2]


def test_values_nested():
    d = {'a': {'b': 1}, 'x': 2}
    assert list(dotted.values(d)) == [1, 2]


def test_values_deep():
    d = {'a': {'b': {'c': 'hello'}}}
    assert list(dotted.values(d)) == ['hello']


def test_values_empty():
    assert list(dotted.values({})) == []


def test_values_with_lists():
    d = {'a': {'b': [1, 2, 3]}, 'x': 'y'}
    assert list(dotted.values(d)) == [[1, 2, 3], 'y']


def test_values_attrs():
    class Pt:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    d = {'point': Pt(3, 4)}
    r = dotted.values(d, attrs=[dotted.Attrs.standard])
    assert 3 in r
    assert 4 in r


def test_items_returns_dict_items():
    d = {'a': {'b': 1}, 'x': 2}
    r = dotted.items(d)
    assert isinstance(r, type({}.items()))
    assert ('a.b', 1) in r
    assert ('x', 2) in r


def test_items_consistent_with_unpack():
    d = {'a': {'b': 1}, 'x': 2, 'extra': 'stuff'}
    assert list(dotted.items(d)) == list(dotted.unpack(d))


def test_items_set_operations():
    a = dotted.items({'x': 1, 'y': 2})
    b = dotted.items({'y': 2, 'z': 3})
    assert a & b == {('y', 2)}
    assert ('x', 1) in (a - b)


def test_keys_values_consistent_with_unpack():
    """
    keys() and values() should correspond to unpack() paths and values.
    """
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'extra': 'stuff'}
    pairs = dotted.unpack(d)
    assert list(dotted.keys(d)) == [k for k, _ in pairs]
    assert list(dotted.values(d)) == [v for _, v in pairs]
