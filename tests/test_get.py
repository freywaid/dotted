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

    # Partial - only updates what exists
    d2 = {'a': 1, 'c': 3}
    result = dotted.update(d2, '(a,b)', 99)
    assert result == {'a': 99, 'c': 3}

    # None exist - no change
    d3 = {'c': 3}
    result = dotted.update(d3, '(a,b)', 99)
    assert result == {'c': 3}


def test_path_grouping_update_conjunction():
    """Test update with path conjunction - all or nothing"""
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.update(d, '(a&b)', 99)
    assert result == {'a': 99, 'b': 99, 'c': 3}

    # One missing - no update
    d2 = {'a': 1, 'c': 3}
    result = dotted.update(d2, '(a&b)', 99)
    assert result == {'a': 1, 'c': 3}


def test_path_grouping_remove_disjunction():
    """Test remove with path disjunction - removes all that exist"""
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.remove(d, '(a,b)')
    assert result == {'c': 3}

    # Partial
    d2 = {'a': 1, 'c': 3}
    result = dotted.remove(d2, '(a,b)')
    assert result == {'c': 3}


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


def test_get_slot():
    r = dotted.get({}, 'hello[*]')
