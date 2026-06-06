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


def test_unpack_mixed_depth_siblings():
    """
    Leaf and nested container as siblings under the same parent.
    """
    d = {'x': {'leaf': 'val1', 'nested': {'deep': 'val2'}}}
    r = dotted.unpack(d)
    assert r == {'x.leaf': 'val1', 'x.nested.deep': 'val2'}


def test_unpack_mixed_depth_siblings_roundtrip():
    """
    Mixed-depth unpack roundtrips through pack.
    """
    d = {'x': {'leaf': 'val1', 'nested': {'deep': 'val2'}}}
    assert dotted.pack(dotted.unpack(d)) == d


def test_unpack_mixed_depth_multi_level():
    """
    Leaves at several different depths within the same tree.
    """
    d = {'a': {'b': 1, 'c': {'d': 2, 'e': {'f': 3}}}}
    r = dotted.unpack(d)
    assert r == {'a.b': 1, 'a.c.d': 2, 'a.c.e.f': 3}


def test_unpack_mixed_depth_three_levels():
    """
    Siblings at three distinct nesting depths under the same parent.
    """
    d = {'a': {'shallow': 1, 'mid': {'x': 2}, 'deep': {'y': {'z': 3}}}}
    r = dotted.unpack(d)
    assert r == {'a.shallow': 1, 'a.mid.x': 2, 'a.deep.y.z': 3}
    assert dotted.pack(r) == d


def test_unpack_mixed_depth_list_and_scalar_siblings():
    """
    A list value and a scalar value as siblings.
    """
    d = {'a': {'b': [1, 2, 3], 'c': 'str'}}
    r = dotted.unpack(d)
    assert r == {'a.b': [1, 2, 3], 'a.c': 'str'}


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


# --- project tests ---

def test_project_single_pattern_string():
    """
    A bare string project selects matching leaves.
    """
    o = {'a': {'b': 1, 'c': 2}, 'x': {'y': 3}}
    assert dotted.unpack(o, project='a') == {'a.b': 1, 'a.c': 2}


def test_project_list_of_patterns():
    o = {'a': {'b': 1, 'c': 2}, 'x': {'y': {'z': 3}}, 'extra': 9}
    assert dotted.unpack(o, project=['a', 'x.y.z']) == {
        'a.b': 1, 'a.c': 2, 'x.y.z': 3}


def test_project_no_match_returns_empty():
    o = {'a': {'b': 1}}
    assert dotted.unpack(o, project='nope') == {}


def test_project_none_returns_full_normal_form():
    o = {'a': {'b': 1}, 'x': 2}
    assert dotted.unpack(o, project=None) == dotted.unpack(o)


def test_project_directional_does_not_pull_ancestor():
    """
    Projecting a deeper path must not select a shallower scalar leaf —
    selection is match-directional, not overlap.
    """
    o = {'a': 1, 'b': 2}
    assert dotted.unpack(o, project='a.b') == {}


def test_project_wildcard_one_level():
    o = {'a': {'b': 1, 'c': 2}, 'x': 3}
    assert dotted.unpack(o, project='a.*') == {'a.b': 1, 'a.c': 2}


def test_project_partial_true_is_greedy():
    """
    Default partial=True lets a trailing segment match deeper leaves.
    """
    o = {'a': {'b': {'c': 1}}, 'd': 9}
    assert dotted.unpack(o, project='a.*') == {'a.b.c': 1}


def test_project_partial_false_is_exact_depth():
    o = {'a': {'b': {'c': 1}}, 'd': 9}
    assert dotted.unpack(o, project='a.*', partial=False) == {}
    assert dotted.unpack(o, project='a.*.*', partial=False) == {'a.b.c': 1}


def test_project_recursive_pattern():
    o = {'a': {'b': 1, 'c': {'d': 2}}, 'x': 9}
    assert dotted.unpack(o, project='a.**', partial=False) == {
        'a.b': 1, 'a.c.d': 2}


def test_project_per_field_partial_override():
    """
    A (pattern, partial) tuple overrides the global partial; bare patterns
    inherit it.
    """
    o = {'a': {'b': {'c': 1}}, 'd': 9}
    r = dotted.unpack(o, project=[('a.*', True), 'd'], partial=False)
    assert r == {'a.b.c': 1, 'd': 9}


def test_project_per_field_mid_pattern_partial():
    """
    Per-field partial matters for mid-pattern matches that '**' can't express:
    'a.*.c' with partial keeps 'a.x.c' and anything below it.
    """
    o = {'a': {'x': {'c': {'deep': 1}}, 'y': {'c': 2}}}
    r = dotted.unpack(o, project=[('a.*.c', True)])
    assert r == {'a.x.c.deep': 1, 'a.y.c': 2}
    # partial=False pins to exactly a.*.c (one leaf at that depth)
    assert dotted.unpack(o, project='a.*.c', partial=False) == {'a.y.c': 2}


def test_project_propagates_to_items_keys_values():
    o = {'a': {'b': 1, 'c': 2}, 'x': 3}
    assert sorted(dotted.items(o, project='a')) == [('a.b', 1), ('a.c', 2)]
    assert sorted(dotted.keys(o, project='a')) == ['a.b', 'a.c']
    assert sorted(dotted.values(o, project='a')) == [1, 2]


def test_project_result_packs_back():
    """
    A projected normal form is still valid input to pack.
    """
    o = {'a': {'b': 1, 'c': 2}, 'x': 9}
    r = dotted.unpack(o, project='a')
    assert dotted.pack(r) == {'a': {'b': 1, 'c': 2}}


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
    AUTO with dict-style pathvalues input.
    """
    r = dotted.update_multi(AUTO, {'a.b': 1, 'c.d': 2})
    assert r == {'a': {'b': 1}, 'c': {'d': 2}}
