"""
Tests for NOP (~) operator: match but don't update.
"""
import dotted
import pytest


def test_nop_segment_skip_update():
    # name.~first: traverse name then NOP at first -> don't update name.first
    data = {'name': {'first': 'hello'}}
    # (name.~first, name.first)?: if name.first exists NOP else update
    r = dotted.update(data, '(name.~first, name.first)?', 'world')
    assert r == data  # unchanged: first branch matched and NOP
    r = dotted.update({'name': {}}, '(name.~first, name.first)?', 'world')
    assert r == {'name': {'first': 'world'}}  # second branch: update


def test_nop_group_skip_update():
    # ~(name.first), name.first)?: NOP the path name.first when it exists
    data = {'name': {'first': 'hello'}}
    r = dotted.update(data, '(~(name.first), name.first)?', 'world')
    assert r == data
    r = dotted.update({'name': {}}, '(~(name.first), name.first)?', 'world')
    assert r == {'name': {'first': 'world'}}


def test_nop_get_unchanged():
    # get with ~ path returns same values as without ~
    data = {'a': 1, 'b': 2}
    assert dotted.get(data, '~a') == 1
    assert dotted.get(data, 'a') == 1
    assert dotted.get(data, '~a.b', default=3) == 3


def test_nop_path_start():
    # ~a.b: NOP at a, then .b
    data = {'a': {'b': 1}}
    r = dotted.update(data, '~a.b', 2)
    assert r['a']['b'] == 2  # we update .b under a (NOP was at a only)
    data2 = {'a': {'b': 1}}
    r2 = dotted.update(data2, 'a.~b', 2)
    assert r2['a']['b'] == 1  # unchanged: NOP at b


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
