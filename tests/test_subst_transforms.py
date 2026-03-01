"""
Tests for substitution transforms: $(name|transform), $(0|transform).
"""
from dotted.api import replace, is_template, parse
from dotted.matchers import Subst
from dotted.results import assemble


# ---- parsing ----

def test_parse_named_with_transform():
    """
    $(name|int) parses as Subst with int transform.
    """
    ops = parse('$(name|int)')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 'name'
    assert len(ops[0].op.transforms) == 1
    assert ops[0].op.transforms[0].name == 'int'


def test_parse_named_no_transform():
    """
    $(name) still parses as Subst with no transforms.
    """
    ops = parse('$(name)')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 'name'
    assert ops[0].op.transforms == ()


def test_parse_numeric_paren_with_transform():
    """
    $(0|str) parses as Subst with str transform.
    """
    ops = parse('$(0|str)')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 0
    assert len(ops[0].op.transforms) == 1
    assert ops[0].op.transforms[0].name == 'str'


def test_parse_numeric_paren_no_transform():
    """
    $(0) parses as Subst with no transforms.
    """
    ops = parse('$(0)')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 0
    assert ops[0].op.transforms == ()


def test_parse_raw_positional():
    """
    $0 still parses as Subst with no transforms.
    """
    ops = parse('$0')
    assert isinstance(ops[0].op, Subst)
    assert ops[0].op.value == 0
    assert ops[0].op.transforms == ()


def test_parse_multiple_transforms():
    """
    $(name|strip|lowercase) parses with two transforms.
    """
    ops = parse('$(name|strip|lowercase)')
    assert isinstance(ops[0].op, Subst)
    assert len(ops[0].op.transforms) == 2
    assert ops[0].op.transforms[0].name == 'strip'
    assert ops[0].op.transforms[1].name == 'lowercase'


# ---- resolve with transforms ----

def test_resolve_named_with_int_transform():
    """
    $(name|int) resolves and applies int transform.
    """
    result = replace('$(name|int)', {'name': '42'})
    assert result == '42'


def test_resolve_named_with_str_transform():
    """
    $(name|str) resolves and applies str transform.
    """
    result = replace('$(name|str)', {'name': 123})
    assert result == '123'


def test_resolve_numeric_with_transform():
    """
    $(0|uppercase) resolves positional and applies transform.
    """
    result = replace('$(0|uppercase)', ['hello'])
    assert result == 'HELLO'


def test_resolve_raw_positional_no_transform():
    """
    $0 resolves against list bindings as before.
    """
    result = replace('prefix.$0', ['world'])
    assert result == 'prefix.world'


# ---- Subst against dict bindings ----

def test_numeric_subst_dict_bindings():
    """
    $(0) against dict bindings looks up numeric key 0.
    """
    result = replace('$(0)', {0: 'zero'})
    assert result == 'zero'


# ---- round-trip ----

def test_repr_named_with_transform():
    """
    Subst with transforms repr includes the suffix.
    """
    ops = parse('$(name|int)')
    assert repr(ops[0].op) == '$(name|int)'


def test_repr_numeric_with_transform():
    """
    Subst with transforms uses paren form.
    """
    ops = parse('$(0|str)')
    assert repr(ops[0].op) == '$(0|str)'


def test_repr_numeric_no_transform():
    """
    Subst without transforms uses bare $N form.
    """
    ops = parse('$0')
    assert repr(ops[0].op) == '$0'


def test_assemble_named_with_transform():
    """
    Assembling a path with $(name|int) round-trips.
    """
    ops = parse('a.$(name|int).b')
    assert assemble(ops) == 'a.$(name|int).b'


def test_assemble_numeric_with_transform():
    """
    Assembling a path with $(0|str) round-trips.
    """
    ops = parse('a.$(0|str).b')
    assert assemble(ops) == 'a.$(0|str).b'


# ---- is_template ----

def test_is_template_with_transform():
    """
    $(name|int) is still a template.
    """
    assert is_template('$(name|int)')


def test_is_template_numeric_with_transform():
    """
    $(0|str) is still a template.
    """
    assert is_template('$(0|str)')
