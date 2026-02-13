"""
Tests for NOP (~) operator: match but don't update.
"""
import copy
import dotted
import pytest


def test_nop_segment_skip_update():
    # name.~first: traverse name then NOP at first -> don't update name.first
    data = {'name': {'first': 'hello'}}
    # (name.~first, name.first)?: if name.first exists NOP else update
    r = dotted.update(data, '(name.~first, name.first)?', 'world')
    assert r == data
    assert dotted.get(r, 'name.first') == 'hello'
    r = dotted.update({'name': {}}, '(name.~first, name.first)?', 'world')
    assert r == {'name': {'first': 'world'}}
    assert dotted.get(r, 'name.first') == 'world'


def test_nop_first_match_empty_path():
    # NOP branch must not create structure when path missing; fall through to update branch
    r = dotted.update({}, '(name.~first, name.first)?', 'bob')
    assert r == {'name': {'first': 'bob'}}
    assert dotted.get(r, 'name.first') == 'bob'
    r = dotted.update({'name': {}}, '(name.~first, name.first)?', 'bob')
    assert r == {'name': {'first': 'bob'}}
    assert dotted.get(r, 'name.first') == 'bob'
    r = dotted.update({}, '(~(name.first), name.first)?', 'bob')
    assert r == {'name': {'first': 'bob'}}
    assert dotted.get(r, 'name.first') == 'bob'


def test_nop_slot_first_match_empty_path():
    # Slot NOP branch must not create structure when path missing; fall through to update branch
    r = dotted.update([], '(~[0].x, [0].x)?', 'bob')
    assert r == [{'x': 'bob'}]
    # List with item missing .x
    r = dotted.update([{}], '(~[0].x, [0].x)?', 'bob')
    assert r == [{'x': 'bob'}]


def test_nop_group_skip_update():
    # ~(name.first), name.first)?: NOP the path name.first when it exists
    data = {'name': {'first': 'hello'}}
    r = dotted.update(data, '(~(name.first), name.first)?', 'world')
    assert r == data
    assert dotted.get(r, 'name.first') == 'hello'
    r = dotted.update({'name': {}}, '(~(name.first), name.first)?', 'world')
    assert r == {'name': {'first': 'world'}}
    assert dotted.get(r, 'name.first') == 'world'


def test_nop_filter_update_nop_create():
    # ( (name&first=None).first, name.~first, name.first )?:
    # If first is None -> update; if first exists with value -> NOP; if missing -> create
    # name.first missing -> create
    r = dotted.update({'name': {}}, '( (name&first=None).first, name.~first, name.first )?', 'hello')
    assert r == {'name': {'first': 'hello'}}
    assert dotted.get(r, 'name.first') == 'hello'
    # name.first exists with value -> NOP (preserve)
    data = {'name': {'first': 'Alice'}}
    r = dotted.update(copy.deepcopy(data), '( (name&first=None).first, name.~first, name.first )?', 'hello')
    assert r == {'name': {'first': 'Alice'}}
    assert dotted.get(r, 'name.first') == 'Alice'
    # name.first is None -> update
    r = dotted.update({'name': {'first': None}}, '( (name&first=None).first, name.~first, name.first )?', 'hello')
    assert r == {'name': {'first': 'hello'}}
    assert dotted.get(r, 'name.first') == 'hello'


def test_nop_opgroup_vs_opgroup_first():
    # OpGroup updates all matches - NOP and plain both match, plain overwrites
    # OpGroupFirst stops at first match - NOP wins, preserves value
    data = {'name': {'first': 'bye'}}
    r_opgroup = dotted.update(copy.deepcopy(data), '( (name&first=None).first, name.~first, name.first )', 'hello')
    r_first = dotted.update(copy.deepcopy(data), '( (name&first=None).first, name.~first, name.first )?', 'hello')
    assert r_opgroup == {'name': {'first': 'hello'}}
    assert dotted.get(r_opgroup, 'name.first') == 'hello'
    assert r_first == {'name': {'first': 'bye'}}
    assert dotted.get(r_first, 'name.first') == 'bye'


def test_nop_get_unchanged():
    # get with ~ path returns same values as without ~
    data = {'a': 1, 'b': 2}
    assert dotted.get(data, '~a') == 1
    assert dotted.get(data, 'a') == 1
    assert dotted.get(data, '~a.b', default=3) == 3


