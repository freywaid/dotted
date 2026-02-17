"""
Tests for OpGroup - operation sequence grouping.

OpGroup enables syntax like:
    a(.b,[])     - from a, get both a.b and a[]
    a(.b.c,.d)   - from a, get both a.b.c and a.d
    x(.a,.b,.c)  - from x, get x.a, x.b, and x.c
"""
import pytest
import dotted


def test_parse_opgroup_basic():
    """
    Test parsing of basic OpGroup syntax.
    """
    ops = dotted.parse('a(.b,.c)')
    assert len(ops.ops) == 2  # 'a' and the OpGroup


def test_parse_opgroup_with_slot():
    """
    Test parsing OpGroup with slot operations.
    """
    ops = dotted.parse('a(.b,[])')
    assert len(ops.ops) == 2


def test_parse_opgroup_nested_path():
    """
    Test parsing OpGroup with nested paths in branches.
    """
    ops = dotted.parse('x(.a.b,.c.d)')
    assert len(ops.ops) == 2


def test_get_opgroup_basic():
    """
    Test get with basic OpGroup - returns tuple of values from all branches.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.get(d, 'a(.b,.c)')
    assert r == (1, 2)


def test_get_opgroup_with_slot():
    """
    Test get with OpGroup containing slot operations.
    """
    d = {'items': [1, 2, 3]}
    r = dotted.get(d, 'items(.0,[1:])')
    assert r == (1, [2, 3])


def test_get_opgroup_mixed_key_slot():
    """
    Test get with OpGroup mixing key and slot access.
    """
    d = {'a': {'x': 10}, 'items': [1, 2, 3]}
    r = dotted.get(d, '(.a.x,.items[0])')
    assert r == (10, 1)


def test_get_opgroup_nested_paths():
    """
    Test get with OpGroup containing nested paths.
    """
    d = {'x': {'a': {'i': 1, 'j': 2}, 'b': {'k': 3}}}
    r = dotted.get(d, 'x(.a.i,.b.k)')
    assert r == (1, 3)


def test_get_opgroup_partial_match():
    """
    Test get with OpGroup where some branches don't match.
    """
    d = {'a': {'b': 1}}  # 'c' doesn't exist
    r = dotted.get(d, 'a(.b,.c)')
    # Only b matches, c is missing
    assert r == (1,)


def test_get_opgroup_no_match():
    """
    Test get with OpGroup where no branches match.
    """
    d = {'a': {}}
    r = dotted.get(d, 'a(.x,.y)')
    assert r == ()


def test_get_opgroup_three_branches():
    """
    Test get with OpGroup with more than two branches.
    """
    d = {'x': {'a': 1, 'b': 2, 'c': 3}}
    r = dotted.get(d, 'x(.a,.b,.c)')
    assert r == (1, 2, 3)


def test_expand_opgroup():
    """
    Test expand with OpGroup.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.expand(d, 'a(.b,.c)')
    assert set(r) == {'a.b', 'a.c'}


def test_expand_opgroup_with_slot():
    """
    Test expand with OpGroup containing slots.
    """
    d = {'items': [10, 20, 30]}
    r = dotted.expand(d, 'items(.0,[2])')
    assert set(r) == {'items.0', 'items[2]'}


def test_update_opgroup():
    """
    Test update with OpGroup - updates all matching branches.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.update(d, 'a(.b,.c)', 99)
    assert r == {'a': {'b': 99, 'c': 99}}
    assert dotted.get(r, 'a(.b,.c)') == (99, 99)


def test_update_opgroup_partial():
    """
    Test update with OpGroup where some branches don't exist.

    Note: OpGroup only updates existing keys (disjunction = update what exists).
    Use OpGroupFirst with [+] for create-if-missing behavior.
    """
    d = {'a': {'b': 1}}  # 'c' doesn't exist
    r = dotted.update(d, 'a(.b,.c)', 99)
    assert r == {'a': {'b': 99}}
    assert dotted.get(r, 'a(.b,.c)') == (99,)


def test_update_opgroup_nested():
    """
    Test update with OpGroup containing nested paths.
    """
    d = {'x': {'a': {'i': 1}, 'b': {'k': 3}}}
    r = dotted.update(d, 'x(.a.i,.b.k)', 99)
    assert r == {'x': {'a': {'i': 99}, 'b': {'k': 99}}}


def test_remove_opgroup():
    """
    Test remove with OpGroup - removes all matching branches.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.remove(d, 'a(.b,.c)')
    assert r == {'a': {'d': 3}}
    assert dotted.has(r, 'a.b') is False
    assert dotted.has(r, 'a.c') is False


