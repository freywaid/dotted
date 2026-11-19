"""
Streaming reads: iget, ipluck, iexpand and their _multi forms read through
one-shot iterators; the item-style reads and the writes never do.
"""
import collections.abc
import itertools

import pytest

import dotted
from dotted import utils


ROWS = (
    {'x': 1, 'y': 'a', 'a': 1, 'b': 2},
    {'x': 2, 'y': 'b', 'a': 3, 'b': 4},
    {'x': 1, 'y': 'c', 'a': 5, 'b': 6},
    {'x': 3, 'y': 'd', 'a': 7, 'b': 8},
)


def streamed():
    """
    A document whose rows are a one-shot iterator.
    """
    return {'name': 'orders', 'rows': iter(ROWS)}


def listed():
    """
    The same document with its rows in a list.
    """
    return {'name': 'orders', 'rows': list(ROWS)}


def counting(pulled):
    """
    An endless stream of rows that records each one drawn in pulled.
    """
    for i in itertools.count():
        pulled.append(i)
        yield {'x': i % 2, 'y': i}


# what a stream and a list of the same items both give

SAME = (
    'rows[*].y',
    'rows[*&x=1].y',
    'rows[*&x=3,x=1].y',
    'rows[*&x=1?].y',
    'rows[*&!x=1].y',
    'rows[*?].y',
    'rows[*]:dict.y',
    'rows[*](.a, .b)',
    'rows[*].*',
    'rows[:2][*].y',
    'rows[1:4:2][*].y',
    'rows[x=1][*].y',
    'name',
)


@pytest.mark.parametrize('path', SAME)
def test_iget_stream_matches_list(path):
    assert list(dotted.iget(streamed(), path)) == list(dotted.iget(listed(), path))


@pytest.mark.parametrize('path', SAME)
def test_ipluck_stream_matches_list(path):
    assert list(dotted.ipluck(streamed(), path)) == list(dotted.ipluck(listed(), path))


@pytest.mark.parametrize('path', SAME)
def test_iexpand_stream_matches_list(path):
    assert list(dotted.iexpand(streamed(), path)) == list(dotted.iexpand(listed(), path))


@pytest.mark.parametrize('path', SAME)
def test_streaming_read_of_list_matches_item_style(path):
    d = listed()
    found = dotted.get(d, path)
    assert list(dotted.iget(d, path)) == (list(found) if dotted.is_pattern(path) else [found])


# iget

def test_iget_reads_a_generator():
    assert list(dotted.iget(streamed(), 'rows[*].y')) == ['a', 'b', 'c', 'd']


def test_iget_root_iterator():
    assert list(dotted.iget(iter(ROWS), '[*].y')) == ['a', 'b', 'c', 'd']


def test_iget_any_iterator():
    assert list(dotted.iget({'rows': map(dict, ROWS)}, 'rows[*].y')) == ['a', 'b', 'c', 'd']


def test_iget_nested_iterators():
    d = iter([{'t': iter([1, 2])}, {'t': iter([3])}])
    assert list(dotted.iget(d, '[*].t[*]')) == [1, 2, 3]


def test_iget_filter():
    assert list(dotted.iget(streamed(), 'rows[*&x=1].y')) == ['a', 'c']


def test_iget_or_filter_in_stream_order():
    assert list(dotted.iget(streamed(), 'rows[*&x=3,x=1].y')) == ['a', 'c', 'd']


def test_iget_value_guard():
    assert list(dotted.iget({'rows': iter([1, 2, 1])}, 'rows[*]=1')) == [1, 1]


def test_iget_transform():
    assert list(dotted.iget({'rows': iter(['1', '2'])}, 'rows[*]|int')) == [1, 2]


def test_iget_aligned_default():
    assert list(dotted.iget({'rows': iter(ROWS[:2])}, 'rows[*+].z', default='-')) == ['-', '-']


def test_iget_is_lazy():
    pulled = []
    g = dotted.iget({'rows': counting(pulled)}, 'rows[*&x=1].y')
    assert pulled == []
    assert next(g) == 1
    assert pulled == [0, 1]
    assert next(g) == 3
    assert pulled == [0, 1, 2, 3]


def test_iget_first_match_filter_stops_drawing():
    pulled = []
    assert list(dotted.iget({'rows': counting(pulled)}, 'rows[*&x=1?].y')) == [1]
    assert pulled == [0, 1]


def test_iget_first_match_wildcard_stops_drawing():
    pulled = []
    assert list(dotted.iget({'rows': counting(pulled)}, 'rows[*?].y')) == [0]
    assert pulled == [0]


# what an iterator cannot answer

