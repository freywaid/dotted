import dotted
import pytest


def test_update_list():
    r = dotted.update([], '[0]', 'hello')
    assert r == ['hello']


def test_update_tuple():
    r = dotted.update((), '[0]', 'hello')
    assert r == ('hello',)
