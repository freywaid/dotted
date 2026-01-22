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

    # try removing again (idempotent)
    dotted.remove(ns, '@hello')
    assert not hasattr(ns, 'hello')


def test_attr_remove_nested():
    ns = types.SimpleNamespace()
    ns.inner = types.SimpleNamespace()
    ns.inner.value = 42
    ns.inner.other = 99
    dotted.remove(ns, '@inner@value')
    assert not hasattr(ns.inner, 'value')
    assert ns.inner.other == 99


def test_attr_remove_with_value():
    ns = types.SimpleNamespace()
    ns.hello = 'there'
    ns.bye = 'world'
    # only remove if value matches
    dotted.remove(ns, '@hello', 'wrong')
    assert ns.hello == 'there'  # unchanged
    dotted.remove(ns, '@hello', 'there')
    assert not hasattr(ns, 'hello')


def test_attr_update_pattern():
    ns = types.SimpleNamespace()
    ns.a = 1
    ns.b = 2
    ns.c = 3
    dotted.update(ns, '@*', 0)
    assert ns.a == 0
    assert ns.b == 0
    assert ns.c == 0


def test_attr_remove_pattern():
    ns = types.SimpleNamespace()
    ns.a = 1
    ns.b = 2
    ns.c = 3
    dotted.remove(ns, '@*')
    assert not hasattr(ns, 'a')
    assert not hasattr(ns, 'b')
    assert not hasattr(ns, 'c')


def test_attr_update_nested():
    ns = types.SimpleNamespace()
    ns.inner = types.SimpleNamespace()
    dotted.update(ns, '@inner@value', 42)
    assert ns.inner.value == 42


def test_attr_update_create():
    ns = types.SimpleNamespace()
    dotted.update(ns, '@config.debug', True)
    assert ns.config == {'debug': True}


def test_attr_match():
    assert dotted.match('@*', '@hello') == '@hello'
    assert dotted.match('@*@*', '@foo@bar') == '@foo@bar'
    assert dotted.match('@hello', '@hello') == '@hello'
    assert dotted.match('@hello', '@bye') is None


def test_attr_match_mixed():
    # attr followed by key
    assert dotted.match('@*.key', '@foo.key') == '@foo.key'
    assert dotted.match('@foo.*', '@foo.bar') == '@foo.bar'


def test_attr_expand():
    ns = types.SimpleNamespace()
    ns.a = 1
    ns.b = 2
    ns.c = 3
    result = dotted.expand(ns, '@*')
    assert set(result) == {'@a', '@b', '@c'}


def test_attr_expand_nested():
    ns = types.SimpleNamespace()
    ns.inner = types.SimpleNamespace()
    ns.inner.x = 10
    ns.inner.y = 20
    result = dotted.expand(ns, '@inner@*')
    assert set(result) == {'@inner@x', '@inner@y'}


def test_attr_has():
    ns = types.SimpleNamespace()
    ns.hello = 'there'
    assert dotted.has(ns, '@hello') is True
    assert dotted.has(ns, '@missing') is False
    assert dotted.has(ns, '@*') is True


def test_attr_setdefault():
    ns = types.SimpleNamespace()
    ns.existing = 'value'
    dotted.setdefault(ns, '@existing', 'new')
    assert ns.existing == 'value'
    dotted.setdefault(ns, '@new_attr', 'created')
    assert ns.new_attr == 'created'


def test_attr_with_transform():
    ns = types.SimpleNamespace()
    ns.count = '42'
    result = dotted.get(ns, '@count|int')
    assert result == 42