def test_nop_remove():
    # remove ~a: NOP at leaf, don't remove
    data = {'a': 1, 'b': 2}
    r = dotted.remove(data.copy(), '~a')
    assert r == {'a': 1, 'b': 2}
    assert dotted.has(r, 'a') is True
    r = dotted.remove(data.copy(), 'a')
    assert r == {'b': 2}
    assert dotted.has(r, 'a') is False
    # remove ~a.b: NOP at a, do remove .b under a
    data = {'a': {'b': 1, 'c': 2}}
    r = dotted.remove(data.copy(), '~a.b')
    assert r == {'a': {'c': 2}}
    assert dotted.has(r, 'a.b') is False
    # remove [~*].x or ~[*].x: NOP at slot (don't remove list items), remove .x from each
    data = [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]
    r = dotted.remove(data.copy(), '[~*].x')
    assert r == [{'y': 2}, {'y': 4}]
    assert dotted.get(r, '[*].x') == ()
    r = dotted.remove(data.copy(), '~[*].x')
    assert r == [{'y': 2}, {'y': 4}]


def test_nop_path_start():
    # ~a.b: NOP at a, then .b
    data = {'a': {'b': 1}}
    r = dotted.update(data, '~a.b', 2)
    assert dotted.get(r, 'a.b') == 2
    data2 = {'a': {'b': 1}}
    r2 = dotted.update(data2, 'a.~b', 2)
    assert dotted.get(r2, 'a.b') == 1


def test_nop_assemble():
    # parse and assemble round-trip for ~ paths
    p = dotted.parse('~name.first')
    assert '~' in dotted.assemble(p.ops)
    p2 = dotted.parse('(name.~first, name.first)?')
    s = dotted.assemble(p2.ops)
    assert '~' in s


def test_nop_canonical_forms():
    # ~. and .~ both canonicalize to .~
    assert dotted.assemble(dotted.parse('a.~b').ops) == 'a.~b'
    assert dotted.assemble(dotted.parse('a~.b').ops) == 'a.~b'
    # ~@ and @~ both canonicalize to @~ when not at top
    assert dotted.assemble(dotted.parse('a@~b').ops) == 'a@~b'
    assert dotted.assemble(dotted.parse('a~@b').ops) == 'a@~b'
    # At top: ~@ and @~ canonicalize to ~@
    assert dotted.assemble(dotted.parse('~@a').ops) == '~@a'
    assert dotted.assemble(dotted.parse('@~a').ops) == '~@a'
    # Slots: ~[*] and [~*] canonicalize to [~*] (tilde inside brackets)
    assert dotted.assemble(dotted.parse('~[*]').ops) == '[~*]'
    assert dotted.assemble(dotted.parse('[~*]').ops) == '[~*]'
    assert dotted.assemble(dotted.parse('~[0]').ops) == '[~0]'
    assert dotted.assemble(dotted.parse('[~0]').ops) == '[~0]'
    # Slices: ~[1:3] and [~1:3] canonicalize to [~1:3]
    assert dotted.assemble(dotted.parse('~[1:3]').ops) == '[~1:3]'
    assert dotted.assemble(dotted.parse('[~1:3]').ops) == '[~1:3]'
    # Empty slice: [~] and ~[] canonicalize to ~[]
    assert dotted.assemble(dotted.parse('[~]').ops) == '~[]'
    assert dotted.assemble(dotted.parse('~[]').ops) == '~[]'


def test_nop_other_api():
    # match: pattern with ~ matches same as without
    assert dotted.match('~a.b', 'a.b') == 'a.b'
    assert dotted.match('~*.c', 'x.c') == 'x.c'
    # expand: ~* expands like *
    d = {'a': 1, 'b': 2}
    assert set(dotted.expand(d, '~*')) == set(dotted.expand(d, '*'))
    # has
    assert dotted.has(d, '~a') is True
    assert dotted.has(d, '~z') is False
    # build with NOP path (traverses same structure)
    assert dotted.build({}, '~a.b') == {'a': {'b': None}}


