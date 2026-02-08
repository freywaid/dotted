import pytest
import dotted


def test_parse_lookahead_keyvalue():
    dotted.parse('hello&id=1')
    dotted.parse('*&id=1')
    dotted.parse('a[id=1]')
    dotted.parse('a[*]')
    dotted.parse('a[*&id=1]')


def test_parse_not_equal():
    """!= parses as its own filter (same semantics as !(key=val), repr stays id!=1)."""
    dotted.parse('hello&id!=1')
    dotted.parse('*&status!="active"')
    dotted.parse('a[id!=1]')
    dotted.parse('a[*&id!=1]')
    # key!=val keeps its own repr so reassemble stays readable
    p = dotted.parse('*&id!=1')
    assert str(p[0].filters[0]) == 'id!=1'


def test_parse_filter_key_slot_path():
    """Filter keys can include slot paths (e.g. tags[*]=*) for array containment."""
    dotted.parse('[*&tags[*]=*]')
    dotted.parse('addresses[*&tags[*]=*]')
    dotted.parse('[*&tags[*]="billing"]')
    dotted.parse('items[*&tags[0]=*]')


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


def test_get_filter_not_equal_on_dict():
    """key!=value matches when key is absent or not equal to value."""
    d = {
        'a': {'id': 1, 'hello': 'there'},
        'b': {'id': 2, 'hello': 'there'},
        'c': {'id': 1, 'hello': 'other'},
    }
    r = dotted.get(d, '*&id!=2')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 1, 'hello': 'other'})

    r = dotted.get(d, '*&id!=1')
    assert r == ({'id': 2, 'hello': 'there'},)

    r = dotted.get(d, '*&hello!="there"')
    assert r == ({'id': 1, 'hello': 'other'},)


def test_get_filter_not_equal_on_list():
    """[key!=value] filters list elements where key is absent or not equal to value."""
    d = {
        'a': [{'id': 1}, {'id': 2}, {'id': 3}],
        'b': [{'id': 2}, {'id': 4}],
    }
    r = dotted.get(d, '*[id!=2]')
    assert r == ([{'id': 1}, {'id': 3}], [{'id': 4}])


def test_match_filter_not_equal():
    """Match treats != like = for path matching (same key, different comparison)."""
    r = dotted.match('*&id!=1', 'a&id!=1')
    assert r == 'a&id!=1'
    r = dotted.match('*&id!=1', 'a&id=2')
    assert r is None  # pattern is !=, path is =


def test_filter_not_equal_and_or():
    """!= composes with & and , like =."""
    d = {
        'a': {'id': 1, 'role': 'admin'},
        'b': {'id': 2, 'role': 'admin'},
        'c': {'id': 1, 'role': 'user'},
    }
    r = dotted.get(d, '*&id!=2&role="admin"')
    assert r == ({'id': 1, 'role': 'admin'},)
    # OR: id!=1 yields b; id!=2 yields a,c; combined we get a, b, c
    r = dotted.get(d, '*&id!=1,id!=2')
    assert set(x['id'] for x in r) == {1, 2}


def test_update_remove_filter_not_equal():
    """update/remove with != filter (pattern: update/remove where key!=value)."""
    d = {
        'a': {'id': 1, 'v': 10},
        'b': {'id': 2, 'v': 20},
    }
    r = dotted.update(d, '*&id!=1', 99)
    assert r == {'a': {'id': 1, 'v': 10}, 'b': 99}
    d2 = {'a': {'id': 1}, 'b': {'id': 2}, 'c': {'id': 3}}
    r = dotted.remove(d2, '*&id!=2')
    assert r == {'b': {'id': 2}}


def test_has_filter_not_equal():
    """has() with != filter."""
    d = {
        'a': {'id': 1, 'name': 'alice'},
        'b': {'id': 2, 'name': 'bob'},
    }
    assert dotted.has(d, '*&id!=2') is True   # a matches
    assert dotted.has(d, '*&id!=99') is True  # both match
    assert dotted.has(d, '*&id!=1') is True   # b matches
    assert dotted.has(d, 'a&id!=1') is False  # a.id is 1
    assert dotted.has(d, 'a&id!=2') is True


