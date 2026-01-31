import pytest
import dotted


def test_parse_lookahead_keyvalue():
    dotted.parse('hello&id=1')
    dotted.parse('*&id=1')
    dotted.parse('a[id=1]')
    dotted.parse('a[*]')
    dotted.parse('a[*&id=1]')


def test_match_filter_keyvalue():
    r = dotted.match('a&id=1', 'a&id=1')
    assert r == 'a&id=1'

    r = dotted.match('*&id=1', 'a&id=1')
    assert r == 'a&id=1'

    r = dotted.match('[*]', '[*&id=1]')
    assert r == '[*&id=1]'

    r = dotted.match('[id=*]', '[id=1]')
    assert r == '[id=1]'

    r = dotted.match('*&id=*', 'a&id=1,other=*')
    assert r == 'a&id=1,other=*'

    r = dotted.match('[*&id=*]', '[id=1]')
    assert r is None


def test_get_filter_keyvalue_on_dict():
    d = {
        'a': {
            'id': 1,
            'hello': 'there',
        },
        'b': {
            'id': 2,
            'hello': 'there',
        },
    }

    #
    r = dotted.get(d, 'a[id=1]')
    assert r == {}

    # FIXME: move to different test
    r = dotted.get(d, 'a[*]')
    assert r == (1, 'there')

    # FIXME: move to different test
    r = dotted.get(d, 'a[]')
    assert r is None

    # as non-pattern
    r = dotted.get(d, 'a&id=1')
    assert r == {'id': 1, 'hello': 'there'}

    # as pattern
    r = dotted.get(d, '*&id=1')
    assert r == ({'id': 1, 'hello': 'there'},)

    r = dotted.get(d, '*&id=*')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

    r = dotted.get(d, '*&hello="there"')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

    r = dotted.get(d, '*&hello="there".id')
    assert r == (1, 2)

    # no match
    r = dotted.get(d, 'a&id=7')
    assert r is None

    # fails to parse
    with pytest.raises(dotted.api.ParseError):
        dotted.get({'a': 1, 'b': 2}, 'a=1')

    # conjunctive eval
    r = dotted.get(d, '*&id=1&hello="there"')
    assert r == ({'id': 1, 'hello': 'there'},)

    # disjunctive eval
    r = dotted.get(d, '*&id=1,hello="there"')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

    # match multiple values
    r = dotted.get(d, '*&id=1,id=2')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})


