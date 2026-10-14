"""
Tests for the pack marker: a ; just inside a group's parens, (a,b;) or
(;a,b), makes the group yield its matches as one tuple instead of one
by one. The group keeps its kind; only the shape of its output changes.
A transform after a packed group applies to the tuple once, which is
how aggregation over a pattern is written: (a.*;)|sum.
"""
import pytest
import dotted
from dotted import groups
from dotted.api import ParseError
from dotted.sql import TranslationError, sqlize


D = {
    'a': {'x': 1, 'y': 2},
    'b': 3,
    'items': [{'p': [1, 2]}, {'p': [3]}, {'p': []}],
}


# -----------------------------------------------------------------------------
# Parse
# -----------------------------------------------------------------------------

def test_parse_trailing_marker():
    """
    (a,b;) parses to a Pack around the or-group.
    """
    ops = dotted.parse('(a,b;)')
    assert len(ops.ops) == 1
    assert isinstance(ops.ops[0], groups.Pack)
    assert isinstance(ops.ops[0].inner, groups.OpGroupOr)


def test_parse_leading_and_doubled_marker():
    """
    A leading ;, or one at each end, is the same pack as a trailing one.
    """
    trailing = dotted.parse('(a,b;)')
    assert dotted.parse('(;a,b)') == trailing
    assert dotted.parse('(;a,b;)') == trailing


def test_parse_keeps_group_kind():
    """
    The marker packs whatever group it sits in.
    """
    assert isinstance(dotted.parse('(a&b;)').ops[0].inner, groups.OpGroupAnd)
    assert isinstance(dotted.parse('(a,b;)?').ops[0].inner, groups.OpGroupFirst)
    assert isinstance(dotted.parse('(a#,b;)').ops[0].inner, groups.OpGroupOr)


def test_parse_one_branch_group():
    """
    (a.*;) packs a one-branch group; the ; is what makes it a group.
    """
    ops = dotted.parse('(a.*;)')
    assert isinstance(ops.ops[0], groups.Pack)


def test_parse_renders_as_written():
    """
    repr and assemble write the marker trailing.
    """
    assert repr(dotted.parse('(;a,b)')) == 'Dotted([(a,b;)])'
    assert dotted.assemble(dotted.parse('(a.*;)|sum')) == '(a.*;)|sum'
    assert dotted.assemble(dotted.parse('x(.a,.b;)?')) == 'x(.a,.b;)?'


def test_parse_empty_pack_is_an_error():
    """
    (;) has nothing to pack.
    """
    with pytest.raises(ParseError):
        dotted.parse('(;)')


def test_parse_marker_is_not_a_separator():
    """
    ; is a marker at either end of the branch list, not a separator.
    """
    with pytest.raises(ParseError):
        dotted.parse('(a;b)')


def test_parse_semicolon_is_reserved_in_keys():
    """
    ; is reserved: a key containing one must be quoted.
    """
    with pytest.raises(ParseError):
        dotted.parse('a;b')
    assert dotted.get({'a;b': 1}, '"a;b"') == 1


def test_parse_negated_pack_is_an_error():
    """
    A packed group has no keys to negate.
    """
    with pytest.raises(ValueError):
        dotted.parse('!(a,b;)')


def test_parse_mid_path_forms():
    """
    The marker works in the explicit, shorthand and attr group forms.
    """
    assert isinstance(dotted.parse('x(.a,.b;)').ops[1], groups.Pack)
    assert isinstance(dotted.parse('x.(a,b;)').ops[1], groups.Pack)
    assert isinstance(dotted.parse('@(a,b;)').ops[0], groups.Pack)
    assert repr(dotted.parse('@(a,b;)')) == 'Dotted([(@a,@b;)])'


def test_parse_outer_parens_add_nothing():
    """
    ((a,b;)) is (a,b;), as ((a,b)) is (a,b).
    """
    assert dotted.parse('((a,b;))') == dotted.parse('(a,b;)')


# -----------------------------------------------------------------------------
# Get: one tuple
# -----------------------------------------------------------------------------

def test_get_packs_pattern_matches():
    """
    A packed pattern yields its matches as one tuple.
    """
    assert dotted.get(D, '(a.*;)') == (1, 2)


def test_get_pack_is_not_a_pattern():
    """
    A path ending in a pack yields one value, so get returns it bare.
    """
    assert not dotted.is_pattern('(a.*;)')
    assert not dotted.is_pattern('(a.*;)|sum')
    assert dotted.get(D, '(a.*;)|sum') == 3


def test_get_pack_under_a_pattern_stays_a_pattern():
    """
    One pack per match of the pattern before it.
    """
    assert dotted.is_pattern('items[*].(p[*];)')
    assert dotted.get(D, 'items[*].(p[*];)|sum') == (3, 3, 0)
    assert dotted.get(D, 'items[*](.p[*];)|sum') == (3, 3, 0)


def test_get_pack_over_whole_path():
    """
    A pack at the head of the path gathers across every level inside it.
    """
    assert dotted.get(D, '(items[*].p[*];)|sum') == 6


