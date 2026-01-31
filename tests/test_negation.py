"""
Tests for negation operator (!) on filters and paths.

Filter negation: [!filter], [!(expr)], [a&!b]
Path negation: (!key), (!(a,b))
"""
import pytest
import dotted


# =============================================================================
# Filter Negation Tests
# =============================================================================

def test_parse_filter_negation():
    """
    Test that negation filters parse correctly.
    """
    dotted.parse('[!status="active"]')
    dotted.parse('[!(a=1&b=2)]')
    dotted.parse('[status="active"&!role="admin"]')
    dotted.parse('[!a=1,b=2]')  # !a=1 OR b=2


def test_negate_simple_filter():
    """
    Negate a simple key=value filter.
    """
    data = [
        {"status": "active", "role": "admin"},
        {"status": "inactive", "role": "user"},
        {"status": "active", "role": "user"},
    ]

    # Get items where status != "active"
    r = dotted.get(data, '[!status="active"]')
    assert len(r) == 1
    assert r[0]["status"] == "inactive"


def test_negate_grouped_filter():
    """
    Negate a grouped (AND) filter expression.
    """
    data = [
        {"status": "active", "role": "admin"},
        {"status": "inactive", "role": "user"},
        {"status": "active", "role": "user"},
    ]

    # Get items that are NOT (active AND admin)
    r = dotted.get(data, '[!(status="active"&role="admin")]')
    assert len(r) == 2
    # Should include inactive user and active user (but not active admin)
    statuses = {item["status"] + "_" + item["role"] for item in r}
    assert statuses == {"inactive_user", "active_user"}


def test_combine_negation_with_and():
    """
    Combine negation with AND operator.
    """
    data = [
        {"status": "active", "role": "admin"},
        {"status": "inactive", "role": "user"},
        {"status": "active", "role": "user"},
    ]

    # Get active non-admins
    r = dotted.get(data, '[status="active"&!role="admin"]')
    assert len(r) == 1
    assert r[0]["status"] == "active"
    assert r[0]["role"] == "user"


def test_combine_negation_with_or():
    """
    Combine negation with OR operator.
    """
    data = [
        {"status": "active", "role": "admin"},
        {"status": "inactive", "role": "user"},
        {"status": "active", "role": "user"},
    ]

    # Get items that are NOT active OR are admin
    r = dotted.get(data, '[!status="active",role="admin"]')
    assert len(r) == 2
    # Should include inactive_user (not active) and active_admin (is admin)
    statuses = {item["status"] + "_" + item["role"] for item in r}
    assert statuses == {"inactive_user", "active_admin"}


def test_negation_precedence():
    """
    ! binds tighter than & and ,
    """
    data = [
        {"a": 1, "b": 2},
        {"a": 1, "b": 3},
        {"a": 2, "b": 2},
    ]

    # [!a=1&b=2] should parse as [(!a=1) & b=2]
    # Items where a != 1 AND b = 2
    r = dotted.get(data, '[!a=1&b=2]')
    assert len(r) == 1
    assert r[0] == {"a": 2, "b": 2}


def test_double_negation():
    """
    Double negation cancels out.
    """
    data = [
        {"status": "active"},
        {"status": "inactive"},
    ]

    # This should match items where status = "active"
    r = dotted.get(data, '[!(!status="active")]')
    assert len(r) == 1
    assert r[0]["status"] == "active"


def test_negate_with_first_match():
    """
    Negation combined with first-match operator.
    """
    data = [
        {"status": "active"},
        {"status": "inactive"},
        {"status": "pending"},
    ]

    # First item where status != "active"
    r = dotted.get(data, '[!status="active"?]')
    assert len(r) == 1
    assert r[0]["status"] == "inactive"


def test_negate_with_dict_pattern():
    """
    Negation with wildcard pattern on dict.
    """
    d = {
        'a': {'id': 1, 'type': 'admin'},
        'b': {'id': 2, 'type': 'user'},
        'c': {'id': 3, 'type': 'admin'},
    }

    # Get items that are NOT admin
    r = dotted.get(d, '*&!type="admin"')
    assert len(r) == 1
    assert r[0]['type'] == 'user'


