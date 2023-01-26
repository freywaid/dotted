import pytest
import dotted



def test_get_key():
    d = {'hello': {'there': [1, '2', 3]}}
    r = dotted.get(d, 'hello.there')
    assert r == [1, '2', 3]


def test_get_slot():
    r = dotted.get({}, 'hello[*]')