def test_nop_empty_slice():
    # [~] and ~[] parse and canonicalize to ~[]; get returns full sequence
    assert dotted.assemble(dotted.parse('[~]').ops) == '~[]'
    assert dotted.assemble(dotted.parse('~[]').ops) == '~[]'
    data = [1, 2, 3]
    assert dotted.get(data, '[~]') == [1, 2, 3]
    assert dotted.get(data, '~[]') == [1, 2, 3]


def test_nop_slot_style():
    # [~*] or ~[*] in path
    data = [{'x': 1}, {'x': 2}]
    # Update [*].x but NOP first element: use (~[*].x, [*].x)? - no, that's group.
    # Simpler: name.~first already tested; slot NOP is [~*]
    r = dotted.update([{'v': 0}, {'v': 0}], '[1].v', 1)
    assert r[1]['v'] == 1
    # [~1].v: NOP at [1] (don't replace list item), then update .v under it
    r2 = dotted.update([{'v': 0}, {'v': 99}], '[~1].v', 1)
    assert r2[1]['v'] == 1  # .v updated; nop applied only at [1] segment


def test_nop_slot_group_parse():
    """NOP (~) inside slot groups parses correctly (was ParseError before fix)."""
    p = dotted.parse('[(~*#, +)]')
    assert p is not None
    p2 = dotted.parse('[(~*&x=1#, +)]')
    assert p2 is not None
    p3 = dotted.parse('[(~*&email=/(?i)alice/#, +)]')
    assert p3 is not None


def test_nop_slot_group_update():
    """NOP slot group without continuation: [(~*#, +)] prevents item replacement."""
    # Existing items: NOP preserves, cut prevents append
    data = [{'x': 1}, {'x': 2}]
    r = dotted.update(copy.deepcopy(data), '[(~*#, +)]', {'x': 3})
    assert r == [{'x': 1}, {'x': 2}]  # NOP: items not replaced

    # Empty list: NOP branch doesn't match, append fires
    r2 = dotted.update([], '[(~*#, +)]', {'x': 3})
    assert r2 == [{'x': 3}]


def test_nop_slot_group_subpath():
    """NOP at slot level doesn't prevent sub-path updates (NOP only protects the slot item)."""
    data = [{'x': 1}, {'x': 2}]
    # NOP on [*] but .x still updates through the matched items
    r = dotted.update(copy.deepcopy(data), '[(~*#, +)].x', 99)
    assert len(r) == 2  # cut prevented append
    assert r[0]['x'] == 99  # .x updated (NOP only protects slot, not sub-paths)
    assert r[1]['x'] == 99


def test_nop_continuation_op_group():
    """NOP on operation group in continuation position: x~(.a,.b)"""
    # Should parse without error
    p = dotted.parse('x~(.a,.b)')
    assert p is not None

    # Functional: x~(.a,.b) should get both .a and .b but NOP (no update)
    data = {'x': {'a': 1, 'b': 2}}
    assert dotted.get(data, 'x~(.a,.b)') == (1, 2)

    # Update: NOP means don't update through the group
    r = dotted.update(copy.deepcopy(data), 'x~(.a,.b)', 99)
    assert r['x']['a'] == 1  # NOP preserved
    assert r['x']['b'] == 2  # NOP preserved

    # Without NOP, update goes through
    r2 = dotted.update(copy.deepcopy(data), 'x(.a,.b)', 99)
    assert r2['x']['a'] == 99
    assert r2['x']['b'] == 99


def test_nop_continuation_op_group_assemble():
    """Round-trip parse/assemble for x~(.a,.b)"""
    p = dotted.parse('x~(.a,.b)')
    s = dotted.assemble(p.ops)
    assert '~' in s
    # Re-parse the assembled form to verify it's valid
    p2 = dotted.parse(s)
    assert p2 is not None


def test_nop_continuation_op_group_first():
    """NOP on operation group first in continuation: x~(.a,.b)?"""
    p = dotted.parse('x~(.a,.b)?')
    assert p is not None

    data = {'x': {'a': 1, 'b': 2}}
    r = dotted.update(copy.deepcopy(data), 'x~(.a,.b)?', 99)
    assert r['x']['a'] == 1  # NOP preserved
    assert r['x']['b'] == 2  # NOP preserved
