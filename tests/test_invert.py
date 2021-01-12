"""
"""
import dotted


def test_invert_get():
    r = dotted.get({'hello': 'there'}, '-hello')
    assert r == 'there'

    r = dotted.get([], '-[0]')
    assert r is None


def test_invert_remove_via_update():
    r = dotted.update({'hello': 'there'}, '-hello', dotted.ANY)
    assert not r


def test_invert_update_via_remove():
    r = dotted.remove({}, '-hello', 'there')
    assert r == {'hello': 'there'}


def test_invert_match_const():
    r = dotted.match('-hello', '-hello')
    assert r == '-hello'

    r = dotted.match('-hello', 'hello')
    assert not r

    r = dotted.match('hello', '-hello')
    assert not r


def test_invert_match_pattern():
    r = dotted.match('-*', '-hello')
    assert r == '-hello'

    r = dotted.match('*', '-hello')
    assert not r


def test_invert_expand():
    r = dotted.expand({'hello': {'there': 'foo'}}, '-*.*')
    assert r == ('-hello.there',)


def test_invert_assemble():
    r = dotted.assemble(('-hello', 'there'))
    assert r == '-hello.there'
