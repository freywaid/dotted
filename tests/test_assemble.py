import pytest
import dotted


def test_assemble_wildcard():
    parsed = dotted.parse('*')
    assembled = dotted.assemble(parsed)
    assert assembled == '*'


def test_assemble_appender():
    parsed = dotted.parse('[+]')
    assembled = dotted.assemble(parsed)
    assert assembled == '[+]'


def test_assemble_regex():
    parsed = dotted.parse('/A.+/')
    assembled = dotted.assemble(parsed)
    assert assembled == '/A.+/'
