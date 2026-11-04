import collections.abc
import dotted


# iget

def test_iget_is_generator():
    assert isinstance(dotted.iget({'a': 1}, 'a'), collections.abc.Iterator)


def test_iget_concrete_yields_value():
    assert list(dotted.iget({'a': {'b': 1}}, 'a.b')) == [1]


def test_iget_concrete_missing_yields_default():
    assert list(dotted.iget({'a': 1}, 'x')) == [None]
    assert list(dotted.iget({'a': 1}, 'x', default=0)) == [0]


def test_iget_pattern_yields_matches():
    assert list(dotted.iget([{'a': 1}, {'a': 2}], '[*].a')) == [1, 2]


def test_iget_pattern_no_match_yields_nothing():
    assert list(dotted.iget({'a': 1}, 'x.*')) == []


def test_iget_transforms():
    assert list(dotted.iget({'n': '4'}, 'n|int')) == [4]
    assert list(dotted.iget({'n': '4'}, 'n|int', apply_transforms=False)) == ['4']


def test_iget_cut():
    assert list(dotted.iget({'a': 1, 'b': 2}, '(a#, b)')) == [1]


def test_iget_matches_get():
    d = {'a': {'b': 1, 'c': 2}, 'x': [1, 2, 3]}
    for path in ('a.*', 'x[*]', '**', 'a.b', 'missing'):
        expect = dotted.get(d, path)
        got = list(dotted.iget(d, path))
        if dotted.is_pattern(path):
            assert tuple(got) == expect
            continue
        assert got == [expect]


def test_iget_transforms_one_at_a_time():
    """
    Each match is transformed as the iteration reaches it, in order, not
    every match an op finds before the first is yielded.
    """
    seen = []
    dotted.register('lazy_probe', lambda v: seen.append(v) or v)
    it = dotted.iget({'a': 1, 'b': 2, 'c': 3}, '*|lazy_probe')
    assert seen == []
    assert next(it) == 1
    assert seen == [1]
    assert next(it) == 2
    assert seen == [1, 2]


def test_iget_transforms_nested_one_at_a_time():
    """
    The same one level down.
    """
    seen = []
    dotted.register('lazy_probe2', lambda v: seen.append(v) or v)
    it = dotted.iget({'a': {'x': 1, 'y': 2}, 'b': {'x': 3}}, '*.*|lazy_probe2')
    assert next(it) == 1
    assert seen == [1]


def test_iget_group_as_lazy_as_its_branch():
    """
    A group yields each match as its branch reaches it: (*)|t transforms
    one match per next(), as *|t does.
    """
    seen = []
    dotted.register('lazy_probe3', lambda v: seen.append(v) or v)
    d = {'a': 1, 'b': 2, 'c': 3}
    for path in ('(*)|lazy_probe3', '(a,b,c)|lazy_probe3', '((*))|lazy_probe3', '(zz#,*)|lazy_probe3'):
        seen.clear()
        it = dotted.iget(d, path)
        assert next(it) == 1
        assert seen == [1]
        assert list(it) == [2, 3]
        assert seen == [1, 2, 3]


class _Unreadable(dict):
    """
    A dict that raises when its items are read, to show a traversal has
    not reached it yet.
    """
    def items(self):
        raise RuntimeError('read too early')

    def keys(self):
        raise RuntimeError('read too early')

    def __iter__(self):
        raise RuntimeError('read too early')


def test_iget_recursive_walks_lazily():
    """
    ** yields its first match before it walks into a later subtree.
    """
    it = dotted.iget({'a': {'c': 1}, 'b': _Unreadable(c=2)}, '**.c')
    assert next(it) == 1


def test_iget_recursive_guard_walks_lazily():
    """
    A guard on ** finds its first match before it walks into a later subtree.
    """
    it = dotted.iget({'a': {'c': 1}, 'b': _Unreadable(c=2)}, '**=1')
    assert next(it) == 1


