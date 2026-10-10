"""
Tests for $$(path) references in transform arguments, guard values and
filter values. $$(path) is the root; $$(^path) is the value under test,
$$(^^path) the node holding it, and so on up.
"""
import dotted
from dotted.api import parse


# ---- trailing transforms ----

def test_transform_root_reference():
    """
    A root reference as a transform argument.
    """
    data = {'config': {'offset': 10}, 'n': 5}
    assert dotted.get(data, 'n|add:$$(config.offset)') == 15


def test_transform_root_reference_string_param():
    """
    A reference stands in for a string parameter too.
    """
    data = {'config': {'fmt': 'v=%s'}, 'n': 5}
    assert dotted.get(data, 'n|str:$$(config.fmt)') == 'v=5'


def test_transform_relative_holder():
    """
    ^ is the value being transformed, so ^^ is the node holding it.
    """
    data = {'items': [{'price': 100, 'rate': 1.5}, {'price': 20, 'rate': 2}]}
    assert dotted.get(data, 'items[*].price|mul:$$(^^rate)') == (150.0, 40)


def test_transform_relative_up_to_root():
    """
    Carets climb the tracked parents: value, item, list, root.
    """
    data = {'rate': 3, 'items': [{'price': 2}]}
    assert dotted.get(data, 'items[*].price|mul:$$(^^^^rate)') == (6,)


def test_transform_reference_on_wildcard():
    """
    The reference is resolved for every matched value.
    """
    data = {'scale': 10, 'a': 1, 'b': 2}
    assert dotted.get(data, '*|mul:$$(scale)') == (100, 10, 20)


def test_transform_unresolved_reference_matches_nothing():
    """
    A reference that does not resolve drops the value, as a missing key would.
    """
    assert dotted.get({'a': 1}, 'a|add:$$(missing)', default='fallback') == 'fallback'
    assert dotted.get({'a': 1, 'b': 2}, '*|add:$$(missing)') == ()


def test_transform_substitution_binding():
    """
    A $(name) substitution resolved through bindings is handed to the
    transform as its value.
    """
    assert dotted.get({'n': 5}, 'n|add:$(x)', bindings={'x': 3}) == 8


# ---- guard transforms and guard values ----

def test_guard_transform_root_reference():
    """
    The transforms before a guard take references.
    """
    data = {'scale': 10, 'a': 1, 'b': 2}
    assert dotted.get(data, '*|mul:$$(scale)=20') == (2,)


def test_guard_transform_relative_reference():
    """
    ^^ in a guard transform is the node holding the guarded value.
    """
    data = {'items': [{'p': 2, 'q': 3}, {'p': 2, 'q': 2}]}
    assert dotted.get(data, 'items[*].p|mul:$$(^^q)=4') == (2,)


def test_guard_value_root_reference():
    """
    A reference as the guard value.
    """
    data = {'x': 5, 'n': 5, 'm': 6}
    assert dotted.get(data, 'n=$$(x)') == 5
    assert dotted.get(data, 'm=$$(x)') is None


def test_guard_value_comparison_reference():
    """
    Comparison guards resolve the reference too.
    """
    data = {'limit': 10, 'a': 5, 'b': 15}
    assert dotted.get(data, '*>$$(limit)') == (15,)


def test_guard_value_not_equal_reference():
    """
    != against a reference.
    """
    data = {'x': 5, 'n': 5, 'm': 6}
    assert dotted.get(data, '*!=$$(x)') == (6,)


def test_guard_value_pattern_reference():
    """
    A reference path that is a pattern matches any of its values.
    """
    data = {'ok': {'a': 1, 'b': 2}, 'v': 2, 'w': 3}
    assert dotted.get(data, '*=$$(ok.*)') == (2,)


def test_guard_value_relative_reference():
    """
    ^^ in a guard value is the node holding the guarded value.
    """
    data = {'a': {'k': 1, 'n': 1}, 'b': {'k': 2, 'n': 3}}
    assert dotted.get(data, '*.n=$$(^^k)') == (1,)


def test_guard_value_unresolved_reference():
    """
    An unresolvable guard reference matches nothing.
    """
    assert dotted.get({'x': 5, 'n': 5}, 'n=$$(missing)', default='none') == 'none'
    assert dotted.get({'x': 5, 'n': 5}, 'n=$$(nope.*)', default='none') == 'none'