def test_get_filter_keyvalue_on_list():
    d = {
        'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
        'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    }

    r = dotted.get(d, '*[id=1]')
    assert r == ([{'id': 1, 'hello': 'there'}], [])


    r = dotted.get(d, 'a[hello="there"][*].id')
    assert r == (1, 2)

    r = dotted.get(d, '*[hello="there"][*].id')
    assert r == (1, 2, 3)


def test_update_fiter_keyvalue_on_dict():
    d = {
        'a': {
            'id': 1,
            'hello': 'there',
        },
        'b': {
            'id': 2,
            'hello': 'there',
        }
    }
    r = dotted.update(d, 'a&id=1', 6)
    assert r == {'a': 6, 'b': {'id': 2, 'hello': 'there'}}

    r = dotted.update(d, 'b["id"]', 5)
    assert r == {'a': 6, 'b': {'id': 5, 'hello': 'there'}}


def test_update_filter_keyvalue_on_list():
    d = {
        'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
        'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    }

    with pytest.raises(RuntimeError):
        r = dotted.update(d, 'a[id=1]', [7])
        assert r == {'a': [7, {'id': 2, 'hello': 'there'}], 'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}]}

    with pytest.raises(RuntimeError):
        r = dotted.update(d, '*[hello="there"]', 'gone')
        assert r == {'a': [7, 'gone'], 'b': ['gone', {'id': 4, 'hello': 'bye'}]}


def test_remove_filter_keyvalue_on_dict():
    d = {
        'a': {
            'id': 1,
            'hello': 'there',
        },
        'b': {
            'id': 2,
            'hello': 'there',
        }
    }

    # no match
    r = dotted.remove(d, 'a&id=2')
    assert r == {'a': {'id': 1, 'hello': 'there'}, 'b': {'id': 2, 'hello': 'there'}}

    # match with pattern
    r = dotted.remove(d, '*&id=1')
    assert r == {'b': {'id': 2, 'hello': 'there'}}


def test_remove_filter_keyvalue_on_list():
    d = {
        'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
        'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    }

    # remove by value
    r = dotted.remove(d, '*[hello="there"]', [])
    assert r == {'a': [], 'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}]}

    # no change
    r = dotted.remove(d, 'a[id=1]')
    assert r == {'a': [], 'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}]}

    # remove again
    r = dotted.remove(d, '*[hello="there"]')
    assert r == {'a': [], 'b': [{'id': 4, 'hello': 'bye'}]}


def test_get_via_briheuga():
    data = {
        'clients': [
            {'name':'John', 'city':'London'},
            {'name':'David', 'city':'Paris'},
            {'name':'Anne', 'city':'London'}
        ],
    }

    r = dotted.get(data, 'clients[1:]')
    assert r == [{'name': 'David', 'city': 'Paris'}, {'name': 'Anne', 'city': 'London'}]

    r = dotted.get(data, 'clients[city="London"]')
    assert r == [{'name': 'John', 'city': 'London'}, {'name': 'Anne', 'city': 'London'}]

    r = dotted.get(data, 'clients[city="London"][0]')
    assert r == {'name': 'John', 'city': 'London'}

    r = dotted.get(data, 'clients[city="London"][1:]')
    assert r == [{'name': 'Anne', 'city': 'London'}]


def test_get_filter_keyvaluefirst_on_list():
    d = {
        'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
        'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    }

    r = dotted.get(d, '*[hello="there"?]')
    assert r == ([{'id': 1, 'hello': 'there'}], [{'id': 3, 'hello': 'there'}])

    r = dotted.get(d, '*?[hello="there"?]')
    assert r == ([{'id': 1, 'hello': 'there'}],)


def test_has_filter_keyvalue():
    d = {
        'a': {'id': 1, 'name': 'alice'},
        'b': {'id': 2, 'name': 'bob'},
    }
    assert dotted.has(d, '*&id=1') is True
    assert dotted.has(d, '*&id=999') is False
    assert dotted.has(d, 'a&id=1') is True
    assert dotted.has(d, 'a&id=2') is False


def test_expand_filter_keyvalue():
    d = {
        'a': {'id': 1, 'type': 'admin'},
        'b': {'id': 2, 'type': 'user'},
        'c': {'id': 3, 'type': 'admin'},
    }
    # expand returns matching keys (without filter suffix)
    r = dotted.expand(d, '*&type="admin"')
    assert set(r) == {'a', 'c'}


def test_pluck_filter_keyvalue():
    d = {
        'a': {'id': 1, 'val': 'x'},
        'b': {'id': 2, 'val': 'y'},
    }
    # pluck returns key without filter suffix
    r = dotted.pluck(d, '*&id=1')
    assert r == (('a', {'id': 1, 'val': 'x'}),)


def test_setdefault_filter_keyvalue():
    d = {
        'a': {'id': 1},
        'b': {'id': 2},
    }
    # key with filter exists - no change
    r = dotted.setdefault(d, 'a&id=1', 'new')
    assert d['a'] == {'id': 1}  # unchanged


def test_get_filter_chained():
    # Test chaining filter with further access
    d = {'items': [{'id': 1, 'name': 'alice'}, {'id': 2, 'name': 'bob'}]}
    r = dotted.get(d, 'items[id=1][0].name')
    assert r == 'alice'


def test_dotted_filter_key():
    """Test filters with dotted paths in the key (e.g., user.id=1)"""
    d = {
        'items': [
            {'user': {'id': 1, 'name': 'alice'}, 'value': 100},
            {'user': {'id': 2, 'name': 'bob'}, 'value': 200},
        ]
    }
    # Filter on nested path
    r = dotted.get(d, 'items[user.id=1]')
    assert r == [{'user': {'id': 1, 'name': 'alice'}, 'value': 100}]

    r = dotted.get(d, 'items[user.name="bob"]')
    assert r == [{'user': {'id': 2, 'name': 'bob'}, 'value': 200}]

    # Filter on nested path, then access value
    r = dotted.get(d, 'items[user.id=1][0].value')
    assert r == 100


def test_dotted_filter_key_deep():
    """Test filters with deeper dotted paths"""
    d = {
        'records': [
            {'meta': {'author': {'id': 1}}, 'data': 'first'},
            {'meta': {'author': {'id': 2}}, 'data': 'second'},
        ]
    }
    r = dotted.get(d, 'records[meta.author.id=1]')
    assert r == [{'meta': {'author': {'id': 1}}, 'data': 'first'}]

    r = dotted.get(d, 'records[meta.author.id=2][0].data')
    assert r == 'second'


def test_wildcard_filter_key():
    """Test filters with wildcard keys (e.g., *=1)"""
    d = {
        'items': [
            {'name': 'foo', 'val': 1},
            {'name': 'bar', 'val': 2},
        ]
    }
    # Wildcard key - match any key with value 1
    r = dotted.get(d, 'items[*=1]')
    assert r == [{'name': 'foo', 'val': 1}]

    # Wildcard key with string value
    r = dotted.get(d, 'items[*="foo"]')
    assert r == [{'name': 'foo', 'val': 1}]

    # Wildcard key and value - match all items
    r = dotted.get(d, 'items[*=*]')
    assert r == [{'name': 'foo', 'val': 1}, {'name': 'bar', 'val': 2}]


def test_regex_filter_key():
    """Test filters with regex pattern keys"""
    d = {
        'items': [
            {'name': 'foo', 'value': 100},
            {'name': 'bar', 'count': 200},
        ]
    }
    # Regex key - match keys starting with 'val'
    r = dotted.get(d, 'items[/val.*/=100]')
    assert r == [{'name': 'foo', 'value': 100}]

    # Regex key - match keys ending with 'ount'
    r = dotted.get(d, 'items[/.*ount/=200]')
    assert r == [{'name': 'bar', 'count': 200}]


def test_filter_grouping():
    """Test parentheses for grouping filter expressions"""
    data = [
        {'id': 1, 'type': 'a', 'active': True},
        {'id': 2, 'type': 'b', 'active': True},
        {'id': 3, 'type': 'a', 'active': False},
        {'id': 4, 'type': 'b', 'active': False},
    ]

    # Basic AND
    r = dotted.get(data, '[id=1&active=True]')
    assert len(r) == 1
    assert r[0]['id'] == 1

    # Basic OR
    r = dotted.get(data, '[id=1,id=2]')
    assert len(r) == 2

    # Grouped OR with AND: (id=1 OR id=2) AND active=True
    r = dotted.get(data, '[(id=1,id=2)&active=True]')
    assert len(r) == 2
    assert all(item['active'] for item in r)
    assert set(item['id'] for item in r) == {1, 2}

    # Mixed: id=1 OR (id=3 AND active=False)
    r = dotted.get(data, '[id=1,(id=3&active=False)]')
    assert len(r) == 2
    assert set(item['id'] for item in r) == {1, 3}

    # Nested groups: ((id=1 OR id=2) AND type='a') OR id=4
    r = dotted.get(data, '[((id=1,id=2)&type="a"),id=4]')
    assert len(r) == 2
    assert set(item['id'] for item in r) == {1, 4}

    # Complex: (type='a' AND active=True) OR (type='b' AND active=False)
    r = dotted.get(data, '[(type="a"&active=True),(type="b"&active=False)]')
    assert len(r) == 2
    assert set(item['id'] for item in r) == {1, 4}


def test_filter_grouping_with_patterns():
    """Test grouping with wildcard patterns"""
    d = {
        'a': {'status': 'active', 'priority': 1},
        'b': {'status': 'inactive', 'priority': 2},
        'c': {'status': 'active', 'priority': 3},
    }

    # Pattern with grouped filter
    r = dotted.get(d, '*&(status="active"&priority=1)')
    assert len(r) == 1
    assert r[0]['priority'] == 1

    # Pattern with OR group
    r = dotted.get(d, '*&(priority=1,priority=3)')
    assert len(r) == 2


def test_literal_parens_in_keys():
    """Test that quoted parens work as literal key characters"""
    d = {'(key)': 'value', 'normal': 'other'}
    assert dotted.get(d, '"(key)"') == 'value'

    d = {'a': {'(nested)': 123}}
    assert dotted.get(d, 'a."(nested)"') == 123


def test_boolean_none_filter_values():
    """Test filters with True, False, and None values"""
    data = [
        {'name': 'alice', 'active': True, 'score': None},
        {'name': 'bob', 'active': False, 'score': 100},
        {'name': 'carol', 'active': True, 'score': 50},
    ]

    # Filter by True
    r = dotted.get(data, '[active=True]')
    assert len(r) == 2
    assert r[0]['name'] == 'alice'
    assert r[1]['name'] == 'carol'

    # Filter by False
    r = dotted.get(data, '[active=False]')
    assert len(r) == 1
    assert r[0]['name'] == 'bob'

    # Filter by None
    r = dotted.get(data, '[score=None]')
    assert len(r) == 1
    assert r[0]['name'] == 'alice'

    # Combined with pattern
    r = dotted.get(data, '[*&active=True]')
    assert len(r) == 2

    # With dict pattern
    d = {
        'a': {'enabled': True, 'val': 1},
        'b': {'enabled': False, 'val': 2},
        'c': {'enabled': None, 'val': 3},
    }
    r = dotted.get(d, '*&enabled=True')
    assert r == ({'enabled': True, 'val': 1},)

    r = dotted.get(d, '*&enabled=False')
    assert r == ({'enabled': False, 'val': 2},)

    r = dotted.get(d, '*&enabled=None')
    assert r == ({'enabled': None, 'val': 3},)