def test_expand_pluck_filter_not_equal():
    """expand/pluck with != filter."""
    d = {
        'a': {'id': 1, 'type': 'admin'},
        'b': {'id': 2, 'type': 'user'},
        'c': {'id': 3, 'type': 'admin'},
    }
    r = dotted.expand(d, '*&type!="admin"')
    assert set(r) == {'b'}

    r = dotted.pluck(d, '*&id!=2')
    assert set(k for k, _ in r) == {'a', 'c'}


def test_setdefault_filter_not_equal():
    """setdefault with !=: no change when path matches; returns value at path."""
    d = {'a': {'id': 1}, 'b': {'id': 2}}
    r = dotted.setdefault(d, 'a&id!=2', 'new')
    assert d['a'] == {'id': 1}  # unchanged, path exists
    assert r == {'id': 1}  # returns get (value at path)


def test_get_chained_filter_not_equal():
    """Chained get with != then index/key."""
    d = {'items': [{'id': 1, 'name': 'alice'}, {'id': 2, 'name': 'bob'}]}
    r = dotted.get(d, 'items[id!=2][0].name')
    assert r == 'alice'


def test_dotted_filter_key_not_equal():
    """Dotted path in filter key with != (e.g. user.id!=2)."""
    d = {
        'items': [
            {'user': {'id': 1, 'name': 'alice'}, 'value': 100},
            {'user': {'id': 2, 'name': 'bob'}, 'value': 200},
        ]
    }
    r = dotted.get(d, 'items[user.id!=2]')
    assert r == [{'user': {'id': 1, 'name': 'alice'}, 'value': 100}]
    r = dotted.get(d, 'items[user.id!=1]')
    assert r == [{'user': {'id': 2, 'name': 'bob'}, 'value': 200}]


def test_filter_grouping_not_equal():
    """!= inside grouped expressions."""
    data = [
        {'id': 1, 'active': True},
        {'id': 2, 'active': False},
        {'id': 3, 'active': True},
    ]
    r = dotted.get(data, '[id!=2&active=True]')
    assert len(r) == 2
    assert set(item['id'] for item in r) == {1, 3}
    # AND: id!=1 and id!=3 leaves only id 2
    r = dotted.get(data, '[id!=1&id!=3]')
    assert len(r) == 1
    assert r[0]['id'] == 2


def test_filter_not_equal_first_match():
    """First-match suffix with != (e.g. [id!=1]?)."""
    d = {
        'a': [{'id': 1}, {'id': 2}, {'id': 3}],
    }
    r = dotted.get(d, 'a[id!=1?]')
    # First element matching id!=1 is {'id': 2}
    flat = r[0] if isinstance(r, tuple) else r
    (first,) = flat if isinstance(flat, list) else (flat,)
    assert first['id'] == 2


def test_filter_not_equal_boolean_none():
    """!= with True/False/None values (matches absent key or not equal)."""
    data = [
        {'name': 'alice', 'active': True},
        {'name': 'bob', 'active': False},
        {'name': 'carol', 'score': None},  # no 'active' key -> matches active!=True
    ]
    r = dotted.get(data, '[active!=True]')
    assert len(r) == 2  # bob (False) and carol (missing active)
    assert set(x['name'] for x in r) == {'bob', 'carol'}
    r = dotted.get(data, '[score!=None]')
    assert len(r) == 2  # alice, bob (no score or score not None)
    d = {'a': {'enabled': True}, 'b': {'enabled': None}}
    r = dotted.get(d, '*&enabled!=None')
    assert r == ({'enabled': True},)


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


def test_pluck_slicefilter():
    """Test pluck/expand with SliceFilter (issue #24)"""
    data = [
        {'id': 1, 'name': 'alice'},
        {'id': 2, 'name': 'bob'},
    ]
    # pluck with filter returns index
    r = dotted.pluck(data, '[id=1]')
    assert r == ('[0]', {'id': 1, 'name': 'alice'})

    # expand with filter returns index
    r = dotted.expand(data, '[id=1]')
    assert r == ('[0]',)

    # multiple matches
    r = dotted.expand(data, '[id=1,id=2]')
    assert r == ('[0]', '[1]')


def test_setdefault_filter_keyvalue():
    d = {
        'a': {'id': 1},
        'b': {'id': 2},
    }
    # key with filter exists - no change, returns value at path
    r = dotted.setdefault(d, 'a&id=1', 'new')
    assert d['a'] == {'id': 1}  # unchanged
    assert r == {'id': 1}


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


