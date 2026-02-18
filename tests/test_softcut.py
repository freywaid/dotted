"""
Unit tests for soft cut (##) in path, op, and slot grouping.

Soft cut semantics: (a##, b) means "try branch a; for keys it covers, don't try
branch b. But for keys a didn't cover, still try b." Unlike hard cut (#), which
stops all later branches entirely when a yields, soft cut only suppresses later
branches for overlapping paths.
"""
import pytest
import dotted
import dotted.elements as el


# -----------------------------------------------------------------------------
# Parse: soft cut marker ## is accepted in path, op, and slot groups
# -----------------------------------------------------------------------------

def test_parse_path_group_with_softcut():
    """
    (a##, b) parses with _BRANCH_SOFTCUT marker.
    """
    ops = dotted.parse('(a##, b)')
    grp = ops.ops[0]
    assert grp.branches[1] is el._BRANCH_SOFTCUT


def test_parse_op_group_with_softcut():
    """
    (.a##,.b) parses with _BRANCH_SOFTCUT marker.
    """
    ops = dotted.parse('x(.a##,.b)')
    grp = ops.ops[1]
    assert grp.branches[1] is el._BRANCH_SOFTCUT


def test_parse_slot_group_with_softcut():
    """
    [(*##,+)] parses with _BRANCH_SOFTCUT marker.
    """
    ops = dotted.parse('items[(*##,+)]')
    grp = ops.ops[1]
    assert grp.branches[1] is el._BRANCH_SOFTCUT


def test_parse_hardcut_still_works():
    """
    (a#, b) still produces _BRANCH_CUT, not _BRANCH_SOFTCUT.
    """
    ops = dotted.parse('(a#, b)')
    grp = ops.ops[0]
    assert grp.branches[1] is el._BRANCH_CUT


# -----------------------------------------------------------------------------
# Assemble: ## appears in repr
# -----------------------------------------------------------------------------

def test_assemble_softcut():
    """
    Assembled path shows ## for soft cut.
    """
    ops = dotted.parse('(a##, b)')
    s = dotted.assemble(ops)
    assert '##' in s
    assert s == '(a##,b)'


def test_assemble_hardcut_unchanged():
    """
    Hard cut still assembles as #, not ##.
    """
    ops = dotted.parse('(a#, b)')
    s = dotted.assemble(ops)
    assert '##' not in s
    assert '#' in s


# -----------------------------------------------------------------------------
# Get: motivating example from the issue
# -----------------------------------------------------------------------------

def test_get_motivating_example():
    """
    Soft cut allows fallback to * for keys not covered by recursive branch.
    """
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'hello': {'there': 'bye'}, 'extra': 'stuff'}
    result = dotted.pluck(d, '(**:-2(.*, [])##, *)')
    assert result == (('a.b', [1, 2, 3]), ('x.y.z', [4, 5]), ('hello.there', 'bye'), ('extra', 'stuff'))


def test_get_motivating_hardcut_comparison():
    """
    Hard cut loses 'extra' — soft cut preserves it.
    """
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'hello': {'there': 'bye'}, 'extra': 'stuff'}
    result = dotted.pluck(d, '(**:-2(.*, [])#, *)')
    assert ('extra', 'stuff') not in result


# -----------------------------------------------------------------------------
# Get: basic soft cut behavior
# -----------------------------------------------------------------------------

