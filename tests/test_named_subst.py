"""
Tests for $(name) named substitutions.
"""
import pytest
import dotted
from dotted.api import parse, assemble, replace, quote
from dotted.matchers import NamedSubst, Word


# ---- parsing ----

def test_parse_named_subst():
    ops = parse('$(name)')
    assert isinstance(ops[0].op, NamedSubst)
    assert ops[0].op.value == 'name'


def test_parse_named_subst_underscore():
    ops = parse('$(my_key)')
    assert isinstance(ops[0].op, NamedSubst)
    assert ops[0].op.value == 'my_key'


def test_parse_named_subst_nested():
    ops = parse('a.$(key).b')
    assert len(ops) == 3
    assert isinstance(ops[1].op, NamedSubst)
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