@pytest.mark.parametrize('path', ('rows[0].y', 'rows[-1].y', 'rows[-2:]', 'rows[::-1]'))
def test_iget_unanswerable_yields_default_and_draws_nothing(path):
    pulled = []
    assert list(dotted.iget({'rows': counting(pulled)}, path, default='none')) == ['none']
    assert pulled == []


@pytest.mark.parametrize('path', ('rows[-2:][*].y', 'rows[::-1][*].y'))
def test_iget_unanswerable_slice_matches_nothing(path):
    assert list(dotted.iget(streamed(), path)) == []


# slices and slice filters are values

def test_iget_slice_is_an_islice():
    found = list(dotted.iget({'rows': iter([1, 2, 3])}, 'rows[:2]'))
    assert len(found) == 1
    assert isinstance(found[0], itertools.islice)
    assert list(found[0]) == [1, 2]


def test_iget_slice_draws_no_further_than_its_bound():
    pulled = []
    assert list(dotted.iget({'rows': counting(pulled)}, 'rows[:3][*].y')) == [0, 1, 2]
    assert pulled == [0, 1, 2]


def test_iget_slice_step():
    assert list(dotted.iget(streamed(), 'rows[1:4:2][*].y')) == ['b', 'd']


def test_iget_slice_filter_is_a_generator():
    found = list(dotted.iget(streamed(), 'rows[x=1]'))
    assert len(found) == 1
    assert isinstance(found[0], collections.abc.Iterator)
    assert [row['y'] for row in found[0]] == ['a', 'c']


def test_iget_slice_filter_or_in_stream_order():
    assert list(dotted.iget(streamed(), 'rows[x=3,x=1][*].y')) == ['a', 'c', 'd']
    assert list(dotted.iget(listed(), 'rows[x=3,x=1][*].y')) == ['d', 'a', 'c']


def test_iget_slice_of_list_is_a_list():
    assert list(dotted.iget(listed(), 'rows[:1]')) == [[ROWS[0]]]


# one-shot

def test_iget_group_below_the_stream():
    assert list(dotted.iget(streamed(), 'rows[*](.a, .b)')) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_iget_group_across_the_stream_finds_it_empty():
    assert list(dotted.iget(streamed(), '(rows[*].a, rows[*].b)')) == [1, 3, 5, 7]


# ipluck / iexpand

def test_ipluck_numbers_by_position():
    assert list(dotted.ipluck(streamed(), 'rows[*&x=1].y')) == [('rows[0].y', 'a'), ('rows[2].y', 'c')]


def test_ipluck_slice_numbers_within_the_slice():
    assert list(dotted.ipluck(streamed(), 'rows[1:3][*].y')) == [('rows[1:3][0].y', 'b'), ('rows[1:3][1].y', 'c')]


def test_ipluck_is_lazy():
    pulled = []
    g = dotted.ipluck({'rows': counting(pulled)}, 'rows[*].y')
    assert next(g) == ('rows[0].y', 0)
    assert pulled == [0]


def test_ipluck_packs():
    pairs = dotted.ipluck(streamed(), 'rows[*](.x, .y)')
    assert dotted.pack(pairs) == {'rows': [{'x': r['x'], 'y': r['y']} for r in ROWS]}


def test_iexpand_numbers_by_position():
    assert list(dotted.iexpand(streamed(), 'rows[*].y')) == ['rows[0].y', 'rows[1].y', 'rows[2].y', 'rows[3].y']


# _multi

def test_iget_multi_is_flat():
    assert list(dotted.iget_multi(streamed(), ['name', 'rows[*].y', 'nope'])) == ['orders', 'a', 'b', 'c', 'd']


def test_ipluck_multi():
    found = list(dotted.ipluck_multi(streamed(), ['name', 'rows[*&x=2].y']))
    assert found == [('name', 'orders'), ('rows[1].y', 'b')]


def test_iexpand_multi():
    assert list(dotted.iexpand_multi(streamed(), ['name', 'rows[*&x=2].y'])) == ['name', 'rows[1].y']


def test_multi_patterns_share_one_stream():
    assert list(dotted.iget_multi(streamed(), ['rows[*].a', 'rows[*].b'])) == [1, 3, 5, 7]


# item-style reads and writes never draw from an iterator

def test_get_does_not_read_an_iterator():
    d = streamed()
    assert dotted.get(d, 'rows[*].y') == ()
    assert dotted.get(d, 'rows') is d['rows']
    assert len(list(d['rows'])) == 4


@pytest.mark.parametrize('read', (dotted.pluck, dotted.expand))
def test_item_style_patterns_do_not_read_an_iterator(read):
    d = streamed()
    assert read(d, 'rows[*].y') == ()
    assert len(list(d['rows'])) == 4


