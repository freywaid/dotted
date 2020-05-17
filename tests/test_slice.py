import dotted


def test_slice_upsert():
    m = dotted.update([], '[:]', ['hello'])
    assert m == ['hello']


def test_slice_append():
    m = dotted.update(['hello', 'there'], '[+:]', ['bye'])
    assert m == ['hello', 'there', 'bye']


def test_slice_prepend():
    m = dotted.update(['hello', 'there'], '[:0]', ['bye'])
    assert m == ['bye', 'hello', 'there']


def test_slice_default():
    m = dotted.update({}, 'stuff[]', ['bye'])
    assert m == {'stuff': ['bye']}


def test_slice_match():
    m = dotted.match('hello[]', 'hello[]')
    assert m == 'hello[]'

    m = dotted.match('hello[:2]', 'hello[]')
    assert m is None

    m = dotted.match('hello[]', 'hello[:2]')
    assert m == 'hello[:2]'