def test_remove_opgroup_partial():
    """
    Test remove with OpGroup where some branches don't exist.
    """
    d = {'a': {'b': 1, 'd': 3}}  # 'c' doesn't exist
    r = dotted.remove(d, 'a(.b,.c)')
    assert r == {'a': {'d': 3}}
    assert dotted.has(r, 'a.b') is False


def test_pluck_opgroup():
    """
    Test pluck with OpGroup.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.pluck(d, 'a(.b,.c)')
    assert set(r) == {('a.b', 1), ('a.c', 2)}


def test_has_opgroup():
    """
    Test has with OpGroup - true if any branch exists.
    """
    d = {'a': {'b': 1}}
    assert dotted.has(d, 'a(.b,.c)') is True
    assert dotted.has(d, 'a(.x,.y)') is False


def test_opgroup_with_wildcard():
    """
    Test OpGroup where one branch uses a wildcard.
    """
    d = {'x': {'a': 1, 'b': 2, 'c': 3}}
    r = dotted.get(d, 'x(.a,[*])')
    # .a returns 1, [*] would return all values as pattern
    assert 1 in r


def test_opgroup_with_filter():
    """
    Test OpGroup with filtered access.
    """
    d = {'items': [{'id': 1, 'v': 10}, {'id': 2, 'v': 20}], 'other': 'x'}
    r = dotted.get(d, '(.items[id=1][0].v,.other)')
    assert r == (10, 'x')


def test_opgroup_first():
    """
    Test OpGroupFirst with ? suffix - returns first matching branch.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.get(d, 'a(.b,.c)?')
    assert r == (1,)


def test_opgroup_at_root():
    """
    Test OpGroup at the root level (no prefix).
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.get(d, '(.a,.b)')
    assert r == (1, 2)


def test_opgroup_multiple_levels():
    """
    Test multiple OpGroups at different levels.
    """
    d = {'x': {'a': {'i': 1, 'j': 2}, 'b': {'k': 3, 'l': 4}}}
    # First from x.a, then .i and .j
    r = dotted.get(d, 'x.a(.i,.j)')
    assert r == (1, 2)
    # From x, get both a.i and b.k
    r = dotted.get(d, 'x(.a.i,.b.k)')
    assert r == (1, 3)


def test_opgroup_repr():
    """
    Test OpGroup string representation.
    """
    ops = dotted.parse('a(.b,.c)')
    assert '(.b,.c)' in str(ops) or '(b,c)' in str(ops)


# =============================================================================
# OpGroupAnd (Conjunction) Tests
# =============================================================================

def test_parse_opgroup_and():
    """
    Test parsing of OpGroupAnd (conjunction) syntax.
    """
    ops = dotted.parse('a(.b&.c)')
    assert len(ops.ops) == 2


def test_get_opgroup_and_both_exist():
    """
    Test get with OpGroupAnd - returns values only if ALL branches exist.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.get(d, 'a(.b&.c)')
    assert r == (1, 2)


def test_get_opgroup_and_one_missing():
    """
    Test get with OpGroupAnd where one branch is missing - returns empty.
    """
    d = {'a': {'b': 1}}  # c missing
    r = dotted.get(d, 'a(.b&.c)')
    assert r == ()


def test_get_opgroup_and_all_missing():
    """
    Test get with OpGroupAnd where all branches are missing.
    """
    d = {'a': {}}
    r = dotted.get(d, 'a(.x&.y)')
    assert r == ()


