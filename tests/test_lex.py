"""
Tests for ReferenceResolver.lex: a host's reading, at parse time, of the
tokens dotted's grammar cannot read, in a slot, a slice bound, a filter
value or a guard value.
"""
import pytest

import dotted
from dotted.api import parse, ParseError


class Names(dotted.ReferenceResolver):
    """
    A bare name, dotted or not, reads the root: lex answers $$(name).
    nope has no reading, bad fails, and loop answers a token dotted
    cannot read either.
    """

    def __init__(self):
        super().__init__()
        self.offered = []

    def lex(self, text, position):
        self.offered.append((text, position))
        if text == 'nope':
            return None
        if text == 'bad':
            raise ValueError('bad name')
        if text == 'loop':
            return 'other'
        return f'$$({text})'


ROOT = {
    'merged': {'x': 1, 'y': 2},
    'k': 'x',
    'c': {'account': 'y', 'cur': 'EUR'},
    'items': [10, 20, 30, 40],
    'n': 2,
    'lots': [{'cur': 'USD', 'v': 1}, {'cur': 'EUR', 'v': 2}],
    'want': 'EUR',
}


def test_lex_slot():
    """
    A bare name in a slot is lexed, a dotted one whole.
    """
    r = Names()
    assert dotted.get(ROOT, 'merged[k]', resolver=r) == 1
    assert dotted.get(ROOT, 'merged[c.account]', resolver=r) == 2
    assert r.offered == [('k', 'slots'), ('c.account', 'slots')]


def test_lex_slice_bounds():
    """
    A bare name is lexed as either bound of a slice.
    """
    r = Names()
    assert dotted.get(ROOT, 'items[:n]', resolver=r) == [10, 20]
    assert dotted.get(ROOT, 'items[n:]', resolver=r) == [30, 40]
    assert ('n', 'slices') in r.offered
    assert ('n', 'slots') not in r.offered


def test_lex_filter_value():
    """
    A bare name in a filter's value is lexed, a dot not ending it.
    """
    r = Names()
    assert dotted.get(ROOT, 'lots[*&cur=want].v', resolver=r) == (2,)
    assert dotted.get(ROOT, 'lots[*&cur=c.cur].v', resolver=r) == (2,)
    assert r.offered == [('want', 'filters'), ('c.cur', 'filters')]


def test_lex_guard_value():
    """
    A bare name in a guard's value is lexed.
    """
    r = Names()
    assert dotted.get(ROOT, 'want=c.cur', resolver=r) == 'EUR'
    assert dotted.get(ROOT, 'want=k', resolver=r) is None
    assert r.offered == [('c.cur', 'guards'), ('k', 'guards')]


def test_lex_transform_argument():
    """
    A bare transform argument is lexed, on the fast path as off it.
    """
    r = Names()
    assert dotted.get(ROOT, 'n|add:n', resolver=r) == 4
    assert dotted.get(ROOT, 'items[0]|add:lots[1].v', resolver=r) == 12
    dotted.set_simple_fastpath(False)
    try:
        assert dotted.get(ROOT, 'n|add:n', resolver=r) == 4
    finally:
        dotted.set_simple_fastpath(True)
    assert r.offered == [('n', 'transforms'), ('lots[1].v', 'transforms')]


def test_lex_transform_argument_none_stays_string():
    """
    A transform argument lex has no reading for stays a plain string.
    """
    assert parse('a|str:nope', resolver=Names()).assemble() == parse('a|str:nope').assemble()


def test_lex_writes():
    """
    update runs on the lexed path.
    """
    r = Names()
    out = dotted.update(ROOT, 'merged[k]', 9, mutable=False, resolver=r)
    assert out['merged'] == {'x': 9, 'y': 2}


def test_lex_offers_only_unread_tokens():
    """
    A token dotted reads is never offered, a filter's key path included.
    """
    r = Names()
    for path in ('a.b', 'merged["k"]', 'merged[0]', 'items[:2]', 'items[1:-1]', 'm[$x]',
                 'a.$x.b', 'lots[*&cur="EUR"]', 'lots[cur=1]', 'merged[a.b=1]', 'a=5',
                 'a|sub:$taken', 'a|sub:3', 'a|sub:"taken"', 'a=null'):
        parse(path, resolver=r)
    assert r.offered == []
    assert parse('merged[a.b=1]', resolver=r).assemble() == parse('merged[a.b=1]').assemble()


def test_lex_reads_no_prefix():
    """
    What dotted reads as the start of a token, null in nullable or 3 in
    3x, leaves the whole token to the lexer.
    """
    r = Names()
    parse('a=nullable', resolver=r)
    parse('items[:3x]', resolver=r)
    assert r.offered == [('nullable', 'guards'), ('3x', 'slices')]


def test_lex_none_leaves_parse_error():
    """
    A token lex has no reading for fails to parse, as it does unlexed.
    """
    r = Names()
    with pytest.raises(ParseError):
        parse('merged[nope]', resolver=r)


def test_lex_raise_fails_parse():
    """
    lex raising fails the parse at the token.
    """
    with pytest.raises(ParseError, match='bad name'):
        parse('merged[bad]', resolver=Names())


def test_lex_answer_read_once():
    """
    lex's answer must be one token dotted reads there; it is not offered
    to lex again.
    """
    r = Names()
    with pytest.raises(ParseError, match='lexed as'):
        parse('merged[loop]', resolver=r)
    assert r.offered == [('loop', 'slots')]


def test_lex_cached_per_resolver():
    """
    A parse is cached per lexing resolver; one without lex reads as before.
    """
    r = Names()
    parse('merged[k]', resolver=r)
    parse('merged[k]', resolver=r)
    assert r.offered == [('k', 'slots')]
    other = Names()
    parse('merged[k]', resolver=other)
    assert other.offered == [('k', 'slots')]
    with pytest.raises(ParseError):
        parse('merged[k]')


def test_lex_process_default():
    """
    set_resolver() lexes for calls without a resolver of their own.
    """
    dotted.set_resolver(Names())
    try:
        assert dotted.get(ROOT, 'merged[k]') == 1
        with pytest.raises(ParseError):
            parse('merged[k]', resolver=dotted.ReferenceResolver())
    finally:
        dotted.set_resolver()