def test_iget_recursive_transforms_one_at_a_time():
    """
    **|t, and a transform on ** with the path going on from it, run t on
    one match per next().
    """
    seen = []
    dotted.register('lazy_probe5', lambda v: seen.append(v) or v)
    d = {'a': {'c': 1}, 'b': {'c': 2}}
    it = dotted.iget(d, '**.c|lazy_probe5')
    assert next(it) == 1
    assert seen == [1]
    assert list(it) == [2]
    seen.clear()
    it = dotted.iget(d, '(**|lazy_probe5).c')
    assert next(it) == 1
    assert seen == [{'c': 1}]
    assert list(it) == [2]


def test_iget_group_cut_stops():
    """
    A cut after a lazily yielded branch still stops the later branches.
    """
    seen = []
    dotted.register('lazy_probe4', lambda v: seen.append(v) or v)
    assert list(dotted.iget({'a': 1, 'b': 2}, '(a#,b)|lazy_probe4')) == [1]
    assert seen == [1]


def test_iget_nested_cut_inside_lazy_group():
    """
    A cut inside a group nested in another group leaves the outer group's
    later branches to run.
    """
    d = {'a': {'x': 1, 'y': 2}, 'b': {'x': 3}, 'n': 5}
    assert list(dotted.iget(d, '((a.x#,a.y),b.x)')) == [1, 3]
    assert list(dotted.iget(d, '((a#,b),n)')) == [{'x': 1, 'y': 2}, 5]


# iexpand / expand duplicates

def test_iexpand_is_generator():
    assert isinstance(dotted.iexpand({'a': 1}, '*'), collections.abc.Iterator)


def test_iexpand_matches_expand():
    d = {'a': {'b': 1, 'c': 2}}
    assert tuple(dotted.iexpand(d, 'a.*')) == dotted.expand(d, 'a.*')


def test_expand_dedups_by_default():
    assert dotted.expand({'a': 1, 'b': 2}, '(a,*)') == ('a', 'b')


def test_expand_duplicates():
    assert dotted.expand({'a': 1, 'b': 2}, '(a,*)', duplicates=True) == ('a', 'a', 'b')
    assert list(dotted.iexpand({'a': 1, 'b': 2}, '(a,*)', duplicates=True)) == ['a', 'a', 'b']


def test_expand_multi_duplicates():
    d = {'a': 1, 'b': 2}
    assert list(dotted.expand_multi(d, ('a', '*'))) == ['a', 'b']
    assert list(dotted.expand_multi(d, ('a', '*'), duplicates=True)) == ['a', 'a', 'b']


# ipluck / pluck duplicates

def test_ipluck_concrete_yields_pair():
    assert list(dotted.ipluck({'a': {'b': 1}}, 'a.b')) == [('a.b', 1)]


def test_ipluck_concrete_missing_yields_nothing():
    assert list(dotted.ipluck({'a': 1}, 'x')) == []


def test_ipluck_pattern_yields_pairs():
    assert list(dotted.ipluck({'a': {'b': 1, 'c': 2}}, 'a.*')) == [('a.b', 1), ('a.c', 2)]


def test_pluck_dedups_by_default():
    assert dotted.pluck({'a': 1, 'b': 2}, '(a,*)') == (('a', 1), ('b', 2))


def test_pluck_duplicates():
    d = {'a': 1, 'b': 2}
    assert dotted.pluck(d, '(a,*)', duplicates=True) == (('a', 1), ('a', 1), ('b', 2))
    assert list(dotted.ipluck(d, '(a,*)', duplicates=True)) == [('a', 1), ('a', 1), ('b', 2)]


def test_pluck_multi_duplicates():
    d = {'a': 1}
    assert list(dotted.pluck_multi(d, ('a', 'a'))) == [('a', 1)]
    assert list(dotted.pluck_multi(d, ('a', 'a'), duplicates=True)) == [('a', 1), ('a', 1)]


# walk is ipluck

def test_walk_is_ipluck():
    assert dotted.walk is dotted.ipluck
    assert dotted.walk_multi is dotted.pluck_multi


def test_walk_dedups_by_default():
    assert list(dotted.walk({'a': 1, 'b': 2}, '(a,*)')) == [('a', 1), ('b', 2)]