def test_item_style_multi_does_not_read_an_iterator():
    d = streamed()
    assert list(dotted.pluck_multi(d, ['rows[*].y'])) == []
    assert list(dotted.expand_multi(d, ['rows[*].y'])) == []
    assert list(dotted.get_multi(d, ['rows[*].y'])) == []
    assert len(list(d['rows'])) == 4


def test_has_does_not_read_an_iterator():
    d = streamed()
    assert dotted.has(d, 'rows')
    assert not dotted.has(d, 'rows[*].y')
    assert len(list(d['rows'])) == 4


def test_update_does_not_read_an_iterator():
    d = streamed()
    rows = d['rows']
    assert dotted.update(d, 'rows[*].y', 0)['rows'] is rows
    assert len(list(rows)) == 4


def test_unpack_leaves_an_iterator_a_leaf():
    d = streamed()
    assert dotted.unpack(d) == {'name': 'orders', 'rows': d['rows']}
    assert len(list(d['rows'])) == 4


# what is read how

class ItemsMapping:
    """
    A dict-like with a lazy items(); reading it any other way fails.
    """
    def __init__(self, data):
        self.data = data

    def keys(self):
        return self.data.keys()

    def items(self):
        return iter(self.data.items())

    def __getitem__(self, key):
        raise AssertionError('read by __getitem__')

    def __iter__(self):
        raise AssertionError('read by __iter__')


class KeysMapping:
    """
    A dict-like without items(): keys by __iter__, values by __getitem__.
    """
    def __init__(self, data):
        self.data = data

    def keys(self):
        return self.data.keys()

    def __getitem__(self, key):
        return self.data[key]

    def __iter__(self):
        return iter(self.data)


class Cursor:
    """
    A sequence that can be both indexed and iterated; indexing fails.
    """
    def __init__(self, data):
        self.data = data

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        raise AssertionError('read by __getitem__')

    def __iter__(self):
        return iter(self.data)


class IndexOnly:
    """
    A sequence with __getitem__ and no __iter__ of its own.
    """
    def __init__(self, data):
        self.data = data

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return self.data[index]


@pytest.mark.parametrize('path', ('m[*]', 'm.*'))
def test_mapping_is_read_through_items(path):
    assert list(dotted.iget({'m': ItemsMapping({'a': 1, 'b': 2})}, path)) == [1, 2]


@pytest.mark.parametrize('path', ('m[*]', 'm.*'))
def test_mapping_without_items_is_read_by_key(path):
    assert list(dotted.iget({'m': KeysMapping({'a': 1, 'b': 2})}, path)) == [1, 2]


def test_mapping_pattern_keys():
    m = ItemsMapping({'a': 1, 'b': 2, 'c': 3})
    assert list(dotted.ipluck({'m': m}, 'm./a|c/')) == [('m.a', 1), ('m.c', 3)]
    assert list(dotted.iget({'m': m}, 'm.*?')) == [1]


def test_iteration_wins_over_getitem():
    assert list(dotted.iget({'c': Cursor([1, 2])}, 'c[*]')) == [1, 2]


def test_getitem_only_is_read_by_index():
    assert list(dotted.iget({'c': IndexOnly([1, 2])}, 'c[*]')) == [1, 2]


def test_set_is_not_iterated():
    assert list(dotted.iget({'s': {1, 2}}, 's[*]')) == []


def test_str_is_read_by_index():
    assert list(dotted.iget({'s': 'abc'}, 's[*]')) == ['a', 'b', 'c']


# iter type restriction

def test_iter_type_restriction():
    stream = iter([1])
    d = {'a': stream, 'b': [1]}
    assert dotted.get(d, '*:iter') == (stream,)
    assert dotted.get(d, '*:!iter') == ([1],)
    assert dotted.get(d, '*:!(list, iter)') == ()


def test_iter_type_restriction_renders():
    assert repr(dotted.parse('*:!(str, iter)').ops[0]) == '*:!(str, iter)'


# utils

def test_iterator_is_not_set_like():
    assert not utils.is_set_like(iter([]))
    assert not utils.is_set_like(x for x in ())
    assert utils.is_set_like({1})
    assert utils.is_set_like(frozenset())


def test_islice_fails_safely():
    stream = iter([1, 2, 3])
    assert utils.islice(stream, -1, None, None) is None
    assert utils.islice(stream, None, None, -1) is None
    assert list(stream) == [1, 2, 3]
    assert list(utils.islice(iter([1, 2, 3]), 1, None, None)) == [2, 3]


def test_mapping_entries():
    assert list(utils.mapping_entries({'a': 1})) == [('a', 1)]
    assert list(utils.mapping_entries(KeysMapping({'a': 1}))) == [('a', 1)]
