"""
Tests for $(name) named substitutions.
"""
import pytest
import dotted
from dotted.api import parse, assemble, replace, quote
from dotted.matchers import Subst, Word


# ---- parsing ----

def test_parse_named_subst():
    ops = parse('$(name)')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 'name'


def test_parse_named_subst_underscore():
    ops = parse('$(my_key)')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 'my_key'


def test_parse_named_subst_nested():
    ops = parse('a.$(key).b')
    assert len(ops) == 3
    assert isinstance(ops[1].op, Subst)
    assert ops[1].op.value == 'key'


def test_parse_named_repr():
    ops = parse('$(name)')
    assert repr(ops[0].op) == '$(name)'


# ---- escaping ----

def test_parse_escaped_named():
    ops = parse('\\$(name)')
    assert isinstance(ops[0].op, Word)
    assert ops[0].op.value == '$(name)'


# ---- assemble round-trip ----

def test_assemble_named():
    assert assemble(parse('a.$(key).b')) == 'a.$(key).b'


# ---- replace ----

def test_replace_named():
    assert replace('$(name).$(attr)', {'name': 'users', 'attr': 'email'}) == 'users.email'


def test_replace_named_partial():
    assert replace('$(name).$(attr)', {'name': 'users'}, partial=True) == 'users.$(attr)'


def test_replace_named_missing_raises():
    with pytest.raises(KeyError):
        replace('$(name)', {})


# ---- dotted-path lookup in named subst ----

def test_replace_dotted_subst():
    # $(a.b) resolves via nested lookup in bindings
    assert replace('prefix.$(a.b)', {'a': {'b': 'val'}}) == 'prefix.val'


def test_replace_dotted_subst_deep():
    assert replace('$(x.y.z)', {'x': {'y': {'z': 42}}}) == '42'


def test_replace_dotted_subst_falls_back_to_literal():
    # When nested lookup finds nothing, a literal dotted key still works
    assert replace('$(a.b)', {'a.b': 'literal'}) == 'literal'


def test_replace_dotted_subst_nested_wins_over_literal():
    # Nested match is preferred over literal key
    assert replace('$(a.b)', {'a.b': 'literal', 'a': {'b': 'nested'}}) == 'nested'


def test_replace_dotted_subst_missing_raises():
    with pytest.raises(KeyError):
        replace('$(a.b)', {'a': {}})


def test_replace_dotted_subst_partial():
    assert replace('$(a.b)', {}, partial=True) == '$(a.b)'


def test_replace_dotted_subst_with_transform():
    assert replace('$(a.b|int)', {'a': {'b': '42'}}) == '42'


# ---- is_template ----

def test_is_template_named():
    assert dotted.is_template('a.$(name)') is True


def test_is_template_named_escaped():
    assert dotted.is_template('\\$(name)') is False


# ---- is_template catches substs in all positions ----

def test_is_template_guard_value():
    # Subst as guard RHS (previously missed)
    assert dotted.is_template('a=$(x)') is True
    assert dotted.is_template('a>=$(x)') is True
    assert dotted.is_template('a!=$(x)') is True


def test_is_template_guard_transform():
    # Subst combined with guard cast
    assert dotted.is_template('a|int=$(x)') is True


def test_is_template_filter_wrap_value():
    # Subst inside filter-wrap predicate value
    assert dotted.is_template('a[*&x=$(y)]') is True


def test_is_template_filter_wrap_key():
    # Subst as filter key
    assert dotted.is_template('a[$(k)=1]') is True


def test_is_template_filter_wrap_transform():
    # Subst with filter transform
    assert dotted.is_template('a[*&x|int=$(y)]') is True


def test_is_template_filter_negation():
    # Subst under filter negation
    assert dotted.is_template('a[!x=$(y)]') is True


def test_is_template_filter_group():
    # Subst inside filter group (AND/OR of predicates)
    assert dotted.is_template('a[*&(b=$(x) & c=1)]') is True


def test_is_template_slice_filter():
    # SliceFilter (compact [filter] form) carrying a subst
    assert dotted.is_template('a[$(k)=1]') is True


def test_is_template_group_branch():
    # Subst in an access-op group branch
    assert dotted.is_template('(a=$(x) & b=1)') is True


def test_is_template_false_for_refs():
    # References are not templates (resolve against data, not bindings)
    assert dotted.is_template('a.$$(b)') is False
    assert dotted.is_template('a=$$(b)') is False


def test_is_template_false_for_patterns():
    # Plain patterns are not templates
    assert dotted.is_template('a.*.b') is False
    assert dotted.is_template('a=/re/') is False


# ---- is_pattern: substitutions are NOT patterns (single-yield) ----

def test_is_pattern_false_for_subst_in_access():
    # $(x) as an access-position matcher is not a pattern (yields one key).
    assert dotted.is_pattern('a.$(x)') is False
    assert dotted.is_pattern('a.$0') is False


def test_is_pattern_false_for_subst_in_guard():
    # Concrete access with subst guard is still not a pattern
    assert dotted.is_pattern('a=$(x)') is False


# ---- quote ----

def test_quote_named_subst_literal():
    assert quote('$(name)') == "'$(name)'"


# ---- mixed positional and named ----

def test_replace_mixed():
    assert replace('$0.$(key)', ['root'], partial=True) == 'root.$(key)'