def test_negate_boolean_filter():
    """
    Negation with boolean values.
    """
    data = [
        {"name": "alice", "active": True},
        {"name": "bob", "active": False},
    ]

    r = dotted.get(data, '[!active=True]')
    assert len(r) == 1
    assert r[0]["name"] == "bob"


def test_negate_none_filter():
    """
    Negation with None values.
    """
    data = [
        {"name": "alice", "score": None},
        {"name": "bob", "score": 100},
    ]

    r = dotted.get(data, '[!score=None]')
    assert len(r) == 1
    assert r[0]["name"] == "bob"


def test_negate_nested_path_filter():
    """
    Negation with dotted filter keys.
    """
    data = [
        {"user": {"role": "admin"}, "value": 1},
        {"user": {"role": "user"}, "value": 2},
    ]

    r = dotted.get(data, '[!user.role="admin"]')
    assert len(r) == 1
    assert r[0]["value"] == 2


def test_update_with_filter_negation_on_dict():
    """
    Update dict values where filter doesn't match.
    """
    d = {
        'a': {'type': 'admin', 'val': 1},
        'b': {'type': 'user', 'val': 2},
        'c': {'type': 'user', 'val': 3},
    }

    # Update all non-admin items
    r = dotted.update(d, '*&!type="admin"', {'type': 'guest', 'val': 0})
    assert r['a'] == {'type': 'admin', 'val': 1}  # unchanged
    assert r['b'] == {'type': 'guest', 'val': 0}
    assert r['c'] == {'type': 'guest', 'val': 0}


def test_update_nested_with_filter_negation():
    """
    Update nested values with negated filter.
    """
    d = {
        'a': {'type': 'admin', 'score': 100},
        'b': {'type': 'user', 'score': 50},
    }

    # Update score for non-admins
    r = dotted.update(d, '*&!type="admin".score', 999)
    assert r['a']['score'] == 100  # unchanged
    assert r['b']['score'] == 999


def test_remove_with_filter_negation_on_dict():
    """
    Remove dict entries where filter doesn't match.
    """
    d = {
        'a': {'type': 'admin', 'val': 1},
        'b': {'type': 'user', 'val': 2},
    }

    # Remove first non-admin item (remove with pattern removes first match)
    r = dotted.remove(d, '*&!type="admin"')
    assert r == {'a': {'type': 'admin', 'val': 1}}


# =============================================================================
# Path Negation Tests
# =============================================================================

def test_parse_path_negation():
    """
    Test that path negation parses correctly.
    """
    dotted.parse('(!a)')
    dotted.parse('(!(a,b))')
    dotted.parse('(!a,b)')  # !a OR b


def test_negate_single_key():
    """
    Exclude a single key.
    """
    d = {'a': 1, 'b': 2, 'c': 3}

    # Get all keys except 'a'
    r = dotted.get(d, '(!a)')
    assert set(r) == {2, 3}


def test_negate_multiple_keys():
    """
    Exclude multiple keys.
    """
    d = {'a': 1, 'b': 2, 'c': 3, 'd': 4}

    # Get all keys except 'a' and 'b'
    r = dotted.get(d, '(!(a,b))')
    assert set(r) == {3, 4}


def test_negate_with_or():
    """
    Path negation combined with OR.
    """
    d = {'a': 1, 'b': 2, 'c': 3}

    # Get NOT 'a' OR 'b' (i.e., keys that aren't 'a', plus 'b')
    r = dotted.get(d, '(!a,b)')
    # !a gives b,c; b gives b; so result is b,c (deduplicated if applicable)
    assert 2 in r
    assert 3 in r


def test_negate_on_list():
    """
    Path negation on list indices.
    """
    data = ['a', 'b', 'c', 'd']

    # Get all items except index 0
    r = dotted.get(data, '(!0)')
    assert set(r) == {'b', 'c', 'd'}


