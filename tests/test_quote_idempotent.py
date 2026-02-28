"""
Tests that quote() is idempotent: quote(quote(x)) == quote(x).
"""
from dotted.api import quote


def test_idempotent_dotted_path():
    assert quote(quote('a.b')) == quote('a.b')


def test_idempotent_dollar_prefix():
    assert quote(quote('$0')) == quote('$0')


def test_idempotent_space():
    assert quote(quote('foo bar')) == quote('foo bar')


def test_idempotent_empty():
    assert quote(quote('')) == quote('')


def test_idempotent_float():
    q = quote(3.14)
    assert quote(q) == q


def test_idempotent_numeric_quoted():
    q = quote("#'3.14'")
    assert quote(q) == q


def test_plain_word_unchanged():
    assert quote('hello') == 'hello'


def test_idempotent_leading_zeros():
    assert quote(quote('007')) == quote('007')
