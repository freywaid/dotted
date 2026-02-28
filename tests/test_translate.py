"""
Tests for translate() and mid-path ** grammar fix.
"""
import pytest
import dotted
from dotted import GroupMode
from dotted.api import parse, translate
from dotted.recursive import Recursive


# ---------------------------------------------------------------------------
# Grammar: mid-path ** produces Recursive op
# ---------------------------------------------------------------------------

def test_parse_leading_dstar_is_recursive():
    """
    Leading ** is Recursive (baseline).
    """
    ops = parse('**')
    assert ops.ops[0].is_recursive()
    assert isinstance(ops.ops[0], Recursive)


def test_parse_midpath_dstar_is_recursive():
    """
    a.** must produce a Recursive op, not two Wildcards.
    """
    ops = parse('a.**')
    assert len(ops) == 2
    assert not ops.ops[0].is_recursive()
    assert ops.ops[1].is_recursive()
    assert isinstance(ops.ops[1], Recursive)


def test_parse_midpath_dstar_with_suffix():
    """
    a.**.c must produce [literal, Recursive, literal].
    """
    ops = parse('a.**.c')
    assert len(ops) == 3
    assert not ops.ops[0].is_recursive()
    assert ops.ops[1].is_recursive()
    assert not ops.ops[2].is_recursive()


def test_parse_midpath_dstar_deep():
    """
    x.y.**.z parses with Recursive in the right position.
    """
    ops = parse('x.y.**.z')
    assert len(ops) == 4
    assert ops.ops[2].is_recursive()


def test_parse_wildcard_then_dstar():
    """
    *.** should produce [Wildcard, Recursive].
    """
    ops = parse('*.**')
    assert len(ops) == 2
    assert ops.ops[0].is_pattern() and not ops.ops[0].is_recursive()
    assert ops.ops[1].is_recursive()


# ---------------------------------------------------------------------------
# match() with GroupMode.patterns
# ---------------------------------------------------------------------------

def test_match_patterns_filters_literals():
    """
    groups='patterns' excludes literal segments.
    """
    r, groups = dotted.match('a.*.b', 'a.hello.b', groups=GroupMode.patterns, partial=False)
    assert r == 'a.hello.b'
    assert groups == ('hello',)


def test_match_patterns_multiple_wildcards():
    """
    Multiple wildcards, all captured.
    """
    r, groups = dotted.match('a.*.b.*', 'a.X.b.Y', groups=GroupMode.patterns, partial=False)
    assert groups == ('X', 'Y')


def test_match_patterns_all_wildcards():
    """
    All-wildcard pattern: patterns == all.
    """
    r, groups = dotted.match('*.*.*', 'x.y.z', groups=GroupMode.patterns, partial=False)
    assert groups == ('x', 'y', 'z')


def test_match_patterns_all_literals():
    """
    All-literal pattern: no captures.
    """
    r, groups = dotted.match('a.b.c', 'a.b.c', groups=GroupMode.patterns, partial=False)
    assert groups == ()


def test_match_patterns_partial_greedy():
    """
    Partial match with wildcard greedy extension.
    """
    r, groups = dotted.match('hello.*', 'hello.there.bye', groups=GroupMode.patterns)
    assert groups == ('there.bye',)


def test_match_patterns_partial_literal_only():
    """
    Partial match where the last segment is literal — no pattern captures.
    """
    r, groups = dotted.match('hello', 'hello.there.bye', groups=GroupMode.patterns)
    assert groups == ()


def test_match_patterns_slot_wildcard():
    """
    Slot wildcard [*] is a pattern.
    """
    r, groups = dotted.match('items[*]', 'items[3]', groups=GroupMode.patterns, partial=False)
    assert groups == (3,)


def test_match_all_unchanged():
    """
    groups=True still returns all segments (backward compat).
    """
    r, groups = dotted.match('a.*.b', 'a.hello.b', groups=True, partial=False)
    assert groups == ('a', 'hello', 'b')


def test_match_false_no_groups():
    """
    groups=False returns just the match result.
    """
    r = dotted.match('a.*.b', 'a.hello.b', partial=False)
    assert r == 'a.hello.b'


# ---------------------------------------------------------------------------
# translate()
# ---------------------------------------------------------------------------

def test_translate_basic():
    """
    Basic translate with single wildcard.
    """
    assert translate('a.hello.b', {'a.*.b': '$0.there'}) == 'hello.there'


def test_translate_multiple_wildcards():
    """
    $N indices map to wildcards in order.
    """
    assert translate('a.X.b.Y.c', {'a.*.b.*.c': '$1.$0'}) == 'Y.X'


def test_translate_slot_wildcard():
    """
    Slot wildcard capture.
    """
    assert translate('items[5]', {'items[*]': 'got.$0'}) == 'got.5'


def test_translate_first_match_wins():
    """
    First matching pattern is used.
    """
    result = translate('a.b', [
        ('a.*', 'first.$0'),
        ('*.*', 'second.$0.$1'),
    ])
    assert result == 'first.b'


def test_translate_no_match_returns_none():
    """
    No matching pattern returns None.
    """
    assert translate('x.y', {'a.*': '$0'}) is None


def test_translate_skips_index_error():
    """
    Template referencing out-of-range $N skips to next rule.
    """
    result = translate('a.b', [
        ('a.*', '$5'),       # IndexError, skip
        ('a.*', 'ok.$0'),   # fallback
    ])
    assert result == 'ok.b'


def test_translate_dict_and_iterable():
    """
    Accepts both dict and iterable of tuples.
    """
    d = {'a.*': 'key.$0'}
    t = [('a.*', 'tuple.$0')]
    assert translate('a.hello', d) == 'key.hello'
    assert translate('a.hello', t) == 'tuple.hello'
