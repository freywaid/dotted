"""
Tests for operator precedence: access ops and :type, then !, then guards,
filters and transforms, then &, then ,. A guard, filter or transform
applies to the whole expression on its left; after a parenthesized group
it distributes to every branch; ops own their transforms.
"""
import types

import dotted
from dotted.api import parse
from dotted.wrappers import TransformWrap, ValueGuard


def _data():
    """
    A dict holding a namespace, for attribute guards.
    """
    o = types.SimpleNamespace(name='b', n=5, s='5')
    return {'sel': 'b', 'x': 5, 'n': 5, 'm': 6, 'o': o}


# ---- group shorthand ----

def test_guard_after_group_distributes():
    """
    (a,b)=5 is (a=5,b=5); for & every branch must pass.
    """
    d = {'first': 7, 'last': 3}
    assert parse('(first,last)>5').assemble() == '(first>5,last>5)'
    assert parse('(first&last)>5').assemble() == '(first>5&last>5)'
    assert parse('(first,last)?>5').assemble() == '(first>5,last>5)?'
    assert dotted.get(d, '(first,last)>5') == (7,)
    assert dotted.get(d, '(first&last)>5') == ()
    assert dotted.get(d, '(first,last)?>5') == (7,)
    assert dotted.get(d, '(first#,last)>5') == (7,)


def test_transform_after_group_distributes():
    """
    (a,b)|int is (a|int,b|int); a,b|int transforms only b.
    """
    d = {'a': '1', 'b': '2'}
    assert parse('(a,b)|int').assemble() == '(a|int,b|int)'
    assert parse('a,b|int').assemble() == '(a,b|int)'
    assert dotted.get(d, '(a,b)|int') == (1, 2)
    assert dotted.get(d, 'a,b|int') == ('1', 2)
    assert dotted.update({'x': 1, 'y': 2}, '(x,y)|str', 5) == {'x': '5', 'y': '5'}


def test_filter_after_group_distributes():
    """
    A filter after a group goes on every branch.
    """
    d = {'a': {'k': 1}, 'b': {'k': 2}}
    assert parse('(a,b)&k=1').assemble() == '(a&k=1,b&k=1)'
    assert dotted.get(d, '(a,b)&k=1') == ({'k': 1},)


def test_postfix_through_nop_and_type_on_group():
    """
    ~ and :type on a group stay outside while the postfix distributes.
    """
    d = {'a': 1, 'b': 2}
    assert parse('~(a,b)=1').assemble() == '~(a=1,b=1)'
    assert dotted.get(d, '~(a,b)=1') == (1,)
    assert dotted.update(d, '~(a,b)=1', 9) == {'a': 1, 'b': 2}
    assert parse('(a,b):dict=1').assemble() == '(a:dict=1,b:dict=1)'


def test_type_restriction_tests_the_yield():
    """
    x:int is x, when its value is an int; on a group it types what the
    group yields, transforms included.
    """
    d = {'a': {'b': 1}, 'c': [1, 2], 'x': 3}
    assert dotted.get(d, 'x:int') == 3
    assert dotted.get(d, 'x:dict') is None
    assert dotted.get(d, '*:dict') == ({'b': 1},)
    assert dotted.get(d, '*:!dict') == ([1, 2], 3)
    assert dotted.get({'a': '5', 'b': 'x'}, '(*|int):int') == (5,)
    assert parse('(*|int):int').assemble() == '((*|int):int)'
    assert parse('(a|int)=5').assemble() == '((a|int)=5)'
    assert dotted.update(d, '*:int', 0) == {'a': {'b': 1}, 'c': [1, 2], 'x': 0}


def test_group_continuation_then_postfix():
    """
    A postfix after a path that continues from a group lands on the last op.
    """
    d = {'a': {'b': 1}, 'c': {'b': 2}}
    assert dotted.get(d, '(a,c).b|mul:2') == (2, 4)
    assert dotted.get(d, '(a,c).b=2') == (2,)


# ---- negation ----

def test_guard_after_negation_filters_its_yield():
    """
    !a=5 is (!a)=5, the values other than a's equal to 5; !(a=5) is the
    complement of a guarded a.
    """
    d = {'a': 1, 'b': 2, 'c': 3}
    assert parse('!a=3').assemble() == '(!a)=3'
    assert dotted.get(d, '!a=3') == (3,)
    assert dotted.get(d, '!a>1') == (2, 3)
    assert dotted.get(d, '!(a=3)') == (1, 2, 3)
    assert dotted.get(d, '!(a=1)') == (2, 3)


def test_negation_writes_through_guard():
    """
    update and remove honor (!a)=value.
    """
    assert dotted.update({'a': 1, 'b': 1, 'c': 2}, '!a=1', 0) == {'a': 1, 'b': 0, 'c': 2}
    assert dotted.remove({'a': 1, 'b': 1, 'c': 2}, '!a=1') == {'a': 1, 'c': 2}


