"""
Tests for $$(path) internal references.
"""
import pytest

from dotted.api import get, update, remove, parse, is_pattern


# ---- parsing ----

def test_parse_reference():
    ops = parse('$$(config.key)')
    assert len(ops) == 1
    assert ops[0].is_reference()


def test_parse_reference_repr():
    ops = parse('$$(config.key)')
    assert repr(ops[0]) == '$$(config.key)'


def test_parse_reference_nested():
    ops = parse('data.$$(meta.field)')
    assert len(ops) == 2
    assert not ops[0].is_reference()
    assert ops[1].is_reference()


def test_parse_reference_slot():
    ops = parse('[$$(meta.idx)]')
    assert len(ops) == 1
    assert ops[0].is_reference()


def test_parse_reference_not_named_subst():
    """
    $(name) is a named substitution, not a reference.
    $$(name) is a reference.
    """
    ops_subst = parse('$(name)')
    assert not ops_subst[0].is_reference()
    assert ops_subst[0].is_template()

    ops_ref = parse('$$(name)')
    assert ops_ref[0].is_reference()
    assert not ops_ref[0].is_template()


# ---- is_pattern ----

def test_reference_non_pattern_path():
    assert not is_pattern('$$(config.key)')


def test_reference_pattern_path():
    assert is_pattern('$$(users.*.name)')


# ---- get ----

def test_get_reference_key():
    data = {'config': {'field': 'name'}, 'name': 'Alice'}
    assert get(data, '$$(config.field)') == 'Alice'


def test_get_reference_nested():
    data = {
        'meta': {'key': 'users'},
        'users': [{'name': 'Alice'}, {'name': 'Bob'}],
    }
    result = get(data, '$$(meta.key)[0].name')
    assert result == 'Alice'


def test_get_reference_slot():
    data = {
        'meta': {'idx': 1},
        'items': ['a', 'b', 'c'],
    }
    assert get(data, 'items[$$(meta.idx)]') == 'b'


def test_get_reference_literal():
    """
    Option A: resolved value is used as a literal key, not re-parsed.
    """
    data = {
        'schema': {'path': 'nested.value'},
        'nested.value': 42,
    }
    assert get(data, '$$(schema.path)') == 42


def test_get_reference_missing_raises():
    data = {'config': {'field': 'missing'}, 'name': 'Alice'}
    assert get(data, '$$(config.field)', default='nope') == 'nope'


def test_get_reference_chain():
    """
    Two references in the same path.
    """
    data = {
        'meta': {'section': 'users', 'field': 'name'},
        'users': {'name': 'Alice'},
    }
    assert get(data, '$$(meta.section).$$(meta.field)') == 'Alice'


# ---- update ----

def test_update_reference_key():
    data = {'config': {'field': 'name'}, 'name': 'Alice'}
    result = update(data, '$$(config.field)', 'Bob')
    assert result['name'] == 'Bob'
    # config unchanged
    assert result['config']['field'] == 'name'


def test_update_reference_nested():
    data = {
        'meta': {'key': 'users'},
        'users': [{'name': 'Alice'}],
    }
    result = update(data, '$$(meta.key)[0].name', 'Bob')
    assert result['users'][0]['name'] == 'Bob'


def test_update_reference_slot():
    data = {
        'meta': {'idx': 0},
        'items': ['a', 'b', 'c'],
    }
    result = update(data, 'items[$$(meta.idx)]', 'z')
    assert result['items'] == ['z', 'b', 'c']


# ---- remove ----

def test_remove_reference_key():
    data = {'config': {'field': 'name'}, 'name': 'Alice', 'age': 30}
    result = remove(data, '$$(config.field)')
    assert 'name' not in result
    assert result['age'] == 30


def test_remove_reference_slot():
    data = {
        'meta': {'idx': 1},
        'items': ['a', 'b', 'c'],
    }
    result = remove(data, 'items[$$(meta.idx)]')
    assert result['items'] == ['a', 'c']


# ---- escaped references ----

def test_escaped_reference():
    """
    \\$$(path) should be treated as a literal key, not a reference.
    """
    ops = parse('\\$$(foo)')
    assert not ops[0].is_reference()


# ---- reference not found ----

def test_reference_path_not_found():
    data = {'other': 'value'}
    # get with default should return default
    assert get(data, '$$(missing.path)', default='fallback') == 'fallback'
