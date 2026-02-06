import pytest
import dotted



def test_get_key():
    d = {'hello': {'there': [1, '2', 3]}}
    r = dotted.get(d, 'hello.there')
    assert r == [1, '2', 3]


def test_get_dot_index():
    """Test dot notation for list index access (items.0 instead of items[0])"""
    # Basic list index
    data = {'items': [1, 2, 3]}
    assert dotted.get(data, 'items.0') == 1
    assert dotted.get(data, 'items.1') == 2
    assert dotted.get(data, 'items.2') == 3

    # Negative index
    assert dotted.get(data, 'items.-1') == 3
    assert dotted.get(data, 'items.-2') == 2

    # Chaining through list
    data2 = {'items': [{'x': 10}, {'x': 20}]}
    assert dotted.get(data2, 'items.0.x') == 10
    assert dotted.get(data2, 'items.1.x') == 20

    # Out of bounds - safe traversal returns None
    assert dotted.get(data, 'items.10') is None
    assert dotted.get(data, 'items.-10') is None

    # Dict with numeric keys still works (dict takes precedence)
    d = {0: 'zero', 1: 'one'}
    assert dotted.get(d, '0') == 'zero'
    assert dotted.get(d, '1') == 'one'

    # Tuple access
    data3 = {'coords': (10, 20, 30)}
    assert dotted.get(data3, 'coords.0') == 10
    assert dotted.get(data3, 'coords.1') == 20

    # String access (character index)
    data4 = {'name': 'hello'}
    assert dotted.get(data4, 'name.0') == 'h'
    assert dotted.get(data4, 'name.-1') == 'o'


def test_update_dot_index():
    """Test update with dot notation for list index"""
    data = {'items': [1, 2, 3]}
    dotted.update(data, 'items.0', 999)
    assert data['items'][0] == 999

    # Nested update
    data2 = {'items': [{'x': 10}, {'x': 20}]}
    dotted.update(data2, 'items.0.x', 100)
    assert data2['items'][0]['x'] == 100


def test_remove_dot_index():
    """Test remove with dot notation for list index"""
    data = {'items': [1, 2, 3]}
    dotted.remove(data, 'items.1')
    assert data['items'] == [1, 3]


def test_path_grouping_disjunction():
    """Test path-level disjunction (a,b) - returns tuple of what exists"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # All exist
    assert dotted.get(d, '(a,b)') == (1, 2)
    assert dotted.get(d, '(a,b,c)') == (1, 2, 3)

    # Partial exist
    assert dotted.get(d, '(a,x)') == (1,)
    assert dotted.get(d, '(x,b,y)') == (2,)

    # None exist
    assert dotted.get(d, '(x,y)') == ()

    # Nested access
    data = {'user': {'name': 'alice', 'email': 'a@x.com'}}
    assert dotted.get(data, 'user.(name,email)') == ('alice', 'a@x.com')
    assert dotted.get(data, 'user.(name,missing)') == ('alice',)


def test_path_grouping_conjunction():
    """Test path-level conjunction (a&b) - returns tuple only if ALL exist"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # All exist
    assert dotted.get(d, '(a&b)') == (1, 2)
    assert dotted.get(d, '(a&b&c)') == (1, 2, 3)

    # One missing - fail
    assert dotted.get(d, '(a&x)') == ()
    assert dotted.get(d, '(a&b&x)') == ()

    # Nested access
    data = {'user': {'name': 'alice', 'email': 'a@x.com'}}
    assert dotted.get(data, 'user.(name&email)') == ('alice', 'a@x.com')
    assert dotted.get(data, 'user.(name&missing)') == ()


def test_path_grouping_first():
    """Test first-match path grouping with ?"""
    d = {'a': 1, 'b': 2}

    assert dotted.get(d, '(a,b)?') == (1,)
    assert dotted.get(d, '(x,a)?') == (1,)
    assert dotted.get(d, '(x,y)?') == ()


