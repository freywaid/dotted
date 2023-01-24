import pytest
import dotted


def test_parse_lookahead_keyvalue():
    dotted.parse('hello.id=1')
    dotted.parse('*.id=1')
    dotted.parse('id=1')
    dotted.parse('a[id=1]')
    dotted.parse('a[*]')
    dotted.parse('a[*.id=1]')


def test_get_filter_keyvalue():
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


    #
#    r = dotted.get(d, 'a[id=1]')
#    import pdb
#    pdb.set_trace()

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

    r = dotted.get(d, '*.hello="there"')
    assert r == ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

    r = dotted.get(d, '*.hello="there".id')
    assert r == (1, 2)

    # no match
    r = dotted.get(d, 'a.id=7')
    assert r is None

    # top match
    r = dotted.get({'a': 1, 'b': 2}, 'a=1')
    assert r == {'a': 1, 'b': 2}

    # top no match
    r = dotted.get({'a': 1, 'b': 2}, 'a=2')
    assert r is None


def test_update_fiter_keyvalue():
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


def _test_remove_lookahead_keyvalue():
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