def test_expand_opgroup_and():
    """
    Test expand with OpGroupAnd.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.expand(d, 'a(.b&.c)')
    assert set(r) == {'a.b', 'a.c'}


def test_expand_opgroup_and_missing():
    """
    Test expand with OpGroupAnd where one branch is missing.
    """
    d = {'a': {'b': 1}}
    r = dotted.expand(d, 'a(.b&.c)')
    assert r == ()


def test_update_opgroup_and_both_exist():
    """
    Test update with OpGroupAnd - updates only if ALL branches exist.
    After update, conjunction eval as true.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.update(d, 'a(.b&.c)', 99)
    assert r == {'a': {'b': 99, 'c': 99}}
    assert dotted.has(r, 'a(.b&.c)') is True
    assert dotted.get(r, 'a(.b&.c)') == (99, 99)


def test_update_opgroup_and_one_missing():
    """
    Test update with OpGroupAnd where one branch is missing - creates it.
    Conjunction: update all branches so conjunction eval as true.
    """
    d = {'a': {'b': 1, 'd': 3}}  # c missing
    r = dotted.update(d, 'a(.b&.c)', 99)
    assert r == {'a': {'b': 99, 'c': 99, 'd': 3}}
    assert dotted.has(r, 'a(.b&.c)') is True
    assert dotted.get(r, 'a(.b&.c)') == (99, 99)


def test_conjunction_all_missing():
    """
    Conjunction when all branches missing - create all.
    After update, conjunction eval as true.
    """
    d = {'a': {}}
    r = dotted.update(d, 'a(.b&.c)', 99)
    assert r == {'a': {'b': 99, 'c': 99}}
    assert dotted.has(r, 'a(.b&.c)') is True
    assert dotted.get(r, 'a(.b&.c)') == (99, 99)


def test_conjunction_filter_blocks():
    """
    Conjunction when filter doesn't match - abort, do nothing.
    No partial update; original structure unchanged.
    """
    d = {'name': {'first': 'Alice'}}
    r = dotted.update(d, '( (name&first=None).first & name.first )', 'hello')
    assert r == {'name': {'first': 'Alice'}}
    assert dotted.get(r, 'name.first') == 'Alice'


def test_remove_opgroup_and_both_exist():
    """
    Test remove with OpGroupAnd - removes only if ALL branches exist.
    After remove, conjunction eval as false (paths gone).
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.remove(d, 'a(.b&.c)')
    assert r == {'a': {'d': 3}}
    assert dotted.has(r, 'a(.b&.c)') is False


def test_remove_opgroup_and_one_missing():
    """
    Test remove with OpGroupAnd where one branch is missing - no remove.
    Conjunction was false before; unchanged after.
    """
    d = {'a': {'b': 1, 'd': 3}}  # c missing
    r = dotted.remove(d, 'a(.b&.c)')
    assert r == {'a': {'b': 1, 'd': 3}}  # unchanged
    assert dotted.has(r, 'a(.b&.c)') is False


def test_has_opgroup_and():
    """
    Test has with OpGroupAnd - true only if ALL branches exist.
    """
    d = {'a': {'b': 1, 'c': 2}}
    assert dotted.has(d, 'a(.b&.c)') is True

    d2 = {'a': {'b': 1}}  # c missing
    assert dotted.has(d2, 'a(.b&.c)') is False


def test_opgroup_and_three_branches():
    """
    Test OpGroupAnd with more than two branches.
    """
    d = {'x': {'a': 1, 'b': 2, 'c': 3}}
    r = dotted.get(d, 'x(.a&.b&.c)')
    assert r == (1, 2, 3)

    # One missing
    d2 = {'x': {'a': 1, 'c': 3}}  # b missing
    r = dotted.get(d2, 'x(.a&.b&.c)')
    assert r == ()


def test_opgroup_and_nested_paths():
    """
    Test OpGroupAnd with nested paths in branches.
    """
    d = {'x': {'a': {'i': 1}, 'b': {'k': 3}}}
    r = dotted.get(d, 'x(.a.i&.b.k)')
    assert r == (1, 3)

    # One path missing
    d2 = {'x': {'a': {'i': 1}}}  # b missing
    r = dotted.get(d2, 'x(.a.i&.b.k)')
    assert r == ()


def test_opgroup_and_repr():
    """
    Test OpGroupAnd string representation.
    """
    ops = dotted.parse('a(.b&.c)')
    assert '&' in str(ops)


# =============================================================================
# OpGroupNot (Negation) Tests
# =============================================================================

def test_parse_opgroup_not():
    """
    Test parsing of OpGroupNot (negation) syntax.
    """
    ops = dotted.parse('a(!.b)')
    assert len(ops.ops) == 2


def test_get_opgroup_not():
    """
    Test get with OpGroupNot - returns values for keys NOT matching.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.get(d, 'a(!.b)')
    assert set(r) == {2, 3}