def test_path_grouping_mixed():
    """Test mixed conjunction/disjunction with grouping"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # (a AND b) OR c - should return a,b since both exist
    assert dotted.get(d, '((a&b),c)') == (1, 2, 3)

    # (a AND x) OR c - a&x fails, falls back to c
    assert dotted.get(d, '((a&x),c)') == (3,)


def test_path_grouping_with_patterns():
    """Test path grouping combined with patterns"""
    data = {'items': [{'x': 10, 'y': 20}, {'x': 30, 'z': 40}]}

    # Get x and y from each item
    result = dotted.get(data, 'items[*].(x,y)')
    assert result == (10, 20, 30)  # y missing from second item


def test_path_grouping_on_list():
    """Test path grouping with numeric indices on lists"""
    data = {'items': [10, 20, 30, 40]}

    # Get indices 0 and 2
    assert dotted.get(data, 'items.(0,2)') == (10, 30)

    # Conjunction with indices
    assert dotted.get(data, 'items.(0&1)') == (10, 20)
    assert dotted.get(data, 'items.(0&10)') == ()  # 10 out of bounds


def test_path_grouping_update_disjunction():
    """Test update with path disjunction - updates all that exist"""
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.update(d, '(a,b)', 99)
    assert result == {'a': 99, 'b': 99, 'c': 3}
    assert dotted.get(result, '(a,b)') == (99, 99)

    # Partial - only updates what exists
    d2 = {'a': 1, 'c': 3}
    result = dotted.update(d2, '(a,b)', 99)
    assert result == {'a': 99, 'c': 3}
    assert dotted.get(result, '(a,b)') == (99,)

    # None exist - creates last concrete path (first concrete when scanning last to first)
    d3 = {'c': 3}
    result = dotted.update(d3, '(a,b)', 99)
    assert result == {'b': 99, 'c': 3}
    assert dotted.get(result, 'b') == 99


def test_path_grouping_update_conjunction():
    """Test update with path conjunction - all or nothing"""
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.update(d, '(a&b)', 99)
    assert result == {'a': 99, 'b': 99, 'c': 3}

    # One missing - creates it (conjunction: make all branches true)
    d2 = {'a': 1, 'c': 3}
    result = dotted.update(d2, '(a&b)', 99)
    assert result == {'a': 99, 'b': 99, 'c': 3}


def test_path_grouping_remove_disjunction():
    """Test remove with path disjunction - removes all that exist"""
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.remove(d, '(a,b)')
    assert result == {'c': 3}
    assert dotted.has(result, 'a') is False
    assert dotted.has(result, 'b') is False

    # Partial
    d2 = {'a': 1, 'c': 3}
    result = dotted.remove(d2, '(a,b)')
    assert result == {'c': 3}
    assert dotted.has(result, 'a') is False


def test_path_grouping_remove_conjunction():
    """Test remove with path conjunction - all or nothing"""
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.remove(d, '(a&b)')
    assert result == {'c': 3}

    # One missing - no removal
    d2 = {'a': 1, 'c': 3}
    result = dotted.remove(d2, '(a&b)')
    assert result == {'a': 1, 'c': 3}


def test_path_grouping_nested_update():
    """Test update with nested path grouping"""
    data = {'user': {'name': 'alice', 'email': 'a@x.com', 'age': 30}}
    result = dotted.update(data, 'user.(name,email)', 'redacted')
    assert result == {'user': {'name': 'redacted', 'email': 'redacted', 'age': 30}}


def test_path_grouping_equivalence():
    """Verify (a,b) path grouping behaves same as (.a,.b) op grouping"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # get - should return same results
    assert dotted.get(d, '(a,b)') == dotted.get(d, '(.a,.b)')
    assert dotted.get(d, '(a,x)') == dotted.get(d, '(.a,.x)')

    # Nested
    data = {'user': {'name': 'alice', 'age': 30}}
    assert dotted.get(data, 'user.(name,age)') == dotted.get(data, 'user(.name,.age)')

    # update
    d1 = {'a': 1, 'b': 2, 'c': 3}
    d2 = {'a': 1, 'b': 2, 'c': 3}
    assert dotted.update(d1, '(a,b)', 99) == dotted.update(d2, '(.a,.b)', 99)

    # remove
    d1 = {'a': 1, 'b': 2, 'c': 3}
    d2 = {'a': 1, 'b': 2, 'c': 3}
    assert dotted.remove(d1, '(a,b)') == dotted.remove(d2, '(.a,.b)')

    # has
    assert dotted.has(d, '(a,b)') == dotted.has(d, '(.a,.b)')
    assert dotted.has(d, '(a,x)') == dotted.has(d, '(.a,.x)')

    # expand - paths may differ in format (with/without leading dot) but same keys matched
    path_expand = set(dotted.expand(d, '(a,b)'))
    op_expand = set(dotted.expand(d, '(.a,.b)'))
    assert path_expand == {'a', 'b'}
    assert {k.lstrip('.') for k in op_expand} == {'a', 'b'}
    # Both expand to 2 paths
    assert len(path_expand) == len(op_expand)

    # pluck - values should match (paths differ in format)
    path_pluck = list(dotted.pluck(d, '(a,b)'))
    op_pluck = list(dotted.pluck(d, '(.a,.b)'))
    assert [v for _, v in path_pluck] == [v for _, v in op_pluck]


