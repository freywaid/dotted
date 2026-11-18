import dotted
import pytest


def test_update_list():
    r = dotted.update([], '[0]', 'hello')
    assert r == ['hello']


def test_update_tuple():
    r = dotted.update((), '[0]', 'hello')
    assert r == ('hello',)


@pytest.mark.parametrize('node', ['xyz', [1, 2, 1], (1, 2)])
def test_update_key_on_a_sequence_raises(node):
    """
    A key needs a mapping: a str, list or tuple can't take one, so the
    write raises, as on an int, rather than build something from the
    sequence's items.
    """
    with pytest.raises(TypeError, match=f'Cannot update {type(node).__name__} by key'):
        dotted.update({'a': node}, 'a.b', 'V')


def test_update_key_on_a_sequence_raises_below_it():
    """
    A path through a str raises there too.
    """
    with pytest.raises(TypeError, match='Cannot update str by key'):
        dotted.update({'a': 'xyz'}, 'a.b.c', 'V')


@pytest.mark.parametrize('path, expected', [
    ('a[0]', 'Vyz'),
    ('a[*]', 'VVV'),
    ('a[5]', 'xyzV'),
    ('a[+]', 'xyzV'),
])
def test_update_slot_on_a_str(path, expected):
    """
    A str is a sequence of characters: a slot sets one, and past the end
    appends one, a str still.
    """
    assert dotted.update({'a': 'xyz'}, path, 'V') == {'a': expected}


def test_update_slot_past_the_end_of_a_tuple():
    """
    Past the end of a tuple a slot appends, a tuple still.
    """
    assert dotted.update({'a': (1, 2)}, 'a[5]', 'V') == {'a': (1, 2, 'V')}


@pytest.mark.parametrize('node', [[], [1, 2]])
def test_update_key_wildcard_on_a_list_writes_nothing(node):
    """
    A key wildcard matches nothing in a list, as a read does, so there
    is nothing to write.
    """
    assert dotted.update({'a': node}, 'a.*', 'V') == {'a': node}


def test_update_int_key_on_a_list_indexes():
    """
    An int key indexes a list, as it does reading.
    """
    assert dotted.update({'a': [1, 2]}, 'a.0', 'V') == {'a': ['V', 2]}
