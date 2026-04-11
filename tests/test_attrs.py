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
    from dotted.matchers import Word, Wildcard
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
# Attr with filters
# =============================================================================

def test_attr_with_filter():
    """
    @*&filter filters attribute access by child values.
    """
    ns = types.SimpleNamespace(a={'x': 1}, b={'x': 2}, c={'x': 3})
    assert dotted.get(ns, '@*&x>1') == ({'x': 2}, {'x': 3})


def test_attr_with_filter_nested():
    """
    @name followed by slot filter.
    """
    data = {'items': [{'x': 1}, {'x': 2}]}
    ns = types.SimpleNamespace(**data)
    result = dotted.get(ns, '@items[*&x>1]')
    assert result == ({'x': 2},)


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


def test_attr_group_cut():
    """
    @(a#, b) with cut marker: if a matches, don't try b.
    """
    ns = types.SimpleNamespace(a=1, b=2)
    # a matches, so cut stops before b
    result = dotted.get(ns, '@(a#, b)')
    assert result == (1,)

    # a missing, so falls through to b
    ns2 = types.SimpleNamespace(b=2)
    result2 = dotted.get(ns2, '@(a#, b)')
    assert result2 == (2,)


def test_attr_group_softcut():
    """
    @(a##, b) with soft cut.
    """
    ns = types.SimpleNamespace(a=1, b=2)
    result = dotted.get(ns, '@(a##, b)')
    assert result == (1, 2)


# =============================================================================
# Concrete attr fallback (__getattr__-based objects)
# =============================================================================

class _DynAttrs:
    """
    Mimics Stripe-like objects: data in _data dict, exposed via __getattr__.
    """
    def __init__(self, **kw):
        self._data = dict(kw)

    def __getattr__(self, k):
        if k.startswith('_'):
            raise AttributeError(k)
        try:
            return self._data[k]
        except KeyError as e:
            raise AttributeError(*e.args) from e

    def __getitem__(self, k):
        return self._data[k]

    def __contains__(self, k):
        return k in self._data


def test_attr_concrete_getattr_fallback():
    """
    Concrete @attr access works on __getattr__-based objects.
    """
    obj = _DynAttrs(name='Alice', email='a@b.com')
    assert dotted.get(obj, '@name') == 'Alice'
    assert dotted.get(obj, '@email') == 'a@b.com'


def test_attr_concrete_getattr_missing():
    """
    Missing concrete @attr on __getattr__ object returns None (safe access).
    """
    obj = _DynAttrs(name='Alice')
    assert dotted.get(obj, '@missing') is None


def test_attr_concrete_getattr_nested():
    """
    Nested access through __getattr__ objects.
    """
    inner = _DynAttrs(value=42)
    outer = {'obj': inner}
    assert dotted.get(outer, 'obj@value') == 42


def test_key_concrete_getitem_no_keys():
    """
    Concrete .key access works on objects with __getitem__+__contains__ but no keys().
    """
    obj = _DynAttrs(name='Alice', email='a@b.com')
    assert dotted.get(obj, '.name') == 'Alice'
    assert dotted.get(obj, '.email') == 'a@b.com'


def test_key_concrete_getitem_missing():
    """
    Missing concrete .key on __getitem__ object returns None.
    """
    obj = _DynAttrs(name='Alice')
    assert dotted.get(obj, '.missing') is None


# =============================================================================
# __slots__ support
# =============================================================================

class _Slotted:
    __slots__ = ('x', 'y')

    def __init__(self, x, y):
        self.x = x
        self.y = y


class _SlottedChild(_Slotted):
    __slots__ = ('z',)

    def __init__(self, x, y, z):
        super().__init__(x, y)
        self.z = z


def test_attr_slots_concrete():
    """
    Concrete @attr access works on __slots__ classes.
    """
    obj = _Slotted(1, 2)
    assert dotted.get(obj, '@x') == 1
    assert dotted.get(obj, '@y') == 2


def test_attr_slots_pattern():
    """
    @* pattern enumerates __slots__ attributes.
    """
    obj = _Slotted(1, 2)
    assert set(dotted.get(obj, '@*')) == {1, 2}


def test_attr_slots_inherited():
    """
    @* pattern includes slots from parent classes.
    """
    obj = _SlottedChild(1, 2, 3)
    assert set(dotted.get(obj, '@*')) == {1, 2, 3}


def test_attr_slots_update():
    """
    @attr update works on __slots__ classes.
    """
    obj = _Slotted(1, 2)
    dotted.update(obj, '@x', 10)
    assert obj.x == 10


def test_attr_slots_expand():
    """
    expand() discovers __slots__ attributes.
    """
    obj = _Slotted(1, 2)
    result = dotted.expand(obj, '@*')
    assert set(result) == {'@x', '@y'}


def test_attr_slots_remove():
    """
    @attr remove works on __slots__ classes.
    """
    obj = _Slotted(1, 2)
    dotted.remove(obj, '@x')
    assert not hasattr(obj, 'x')
    assert obj.y == 2


class _MixedSlots:
    __slots__ = ('x', '__dict__')

    def __init__(self, x, **kw):
        self.x = x
        self.__dict__.update(kw)


def test_attr_mixed_slots_and_dict():
    """
    @* enumerates both __slots__ and __dict__ attributes.
    """
    obj = _MixedSlots(1, y=2, z=3)
    assert set(dotted.get(obj, '@*')) == {1, 2, 3}


def test_dynattrs_pattern_returns_internals():
    """
    @* on __getattr__ objects enumerates __dict__ (internal attrs),
    not the dynamic keys.  This is the expected limitation.
    """
    obj = _DynAttrs(name='Alice', email='a@b.com')
    result = dotted.get(obj, '@*')
    # Gets the internal _data dict, not the user-facing keys
    assert result == ({'name': 'Alice', 'email': 'a@b.com'},)


def test_attr_concrete_fallback_with_filter():
    """
    Concrete @attr fallback respects filters.
    """
    obj = _DynAttrs(x={'v': 5}, y={'v': 10})
    # Filter should pass
    result = dotted.get({'obj': obj}, 'obj@x&v>3')
    assert result == {'v': 5}
    # Filter should reject
    result = dotted.get({'obj': obj}, 'obj@x&v>10')
    assert result is None


def test_namedtuple_attr_access():
    """
    Namedtuple attr access still works after isinstance(tuple) guard.
    """
    from collections import namedtuple
    Pt = namedtuple('Pt', ['x', 'y'])
    p = Pt(3, 4)
    assert dotted.get(p, '@x') == 3
    assert dotted.get(p, '@y') == 4
    assert set(dotted.get(p, '@*')) == {3, 4}
    assert set(dotted.expand(p, '@*')) == {'@x', '@y'}
