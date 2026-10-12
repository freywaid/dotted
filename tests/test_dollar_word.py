"""
Tests for $str: a $ before a word is a plain string, as ^str is. $0,
$(name) and $$(path) keep their shapes.
"""
import dotted
from dotted.api import parse
from dotted.matchers import Word


def test_dollar_word_is_a_key():
    """
    $str parses as the literal key '$str' and round-trips quoted.
    """
    ops = parse('$str')
    assert isinstance(ops[0].op, Word) and ops[0].op.value == '$str'
    assert ops.assemble() == "'$str'"
    assert dotted.get({'$str': 1}, '$str') == 1
    assert dotted.get({'$str': 1}, ops.assemble()) == 1


def test_dollar_word_in_segments():
    """
    Keys, slots and attrs take it, and the path continues past it.
    """
    d = {'a': {'$amount': {'value': 2}}}
    assert dotted.get(d, 'a.$amount.value') == 2
    assert dotted.get(d, 'a[$amount].value') == 2
    assert dotted.update(d, 'a.$amount.value', 3) == {'a': {'$amount': {'value': 3}}}


def test_dollar_word_as_a_value():
    """
    As a guard value or transform argument it is the string.
    """
    d = {'k': '$x', 'n': None}
    assert dotted.get(d, 'k=$x') == '$x'
    assert dotted.get(d, 'k=$y') is None
    assert dotted.get(d, 'n|coalesce:$fallback') == '$fallback'


def test_dollar_shapes_are_distinct():
    """
    $(name) still substitutes and $$(path) still references.
    """
    d = {'$str': 1, 'k': '$str'}
    assert dotted.get(d, '$(x)', bindings={'x': '$str'}) == 1
    assert dotted.get(d, '$$(k)') == 1
    assert not dotted.is_template('$str')
    assert not dotted.is_reference('$str')
