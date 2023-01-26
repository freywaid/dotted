import pytest
import dotted


def _test_parse_lookahead_keyvalue():
    dotted.parse('hello.id=1')
    dotted.parse('*.id=1')
    dotted.parse('id=1')
    dotted.parse('a[id=1]')
    dotted.parse('a[*]')
    dotted.parse('a[*.id=1]')


def test_match_filter_keyvalue():
    r = dotted.match('a.id=1', 'a.id=1')
    assert r == 'a.id=1'

    r = dotted.match('*.id=1', 'a.id=1')
    assert r == 'a.id=1'

    r = dotted.match('[*]', '[*.id=1]')
    assert r == '[*.id=1]'

    r = dotted.match('[id=*]', '[id=1]')
    assert r == '[id=1]'

    r = dotted.match('*.id=*', 'a.id=1,other=*')
    assert r == 'a.id=1,other=*'

    r = dotted.match('[*.id=*]', '[id=1]')
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
    r = dotted.get(d, 'a.id=1')
    assert r == {'id': 1, 'hello': 'there'}

    # as pattern
    r = dotted.get(d, '*.id=1')
    assert r == ({'id': 1, 'hello': 'there'},)

    r = dotted.get(d, '*.id=*')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

    r = dotted.get(d, '*.hello="there"')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

    r = dotted.get(d, '*.hello="there".id')
    assert r == (1, 2)

    # no match
    r = dotted.get(d, 'a.id=7')
    assert r is None

    # fails to parse
    with pytest.raises(dotted.api.ParseError):
        dotted.get({'a': 1, 'b': 2}, 'a=1')


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
    r = dotted.update(d, 'a.id=1', 6)
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
    r = dotted.remove(d, 'a.id=2')
    assert r == {'a': {'id': 1, 'hello': 'there'}, 'b': {'id': 2, 'hello': 'there'}}

    # match with pattern
    r = dotted.remove(d, '*.id=1')
    {'b': {'id': 2, 'hello': 'there'}}


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