def test_get_opgroup_not_on_list():
    """
    Test get with OpGroupNot on a list.
    """
    d = {'items': [10, 20, 30, 40]}
    r = dotted.get(d, 'items(![0])')
    assert set(r) == {20, 30, 40}


def test_get_opgroup_not_missing_key():
    """
    Test get with OpGroupNot where the negated key doesn't exist.
    """
    d = {'a': {'c': 2, 'd': 3}}  # b doesn't exist
    r = dotted.get(d, 'a(!.b)')
    # Negating non-existent key returns all
    assert set(r) == {2, 3}


def test_expand_opgroup_first():
    """
    Test expand with OpGroupFirst - expands only the first matching branch.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.expand(d, 'a(.b,.c)?')
    assert r == ('a.b',)


def test_expand_opgroup_first_fallback():
    """
    Test expand with OpGroupFirst - falls back to second branch.
    """
    d = {'a': {'c': 2}}
    r = dotted.expand(d, 'a(.b,.c)?')
    assert r == ('a.c',)


def test_expand_opgroup_not():
    """
    Test expand with OpGroupNot.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.expand(d, 'a(!.b)')
    assert set(r) == {'a.c', 'a.d'}


def test_update_opgroup_not():
    """
    Test update with OpGroupNot - updates keys NOT matching.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.update(d, 'a(!.b)', 99)
    assert r == {'a': {'b': 1, 'c': 99, 'd': 99}}
    assert dotted.get(r, 'a.b') == 1
    assert set(dotted.get(r, 'a(!.b)')) == {99} and len(dotted.get(r, 'a(!.b)')) == 2


def test_remove_opgroup_not():
    """
    Test remove with OpGroupNot - removes keys NOT matching.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.remove(d, 'a(!.b)')
    assert r == {'a': {'b': 1}}
    assert dotted.get(r, 'a.b') == 1
    assert dotted.has(r, 'a.c') is False
    assert dotted.has(r, 'a.d') is False


def test_has_opgroup_not():
    """
    Test has with OpGroupNot - true if any non-matching key exists.
    """
    d = {'a': {'b': 1, 'c': 2}}
    assert dotted.has(d, 'a(!.b)') is True

    d2 = {'a': {'b': 1}}  # only b exists
    assert dotted.has(d2, 'a(!.b)') is False


def test_opgroup_not_with_pattern():
    """
    Test OpGroupNot with wildcard pattern.
    """
    d = {'a': {'x': 1, 'y': 2, 'z': 3}}
    # Negate wildcard = nothing
    r = dotted.get(d, 'a(!.*)')
    assert r == ()


def test_opgroup_not_repr():
    """
    Test OpGroupNot string representation.
    """
    ops = dotted.parse('a(!.b)')
    assert '!' in str(ops)


def test_opgroup_not_multiple_keys():
    """
    Test OpGroupNot with multiple keys: (!(.a,.b))
    """
    d = {'a': 1, 'b': 2, 'c': 3, 'd': 4}

    # Negate multiple keys at root
    r = dotted.get(d, '(!(.a,.b))')
    assert set(r) == {3, 4}

    # Negate multiple keys nested
    data = {'user': {'a': 1, 'b': 2, 'c': 3}}
    r = dotted.get(data, 'user(!(.a,.b))')
    assert r == (3,)

    # Update with multi-key negation
    d2 = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.update(d2, '(!(.a,.b))', 99)
    assert r == {'a': 1, 'b': 2, 'c': 99}
    assert dotted.get(r, '(!(.a,.b))') == (99,)

    # Remove with multi-key negation
    d3 = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.remove(d3, '(!(.a,.b))')
    assert r == {'a': 1, 'b': 2}
    assert dotted.has(r, 'c') is False


