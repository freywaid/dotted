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
    r = dotted.setdefault(ns, '@existing', 'new')
    assert r == 'value'
    assert ns.existing == 'value'
    r = dotted.setdefault(ns, '@new_attr', 'created')
    assert r == 'created'
    assert ns.new_attr == 'created'


def test_attr_with_transform():
    ns = types.SimpleNamespace()
    ns.count = '42'
    result = dotted.get(ns, '@count|int')
    assert result == 42


def test_format_path_attr_segment():
    """_format_path consumes (op, k) segments and renders obj@attr (no dot before @)."""
    from dotted import engine
    from dotted.access import Key, Attr, Slot
    from dotted.match import Word, Wildcard
    # Segments: (Key, 'obj'), (Attr, 'name') -> obj@name
    path = [(Key(Word('obj')), 'obj'), (Attr(Word('name')), 'name')]
    assert engine._format_path(path) == 'obj@name'
    # Key, Key, Attr -> obj.foo@name
    path2 = [
        (Key(Word('obj')), 'obj'),
        (Key(Word('foo')), 'foo'),
        (Attr(Word('name')), 'name'),
    ]
    assert engine._format_path(path2) == 'obj.foo@name'
    # First segment Attr -> @name (no leading dot)
    path3 = [(Attr(Word('name')), 'name')]
    assert engine._format_path(path3) == '@name'
    # Slot segment -> [0]
    path4 = [(Key(Word('items')), 'items'), (Slot(Wildcard()), 0)]
    assert engine._format_path(path4) == 'items[0]'


# =============================================================================
# Attribute group access: @(a,b)
# =============================================================================

def test_attr_group_get():
    """
    @(a,b) accesses multiple attributes.
    """
    ns = types.SimpleNamespace(a=1, b=2, c=3)
    assert dotted.get(ns, '@(a,b)') == (1, 2)
    assert dotted.get(ns, '@(a,b)') == dotted.get(ns, '(@a,@b)')


def test_attr_group_conjunction():
    """
    @(a&b) conjunction of attrs.
    """
    ns = types.SimpleNamespace(a=1, b=2, c=3)
    assert dotted.get(ns, '@(a&b)') == dotted.get(ns, '(@a&@b)')


def test_attr_group_negation():
    """
    @(!a) negation of attr.
    """
    ns = types.SimpleNamespace(a=1, b=2, c=3)
    assert dotted.get(ns, '@(!a)') == dotted.get(ns, '(!@a)')
    assert set(dotted.get(ns, '@(!a)')) == {2, 3}


def test_attr_group_nested():
    """
    prefix@(a,b) accesses attrs from prefix.
    """
    ns = types.SimpleNamespace(inner=types.SimpleNamespace(x=10, y=20, z=30))
    assert dotted.get(ns, '@inner@(x,y)') == (10, 20)


def test_attr_group_first():
    """
    @(a,b)? first-match version.
    """
    ns = types.SimpleNamespace(b=2, c=3)
    assert dotted.get(ns, '@(a,b)?') == dotted.get(ns, '(@a,@b)?')


def test_attr_group_update():
    """
    @(a,b) update multiple attrs.
    """
    ns = types.SimpleNamespace(a=1, b=2, c=3)
    dotted.update(ns, '@(a,b)', 0)
    assert ns.a == 0
    assert ns.b == 0
    assert ns.c == 3


def test_attr_group_assemble():
    """
    @(a,b) round-trips through assemble.
    """
    parsed = dotted.parse('@(a,b)')
    assembled = dotted.assemble(parsed)
    # Round-trip stability: parse(assembled) produces same assembled form
    assert dotted.assemble(dotted.parse(assembled)) == assembled
