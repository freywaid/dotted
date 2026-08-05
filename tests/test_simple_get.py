"""
Tests for the simple-path fast path in get() (issue #58).

Simple paths — plain chains of literal Key/Attr/Slot accesses — resolve
via engine.simple_get() with direct dict/list/attr lookups, skipping
walk(). These tests pin fast-path/walk parity.
"""
import collections
import types

import dotted
from dotted import engine
from dotted.api import parse


# simple_chain

def test_simple_chain_kinds():
    assert parse('a.b.c').simple_chain == (('key', 'a'), ('key', 'b'), ('key', 'c'))
    assert parse('a[0].b').simple_chain == (('key', 'a'), ('slot', 0), ('key', 'b'))
    assert parse('a@attr').simple_chain == (('key', 'a'), ('attr', 'attr'))


def test_simple_chain_none_for_non_simple():
    assert parse('a.*.b').simple_chain is None
    assert parse('a|int').simple_chain is None
    assert parse('a.$(x)').simple_chain is None
    assert parse('a.$$(b)').simple_chain is None
    assert parse('a[x=1]').simple_chain is None
    assert parse('(a, b)').simple_chain is None
    assert parse('a[+]').simple_chain is None
    assert parse('a=30').simple_chain is None
    assert parse('a[1:]').simple_chain is None
    assert parse('').simple_chain is None


def test_simple_chain_cached_on_instance():
    ops = parse('a.b.c')
    first = ops.simple_chain
    assert ops.__dict__['simple_chain'] is first
    assert ops.simple_chain is first


# get() through the fast path

def test_get_simple_dict_chain():
    d = {'a': {'b': {'c': 7}}}
    assert dotted.get(d, 'a.b.c') == 7
    assert dotted.get(d, 'a.b') == {'c': 7}
    assert dotted.get(d, 'a.b.x') is None
    assert dotted.get(d, 'a.b.x', default='missing') == 'missing'


def test_get_simple_found_none_vs_missing():
    d = {'a': {'b': None}}
    assert dotted.get(d, 'a.b', default='missing') is None
    assert dotted.get(d, 'a.c', default='missing') == 'missing'


def test_get_simple_list_and_tuple():
    d = {'a': [10, 20, 30], 'b': (1, 2)}
    assert dotted.get(d, 'a[1]') == 20
    assert dotted.get(d, 'a[-1]') == 30
    assert dotted.get(d, 'a[5]', default='missing') == 'missing'
    assert dotted.get(d, 'b[0]') == 1
    # Key with numeric segment coerces to list index (non-strict)
    assert dotted.get(d, 'a.1') == 20


def test_get_simple_attr():
    o = types.SimpleNamespace(x=types.SimpleNamespace(y=5))
    assert dotted.get(o, '@x@y') == 5
    assert dotted.get(o, '@x@z', default='missing') == 'missing'
    assert dotted.get({'a': o}, 'a@x@y') == 5


def test_get_simple_slot_string_key_on_dict():
    d = {'a': {'k': 1}}
    assert dotted.get(d, "a['k']") == 1


def test_get_simple_strict():
    d = {'a': [10, 20], 'b': {'k': 1}}
    # strict: numeric Key never coerces to list index
    assert dotted.get(d, 'a.1', strict=True) is None
    assert dotted.get(d, 'a[1]', strict=True) == 20
    # strict: Slot never coerces to dict key
    assert dotted.get(d, "b['k']", strict=True) is None
    assert dotted.get(d, 'b.k', strict=True) == 1


def test_get_simple_defaultdict_no_entry_creation():
    d = collections.defaultdict(dict)
    d['a']['b'] = 1
    assert dotted.get(d, 'a.b') == 1
    assert dotted.get(d, 'x.y', default='missing') == 'missing'
    # the miss must not have created 'x' via defaultdict __getitem__
    assert 'x' not in d


def test_get_simple_dict_subclass_falls_back_to_walk():
    class MyDict(dict):
        pass
    d = MyDict(a=MyDict(b=3))
    assert dotted.get(d, 'a.b') == 3
    assert dotted.get(d, 'a.x', default='missing') == 'missing'


def test_get_simple_none_mid_chain():
    d = {'a': None}
    assert dotted.get(d, 'a.b', default='missing') == 'missing'


def test_get_simple_preparsed():
    ops = parse('a.b')
    assert dotted.get({'a': {'b': 2}}, ops) == 2


# module-level switches

def test_simple_fastpath_toggle():
    d = {'a': {'b': 1}}
    prev = dotted.set_simple_fastpath(False)
    try:
        assert prev is True
        # walk path still answers correctly
        assert dotted.get(d, 'a.b') == 1
        assert dotted.get(d, 'a.x', default='missing') == 'missing'
    finally:
        dotted.set_simple_fastpath(prev)
    assert dotted.set_simple_fastpath(True) is True


def test_parse_cache_resize():
    cached = parse('toggle.test')
    assert parse('toggle.test') is cached
    prev = dotted.set_parse_cache(0)
    try:
        # caching disabled: every parse is fresh
        fresh = parse('toggle.test')
        assert fresh == cached
        assert fresh is not cached
        assert parse('toggle.test') is not fresh
    finally:
        assert dotted.set_parse_cache(prev) == 0
    # cache was discarded on resize, but caching works again
    again = parse('toggle.test')
    assert parse('toggle.test') is again


# engine.simple_get directly

def test_simple_get_bails_on_unknown_container():
    class Custom:
        def __getitem__(self, k):
            return 42
    chain = parse('a.b').simple_chain
    assert engine.simple_get(chain, Custom()) is engine.SIMPLE_BAIL


def test_simple_get_miss_returns_marker():
    from dotted import base
    chain = parse('a.b').simple_chain
    assert engine.simple_get(chain, {'a': {}}) is base.marker
