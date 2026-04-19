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


# ---- quote ----

def test_quote_named_subst_literal():
    assert quote('$(name)') == "'$(name)'"


# ---- mixed positional and named ----

def test_replace_mixed():
    assert replace('$0.$(key)', ['root'], partial=True) == 'root.$(key)'