def test_update_guard_value_reference():
    """
    update honors a guard reference at the leaf.
    """
    data = {'x': 5, 'n': 5, 'm': 6}
    assert dotted.update(data, '*=$$(x)', 0) == {'x': 0, 'n': 0, 'm': 6}


def test_remove_guard_transform_reference():
    """
    remove honors a guard transform reference at the leaf.
    """
    data = {'x': 5, 'n': '5', 'm': '6'}
    assert dotted.remove(data, 'n|int=$$(x)') == {'x': 5, 'm': '6'}


def test_has_guard_value_reference():
    """
    has goes through the same guard.
    """
    assert dotted.has({'x': 5, 'n': 5}, 'n=$$(x)')


def test_recursive_guard_value_reference():
    """
    A guard reference on a recursive pattern.
    """
    data = {'sel': 2, 'a': {'id': 1, 'b': {'id': 2}}}
    assert dotted.get(data, '**.id=$$(sel)') == (2,)


# ---- filters ----

def test_filter_value_root_reference():
    """
    A reference as a filter value, in a slot filter and a key-value filter.
    """
    data = {'selected': 'bob', 'users': [{'name': 'alice'}, {'name': 'bob'}]}
    assert dotted.get(data, 'users[*&name=$$(selected)].name') == ('bob',)
    assert dotted.get(data, 'users[name=$$(selected)]') == [{'name': 'bob'}]


def test_filter_transform_root_reference():
    """
    A reference in a filter transform.
    """
    data = {'scale': 10, 'items': [{'v': 2}, {'v': 3}]}
    assert dotted.get(data, 'items[*&v|mul:$$(scale)=30].v') == (3,)


def test_filter_value_relative_reference():
    """
    ^^ in a filter value is the item being filtered; more carets climb from there.
    """
    data = {'items': [{'v': 2, 'w': 2}, {'v': 3, 'w': 4}]}
    assert dotted.get(data, 'items[*&v=$$(^^w)].v') == (2,)
    data = {'w': 3, 'items': [{'v': 2}, {'v': 3}]}
    assert dotted.get(data, 'items[*&v=$$(^^^^w)].v') == (3,)


def test_filter_comparison_reference():
    """
    Comparison filters resolve the reference.
    """
    data = {'min': 2, 'items': [{'v': 2}, {'v': 3}]}
    assert dotted.get(data, 'items[*&v>$$(min)].v') == (3,)


def test_filter_negation_reference():
    """
    A negated filter against a reference.
    """
    data = {'sel': 'bob', 'users': [{'name': 'alice'}, {'name': 'bob'}]}
    assert dotted.get(data, 'users[*&!name=$$(sel)].name') == ('alice',)


def test_recursive_filter_reference():
    """
    A filter reference on a recursive pattern.
    """
    data = {'sel': 2, 'a': {'id': 1, 'b': {'id': 2}}}
    assert dotted.get(data, '**&id=$$(sel)') == ({'id': 2},)


# ---- update and apply ----

def test_update_transform_reference():
    """
    The transforms apply to the value being set; a root reference resolves
    against the object, an unresolvable one leaves the object as is.
    """
    assert dotted.update({'n': 5, 'inc': 3}, 'n|add:$$(inc)', 10) == {'n': 13, 'inc': 3}
    assert dotted.update({'n': 5}, 'n|add:$$(missing)', 10) == {'n': 5}


def test_apply_transform_reference():
    """
    apply() resolves references in the transforms it applies in place.
    """
    assert dotted.apply({'n': '5', 'inc': 2}, 'n|int|add:$$(inc)') == {'n': 7, 'inc': 2}


def test_concat_part_transform_reference():
    """
    A per-part transform in a concat takes a reference.
    """
    data = {'i': 0, 'inc': 1, 'x': ['a', 'b']}
    assert dotted.get(data, 'x[$$(i)|add:$$(inc)+0]') == 'b'


# ---- parsing ----

def test_transform_is_reference():
    """
    A transform with a reference argument reports it and assembles back.
    """
    ops = parse('n|add:$$(config.offset)')
    assert ops.transforms[0].is_reference()
    assert ops.assemble() == 'n|add:$$(config.offset)'


def test_needs_parents():
    """
    Parents are tracked when a reference in a transform or guard reaches
    above the value under test; not for a root reference.
    """
    assert parse('a.b|add:$$(^^x)').needs_parents
    assert not parse('a.b|add:$$(x)').needs_parents
    assert parse('a.b=$$(x)').needs_parents
