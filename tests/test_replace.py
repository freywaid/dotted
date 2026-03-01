"""
Tests for $N substitution grammar and replace().
"""
import pytest
import dotted
from dotted.api import parse
from dotted.access import Key, Attr, Slot
from dotted.matchers import Subst
from dotted.results import assemble


# ---------------------------------------------------------------------------
# Grammar: $N parsing
# ---------------------------------------------------------------------------

def test_parse_subst_key_position():
    ops = parse('a.$0')
    assert len(ops) == 2
    assert isinstance(ops[1], Key)
    assert isinstance(ops[1].op, Subst)
    assert ops[1].op.value == 0


def test_parse_subst_slot_position():
    ops = parse('a[$0]')
    assert len(ops) == 2
    assert isinstance(ops[1], Slot)
    assert isinstance(ops[1].op, Subst)
    assert ops[1].op.value == 0


def test_parse_subst_attr_position():
    ops = parse('$0@$1')
    assert len(ops) == 2
    assert isinstance(ops[0], Key)
    assert isinstance(ops[0].op, Subst)
    assert isinstance(ops[1], Attr)
    assert isinstance(ops[1].op, Subst)
    assert ops[1].op.value == 1


def test_parse_subst_multi_digit():
    ops = parse('$10')
    assert isinstance(ops[0].op, Subst)
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
# Subst.resolve
# ---------------------------------------------------------------------------

def test_resolve_in_range():
    s = Subst(2)
    r = s.resolve(('a', 'b', 'c'))
    assert r.value == 'c'


def test_resolve_out_of_range():
    s = Subst(5)
    assert s.resolve(('a', 'b'), partial=True) is s


def test_resolve_out_of_range_strict():
    s = Subst(5)
    with pytest.raises(IndexError):
        s.resolve(('a', 'b'))


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


# ---------------------------------------------------------------------------
# replace() — deep resolution (ValueGuard, filters, nop, groups, recursive)
# ---------------------------------------------------------------------------

def test_replace_value_guard():
    """$N in ValueGuard guard: status=$0"""
    assert dotted.replace('status=$0', ('active',)) == 'status=active'


def test_replace_value_guard_recursive():
    """$N in recursive value guard: **=$0"""
    assert dotted.replace('**=$0', ('hello',)) == '**=hello'


def test_replace_type_restriction():
    """$N with type restriction: $0:dict"""
    assert dotted.replace('$0:dict', ('items',)) == 'items:dict'


def test_replace_nop_wrap():
    """$N inside NopWrap: ~$0.name"""
    assert dotted.replace('~$0.name', ('users',)) == '~users.name'


def test_replace_opgroup():
    """$N in OpGroup branches: (.$0,.$1)"""
    assert dotted.replace('a(.$0,.$1)', ('b', 'c')) == 'a(.b,.c)'


def test_replace_recursive():
    """$N after recursive descent: **.$0"""
    assert dotted.replace('**.$0', ('name',)) == '**.name'


def test_replace_negation():
    """-$0 via replace"""
    assert dotted.replace('-$0', ('hello',)) == '-hello'


def test_replace_opgroup_partial():
    """$N in OpGroup, partial mode"""
    assert dotted.replace('a(.$0,.$1)', ('b',), partial=True) == 'a(.b,.$1)'


def test_replace_value_guard_negate():
    """$N in negated ValueGuard: status!=$0"""
    assert dotted.replace('status!=$0', ('active',)) == 'status!=active'


def test_replace_slot_guard():
    """$N in slot ValueGuard: [*]=$0"""
    assert dotted.replace('[*]=$0', ('hello',)) == '[*]=hello'


def test_replace_slice_filter():
    """$N in SliceFilter: a[*=$0]"""
    assert dotted.replace('a[*=$0]', ('7',)) == 'a[*=7]'


def test_replace_identity_no_substs():
    """resolve() returns self when no $N present"""
    parsed = parse('hello.world')
    assert parsed.resolve((), partial=True) is parsed
