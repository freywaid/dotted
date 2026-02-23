"""
Unit tests for per-branch cut (#) in path and op grouping.

Cut semantics: (a#, b) means "if branch a matches, return its results and do not
try branch b". So the first branch that yields results and has # after it commits;
later branches are never tried. If that branch didn't match, the cut is skipped
and the next branch is tried.
"""
import pytest
import dotted
import dotted.elements as el


# -----------------------------------------------------------------------------
# Parse: cut marker is accepted in path and op groups
# -----------------------------------------------------------------------------

def test_parse_path_group_with_cut():
    """(a#, b) and (.a#,.b) parse successfully."""
    ops = dotted.parse('(a#, b)')
    assert len(ops.ops) == 1
    assert ops.ops[0].branches  # OpGroup with branches

    ops = dotted.parse('(.a#,.b)')
    assert len(ops.ops) == 1


def test_parse_op_group_with_cut():
    """Op group with cut parses."""
    ops = dotted.parse('x(.a#,.b)')
    assert len(ops.ops) == 2


def test_parse_slot_group_with_cut():
    """Slot grouping [(*#, +)] parses."""
    ops = dotted.parse('items[(*#,+)]')
    assert len(ops.ops) == 2


# -----------------------------------------------------------------------------
# Get: path grouping cut (a#, b)
# -----------------------------------------------------------------------------