# =============================================================================
# OpGroupFirst Update/Remove Tests
# =============================================================================

def test_update_opgroup_first_matches():
    """
    Test update with OpGroupFirst - updates only the first matching branch.
    """
    d = {'a': {'b': 1, 'c': 2}}
    r = dotted.update(d, 'a(.b,.c)?', 99)
    assert r == {'a': {'b': 99, 'c': 2}}
    assert dotted.get(r, 'a(.b,.c)?') == (99,)


def test_update_opgroup_first_no_first_match():
    """
    Test update with OpGroupFirst where first branch doesn't exist.
    """
    d = {'a': {'c': 2}}  # b doesn't exist
    r = dotted.update(d, 'a(.b,.c)?', 99)
    assert r == {'a': {'c': 99}}
    assert dotted.get(r, 'a(.b,.c)?') == (99,)


def test_update_opgroup_first_no_match_creates():
    """
    Test update with OpGroupFirst where no branch exists - creates last concrete.
    When nothing matches, we take first concrete path (scanning last to first).
    """
    d = {'a': {}}
    r = dotted.update(d, 'a(.b,.c)?', 99)
    assert r == {'a': {'c': 99}}
    assert dotted.get(r, 'a.c') == 99


def test_disjunction_fallback_wildcard_only():
    """
    When nothing matches and last branch is wildcard - do nothing.
    No concrete path to create.
    """
    d = {'a': {}}
    r = dotted.update(d, 'a([*])', 99)
    assert r == {'a': {}}
    assert dotted.get(r, 'a([*])') == ()


def test_disjunction_fallback_single_branch():
    """
    When nothing matches and single branch - use it.
    """
    r = dotted.update({}, '(a)', 99)
    assert r == {'a': 99}
    assert dotted.get(r, 'a') == 99


def test_disjunction_fallback_opgroup():
    """
    OpGroup (no ?) when nothing matches - same fallback, creates last concrete.
    """
    d = {'a': {}}
    r = dotted.update(d, 'a(.b,.c)', 99)
    assert r == {'a': {'c': 99}}
    assert dotted.get(r, 'a.c') == 99


def test_remove_opgroup_first_matches():
    """
    Test remove with OpGroupFirst - removes only the first matching branch.
    """
    d = {'a': {'b': 1, 'c': 2, 'd': 3}}
    r = dotted.remove(d, 'a(.b,.c)?')
    assert r == {'a': {'c': 2, 'd': 3}}
    assert dotted.has(r, 'a.b') is False
    assert dotted.has(r, 'a.c') is True


def test_remove_opgroup_first_no_first_match():
    """
    Test remove with OpGroupFirst where first branch doesn't exist.
    """
    d = {'a': {'c': 2, 'd': 3}}  # b doesn't exist
    r = dotted.remove(d, 'a(.b,.c)?')
    assert r == {'a': {'d': 3}}
    assert dotted.has(r, 'a.c') is False


def test_remove_opgroup_first_no_match():
    """
    Test remove with OpGroupFirst where no branch exists - no change.
    """
    d = {'a': {'x': 1}}
    r = dotted.remove(d, 'a(.b,.c)?')
    assert r == {'a': {'x': 1}}
    assert dotted.has(r, 'a.x') is True


# =============================================================================
# Upsert Pattern Tests: ([filter],[+])?
# =============================================================================

def test_upsert_pattern_empty_list():
    """
    Test upsert pattern on empty list - should append.
    """
    data = []
    result = dotted.update(data, '([*&name="b"],[+])?', {'name': 'b', 'val': 1})
    assert result == [{'name': 'b', 'val': 1}]


def test_upsert_pattern_no_match():
    """
    Test upsert pattern with no filter match - should append.
    """
    data = [{'name': 'a', 'val': 7}]
    result = dotted.update(data, '([*&name="b"],[+])?', {'name': 'b', 'val': 1})
    assert result == [{'name': 'a', 'val': 7}, {'name': 'b', 'val': 1}]


