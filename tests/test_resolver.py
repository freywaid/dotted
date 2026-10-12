"""
Tests for ReferenceResolver: a host's reading of references and variables
at run time, per call with resolver= or process-wide with set_resolver().
"""
import pytest

import dotted
from dotted.api import parse


class Store(dotted.ReferenceResolver):
    """
    $$(path) reads a separate store, not the root.
    """

    def __init__(self, store):
        self.store = store

    def resolve(self, ref, settings, node=None, parents=()):
        if not ref.is_reference():
            return super().resolve(ref, settings, node, parents)
        val = dotted.get(self.store, ref.inner_path, default=MISSING)
        if val is MISSING:
            raise dotted.UnresolvedReference(ref.inner_path)
        return val


MISSING = object()


class Sigils(Store):
    """
    $name tokens are host variables mapped to keys; everything else literal.
    """
    literals = True

    def __init__(self, names, store):
        super().__init__(store)
        self.names = names

    def resolve(self, ref, settings, node=None, parents=()):
        if ref.is_reference():
            return super().resolve(ref, settings, node, parents)
        if isinstance(ref.value, str) and ref.value.startswith('$'):
            return self.names[ref.value]
        return ref.value


class Folding(Sigils):
    """
    $name.rest is one variable when the namespace knows the joined name.
    """

    def fold(self, segments):
        runs = []
        i = 0
        while i < len(segments):
            seg = segments[i]
            if not (isinstance(seg, str) and seg.startswith('$')):
                i += 1
                continue
            for stop in range(len(segments), i, -1):
                name = '.'.join(str(s) for s in segments[i:stop])
                if name in self.names:
                    runs.append((i, stop, self.names[name]))
                    i = stop
                    break
            else:
                i += 1
        return runs


ROOT = {'amt': 10, 'amt_obj': {'value': 10, 'cur': 'USD'}, 'a': {'k': {'b': 5}},
        'users': [{'name': 'alice'}, {'name': 'bob'}], 'n': None}
STORE = {'lots': {'taken': 3}}
NAMES = {'$amount.value': 'amt', '$amount': 'amt_obj', '$x.y': 'k', '$sel': 'bob', '$taken': 3}


@pytest.fixture(autouse=True)
def default_resolver():
    """
    Every test starts and ends with dotted's own resolver in force.
    """
    dotted.set_resolver()
    yield
    dotted.set_resolver()


# ---- resolve ----

def test_default_resolver_is_dotted_own():
    """
    Without a resolver, $$(path) reads the root as before.
    """
    assert isinstance(dotted.get_resolver(), dotted.ReferenceResolver)
    assert dotted.get({'config': {'offset': 10}, 'n': 5}, 'n|add:$$(config.offset)') == 15


def test_resolver_reads_references_elsewhere():
    """
    A resolver decides what $$(path) means, in arguments and guards alike.
    """
    r = Store(STORE)
    assert dotted.get(ROOT, 'amt|sub:$$(lots.taken)', resolver=r) == 7
    assert dotted.get(ROOT, 'amt=$$(lots.taken)', resolver=r) is None
    assert dotted.get({'amt': 3}, 'amt=$$(lots.taken)', resolver=r) == 3


def test_resolver_falls_back_to_super():
    """
    super().resolve is dotted's reading; an unresolvable reference matches nothing.
    """
    r = Store(STORE)
    assert dotted.get({'k': 'x', 'x': 1}, '$$(k)', resolver=r) is None  # k is not in the store
    assert dotted.get(ROOT, 'amt|add:$$(nope)', resolver=r, default='dropped') == 'dropped'


def test_literals_are_offered_only_when_asked():
    """
    A plain token reaches resolve() only for a resolver with literals = True.
    """
    r = Sigils(NAMES, STORE)
    assert dotted.get(ROOT, '$sel', default='no key', resolver=Store(STORE)) == 'no key'
    assert dotted.get(ROOT, 'users[*&name=$sel].name', resolver=r) == ('bob',)
    assert dotted.get(ROOT, 'users[*].name=$sel', resolver=r) == ('bob',)
    assert dotted.get(ROOT, 'n|coalesce:$taken', resolver=r) == 3
    assert dotted.get(ROOT, 'amt', resolver=r) == 10


def test_literal_key_resolved_once():
    """
    A key the resolver answers is not offered to it again.
    """
    r = Sigils({'$amount': 'amt'}, STORE)
    assert dotted.get(ROOT, '$amount', resolver=r) == 10
    assert dotted.update(dict(ROOT), '$amount', 0, resolver=r)['amt'] == 0
    assert 'amt' not in dotted.remove(dict(ROOT), '$amount', resolver=r)


def test_unknown_variable_matches_nothing():
    """
    A KeyError from the resolver reads as nothing there.
    """
    r = Sigils(NAMES, STORE)
    assert dotted.get(ROOT, '$nope', default='missing', resolver=r) == 'missing'


# ---- fold ----

def test_fold_head_run():
    """
    A run of segments at the head folds into one key; its decorations stay.
    """
    r = Folding(NAMES, STORE)
    assert dotted.get(ROOT, '$amount.value|sub:$$(lots.taken)', resolver=r) == 7
    assert dotted.get(ROOT, '$amount.value|str', resolver=r) == '10'
    assert dotted.get(ROOT, '$amount.value=10', resolver=r) == 10
    assert dotted.get(ROOT, '$amount.cur', resolver=r) == 'USD'


def test_fold_mid_path_run():
    """
    A run in the middle of a path folds the same way.
    """
    r = Folding(NAMES, STORE)
    assert dotted.get(ROOT, 'a.$x.y.b', resolver=r) == 5
    assert dotted.get(ROOT, 'a.$x.y.b=5', resolver=r) == 5
    assert dotted.pluck(ROOT, 'a.$x.y.b', resolver=r) == ('a.k.b', 5)


def test_fold_writes_and_walks():
    """
    update, remove and expand run on the folded path.
    """
    r = Folding(NAMES, STORE)
    assert dotted.update(dict(ROOT), '$amount.value', 0, resolver=r)['amt'] == 0
    assert 'amt' not in dotted.remove(dict(ROOT), '$amount.value', resolver=r)
    assert [str(p) for p in dotted.expand(ROOT, '$amount.*', resolver=r)] == ['amt_obj.value', 'amt_obj.cur']


def test_fold_inner_decoration_is_an_error():
    """
    Only the last segment of a run may carry a decoration.
    """
    r = Folding(NAMES, STORE)
    with pytest.raises(ValueError):
        dotted.get(ROOT, '~$amount.value', resolver=r)


def test_segments_and_slices():
    """
    segments() gives each segment's plain value, wrappers aside, and a
    slice of a parsed path is a path.
    """
    assert parse('a.$x.y|int=5').segments() == ('a', '$x', 'y')
    assert parse('a[0]@n.*.(x,y).**').segments() == ('a', 0, 'n', '*', '(x,y)', '**')
    assert parse('a.b.c')[1:].assemble() == 'b.c'


# ---- process default ----

def test_process_default_resolver():
    """
    set_resolver() installs a resolver for calls without one and resets with no argument.
    """
    r = Folding(NAMES, STORE)
    dotted.set_resolver(r)
    assert dotted.get_resolver() is r
    assert dotted.get(ROOT, '$amount.value') == 10
    assert dotted.get(ROOT, '$amount.value', resolver=Store(STORE), default='per call wins') == 'per call wins'
    dotted.set_resolver()
    assert dotted.get(ROOT, '$amount.value', default='reset') == 'reset'
