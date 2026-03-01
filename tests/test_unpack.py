"""
Tests for dotted.unpack and dotted.AUTO.
"""
import dotted
from dotted import AUTO


def test_unpack_nested_mixed():
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'extra': 'stuff'}
    r = dotted.unpack(d)
    assert r == {'a.b': [1, 2, 3], 'x.y.z': [4, 5], 'extra': 'stuff'}


def test_unpack_flat_dict():
    d = {'a': 1, 'b': 2}
    r = dotted.unpack(d)
    assert r == {'a': 1, 'b': 2}


def test_unpack_deep_scalar():
    d = {'a': {'b': {'c': 1}}}
    r = dotted.unpack(d)
    assert r == {'a.b.c': 1}


def test_unpack_list_values():
    d = {'items': [1, 2, 3], 'name': 'test'}
    r = dotted.unpack(d)
    assert r == {'items': [1, 2, 3], 'name': 'test'}


def test_unpack_empty():
    assert dotted.unpack({}) == {}


def test_unpack_roundtrip():
    """
    Unpack then update_multi(AUTO, ...) should reconstruct the original object.
    """
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'extra': 'stuff'}
    r = dotted.unpack(d)
    assert dotted.update_multi(AUTO, r) == d


def test_unpack_roundtrip_flat():
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.unpack(d)
    assert dotted.update_multi(AUTO, r) == d


def test_unpack_roundtrip_deep():
    d = {'a': {'b': {'c': {'d': 'hello'}}}, 'x': 'world'}
    r = dotted.unpack(d)
    assert dotted.update_multi(AUTO, r) == d


def test_unpack_nested_lists():
    d = {'a': {'b': [1, 2]}, 'c': {'d': [3, 4]}}
    r = dotted.unpack(d)
    assert r == {'a.b': [1, 2], 'c.d': [3, 4]}


def test_unpack_mixed_depth():
    """
    Keys at different nesting depths.
    """
    d = {'shallow': 1, 'deep': {'a': {'b': 2}}, 'mid': {'x': 3}}
    r = dotted.unpack(d)
    assert r['deep.a.b'] == 2
    assert r['mid.x'] == 3
    assert r['shallow'] == 1


# --- attrs tests ---

def test_unpack_attrs():
    """
    attrs=[Attrs.standard] descends into non-dunder object attributes.
    """
    class Pt:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    d = {'point': Pt(3, 4)}
    r = dotted.unpack(d, attrs=[dotted.Attrs.standard])
    assert r['point@x'] == 3
    assert r['point@y'] == 4


def test_unpack_attrs_false_skips_attrs():
    """
    Default attrs=False does not descend into object attributes.
    """
    class Pt:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    d = {'point': Pt(3, 4)}
    r = dotted.unpack(d)
    # With attrs=False, the object is a leaf — returned as-is
    assert len(r) == 1
    assert 'point' in r


# --- AUTO tests ---

def test_auto_update_dict():
    r = dotted.update(AUTO, 'a.b', 1)
    assert r == {'a': {'b': 1}}


def test_auto_update_list():
    r = dotted.update(AUTO, '[0]', 'hello')
    assert r == ['hello']


def test_auto_update_multi_dict():
    r = dotted.update_multi(AUTO, [('a', 1), ('b', 2)])
    assert r == {'a': 1, 'b': 2}


def test_auto_update_multi_list():
    r = dotted.update_multi(AUTO, [('[0]', 'a'), ('[1]', 'b')])
    assert r == ['a', 'b']


def test_auto_update_multi_empty():
    r = dotted.update_multi(AUTO, [])
    assert r == {}


def test_auto_update_if():
    r = dotted.update_if(AUTO, 'a', 1)
    assert r == {'a': 1}


def test_auto_build():
    r = dotted.build(AUTO, 'a.b.c')
    assert r == {'a': {'b': {'c': None}}}


def test_auto_build_list():
    r = dotted.build(AUTO, '[0].a')
    assert r == [{'a': None}]


def test_auto_setdefault():
    r = dotted.setdefault(AUTO, 'a.b', 7)
    assert r == 7


def test_auto_update_multi_dict_from_dict():
    """
    AUTO with dict-style keyvalues input.
    """
    r = dotted.update_multi(AUTO, {'a.b': 1, 'c.d': 2})
    assert r == {'a': {'b': 1}, 'c': {'d': 2}}