def test_filter_key_slot_path():
    """Filter keys with slot paths: tags[*]=* means 'any element of tags matches'."""
    profile = {
        'addresses': [
            {
                'tags': ['billing'],
                'country': 'us',
                'line': ['456 Old St'],
                'city': 'Oakland',
                'state': 'CA',
                'zipcode': '94601',
            },
            {
                'tags': ['shipping'],
                'country': 'us',
                'city': 'Portland',
            },
            {
                'country': 'uk',
                'city': 'London',
            },
        ]
    }

    # addresses where tags has any element (tags[*]=*)
    r = dotted.get(profile, 'addresses[*&tags[*]=*]')
    assert len(r) == 2
    assert r[0]['city'] == 'Oakland'
    assert r[1]['city'] == 'Portland'

    # addresses where tags contains "billing"
    r = dotted.get(profile, 'addresses[*&tags[*]="billing"]')
    assert len(r) == 1
    assert r[0]['city'] == 'Oakland'

    # addresses where tags contains "shipping"
    r = dotted.get(profile, 'addresses[*&tags[*]="shipping"]')
    assert len(r) == 1
    assert r[0]['city'] == 'Portland'

    # no addresses with tag "admin"
    r = dotted.get(profile, 'addresses[*&tags[*]="admin"]')
    assert r == ()

    # filter key with slot index: first tag equals "billing"
    r = dotted.get(profile, 'addresses[*&tags[0]="billing"]')
    assert len(r) == 1
    assert r[0]['city'] == 'Oakland'


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


def test_filter_primitives_with_wildcard():
    """
    Test filtering primitive lists with wildcard key.
    The wildcard matches the value itself for primitives.
    """
    # Filter None in primitive list
    data = [None, 1, 2]
    r = dotted.get(data, '[*=None]')
    assert r == [None]

    r = dotted.get(data, '[*=1]')
    assert r == [1]

    r = dotted.get(data, '[*=2]')
    assert r == [2]


def test_filter_primitives_booleans():
    """
    Test filtering primitive lists with boolean values.
    Note: Python equality means 1==True and 0==False.
    """
    data = [True, False, True, None, 1, 0]

    # True matches True and 1
    r = dotted.get(data, '[*=True]')
    assert True in r and 1 in r
    assert len(r) == 3  # True, True, 1

    # False matches False and 0
    r = dotted.get(data, '[*=False]')
    assert False in r and 0 in r
    assert len(r) == 2  # False, 0

    # None only matches None
    r = dotted.get(data, '[*=None]')
    assert r == [None]


def test_filter_primitives_strings():
    """
    Test filtering primitive lists with string values.
    """
    data = ['hello', 'world', 'hello', 'foo']

    r = dotted.get(data, '[*="hello"]')
    assert r == ['hello', 'hello']

    r = dotted.get(data, '[*="world"]')
    assert r == ['world']

    r = dotted.get(data, '[*="bar"]')
    assert r == []


def test_filter_primitives_with_negation():
    """
    Test negation filter on primitive lists.
    """
    data = [True, False, None, 1, 2]

    # NOT True (excludes True and 1 due to Python equality)
    r = dotted.get(data, '[!*=True]')
    assert True not in r
    assert 1 not in r
    assert False in r
    assert None in r
    assert 2 in r

    # NOT None
    r = dotted.get(data, '[!*=None]')
    assert None not in r
    assert len(r) == 4


def test_filter_primitives_mixed_with_dicts():
    """
    Test that primitive filtering doesn't break dict filtering.
    """
    # Dicts still work as before
    data = [{'val': None}, {'val': 1}, {'val': 2}]
    r = dotted.get(data, '[val=None]')
    assert r == [{'val': None}]

    r = dotted.get(data, '[val=1]')
    assert r == [{'val': 1}]

    # Mixed list with primitives and dicts - only dicts match keyed filters
    mixed = [None, {'val': None}, 1, {'val': 1}]
    r = dotted.get(mixed, '[val=None]')
    assert r == [{'val': None}]

    # Wildcard on mixed list - primitives match by value
    r = dotted.get(mixed, '[*=None]')
    # None primitive matches, and {'val': None} has a key with value None
    assert None in r
    assert {'val': None} in r