def test_upsert_pattern_has_match():
    """
    Test upsert pattern with filter match - should update matched item.
    """
    data = [{'name': 'a', 'val': 7}, {'name': 'b', 'val': 8}]
    result = dotted.update(data, '([*&name="b"],[+])?', {'name': 'b', 'val': 99})
    assert result == [{'name': 'a', 'val': 7}, {'name': 'b', 'val': 99}]


def test_upsert_pattern_multiple_matches():
    """
    Test upsert pattern with multiple filter matches - updates first only.
    """
    data = [{'name': 'b', 'val': 1}, {'name': 'b', 'val': 2}]
    result = dotted.update(data, '([*&name="b"?],[+])?', {'name': 'b', 'val': 99})
    # With [*&name="b"?] (first match filter), only first is updated
    assert result == [{'name': 'b', 'val': 99}, {'name': 'b', 'val': 2}]


def test_upsert_pattern_update_nested_field():
    """
    Test upsert pattern updating a specific field in matched item.
    """
    data = [{'name': 'a', 'val': 7}, {'name': 'b', 'val': 8}]
    result = dotted.update(data, '([*&name="b"],[+])?.val', 99)
    assert result == [{'name': 'a', 'val': 7}, {'name': 'b', 'val': 99}]


def test_upsert_pattern_append_with_nested_field():
    """
    Test upsert pattern appending when updating nested field.
    """
    data = [{'name': 'a', 'val': 7}]
    result = dotted.update(data, '([*&name="b"],[+])?.val', 99)
    # Appends new item with just the val field
    assert result == [{'name': 'a', 'val': 7}, {'val': 99}]


def test_upsert_pattern_empty_list_with_nested_field():
    """
    Test upsert pattern on empty list updating nested field.
    """
    data = []
    result = dotted.update(data, '([*&name="b"],[+])?.val', 99)
    assert result == [{'val': 99}]


def test_slot_special_items_empty_list():
    """
    Test that SlotSpecial.items handles empty list without error.
    """
    data = []
    # This should not raise IndexError
    result = dotted.get(data, '[+]')
    assert result is None


def test_slot_special_get_non_empty():
    """
    Test that [+] on get returns last element.
    """
    data = [1, 2, 3]
    result = dotted.get(data, '[+]')
    assert result == 3


# =============================================================================
# Slot Grouping Tests
# =============================================================================

def test_slot_grouping_parse():
    """
    Test parsing of slot grouping syntax [(*,+)].
    """
    ops = dotted.parse('items[(*,+)]')
    assert len(ops.ops) == 2

    ops = dotted.parse('items[(*&email="hello",+)]')
    assert len(ops.ops) == 2


def test_slot_grouping_get():
    """
    Test get with slot grouping.
    """
    data = {'items': [1, 2, 3]}
    result = dotted.get(data, 'items[(*,+)]')
    # * matches all, + returns last
    assert 1 in result and 2 in result and 3 in result


def test_slot_grouping_upsert_exists():
    """
    Test upsert pattern [(*&filter,+)?] when item exists.
    """
    data = {'emails': [{'email': 'hello', 'verified': False}]}
    result = dotted.update(data, 'emails[(*&email="hello",+)?]',
                          {'email': 'hello', 'verified': True})
    assert result == {'emails': [{'email': 'hello', 'verified': True}]}


def test_slot_grouping_upsert_missing():
    """
    Test upsert pattern [(*&filter,+)?] when item missing - appends.
    """
    data = {'emails': [{'email': 'other', 'verified': False}]}
    result = dotted.update(data, 'emails[(*&email="hello",+)?]',
                          {'email': 'hello', 'verified': True})
    assert result == {'emails': [
        {'email': 'other', 'verified': False},
        {'email': 'hello', 'verified': True}
    ]}


def test_slot_grouping_first():
    """
    Test slot grouping with first-match [(*,+)?].
    """
    data = {'items': [1, 2, 3]}
    # First match returns only first matching branch
    result = dotted.get(data, 'items[(*,+)?]')
    assert result == (1,)  # * matches first


def test_slot_grouping_remove():
    """
    Test remove with slot grouping.
    """
    data = {'items': [1, 2, 3]}
    result = dotted.remove(data, 'items[(*)]')
    assert result == {'items': []}
