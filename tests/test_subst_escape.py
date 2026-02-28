"""
Tests for $N substitution escaping and is_template API.
"""
import dotted
from dotted.api import parse, assemble, normalize
from dotted.match import Word, PositionalSubst


# ---- escaping: \$ produces literal dollar-sign keys ----

def test_parse_escaped_dollar_is_word():
    ops = parse('\\$0')
    assert isinstance(ops[0].op, Word)
    assert ops[0].op.value == '$0'


def test_parse_escaped_dollar_bare():
    ops = parse('\\$')
    assert isinstance(ops[0].op, Word)
    assert ops[0].op.value == '$'


def test_parse_escaped_dollar_multi_digit():
    ops = parse('\\$12')
    assert isinstance(ops[0].op, Word)
    assert ops[0].op.value == '$12'


def test_parse_raw_subst_still_works():
    ops = parse('$0')
    assert isinstance(ops[0].op, PositionalSubst)
    assert ops[0].op.value == 0


def test_roundtrip_escaped_dollar():
    assert assemble(parse('\\$0')) == '\\$0'


def test_roundtrip_escaped_dollar_bare():
    assert assemble(parse('\\$')) == '\\$'


def test_roundtrip_escaped_dollar_nested():
    assert assemble(parse('a.\\$1.b')) == 'a.\\$1.b'


def test_get_dollar_key():
    assert dotted.get({'$0': 'hello'}, '\\$0') == 'hello'


def test_get_dollar_key_nested():
    assert dotted.get({'a': {'$1': 'val'}}, 'a.\\$1') == 'val'


def test_get_bare_dollar_key():
    assert dotted.get({'$': 'x'}, '\\$') == 'x'


def test_normalize_dollar_prefix():
    assert normalize('$0') == '\\$0'


def test_normalize_dollar_bare():
    assert normalize('$') == '\\$'


def test_normalize_mid_dollar_quoted():
    assert normalize('foo$bar') == "'foo$bar'"


def test_normalize_angle_bracket_quoted():
    assert normalize('a<b') == "'a<b'"


def test_unpack_dollar_key():
    result = dict(dotted.unpack({'$0': 'x'}))
    assert result == {'\\$0': 'x'}


# ---- is_template ----

def test_is_template_simple():
    assert dotted.is_template('a.$0') is True


def test_is_template_slot():
    assert dotted.is_template('items[$0]') is True


def test_is_template_plain():
    assert dotted.is_template('a.b.c') is False


def test_is_template_pattern():
    assert dotted.is_template('a.*') is False


def test_is_template_escaped():
    assert dotted.is_template('\\$0') is False


def test_is_template_multi():
    assert dotted.is_template('$0.$1.$2') is True


def test_is_template_parsed():
    parsed = parse('a.$0')
    assert dotted.is_template(parsed) is True