def test_path_grouping_equivalence_conjunction():
    """Verify (a&b) path grouping behaves same as (.a&.b) op grouping"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # get - all exist
    assert dotted.get(d, '(a&b)') == dotted.get(d, '(.a&.b)')

    # get - one missing
    assert dotted.get(d, '(a&x)') == dotted.get(d, '(.a&.x)')

    # update - all exist
    d1 = {'a': 1, 'b': 2, 'c': 3}
    d2 = {'a': 1, 'b': 2, 'c': 3}
    assert dotted.update(d1, '(a&b)', 99) == dotted.update(d2, '(.a&.b)', 99)

    # update - one missing (no change)
    d1 = {'a': 1, 'c': 3}
    d2 = {'a': 1, 'c': 3}
    assert dotted.update(d1, '(a&b)', 99) == dotted.update(d2, '(.a&.b)', 99)

    # has
    assert dotted.has(d, '(a&b)') == dotted.has(d, '(.a&.b)')
    assert dotted.has(d, '(a&x)') == dotted.has(d, '(.a&.x)')


def test_path_grouping_equivalence_negation():
    """Verify (!a) path grouping behaves same as (!.a) op grouping"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # get - negate single key (same values)
    assert set(dotted.get(d, '(!a)')) == set(dotted.get(d, '(!.a)'))

    # get - negate multiple keys (both syntaxes now work)
    assert set(dotted.get(d, '(!(a,b))')) == set(dotted.get(d, '(!(.a,.b))'))
    assert set(dotted.get(d, '(!(a,b))')) == {3}  # only c remains

    # Nested negation works with both syntaxes
    data = {'user': {'a': 1, 'b': 2, 'c': 3}}
    assert set(dotted.get(data, 'user.(!a)')) == set(dotted.get(data, 'user(!.a)'))
    assert set(dotted.get(data, 'user.(!(a,b))')) == set(dotted.get(data, 'user(!(.a,.b))'))

    # has
    assert dotted.has(d, '(!a)') == dotted.has(d, '(!.a)')

    # expand - same number of paths matched
    path_expand = set(dotted.expand(d, '(!a)'))
    op_expand = set(dotted.expand(d, '(!.a)'))
    assert len(path_expand) == len(op_expand) == 2  # b and c


def test_path_grouping_with_slice():
    """Test path grouping with numeric keys on lists"""
    data = {'items': [10, 20, 30, 40]}

    # Numeric keys work in path grouping (treated as indices for lists)
    result = dotted.get(data, 'items.(0,2)')
    assert result == (10, 30)

    # Multiple indices
    assert dotted.get(data, 'items.(0,1,2)') == (10, 20, 30)

    # TODO: Slice syntax [0] in path grouping not yet supported
    # This would be needed for: **-2:(.*,[])


def test_path_grouping_mixed_ops():
    """Test path grouping with mixed op types (key vs slice)"""
    # This is the key use case for recursive wildcard terminal handling
    data = {'a': [1, 2], 'b': {'x': 10}}

    # Get 'a' (list) or 'b' (dict)
    result = dotted.get(data, '(a,b)')
    assert result == ([1, 2], {'x': 10})

    # Wildcard combined with key
    d = {'x': 1, 'y': 2}
    result = dotted.get(d, '(*,x)')  # * matches all, x adds x again
    assert set(result) == {1, 2}  # x may appear twice, but values dedupe


def test_path_grouping_with_wildcard():
    """Test path grouping with wildcard patterns"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # Wildcard in grouping
    result = dotted.get(d, '(*)')
    assert set(result) == {1, 2, 3}

    # Wildcard with specific key
    result = dotted.get(d, '(a,*)')
    # 'a' matched, then * matches all including a again
    assert 1 in result and 2 in result and 3 in result


def test_path_grouping_with_regex():
    """Test path grouping with regex patterns"""
    d = {'foo': 1, 'bar': 2, 'baz': 3}

    # Direct regex in path grouping
    result = dotted.get(d, '(/^ba./)')
    assert set(result) == {2, 3}

    # Regex combined with key
    result = dotted.get(d, '(foo,/^ba./)')
    assert set(result) == {1, 2, 3}

    # Also works with op grouping syntax
    result = dotted.get(d, '(./^ba./)')
    assert set(result) == {2, 3}


def test_path_grouping_has():
    """Test has() with path grouping"""
    d = {'a': 1, 'b': 2}

    # Disjunction - true if any exist
    assert dotted.has(d, '(a,b)') is True
    assert dotted.has(d, '(a,x)') is True
    assert dotted.has(d, '(x,y)') is False

    # Conjunction - true only if all exist
    assert dotted.has(d, '(a&b)') is True
    assert dotted.has(d, '(a&x)') is False


def test_path_grouping_pluck():
    """Test pluck() with path grouping"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # Pluck returns (path, value) pairs
    result = list(dotted.pluck(d, '(a,b)'))
    assert ('a', 1) in result
    assert ('b', 2) in result
    assert len(result) == 2


def test_path_grouping_expand():
    """Test expand() with path grouping"""
    d = {'a': 1, 'b': 2, 'c': 3}

    # Expand returns paths
    result = list(dotted.expand(d, '(a,b)'))
    assert 'a' in result
    assert 'b' in result
    assert len(result) == 2


def test_get_slot():
    r = dotted.get({}, 'hello[*]')