def test_get_path_cut_first_branch_matches():
    """(a#, b): first branch matches -> return only its results, do not try b."""
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(a#, b)') == (1,)


def test_get_path_cut_first_branch_misses():
    """(a#, b): first branch doesn't match -> try b, return b's results."""
    d = {'b': 2}
    assert dotted.get(d, '(a#, b)') == (2,)


def test_get_path_cut_neither_matches():
    """(a#, b): no branch matches -> return empty."""
    d = {'c': 3}
    assert dotted.get(d, '(a#, b)') == ()


def test_get_path_cut_three_branches():
    """(a#, b#, c): first match wins and cuts."""
    d = {'a': 1, 'b': 2, 'c': 3}
    assert dotted.get(d, '(a#, b#, c)') == (1,)

    d2 = {'b': 2, 'c': 3}
    assert dotted.get(d2, '(a#, b#, c)') == (2,)

    d3 = {'c': 3}
    assert dotted.get(d3, '(a#, b#, c)') == (3,)


def test_get_path_cut_no_cut_on_last_branch():
    """(a, b#): no cut after last branch, so both branches can yield (no cut between)."""
    d = {'a': 1, 'b': 2}
    # Without cut we get both; with (a, b#) we get both then cut (no further branches)
    assert dotted.get(d, '(a, b#)') == (1, 2)


# -----------------------------------------------------------------------------
# Get: op grouping cut (.a#,.b)
# -----------------------------------------------------------------------------

def test_get_op_cut_first_branch_matches():
    """(.a#,.b): first branch matches -> return only its results."""
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(.a#,.b)') == (1,)


def test_get_op_cut_first_branch_misses():
    """(.a#,.b): first branch doesn't match -> try .b."""
    d = {'b': 2}
    assert dotted.get(d, '(.a#,.b)') == (2,)


def test_get_op_cut_nested_path():
    """x(.a#,.b): cut applies at op level."""
    d = {'x': {'a': 10, 'b': 20}}
    assert dotted.get(d, 'x(.a#,.b)') == (10,)


def test_get_op_cut_nested_second_branch():
    """x(.a#,.b): when .a missing, return .b."""
    d = {'x': {'b': 20}}
    assert dotted.get(d, 'x(.a#,.b)') == (20,)


# -----------------------------------------------------------------------------
# Get: first-match (?) with cut
# -----------------------------------------------------------------------------

def test_get_first_match_with_cut():
    """(a#, b)?: first branch that matches wins; cut prevents trying later branches."""
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(a#, b)?') == (1,)

    d2 = {'b': 2}
    assert dotted.get(d2, '(a#, b)?') == (2,)


# -----------------------------------------------------------------------------
# Cut with NOP (~): first branch match (even NOP) triggers cut
# -----------------------------------------------------------------------------

def test_parse_op_group_nop_with_cut():
    """(.~a#,.b) parses: NOP on first branch with cut."""
    ops = dotted.parse('(.~a#,.b)')
    assert len(ops.ops) == 1
    assert ops.ops[0].branches


def test_get_op_cut_nop_first_branch_matches():
    """(.~a#,.b): first branch (NOP) matches -> return its result only, cut, don't try .b."""
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(.~a#,.b)') == (1,)


def test_get_op_cut_nop_first_branch_misses():
    """(.~a#,.b): when .a missing, first branch yields nothing -> try .b."""
    d = {'b': 2}
    assert dotted.get(d, '(.~a#,.b)') == (2,)


def test_update_op_cut_nop_first_branch_matches():
    """(.~a#,.b) update: first branch matches (NOP so no update), cut -> .b not updated."""
    d = {'a': 1, 'b': 2}
    r = dotted.update(d, '(.~a#,.b)', 99)
    assert r == {'a': 1, 'b': 2}


def test_update_op_cut_nop_first_branch_misses():
    """(.~a#,.b) update: when .a missing, try .b and update it."""
    d = {'b': 2}
    r = dotted.update(d, '(.~a#,.b)', 99)
    assert r == {'b': 99}


# -----------------------------------------------------------------------------
# iter_until_cut helper
# -----------------------------------------------------------------------------

def test_iter_until_cut_stops_on_sentinel():
    """iter_until_cut yields values until _CUT_SENTINEL, then stops."""
    def gen():
        yield 1
        yield 2
        yield el.CUT_SENTINEL
        yield 3

    out = tuple(el.iter_until_cut(gen()))
    assert out == (1, 2)


def test_iter_until_cut_no_sentinel():
    """iter_until_cut yields all if no _CUT_SENTINEL."""
    out = tuple(el.iter_until_cut(iter([1, 2, 3])))
    assert out == (1, 2, 3)


# -----------------------------------------------------------------------------
# Update with cut
# -----------------------------------------------------------------------------

def test_update_path_cut_first_branch_matches():
    """update with (a#, b): only first matching branch is updated."""
    d = {'a': 1, 'b': 2}
    r = dotted.update(d, '(a#, b)', 99)
    assert r == {'a': 99, 'b': 2}


def test_update_path_cut_first_branch_misses():
    """update with (a#, b): when a missing, update b."""
    d = {'b': 2}
    r = dotted.update(d, '(a#, b)', 99)
    assert r == {'b': 99}


def test_update_op_cut():
    """update with (.a#,.b): only first matching branch updated."""
    d = {'a': 1, 'b': 2}
    r = dotted.update(d, '(.a#,.b)', 99)
    assert r == {'a': 99, 'b': 2}


# -----------------------------------------------------------------------------
# Remove with cut
# -----------------------------------------------------------------------------

def test_remove_path_cut_first_branch_matches():
    """remove with (a#, b): only first matching branch is removed."""
    d = {'a': 1, 'b': 2}
    r = dotted.remove(d, '(a#, b)')
    assert r == {'b': 2}


def test_remove_path_cut_first_branch_misses():
    """remove with (a#, b): when a missing, remove b."""
    d = {'b': 2}
    r = dotted.remove(d, '(a#, b)')
    assert r == {}


def test_remove_op_cut():
    """remove with (.a#,.b): only first matching branch removed."""
    d = {'a': 1, 'b': 2}
    r = dotted.remove(d, '(.a#,.b)')
    assert r == {'b': 2}


# -----------------------------------------------------------------------------
# Slot grouping cut [(*#, +)]
# -----------------------------------------------------------------------------

def test_get_slot_cut_first_branch_matches():
    """items[(*#,+)]: * matches all -> return those, don't try +."""
    d = {'items': [1, 2, 3]}
    r = dotted.get(d, 'items[(*#,+)]')
    assert 1 in r and 2 in r and 3 in r
    assert len(r) == 3


def test_get_slot_cut_second_branch():
    """When first branch has no match (e.g. filter), second branch can match."""
    # [(*&x=1#, +)] on list with no x=1: *&x=1 yields nothing, so try +
    d = {'items': [1, 2, 3]}
    r = dotted.get(d, 'items[(*&x=99#,+)]')
    # *&x=99 matches nothing; + yields last
    assert r == (3,)


# -----------------------------------------------------------------------------
# Expand with cut
# -----------------------------------------------------------------------------

def test_expand_cut_first_branch_matches():
    """expand with (a#, b): when a matches, only a is expanded."""
    d = {'a': 1, 'b': 2}
    r = dotted.expand(d, '(a#, b)')
    assert r == ('a',)


def test_expand_cut_first_branch_misses():
    """expand with (a#, b): when a missing, b is expanded."""
    d = {'b': 2}
    r = dotted.expand(d, '(a#, b)')
    assert r == ('b',)


def test_expand_cut_no_cut():
    """expand with (a, b) without cut: both expanded."""
    d = {'a': 1, 'b': 2}
    r = dotted.expand(d, '(a, b)')
    assert set(r) == {'a', 'b'}


def test_pluck_cut_first_branch_matches():
    """pluck with (a#, b): when a matches, only a is plucked."""
    d = {'a': 1, 'b': 2}
    r = dotted.pluck(d, '(a#, b)')
    assert r == (('a', 1),)


def test_pluck_cut_first_branch_misses():
    """pluck with (a#, b): when a missing, b is plucked."""
    d = {'b': 2}
    r = dotted.pluck(d, '(a#, b)')
    assert r == (('b', 2),)


# -----------------------------------------------------------------------------
# Assemble: cut appears as # in path
# -----------------------------------------------------------------------------

def test_assemble_path_cut():
    """Assembled path shows # after branch that has cut."""
    ops = dotted.parse('(a#, b)')
    s = dotted.assemble(ops)
    assert '#' in s
    assert s.startswith('(') and 'a' in s and 'b' in s


# -----------------------------------------------------------------------------
# Nested cut containment: inner group cut must not leak to outer group
# -----------------------------------------------------------------------------

def test_get_nested_cut_does_not_leak():
    """
    (((a#, b)), c): inner cut between a and b should not suppress outer c.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    assert dotted.get(d, '(((a#, b)), c)') == (1, 3)


def test_get_nested_cut_inner_miss():
    """
    (((a#, b)), c): when a missing, inner group falls through to b; outer adds c.
    """
    d = {'b': 2, 'c': 3}
    assert dotted.get(d, '(((a#, b)), c)') == (2, 3)


def test_get_nested_single_branch_cut():
    """
    (((a#)&b), c): cut on single-branch inner group should not leak.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    assert dotted.get(d, '(((a#)&b), c)') == (1, 3)


def test_update_nested_cut_contained():
    """
    (((a#, b)), c): inner cut only affects inner group; c still updated.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.update(d, '(((a#, b)), c)', 99)
    assert r == {'a': 99, 'b': 2, 'c': 99}


def test_update_nested_nop_cut():
    """
    ((~a#, b), c): NOP a matches and cuts b in inner group; only c updated.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.update(d, '((~a#, b), c)', 99)
    assert r == {'a': 1, 'b': 2, 'c': 99}


def test_remove_nested_cut_contained():
    """
    (((a#, b)), c): inner cut removes a (not b); outer removes c.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.remove(d, '(((a#, b)), c)')
    assert r == {'b': 2}