def test_get_softcut_branch_yields_no_overlap():
    """
    (a##, b): both keys are different — softcut doesn't suppress b.
    """
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(a##, b)') == (1, 2)


def test_get_softcut_overlapping_wildcard():
    """
    (a##, *): a overlaps with * for key 'a', but * still yields 'b'.
    """
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(a##, *)') == (1, 2)


def test_get_softcut_first_branch_misses():
    """
    (a##, b): first branch doesn't match — second branch runs normally.
    """
    d = {'b': 2}
    assert dotted.get(d, '(a##, b)') == (2,)


def test_get_softcut_neither_matches():
    """
    (a##, b): no branch matches — empty result.
    """
    d = {'c': 3}
    assert dotted.get(d, '(a##, b)') == ()


def test_get_softcut_deep_prefix_suppression():
    """
    Softcut path a.b.c suppresses later branch yielding a (prefix overlap).
    """
    d = {'a': {'b': {'c': 1}}}
    r = dotted.pluck(d, '(a.b.c##, a)')
    # a.b.c covers a, so a is suppressed
    assert r == (('a.b.c', 1),)


def test_get_softcut_no_suppression_for_sibling():
    """
    Softcut path a.b.c does NOT suppress a.e (no prefix overlap).
    """
    d = {'a': {'b': {'c': 1}, 'e': 3}}
    r = dotted.pluck(d, '(a.b.c##, a.e)')
    assert r == (('a.b.c', 1), ('a.e', 3))


def test_get_softcut_sibling_keys_not_suppressed():
    """
    (*.x##, *.y): softcut on x doesn't suppress y (different second component).
    """
    d = {'a': {'x': 1, 'y': 2}, 'b': {'x': 3, 'y': 4}}
    r = dotted.pluck(d, '(*.x##, *.y)')
    assert ('a.x', 1) in r
    assert ('b.x', 3) in r
    assert ('a.y', 2) in r
    assert ('b.y', 4) in r


# -----------------------------------------------------------------------------
# Get: op group soft cut
# -----------------------------------------------------------------------------

def test_get_op_softcut_first_branch_matches():
    """
    (.a##,.b): first branch matches — but b is not suppressed (different key).
    """
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(.a##,.b)') == (1, 2)


def test_get_op_softcut_first_branch_misses():
    """
    (.a##,.b): first branch misses — second branch runs normally.
    """
    d = {'b': 2}
    assert dotted.get(d, '(.a##,.b)') == (2,)


def test_get_op_softcut_overlap():
    """
    (.a##,.*): softcut on a suppresses a from wildcard, but b comes through.
    """
    d = {'a': 1, 'b': 2}
    assert dotted.get(d, '(.a##,.*)') == (1, 2)


# -----------------------------------------------------------------------------
# Update: soft cut
# -----------------------------------------------------------------------------

def test_update_softcut_both_keys():
    """
    (a##, b): both keys updated since no overlap.
    """
    d = {'a': 1, 'b': 2}
    r = dotted.update(d, '(a##, b)', 99)
    assert r == {'a': 99, 'b': 99}


def test_update_softcut_first_branch_misses():
    """
    (a##, b): when a missing, update b.
    """
    d = {'b': 2}
    r = dotted.update(d, '(a##, b)', 99)
    assert r == {'b': 99}


def test_update_softcut_overlapping_wildcard():
    """
    (a##, *): update a via first branch, update b (not a) via wildcard.
    """
    d = {'a': 1, 'b': 2}
    r = dotted.update(d, '(a##, *)', 99)
    assert r == {'a': 99, 'b': 99}


def test_update_softcut_deep_with_fallback():
    """
    Update with softcut — deep branch updates covered keys, fallback updates the rest.
    """
    d = {'a': {'b': 1}, 'extra': 'stuff'}
    r = dotted.update(d, '(a.b##, *)', 99)
    assert r['a']['b'] == 99
    assert r['extra'] == 99


# -----------------------------------------------------------------------------
# Remove: soft cut
# -----------------------------------------------------------------------------

def test_remove_softcut_both_keys():
    """
    (a##, b): both removed since no overlap.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.remove(d, '(a##, b)')
    assert r == {'c': 3}


def test_remove_softcut_first_branch_misses():
    """
    (a##, b): when a missing, remove b.
    """
    d = {'b': 2}
    r = dotted.remove(d, '(a##, b)')
    assert r == {}


def test_remove_softcut_overlapping_wildcard():
    """
    (a##, *): remove a via first branch, remove b (not a again) via wildcard.
    """
    d = {'a': 1, 'b': 2}
    r = dotted.remove(d, '(a##, *)')
    assert r == {}


# -----------------------------------------------------------------------------
# Expand / pluck with soft cut
# -----------------------------------------------------------------------------

def test_expand_softcut():
    """
    expand with (a##, *): a expanded, and * expands only b.
    """
    d = {'a': 1, 'b': 2}
    r = dotted.expand(d, '(a##, *)')
    assert set(r) == {'a', 'b'}


def test_pluck_softcut():
    """
    pluck with (a##, *): a plucked, and * plucks only b.
    """
    d = {'a': 1, 'b': 2}
    r = dotted.pluck(d, '(a##, *)')
    assert set(r) == {('a', 1), ('b', 2)}


# -----------------------------------------------------------------------------
# overlaps() API
# -----------------------------------------------------------------------------

def test_overlaps_prefix():
    """
    overlaps detects prefix relationship.
    """
    assert dotted.overlaps('a', 'a.b.c')
    assert dotted.overlaps('a.b.c', 'a')


def test_overlaps_exact():
    """
    overlaps on identical paths.
    """
    assert dotted.overlaps('a.b', 'a.b')


def test_overlaps_no_overlap():
    """
    overlaps returns False for non-overlapping paths.
    """
    assert not dotted.overlaps('a.b', 'a.c')
    assert not dotted.overlaps('a.b.c', 'a.b.d')
    assert not dotted.overlaps('x', 'y')


# -----------------------------------------------------------------------------
# Nested softcut containment: inner group softcut must not leak to outer group
# -----------------------------------------------------------------------------

def test_get_nested_softcut_does_not_leak():
    """
    (((a##, *)), c): inner softcut scoped to inner group; outer c still yielded.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.get(d, '(((a##, *)), c)')
    assert 1 in r and 2 in r and 3 in r


def test_update_nested_softcut_contained():
    """
    (((a##, *)), c): inner softcut updates a and b; outer updates c.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.update(d, '(((a##, *)), c)', 99)
    assert r == {'a': 99, 'b': 99, 'c': 99}


def test_update_nested_nop_softcut():
    """
    ((~a##, *), c): NOP a matches (softcut), * updates b and c, outer c also updates.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.update(d, '((~a##, *), c)', 99)
    assert r['a'] == 1   # NOP
    assert r['b'] == 99   # * after softcut
    assert r['c'] == 99   # both * and outer c


# -----------------------------------------------------------------------------
# overlaps() API
# -----------------------------------------------------------------------------

def test_overlaps_with_tuples():
    """
    overlaps accepts op tuples from walk.
    """
    d = {'a': {'b': 1}}
    ops = dotted.parse('a.b')
    paths = [path for path, _ in el.walk(ops.ops, d, paths=True)]
    assert len(paths) == 1
    assert dotted.overlaps(paths[0], 'a')
    assert dotted.overlaps(paths[0], 'a.b')
    assert not dotted.overlaps(paths[0], 'a.c')
