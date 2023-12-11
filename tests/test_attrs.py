import dotted
import pytest
import types


def test_attr_get():
    ns = types.SimpleNamespace()
    ns.hello = 'there'
    assert dotted.get(ns, '@hello') == 'there'

    ns.moar = dict(even='stuff')
    assert dotted.get(ns, '@moar.even') == 'stuff'

    ns2 = types.SimpleNamespace()
    ns2.lucky = 7
    ns.extra = ns2
    assert dotted.get(ns, '@extra@lucky') == 7

    # test chain
    ns.foo = dict(bar=ns2)
    assert dotted.get(ns, '@foo.bar@lucky') == 7

    # test pattern
    assert dotted.get(ns, '@*') == ('there', dict(even='stuff'), ns2, dict(bar=ns2))
    assert dotted.pluck(ns, '@*@*') == (('@extra@lucky', 7),)


def test_attr_update():
    ns = types.SimpleNamespace()
    dotted.update(ns, '@hello', 'there')
    assert ns.hello == 'there'


def test_attr_remove():
    ns = types.SimpleNamespace()
    ns.hello = 'there'
    ns.good = 'bye'
    dotted.remove(ns, '@hello')
    assert not hasattr(ns, 'hello')

    # try removing again
    dotted.remove(ns, '@hello')
    assert not hasattr(ns, 'hello')