def test_filter_and_transform_after_negation():
    """
    !a&k=v filters what !a yields; !a|int transforms it.
    """
    d = {'first': 7, 'o': {'name': 'x', 'v': 1}, 'p': {'name': 'y', 'v': 2}}
    assert parse('!first&name="x"').assemble() == "(!first)&name='x'"
    assert dotted.get(d, '!first&name="x"') == ({'name': 'x', 'v': 1},)
    assert dotted.get({'a': 1, 'b': '2'}, '!a|int') == (2,)


def test_negation_continues_into_the_rest_of_the_path():
    """
    !a.b negates a and continues into b from every other key.
    """
    d = {'a': {'b': 1}, 'c': {'b': 2}, 'x': 3}
    assert dotted.get(d, '!a.b') == (2,)
    assert dotted.get(d, '!a.b=2') == (2,)
    assert dotted.get(d, '!a.b&x') == (2, 3)


def test_negation_binds_above_conjunction():
    """
    !a&b,c is ((!a)&b),c, as before.
    """
    assert dotted.get({'a': 1, 'b': 2, 'c': 3}, '!a&b,c') == (2, 3, 2, 3)


# ---- ops own their transforms ----

def test_transform_is_on_the_last_op():
    """
    a.b|int carries int on b; Dotted.transforms reads it back.
    """
    ops = parse('a.b|int')
    assert isinstance(ops[-1], TransformWrap)
    assert ops.transforms == ops[-1].transforms
    assert parse('a.b').transforms == ()
    assert ops.assemble() == 'a.b|int'
    assert repr(ops) == 'Dotted([a, b|int])'


def test_transform_before_guard_is_match_only():
    """
    a|int=5 compares int(a) and yields the raw a; a|int=5|str transforms
    what passes.
    """
    d = {'a': '5'}
    ops = parse('a|int=5')
    assert isinstance(ops[0], ValueGuard) and [t.name for t in ops[0].guard_transforms] == ['int']
    assert dotted.get(d, 'a|int=5') == '5'
    assert dotted.get(d, 'a|int=5|str') == '5'
    assert dotted.get({'a': '7'}, 'a|int=5') is None
    assert parse('a|int=5|str').assemble() == 'a|int=5|str'


def test_walked_paths_keep_transforms():
    """
    expand, pluck and build see the transform on each concrete path.
    """
    d = {'a': {'b': 1, 'c': 2}}
    assert [str(p) for p in dotted.expand(d, 'a.*|str')] == ['a.b|str', 'a.c|str']
    assert dotted.pluck(d, 'a.*|str') == (('a.b|str', 1), ('a.c|str', 2))
    assert dotted.build({'a': {'b': 1}}, 'a.b|str') == {'a': {'b': '1'}}


def test_apply_transforms_once():
    """
    apply() writes the transformed value back, without transforming it again.
    """
    assert dotted.apply({'n': '5', 'inc': 2}, 'n|int|add:$$(inc)') == {'n': 7, 'inc': 2}


def test_transform_disabled():
    """
    apply_transforms=False skips an op's transforms.
    """
    assert dotted.get({'a': '5'}, 'a|int', apply_transforms=False) == '5'
    assert dotted.update({'a': 1}, 'a|str', 5, apply_transforms=False) == {'a': 5}


def test_transform_on_root_and_match():
    """
    '|int' transforms the root; matching ignores a path's transforms.
    """
    assert dotted.get('10', '|int') == 10
    assert dotted.match('a.*|str', 'a.b|str') == 'a.b|str'
    assert dotted.match('a.*', 'a.b|str') == 'a.b|str'


def test_transform_parameter_colon_is_not_a_type():
    """
    : after a transform name is a parameter; :type goes on the access op.
    """
    assert dotted.get({'a': 'ff'}, 'a|int:16') == 255
    assert parse('a:str|int').assemble() == 'a:str|int'


# ---- attribute guards ----

def test_attr_guard():
    """
    @name=value is a value guard on the attr op, honored by update and remove.
    """
    d = _data()
    assert parse('o@name=5').assemble() == 'o@name=5'
    assert parse('o@~name=5').assemble() == 'o@~name=5'
    assert dotted.get(d, 'o@name="b"') == 'b'
    assert dotted.get(d, 'o@name="z"') is None
    assert dotted.get(d, 'o@s|int=5') == '5'
    assert dotted.get(d, 'o@name=$$(sel)') == 'b'
    dotted.update(d, 'o@n=5', 7)
    assert d['o'].n == 7
    dotted.remove(d, 'o@n=7')
    assert not hasattr(d['o'], 'n')


def test_nop_guards_do_not_write():
    """
    ~key=value, [~slot]=value and @~name=value match without mutating.
    """
    d = _data()
    dotted.update(d, 'o@~name="b"', 'z')
    assert d['o'].name == 'b'
    assert dotted.get({'a': 1}, '~a=1') == 1
    assert dotted.update({'a': 1}, '~a=1', 9) == {'a': 1}
    assert dotted.remove({'a': 1}, '~a=1') == {'a': 1}
    assert dotted.update({'x': {'a': 1}}, 'x.~a=1', 9) == {'x': {'a': 1}}
    assert dotted.update([1, 2], '[~0]=1', 9) == [1, 2]
    assert dotted.remove([1, 2], '[~0]=1') == [1, 2]