def test_negate_nested():
    """
    Path negation in nested access.
    """
    d = {
        'user': {'name': 'alice', 'email': 'a@x.com', 'password': 'secret'}
    }

    # Get all user fields except password
    r = dotted.get(d, 'user.(!password)')
    assert set(r) == {'alice', 'a@x.com'}


def test_negate_missing_key():
    """
    Negating a missing key returns all keys.
    """
    d = {'a': 1, 'b': 2}

    # Exclude 'x' which doesn't exist - should return all
    r = dotted.get(d, '(!x)')
    assert set(r) == {1, 2}


def test_update_with_path_negation():
    """
    Update all keys except excluded ones.
    """
    d = {'a': 1, 'b': 2, 'c': 3}

    r = dotted.update(d, '(!a)', 99)
    assert r == {'a': 1, 'b': 99, 'c': 99}


def test_remove_with_path_negation():
    """
    Remove all keys except excluded ones.
    """
    d = {'a': 1, 'b': 2, 'c': 3}

    r = dotted.remove(d, '(!a)')
    assert r == {'a': 1}


# =============================================================================
# Pluck, Expand, Has, Setdefault Tests
# =============================================================================

def test_expand_with_filter_negation():
    """
    Expand keys matching negated filter.
    """
    d = {
        'a': {'type': 'admin', 'val': 1},
        'b': {'type': 'user', 'val': 2},
        'c': {'type': 'user', 'val': 3},
    }

    r = dotted.expand(d, '*&!type="admin"')
    assert set(r) == {'b', 'c'}


def test_pluck_with_filter_negation():
    """
    Pluck key-value pairs with negated filter.
    """
    d = {
        'a': {'type': 'admin', 'val': 1},
        'b': {'type': 'user', 'val': 2},
    }

    r = dotted.pluck(d, '*&!type="admin"')
    assert len(r) == 1
    assert r[0][0] == 'b'
    assert r[0][1] == {'type': 'user', 'val': 2}


def test_expand_with_path_negation():
    """
    Expand with path negation.
    """
    d = {'a': 1, 'b': 2, 'c': 3}

    r = dotted.expand(d, '(!a)')
    assert set(r) == {'b', 'c'}


def test_pluck_with_path_negation():
    """
    Pluck with path negation.
    """
    d = {'a': 1, 'b': 2, 'c': 3}

    r = dotted.pluck(d, '(!a)')
    keys = {item[0] for item in r}
    vals = {item[1] for item in r}
    assert keys == {'b', 'c'}
    assert vals == {2, 3}


def test_has_with_filter_negation():
    """
    Check existence with negated filter.
    """
    d = {
        'a': {'type': 'admin'},
        'b': {'type': 'user'},
    }

    assert dotted.has(d, '*&!type="admin"') is True
    assert dotted.has(d, '*&!type="user"') is True
    # All items have a type, so negating both should still find items
    assert dotted.has(d, '*&!type="guest"') is True


def test_has_with_path_negation():
    """
    Check existence with path negation.
    """
    d = {'a': 1, 'b': 2}

    assert dotted.has(d, '(!a)') is True  # b exists
    assert dotted.has(d, '(!(a,b))') is False  # exclude both, nothing left


def test_setdefault_with_path_negation():
    """
    Setdefault with path negation.
    """
    d = {'a': 1, 'b': 2}

    # (!a) matches 'b', which exists, so no change
    r = dotted.setdefault(d, '(!a)', 99)
    assert r == {'a': 1, 'b': 2}


# =============================================================================
# Repr Tests
# =============================================================================

def test_filter_not_repr():
    """
    FilterNot repr shows ! prefix.
    """
    ops = dotted.parse('[!status="active"]')
    # The SliceFilter contains the FilterNot
    assert '!' in repr(ops)


def test_path_not_repr():
    """
    PathNot repr shows ! prefix.
    """
    ops = dotted.parse('(!a)')
    assert '!' in repr(ops)
