"""
Tests for $N substitution grammar and replace().
"""
import pytest
import dotted
from dotted.api import parse
from dotted.access import Key, Attr, Slot
from dotted.match import PositionalSubst
from dotted.results import assemble


# ---------------------------------------------------------------------------
# Grammar: $N parsing
# ---------------------------------------------------------------------------

def test_parse_subst_key_position():
    ops = parse('a.$0')
    assert len(ops) == 2
    assert isinstance(ops[1], Key)
    assert isinstance(ops[1].op, PositionalSubst)
    assert ops[1].op.value == 0


def test_parse_subst_slot_position():
    ops = parse('a[$0]')
    assert len(ops) == 2
    assert isinstance(ops[1], Slot)
    assert isinstance(ops[1].op, PositionalSubst)
    assert ops[1].op.value == 0


def test_parse_subst_attr_position():
    ops = parse('$0@$1')
    assert len(ops) == 2
    assert isinstance(ops[0], Key)
    assert isinstance(ops[0].op, PositionalSubst)
    assert isinstance(ops[1], Attr)
    assert isinstance(ops[1].op, PositionalSubst)
    assert ops[1].op.value == 1


def test_parse_subst_multi_digit():
    ops = parse('$10')
    assert isinstance(ops[0].op, PositionalSubst)
    assert ops[0].op.value == 10


def test_parse_subst_roundtrip():
    assert assemble(parse('people.$1.$2')) == 'people.$1.$2'


def test_parse_subst_roundtrip_slot():
    assert assemble(parse('items[$0]')) == 'items[$0]'


def test_parse_subst_roundtrip_attr():
    assert assemble(parse('$0@$1')) == '$0@$1'


def test_parse_subst_in_group():
    ops = parse('(.$0, @$0)')
    assert ops is not None


# ---------------------------------------------------------------------------
# Grammar: $N in various contexts
# ---------------------------------------------------------------------------

def test_parse_subst_filter_value():
    """$N as filter value: a[name=$0]"""
    ops = parse('a[name=$0]')
    assert ops is not None


def test_parse_subst_transform_arg():
    """$N as transform argument: a|str:$0"""
    ops = parse('a|str:$0')
    assert ops is not None


def test_parse_subst_negation():
    """-$N as negated key"""
    ops = parse('-$0')
    assert ops is not None
    assert assemble(parse('-$0')) == '-$0'


def test_parse_subst_after_recursive():
    """$N after recursive: **.$0"""
    ops = parse('**.$0')
    assert ops is not None
    assert assemble(parse('**.$0')) == '**.$0'


def test_parse_subst_container_value():
    """$N inside pattern container value: a[name={$0,$1}]"""
    ops = parse('a[name={$0,$1}]')
    assert ops is not None


# ---------------------------------------------------------------------------
# PositionalSubst.resolve
# ---------------------------------------------------------------------------

def test_resolve_in_range():
    s = PositionalSubst(2)
    assert s.resolve(('a', 'b', 'c')) == 'c'


def test_resolve_out_of_range():
    s = PositionalSubst(5)
    assert s.resolve(('a', 'b')) is None


# ---------------------------------------------------------------------------
# replace()
# ---------------------------------------------------------------------------

def test_replace_basic():
    assert dotted.replace('people.$1.$2', ('users', 'alice', 'age')) == 'people.alice.age'


def test_replace_swap():
    assert dotted.replace('by_field.$2.$1', ('users', 'alice', 'age')) == 'by_field.age.alice'


def test_replace_slot_context():
    assert dotted.replace('items[$0]', ('3',)) == 'items[3]'


def test_replace_attr_context():
    assert dotted.replace('$0@$1', ('obj', 'name')) == 'obj@name'


def test_replace_multi_segment_group():
    assert dotted.replace('new.$0', ('a.b.c',)) == 'new.a.b.c'


def test_replace_out_of_range_strict():
    with pytest.raises(IndexError):
        dotted.replace('$5', ('a',))


def test_replace_partial_unresolved():
    assert dotted.replace('$0.$1.$2', ('a', 'b'), partial=True) == 'a.b.$2'


def test_replace_partial_chained():
    first = dotted.replace('$0.$1.$2', ('a',), partial=True)
    assert first == 'a.$1.$2'
    # Indices are preserved: $1 needs groups[1], $2 needs groups[2]
    second = dotted.replace(first, (None, 'b', 'c'))
    assert second == 'a.b.c'


def test_replace_group_slicing():
    _, groups = dotted.match('users.*.*', 'users.alice.age', groups=True)
    assert dotted.replace('people.$0.$1', groups[1:]) == 'people.alice.age'


def test_replace_transform_arg():
    assert dotted.replace('a|str:$0', ('hello',)) == 'a|str:hello'


def test_replace_transform_arg_partial():
    assert dotted.replace('a|str:$1', ('x',), partial=True) == 'a|str:$1'


def test_replace_transform_no_arg():
    assert dotted.replace('$0|int', ('n',)) == 'n|int'


def test_replace_no_substs():
    assert dotted.replace('hello.world', ()) == 'hello.world'


def test_replace_top_position():
    assert dotted.replace('$0.name', ('users',)) == 'users.name'
