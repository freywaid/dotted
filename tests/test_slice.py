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


# References and substitutions as slice bounds

def test_slice_reference_stop():
    d = {'b': [3, 0, 5], 'i': 2}
    assert dotted.get(d, 'b[:$$(i)]') == [3, 0]
    assert dotted.get(d, 'b[:$$(i)]|sum') == 3

def test_slice_reference_start():
    d = {'b': [3, 0, 5], 'i': 2}
    assert dotted.get(d, 'b[$$(i):]') == [5]

def test_slice_reference_both():
    d = {'b': [3, 0, 5, 7], 'i': 1, 'j': 3}
    assert dotted.get(d, 'b[$$(i):$$(j)]') == [0, 5]

def test_slice_reference_step():
    d = {'b': [1, 2, 3, 4, 5], 'n': 2}
    assert dotted.get(d, 'b[::$$(n)]') == [1, 3, 5]

def test_slice_reference_empty_prefix():
    """
    b[:0] is empty, so its sum is 0 whether the bound is literal or referenced.
    """
    d = {'b': [3, 0, 5], 'i': 0}
    assert dotted.get(d, 'b[:$$(i)]|sum') == dotted.get(d, 'b[:0]|sum') == 0

def test_slice_reference_relative():
    d = {'x': {'b': [3, 0, 5], 'i': 1}}
    assert dotted.get(d, 'x.b[:$$(^^i)]') == [3]

def test_slice_reference_missing():
    d = {'b': [3, 0, 5]}
    assert dotted.get(d, 'b[:$$(i)]', default='nope') == 'nope'

def test_slice_reference_update():
    d = {'b': [3, 0, 5], 'i': 2}
    assert dotted.update(d, 'b[:$$(i)]', [9]) == {'b': [9, 5], 'i': 2}

def test_slice_reference_remove():
    d = {'b': [3, 0, 5], 'i': 2}
    assert dotted.remove(d, 'b[:$$(i)]') == {'b': [5], 'i': 2}

def test_slice_reference_is_reference():
    assert dotted.is_reference('b[:$$(i)]')
    assert not dotted.is_reference('b[:2]')

def test_slice_reference_repr():
    assert repr(dotted.parse('b[$$(i):$$(j):$$(k)]')[1]) == '[$$(i):$$(j):$$(k)]'

def test_slice_subst_named():
    d = {'b': [3, 0, 5]}
    assert dotted.get(d, 'b[:$(i)]', bindings={'i': 2}) == [3, 0]
    assert dotted.get(d, 'b[$(i):]|sum', bindings={'i': 1}) == 5

def test_slice_subst_positional():
    d = {'b': [3, 0, 5, 7]}
    assert dotted.get(d, 'b[$0:$1]', bindings=[1, 3]) == [0, 5]

def test_slice_subst_step():
    d = {'b': [1, 2, 3, 4, 5]}
    assert dotted.get(d, 'b[::$(n)]', bindings={'n': 2}) == [1, 3, 5]

def test_slice_subst_update():
    d = {'b': [3, 0, 5]}
    assert dotted.update(d, 'b[:$(i)]', [9], bindings={'i': 2}) == {'b': [9, 5]}

def test_slice_subst_replace():
    assert dotted.replace('b[$(i):$(j)]', {'i': 1, 'j': 3}) == 'b[1:3]'
    assert dotted.replace('b[$(i):$(j)]', {'i': 1}, partial=True) == 'b[1:$(j)]'

def test_slice_subst_is_template():
    assert dotted.is_template('b[:$(i)]')
    assert not dotted.is_template('b[:2]')

def test_slice_step_repr():
    assert repr(dotted.parse('b[1:5:2]')[1]) == '[1:5:2]'