def test_get_no_matches_pack_to_empty():
    """
    Nothing matched is (), so sum is 0 and len is 0.
    """
    assert dotted.get(D, '(nothing.*;)') == ()
    assert dotted.get(D, '(nothing.*;)|sum') == 0
    assert dotted.get(D, '(nothing.*;)|len') == 0


def test_get_concrete_branch_packs_to_one_tuple():
    """
    A concrete path packs to a one-tuple.
    """
    assert dotted.get(D, '(b;)') == (3,)


def test_get_or_group_packs_what_matches():
    """
    (a,b;) packs the branches that match; a missing one is left out.
    """
    assert dotted.get(D, '(b,a.x;)') == (3, 1)
    assert dotted.get(D, '(b,zz;)') == (3,)


def test_get_and_group_packs_all_or_nothing():
    """
    (a&b;) packs both, or () when a branch is missing.
    """
    assert dotted.get(D, '(b&a.x;)') == (3, 1)
    assert dotted.get(D, '(b&zz;)') == ()


def test_get_cut_inside_pack():
    """
    A cut inside the group stops it; the pack holds what got through.
    """
    assert dotted.get(D, '(a.*#,b;)') == (1, 2)
    assert dotted.get(D, '(zz#,b;)') == (3,)


def test_get_first_group_packs_first_match():
    """
    (a,b;)? packs the first branch that matches.
    """
    assert dotted.get(D, '(zz,b,a.x;)?') == (3,)


# -----------------------------------------------------------------------------
# After the pack: transforms, slots, guards
# -----------------------------------------------------------------------------

def test_transforms_apply_to_the_tuple_once():
    """
    The tuple is one value: transforms chain on it.
    """
    assert dotted.get(D, '(a.*;)|mean') == 1.5
    assert dotted.get(D, '(a.*;)|len') == 2
    assert dotted.get(D, '(a.*;)|set|len') == 2
    assert dotted.get(D, '(a.*;)|list') == [1, 2]


def test_transforms_do_not_distribute_over_a_pack():
    """
    (a,b;)|sum sums a and b together; it is not (a|sum,b|sum).
    """
    assert dotted.get({'a': 1, 'b': 2}, '(a,b;)|sum') == 3
    assert dotted.get({'a': 1, 'b': 2}, '(a,b)|sum') == (1, 2)


def test_slot_into_the_tuple():
    """
    The path may continue into the tuple.
    """
    assert dotted.get(D, '(a.*;)[0]') == 1
    assert dotted.get(D, '(a.*;)[*]') == (1, 2)


def test_guard_on_the_tuple():
    """
    A guard compares the tuple, with guard transforms before it.
    """
    assert dotted.get(D, '(a.*;)=[1, 2]') == (1, 2)
    assert dotted.get(D, '(a.*;)|sum=3') == (1, 2)
    assert dotted.get(D, '(a.*;)|sum>5', default='no') == 'no'
    assert dotted.get(D, '(a.*;):tuple') == (1, 2)


def test_reference_argument_sees_the_packing_node():
    """
    ^ is the tuple, ^^ the node the group ran from.
    """
    assert dotted.get(D, '(a.*;)|mul:$$(^^b)') == (1, 2, 1, 2, 1, 2)


# -----------------------------------------------------------------------------
# Paths: walk, expand, match
# -----------------------------------------------------------------------------

def test_walk_and_expand_write_the_pack():
    """
    A pack is its own path segment, read back as written.
    """
    assert list(dotted.walk(D, '(a.*;)')) == [('(a.*;)', (1, 2))]
    assert dotted.expand(D, '(a.*;)|sum') == ('(a.*;)|sum',)
    assert dotted.expand({'a': {'x': 1}, 'b': {'x': 2}}, '*.(x;)') == ('a(.x;)', 'b(.x;)')


def test_match_pack_against_itself():
    """
    A pack matches only an equal pack.
    """
    assert dotted.match('(a.*;)', '(a.*;)') == '(a.*;)'
    assert dotted.match('(a.*;)', '(a.x;)') is None
    assert dotted.match('*', '(a.*;)') is None


# -----------------------------------------------------------------------------
# Writes raise
# -----------------------------------------------------------------------------

def test_update_through_a_pack_raises():
    """
    There is nothing under a tuple of matches to write to.
    """
    with pytest.raises(ValueError):
        dotted.update(dict(D), '(a.*;)', 5)
    with pytest.raises(ValueError):
        dotted.update(dict(D), '(a.*;)|sum', 5)


def test_remove_through_a_pack_raises():
    """
    remove through a pack raises as update does.
    """
    with pytest.raises(ValueError):
        dotted.remove(dict(D), '(a.*;)')


def test_setdefault_never_writes_through_a_pack():
    """
    A pack always yields, () at the least, so setdefault only ever reads.
    """
    assert dotted.setdefault(dict(D), '(a.*;)|sum', 5) == 3
    assert dotted.setdefault({}, '(a.*;)', 5) == ()


# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------

def test_sqlize_pack_not_supported():
    """
    The SQL translator has no aggregate mapping yet.
    """
    with pytest.raises(TranslationError):
        sqlize("(a.*;)|sum", driver="asyncpg")


# -----------------------------------------------------------------------------
# Transforms: sum and mean
# -----------------------------------------------------------------------------

def test_sum_transform():
    """
    |sum sums a container value; |sum:<start> seeds it.
    """
    assert dotted.get({'n': [1, 2, 3]}, 'n|sum') == 6
    assert dotted.get({'n': [1, 2, 3]}, 'n|sum:10') == 16
    assert dotted.get({'n': 'abc'}, 'n|sum') == 'abc'
    with pytest.raises(TypeError):
        dotted.get({'n': 'abc'}, 'n|sum::raises')


def test_mean_transform():
    """
    |mean averages a container value; empty or non-numeric is left alone.
    """
    assert dotted.get({'n': [1, 2, 3]}, 'n|mean') == 2
    assert dotted.get({'n': []}, 'n|mean') == []
    assert dotted.get({'n': 'abc'}, 'n|mean') == 'abc'
    with pytest.raises(Exception):
        dotted.get({'n': []}, 'n|mean:raises')


# -----------------------------------------------------------------------------
# A packed group as a filter key
# -----------------------------------------------------------------------------

F = {'items': [{'p': [1, 2], 'n': 'a', 'r': 2}, {'p': [3], 'n': 'b', 'r': 4}, {'p': [], 'n': 'c', 'r': 0}]}


def test_filter_key_pack_parses():
    """
    A packed group leads a filter key, with either marker position.
    """
    ops = dotted.parse('items[(p[*];)|sum>2]')
    assert isinstance(ops.ops[1].filters[0].key.parts[0], groups.Pack)
    assert dotted.parse('items[(;p[*])|sum>2]') == ops
    assert dotted.assemble(ops) == 'items[(p[*];)|sum>2]'


def test_filter_key_unpacked_group_is_an_error():
    """
    Only a packed group has one value to compare; an unpacked one fails
    to parse, as it always did.
    """
    with pytest.raises(ParseError):
        dotted.parse('items[(p[*])|sum>2]')
    with pytest.raises(ParseError):
        dotted.parse('items[(p,n)|len>2]')


def test_filter_by_aggregate_over_each_item():
    """
    The group runs from each item; its tuple is what the filter compares.
    """
    assert dotted.get(F, 'items[(p[*];)|sum>2]') == [F['items'][0], F['items'][1]]
    assert dotted.get(F, 'items[*&(p[*];)|sum>2].n') == ('a', 'b')
    assert dotted.get(F, 'items[*&(p[*];)|len=0].n') == ('c',)


def test_filter_key_pack_continues_into_the_tuple():
    """
    A slot after the pack indexes the tuple, and a container value compares whole.
    """
    assert dotted.get(F, 'items[*&(p[*];)[0]=1].n') == ('a',)
    assert dotted.get(F, 'items[*&(p[*];)=[3]].n') == ('b',)


def test_filter_key_pack_combines():
    """
    Packed keys sit in &, , ! and ? filter expressions like any key.
    """
    assert dotted.get(F, 'items[*&(p[*];)|sum>2&n="a"].n') == ('a',)
    assert dotted.get(F, 'items[*&n="c",(p[*];)|sum>2].n') == ('a', 'b', 'c')
    assert dotted.get(F, 'items[*&!(p[*];)|sum>2].n') == ('c',)
    assert dotted.get(F, 'items[*&(p[*];)|sum>2?].n') == ('a',)


def test_filter_key_pack_on_list_items():
    """
    Items need no keys: the group may address the item itself.
    """
    assert dotted.get({'lists': [[1, 2], [3], []]}, 'lists[([*];)|sum>2]') == [[1, 2], [3]]


def test_filter_key_pack_references():
    """
    In the guard, ^ is the tuple and ^^ the item; inside the group, the
    item is ^^ of a value and the list ^^^.
    """
    assert dotted.get(F, 'items[*&(p[*];)|sum>$$(^^r)].n') == ('a',)
    assert dotted.get(F, 'items[*&(p[*]|mul:$$(^^^r);)|sum>5].n') == ('a', 'b')


def test_filter_key_pack_matches_itself():
    """
    Path matching compares packed keys by equality.
    """
    assert dotted.match('items[(p[*];)|sum>2]', 'items[(p[*];)|sum>2]') == 'items[(p[*];)|sum>2]'
    assert dotted.match('items[(p[*];)|sum>2]', 'items[(p[*];)|sum>3]') is None
    assert dotted.match('items[*]', 'items[(p[*];)|sum>2]') is None


def test_sqlize_filter_key_pack_not_supported():
    """
    No JSONPath for a pack yet.
    """
    with pytest.raises(TranslationError):
        sqlize('items[(p[*];)|sum>2]', driver='asyncpg')
