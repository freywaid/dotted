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


# Immutable sequence (tuple) operations

def test_tuple_update_empty():
    assert dotted.update((), '[]', (1, 2, 3)) == (1, 2, 3)

def test_tuple_update_index():
    assert dotted.update((1, 2), '[0]', 9) == (9, 2)

def test_tuple_update_slice():
    assert dotted.update((1, 2, 3), '[1:]', (8, 9)) == (1, 8, 9)

def test_tuple_update_whole():
    assert dotted.update((1, 2), '[]', (3, 4, 5)) == (3, 4, 5)

def test_tuple_remove_index():
    assert dotted.remove((1, 2, 3), '[0]') == (2, 3)

def test_tuple_remove_negative_index():
    assert dotted.remove((1, 2, 3), '[-1]') == (1, 2)

def test_tuple_remove_whole():
    assert dotted.remove((1, 2, 3), '[]') == ()

def test_tuple_remove_slice():
    assert dotted.remove((1, 2, 3), '[1:]') == (1,)

def test_tuple_remove_by_value():
    assert dotted.remove((1, 2, 3), '[*]', 2) == (1, 3)
