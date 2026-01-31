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


def test_get_slot():
    r = dotted.get({}, 'hello[*]')
