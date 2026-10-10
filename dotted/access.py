"""
"""
import functools
import itertools
import numbers
import types

from . import base
from . import matchers
from . import utils


def itemof(node, val):
    return val if isinstance(node, (str, bytes)) else node.__class__([val])


class BaseOp(base.TraversalOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = ()

    def match(self, op):
        results = ()
        for f, of in zip(self.filters, op.filters):
            if not f.matchable(of):
                return None
            m = f.match(of)
            if m is None:
                return None
            results += (base.MatchResult(m),)
        return results

    def filtered(self, items, settings=base.SETTINGS):
        """
        items through this op's filters, under the Settings of the items
        (see filters.FilterOp).
        """
        for f in self.filters:
            items = f.filtered(items, settings)
        return items

    def keys(self, node, settings=base.SETTINGS):
        return (k for k, _ in self.items(node, settings=settings))

    def values(self, node, settings=base.SETTINGS):
        return (v for _, v in self.items(node, settings=settings))

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        if not ops:
            if nop:
                return node
            if settings.strict and not any(True for _ in self.items(node, settings=settings)):
                return node
            return self.upsert(node, val)
        items = self.update_items(node, settings=settings)
        if items is None:
            if not has_defaults:
                if nop or isinstance(ops[0], wrappers.NopWrap):
                    return node
                built = engine.updates(ops, engine.build_default(ops), val, True, _path, nop, settings=settings)
                return self.upsert(node, built)
            items = self.items(node, settings=settings)
        inner = settings.below(node)
        pass_nop = nop and not nop_from_unwrap
        for k, v in items:
            if v is None:
                v = engine.build_default(ops)
            node = self.update(node, k, engine.updates(ops, v, val, has_defaults, _path + [(self, k)], pass_nop, settings=inner))
        return node

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        if not ops:
            if nop:
                return node
            if settings.strict and not any(True for _ in self.items(node, settings=settings)):
                return node
            return self.remove(node, val)
        inner = settings.below(node)
        for k, v in self.items(node, settings=settings):
            node = self.update(node, k, engine.removes(ops, v, val, nop=False, settings=inner))
        return node



class SimpleOp(BaseOp):
    """
    Base for ops with items()/concrete() that share the standard walk pattern.
    """

    def items(self, node, settings=base.SETTINGS, filtered=True):
        """
        (key, value) for each match of this op in node.
        """
        raise NotImplementedError

    def push_children(self, stack, frame, paths):
        """
        Push matching children onto the traversal stack.
        Reverse order so first match is popped first (LIFO).

        NOTE: We tried skipping list()+reversed() for concrete (non-pattern)
        keys since they match at most one item.  Benchmarked as neutral —
        the is_pattern() check costs roughly what the single-item reversal
        saves.  Keeping the simple uniform path for clarity.
        """
        children = list(self.items(frame.node, settings=frame.settings))
        below = frame.settings.below(frame.node)
        for k, v in reversed(children):
            cp = frame.prefix + (self.concrete(k),) if paths else frame.prefix
            stack.push(base.Frame(frame.ops, v, cp, settings=below))
        return ()


class Empty(SimpleOp):
    """
    Represents an empty path - the root of the data structure.

    Examples:
        get(data, '')      → returns data itself
        update(data, '', v) → replaces root with v
        remove(data, '')   → returns None (root removed)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.args

    def __repr__(self):
        return '.'.join(repr(f) for f in self.filters)

    def is_pattern(self):
        return False

    def is_template(self):
        """
        True if this op contains substitution references.
        """
        return False

    def is_empty(self, node):
        return False

    def operator(self, top=False):
        return self.__repr__()

    def items(self, node, settings=base.SETTINGS, filtered=True):
        """
        The root as a single item with empty key.
        """
        return self._root_items(node, settings)

    def _root_items(self, node, settings=base.SETTINGS):
        for v in self.filtered((node,), settings):
            yield ('', v)

    def keys(self, node, settings=base.SETTINGS):
        """
        Yield empty string as the 'key' for root.
        """
        items = self.items(node, settings=settings)
        return (k for k, _ in items)

    def values(self, node, settings=base.SETTINGS):
        return self.filtered((node,), settings)

    def default(self):
        return None

    def update(self, node, key, val):
        """
        Replace root with val.
        """
        return val

    def upsert(self, node, val):
        """
        Replace root with val.
        """
        return val

    def pop(self, node, key):
        """
        Remove root - return None.
        """
        return None

    def remove(self, node, val):
        """
        Remove root if it matches val.
        """
        if val is base.ANY or node == val:
            return None
        return node

    @classmethod
    @functools.lru_cache()
    def concrete(cls, val):
        """
        Return a concrete Empty op for the given key value.
        For empty path, the key is always ''.
        """
        return cls()

    def match(self, op, specials=False):
        if not isinstance(op, Empty):
            return None
        m = super().match(op)
        if m is None:
            return m
        return (base.MatchResult(''),) + m


class AccessOp(SimpleOp):
    """
    Base class for the three access operations: Key (.), Attr (@), Slot ([]).

    Access ops are the traversal primitives that actually look up a child
    value from a node.  Modifiers like ! (negation) and ~ (nop) are not
    access ops — they wrap or filter but don't access anything themselves.

    Inside mid-path groups, every branch must begin with an access op
    (explicit form) or inherit one from a prefix (shorthand form).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.op = self.args[0]

    def __repr__(self):
        return self.operator(top=True)

    def quote(self):
        """
        Return the dotted notation form of this op's key.
        """
        return self.op.quote()

    def is_pattern(self):
        return self.op.is_pattern()

    def is_template(self):
        """
        True if the inner match op is a substitution.
        """
        return self.op.is_template()

    def is_reference(self):
        """
        True if the inner match op is an internal reference.
        """
        return self.op.is_reference()

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in the inner match op.
        """
        new_op = self.op.resolve(bindings, partial)
        if new_op is self.op:
            return self
        return self.__class__(new_op)

    def _resolved(self, node=None, settings=base.SETTINGS):
        """
        Resolve a Reference against the appropriate target.  Returns a
        tuple of concrete ops (empty if the reference path is not found).
        Callers must guard with is_reference() before calling.
        """
        root = settings.root
        parents = settings.parents or ()
        try:
            val = self.op.resolve_ref(root, node=node, parents=parents)
        except KeyError:
            return ()
        if self.op.is_pattern():
            return tuple(self.__class__(matchers.Const(v)) for v in val)
        return (self.__class__(matchers.Const(val)),)

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        """
        Resolve references before updating.
        """
        if self.is_reference():
            for r in self._resolved(node=node, settings=settings):
                node = r.do_update(ops, node, val, has_defaults, _path, nop, nop_from_unwrap=nop_from_unwrap, settings=settings)
            return node
        return super().do_update(ops, node, val, has_defaults, _path, nop, nop_from_unwrap=nop_from_unwrap, settings=settings)

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        """
        Resolve references before removing.
        """
        if self.is_reference():
            for r in self._resolved(node=node, settings=settings):
                node = r.do_remove(ops, node, val, nop, settings=settings)
            return node
        return super().do_remove(ops, node, val, nop, settings=settings)

    def is_empty(self, node):
        return not any(True for _ in self.keys(node))

    def update_items(self, node, settings=base.SETTINGS):
        """
        One lookup, not two: is_empty() above asks whether items() yields
        anything, so take the first item and keep the rest. Only when the
        two would see the same items: is_empty() ignores strict, and a
        subclass may define emptiness differently.
        """
        if settings.strict or type(self).is_empty is not AccessOp.is_empty:
            # explicit super: the compiler gives a cpdef method no __class__ cell
            return super(AccessOp, self).update_items(node, settings=settings)
        items = iter(self.items(node, settings=settings))
        first = next(items, base.marker)
        if first is base.marker:
            return None
        return itertools.chain((first,), items)


class Key(AccessOp):
    """
    Also serves as an unresolved bare identifier in grouped expressions
    like (a, b).  The prefix outside the group determines the final access
    mode: .() keeps them as Keys, @() promotes them to Attrs via
    as_attrs_opgroup.
    """
    @classmethod
    def concrete(cls, val):
        """
        Return a concrete Key op for the given key value.
        """
        return cls._concrete_cached(type(val), val)

    @classmethod
    @functools.lru_cache()
    def _concrete_cached(cls, _type, val):
        if isinstance(val, numbers.Number):
            return cls(matchers.NumericQuoted(val))
        return cls(matchers.Word(val))

    def operator(self, top=False):
        if top:
            return self.op.quote_top()
        return '.' + self.op.quote()

    def _items(self, node, keys, filtered=True, settings=base.SETTINGS):
        if not filtered or not self.filters:
            # nothing to filter: one generator, not two threaded through a filter
            def _unfiltered():
                for k in keys:
                    try:
                        v = node[k]
                    except (TypeError, KeyError, IndexError):
                        continue
                    yield (k, v)

            return _unfiltered()

        curkey = None

        def _values():
            nonlocal curkey
            for k in keys:
                try:
                    v = node[k]
                except (TypeError, KeyError, IndexError):
                    continue
                curkey = k
                yield v

        def _items():
            # the values' own settings: node is their parent
            values = self.filtered(_values(), settings.below(node)) if filtered else _values()
            for v in values:
                yield (curkey, v)

        return _items()

    def _reference_items(self, node, filtered, settings):
        """
        The items of each op this reference resolves to, chained.
        """
        return itertools.chain.from_iterable(
            r.items(node, filtered=filtered, settings=settings)
            for r in self._resolved(node=node, settings=settings))

    def _const_items(self, node):
        """
        The items of a constant key: what _items(node, match_keys(node))
        yields, looked up now instead of through a generator, since there is
        at most one.
        """
        pairs = []
        for k in self.op.match_keys(node):
            try:
                pairs.append((k, node[k]))
            except (TypeError, KeyError, IndexError):
                continue
        return iter(pairs)

    def items(self, node, settings=base.SETTINGS, filtered=True):
        if self.is_reference():
            return self._reference_items(node, filtered, settings)
        # Dict-like: use key matching
        if hasattr(node, 'keys'):
            if filtered and not self.filters and isinstance(self.op, matchers.Const):
                return self._const_items(node)
            keys = self.op.match_keys(node) if filtered else node.keys()
            return self._items(node, keys, filtered, settings)
        # In strict mode, numeric keys never coerce to list indices
        if settings.strict:
            return ()
        # Key only handles lists with concrete numeric keys
        if not hasattr(node, '__getitem__'):
            return ()
        if not isinstance(self.op, matchers.Const):
            return ()
        key = self.op.value
        if not isinstance(key, int):
            # Concrete non-int key on object with __getitem__ but no keys():
            # try direct access (e.g. Stripe-like objects)
            if hasattr(node, '__contains__') and key in node:
                try:
                    return iter([(key, node[key])])
                except (KeyError, TypeError):
                    pass
            return ()
        if not filtered:
            return self._items(node, range(len(node)), filtered=False)
        # Treat as sequence index
        try:
            return iter([(key, node[key])])
        except (IndexError, TypeError):
            return ()

    def default(self):
        if self.is_pattern():
            return {}
        return {self.op.value: None}

    def match(self, op, specials=False):
        if isinstance(op, wrappers.FilterWrap):
            op = op.inner
        if not isinstance(op, Key):
            return None
        if not self.op.matchable(op.op, specials):
            return None

        # explicit super: the compiler gives a cpdef method no __class__ cell
        results = super(Key, self).match(op)
        if results is None:
            return None

        # match key
        val = next(self.op.matches((op.op.value,)), base.marker)
        if val is base.marker:
            return None
        results += (base.MatchResult(val),)
        return results

    def update(self, node, key, val):
        val = self.default() if val is base.ANY else val
        try:
            node[key] = val
            return node
        except TypeError:
            pass
        iterable = ((k, node[k]) for k in node if k != key)
        iterable = itertools.chain(iterable, ((key, val),))
        return type(node)(iterable)
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)

        keys = tuple(self.keys(node))
        iterable = ((k, node[k]) for k in node if k not in keys)
        items = itertools.chain(iterable, ((k, val) for k in keys))
        try:
            for k, v in items:
                node[k] = v
            return node
        except TypeError:
            pass
        return type(node)(items)

    def pop(self, node, key):
        try:
            del node[key]
            return node
        except KeyError:
            return node
        except TypeError:
            pass
        return type(node)((k, v) for k, v in node.items() if k != key)
    def remove(self, node, val, settings=base.SETTINGS):
        to_remove = [k for k, v in self.items(node, settings=settings) if val is base.ANY or v == val]
        for k in to_remove:
            node = self.pop(node, k)
        return node


class Attr(Key):
    @classmethod
    @functools.lru_cache()
    def concrete(cls, val):
        return cls(matchers.Word(val))

    def operator(self, top=False):
        return '@' + self.op.quote()

    def _items(self, node, keys, filtered=True, settings=base.SETTINGS):
        if not filtered or not self.filters:
            # nothing to filter: one generator, not two threaded through a filter
            def _unfiltered():
                for k in keys:
                    try:
                        v = getattr(node, k)
                    except AttributeError:
                        continue
                    yield (k, v)

            return _unfiltered()

        curkey = None

        def _values():
            nonlocal curkey
            for k in keys:
                try:
                    v = getattr(node, k)
                except AttributeError:
                    continue
                curkey = k
                yield v

        def _items():
            # the values' own settings: node is their parent
            values = self.filtered(_values(), settings.below(node)) if filtered else _values()
            for v in values:
                yield (curkey, v)

        return _items()

    @staticmethod
    def _all_keys(node):
        """
        Collect all known attribute names from an object:
        __dict__, _fields (namedtuple), and __slots__ (MRO walk).
        Preserves insertion order.
        """
        seen = set()
        keys = []
        try:
            for k in node.__dict__.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        except AttributeError:
            pass
        if isinstance(node, tuple):
            for k in getattr(node, '_fields', ()):
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        for cls in type(node).__mro__:
            for s in getattr(cls, '__slots__', ()):
                if s not in ('__dict__', '__weakref__') and s not in seen:
                    seen.add(s)
                    keys.append(s)
        return keys

    def _concrete_fallback(self, node, inner, filtered, settings=base.SETTINGS):
        """
        Wrap an items iterator: yield from inner, and if nothing
        was yielded, try getattr directly for concrete (non-pattern) ops.
        """
        found = False
        for pair in inner:
            found = True
            yield pair
        if found:
            return
        key = self.op.value
        try:
            v = getattr(node, key)
        except AttributeError:
            return
        if filtered and not tuple(self.filtered(iter((v,)), settings.below(node))):
            return
        yield (key, v)

    def items(self, node, settings=base.SETTINGS, filtered=True):
        if self.is_reference():
            return self._reference_items(node, filtered, settings)
        all_keys = self._all_keys(node)
        keys = self.op.matches(all_keys) if filtered else all_keys
        result = self._items(node, keys, filtered, settings)
        if not self.is_pattern():
            result = self._concrete_fallback(node, result, filtered, settings)
        return result

    def default(self):
        o = types.SimpleNamespace()
        if self.is_pattern():
            return o
        setattr(o, self.op.value, None)
        return o

    def update(self, node, key, val):
        val = self.default() if val is base.ANY else val
        try:
            setattr(node, key, val)
            return node
        except AttributeError:
            pass
        # Try namedtuple _replace
        if hasattr(node, '_replace'):
            return node._replace(**{key: val})
        # Try dataclasses.replace for frozen dataclass (skipped on 3.6)
        if utils.is_dataclass(node):
            return utils.dataclass_replace(node, **{key: val})
        raise AttributeError(f"Cannot set attribute '{key}' on {type(node).__name__}")
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)
        keys = tuple(self.keys(node))
        # Try mutable update first
        node_keys = self._all_keys(node)
        iterable = ((k, getattr(node, k)) for k in node_keys if k not in keys)
        items = list(itertools.chain(iterable, ((k, val) for k in keys)))
        try:
            for k, v in items:
                setattr(node, k, v)
            return node
        except AttributeError:
            pass
        # Immutable: build replacement dict
        updates = {k: val for k in keys}
        if hasattr(node, '_replace'):
            return node._replace(**updates)
        if utils.is_dataclass(node):
            return utils.dataclass_replace(node, **updates)
        raise AttributeError(f"Cannot set attributes on {type(node).__name__}")

    def pop(self, node, key):
        try:
            delattr(node, key)
        except AttributeError:
            pass
        return node

    def remove(self, node, val, settings=base.SETTINGS):
        to_remove = [k for k, v in self.items(node, settings=settings) if val is base.ANY or v == val]
        for k in to_remove:
            self.pop(node, k)
        return node


class Slot(Key):
    @classmethod
    def concrete(cls, val):
        """
        Return a concrete Slot op for the given key value.
        """
        return cls._concrete_cached(type(val), val)

    @classmethod
    @functools.lru_cache()
    def _concrete_cached(cls, _type, val):
        if isinstance(val, numbers.Number):
            return cls(matchers.Numeric(val))
        return cls(matchers.String(val))

    def operator(self, top=False):
        if self.op is None:
            return '[]'
        return '[' + self.op.quote() + ']'

    def items(self, node, settings=base.SETTINGS, filtered=True):
        if self.is_reference():
            return self._reference_items(node, filtered, settings)
        if hasattr(node, 'keys'):
            if settings.strict:
                return ()
            # explicit super: the compiler gives a cpdef method no __class__ cell
            return super(Slot, self).items(node, filtered=filtered, settings=settings)

        if not hasattr(node, '__getitem__'):
            return ()

        if not filtered:
            keys = range(len(node))
        elif self.is_pattern():
            keys = self.op.matches(utils.indices(node))
        else:
            keys = (self.op.value,)

        return self._items(node, keys, filtered, settings)

    def default(self):
        if isinstance(self.op, matchers.Numeric) and self.op.is_int():
            return []
        # explicit super: the compiler gives a cpdef method no __class__ cell
        return super(Slot, self).default()

    def update(self, node, key, val):
        if hasattr(node, 'keys'):
            return super().update(node, key, val)
        val = self.default() if val is base.ANY else val
        if len(node) <= key:
            node += itemof(node, val)
            return node
        try:
            node[key] = val
        except TypeError:
            node = node[:key] + itemof(node, val) + node[key+1:]
        return node

    def upsert(self, node, val):
        if hasattr(node, 'keys'):
            return super().upsert(node, val)
        val = self.default() if val is base.ANY else val
        if self.is_pattern():
            keys = tuple(self.keys(node))
        else:
            keys = (self.op.value,)
        update_keys = tuple(k for k in keys if k < len(node))
        append_keys = tuple(k for k in keys if k >= len(node))
        try:
            for k in update_keys:
                node[k] = val
            node += type(node)(val for _ in append_keys)
            return node
        except TypeError:
            pass

        def _gen():
            for i, v in enumerate(node):
                if i in update_keys:
                    yield val
                else:
                    yield v

        # Handle different immutable types
        if isinstance(node, str):
            # str() doesn't accept iterables
            node = ''.join(_gen())
            node += ''.join(str(val) for _ in append_keys)
        elif hasattr(node, '_make'):
            # namedtuple - use _make() and ignore appends (fixed structure)
            node = type(node)._make(_gen())
        elif isinstance(node, frozenset):
            # frozenset - use union
            node = frozenset(_gen())
            if append_keys:
                node = node | frozenset([val])
        else:
            node = type(node)(_gen())
            node += type(node)(val for _ in append_keys)
        return node

    def pop(self, node, key):
        if hasattr(node, 'keys'):
            return super().pop(node, key)
        try:
            del node[key]
            return node
        except (KeyError, IndexError):
            return node
        except TypeError:
            pass
        idx = key if key >= 0 else len(node) + key
        return type(node)(v for i,v in enumerate(node) if i != idx)
    def remove(self, node, val):
        if hasattr(node, 'keys'):
            return super().remove(node, val)
        keys = tuple(self.keys(node))
        if val is base.ANY:
            if hasattr(node, '__delitem__'):
                for k in reversed(keys):
                    del node[k]
                return node
            n = len(node)
            normalized = {k if k >= 0 else n + k for k in keys}
            return node.__class__(v for i, v in enumerate(node) if i not in normalized)
        if hasattr(node, 'remove'):
            try:
                node.remove(val)
            except (ValueError, KeyError):
                pass
            return node
        try:
            if hasattr(node, 'index'):
                idx = node.index(val)
            else:
                idx = next(idx for idx, v in enumerate(node) if val == v)
        except (ValueError, StopIteration):
            return node
        node = node[:idx] + node[idx+1:]
        return node


class SlotSpecial(Slot):
    @classmethod
    @functools.lru_cache()
    def concrete(cls, val):
        return cls(val)
    def default(self):
        return []

    def items(self, node, settings=base.SETTINGS, filtered=True):
        return self._last_item(node)

    def _last_item(self, node):
        try:
            yield -1, node[-1]
        except (TypeError, IndexError):
            pass

    def is_empty(self, node):
        return True

    def update(self, node, key, val):
        if not isinstance(key, str):
            return super().update(node, key, val)
        if key == '+' or (key == '+?' and val not in node):
            item = itemof(node, val)
            if isinstance(node, frozenset):
                node = node | item
            else:
                try:
                    node += item
                except TypeError:
                    # Immutable sequence - concatenate
                    node = node + item
        return node
    def upsert(self, node, val):
        return self.update(node, self.op.value, val)

    def pop(self, node, key):
        if isinstance(key, str):
            return None
        return node
    def remove(self, node, val):
        return node


class SliceFilter(BaseOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.args

    def __repr__(self):
        return self.operator(top=True)

    def operator(self, top=False):
        """
        Render as [filter1&filter2&...].
        """
        inner = '&'.join(repr(f) for f in self.filters)
        return '[' + inner + ']'

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in filters.
        """
        new_filters = tuple(
            f.resolve(bindings, partial) if hasattr(f, 'resolve') else f
            for f in self.filters)
        if all(nf is of for nf, of in zip(new_filters, self.filters)):
            return self
        result = SliceFilter(*new_filters)
        return result

    @classmethod
    def concrete(cls, val):
        return Slot.concrete(val)

    def is_pattern(self):
        return False

    def is_template(self):
        """
        True if any attached filter contains a substitution reference.
        """
        return any(
            hasattr(f, 'is_template') and f.is_template()
            for f in self.filters)

    def is_reference(self):
        """
        True if any attached filter contains an internal reference.
        """
        return any(
            hasattr(f, 'is_reference') and f.is_reference()
            for f in self.filters)

    def is_empty(self, node):
        return not node

    def match(self, op, specials=False):
        if not isinstance(op, SliceFilter):
            return None
        return super().match(op)

    def items(self, node, settings=base.SETTINGS):
        below = settings.below(node)
        for idx, v in enumerate(node):
            if any(True for _ in self.filtered((v,), below)):
                yield (idx, v)

    def upsert(self, node, val):
        return self.update(node, None, val)

    def update(self, node, key, val):
        raise RuntimeError('Updates not supported for slice filtering')

    def remove(self, node, val, settings=base.SETTINGS):
        removes = [idx for idx, _ in self.items(node, settings=settings)]

        if not removes:
            return node

        def _build():
            iterable = (v for idx, v in enumerate(node) if idx not in removes)
            return type(node)(iterable)

        # if we're removing by value, then we need to see _if_ new list will equal
        new = None
        if val is not base.ANY:
            new = _build()
            if new != val:
                return node

        # attempt to mutate
        try:
            for idx in reversed(removes):
                del node[idx]
            return node
        except TypeError:
            pass

        # otherwise we can't mutate, so generate a new one
        return _build() if new is not None else new

    def pop(self, node, key):
        return self.remove(node, base.ANY)

    def push_children(self, stack, frame, paths):
        """
        Push filtered container onto the stack.
        Path segment is [] (Slice) since the filter narrows the whole collection.
        """
        filtered = type(frame.node)(self.filtered(frame.node, frame.settings.below(frame.node)))
        cp = frame.prefix + (Slice.concrete(slice(None)),) if paths else frame.prefix
        stack.push(base.Frame(frame.ops, filtered, cp, settings=frame.settings))
        return ()



class Slice(SimpleOp):
    @classmethod
    def concrete(cls, val):
        o = cls()
        o.args = (val.start, val.stop, val.step)
        return o
    @classmethod
    def munge(cls, toks):
        out = []
        while toks:
            item, *toks = toks
            if item == ':':
                item = None
            else:
                toks = toks[1:]
            out.append(item)
        out += [None] * (3 - len(out))
        return out[:3]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.args = tuple(self.munge(self.args))
    def __repr__(self):
        def s(a):
            return str(a) if a is not None else ''
        if not self.args or self.args == (None, None, None):
            return '[]'
        start, stop, step = self.args
        m = [s(start), s(stop)]
        if step is not None:
            m += [s(stop)]
        return '[' + ':'.join(m) + ']'
    def is_pattern(self):
        return False
    def is_slice(self):
        return True
    def operator(self, top=False):
        return str(self)
    def slice(self, node=None):
        args = self.args
        if node is not None:
            args = ( len(node) if a == '+' else a for a in self.args )
        return slice(*args)
    def cardinality(self, node=None):
        """
        Calculate cardinality of a slice; don't both dealing with countably infinite
        set arithmetic, instead just pick a suitably large integer
        """
        s = self.slice(node)
        start = s.start or 0
        stop = s.stop or '+'
        step = s.step or 1
        if '+' in (start, step):
            return 0
        if stop == '+':
            return 1 << 64
        return max(0, int((stop - start) / step))

    def keys(self, node, settings=base.SETTINGS):
        return (self.slice(node),)
    def items(self, node, settings=base.SETTINGS, filtered=True):
        return self._slice_items(node, settings)
    def _slice_items(self, node, settings):
        for k in self.keys(node, settings=settings):
            try:
                yield (k, node[k])
            except (TypeError, KeyError, IndexError):
                pass
    def values(self, node, settings=base.SETTINGS):
        items = self.items(node, settings=settings)
        return (v for _, v in items)
    def is_empty(self, node):
        return not node[self.slice(node)]
    def default(self):
        return []
    def matchable(self, op):
        return isinstance(op, Slice)

    def match(self, op, specials=False):
        if not isinstance(op, Slice):
            return None
        if self.cardinality() < op.cardinality():
            return None
        return base.MatchResult(op.slice())
    def update(self, node, key, val):
        if node[key] == val:
            return node
        try:
            node[key] = self.default() if val is base.ANY else val
            return node
        except TypeError:
            pass
        r = range(*key.indices(len(node)))
        idx = 0
        out = []
        for i,v in enumerate(node):
            if i not in r:
                out.append(v)
                continue
            if idx < len(val):
                out.append(val[idx])
                idx += 1
        # Append remaining val items (handles empty/shorter node)
        while idx < len(val):
            out.append(val[idx])
            idx += 1
        if hasattr(node, 'join'):
            return node.__class__().join(out)
        return node.__class__(out)
    def upsert(self, node, val):
        return self.update(node, self.slice(node), val)

    def pop(self, node, key):
        try:
            del node[key]
            return node
        except TypeError:
            pass
        r = range(*key.indices(len(node)))
        iterable = ( v for i,v in enumerate(node) if i not in r )
        if hasattr(node, 'join'):
            return node.__class__().join(iterable)
        return node.__class__(iterable)
    def remove(self, node, val):
        if val is base.ANY:
            return self.pop(node, self.slice(node))
        key = self.slice(node)
        if node[key] == val:
            return self.pop(node, key)
        return node


class Invert(SimpleOp):
    @classmethod
    @functools.lru_cache()
    def concrete(cls, val):
        return cls(val)
    def __repr__(self):
        return '-'
    def is_pattern(self):
        return False
    @property
    def op(self):
        return base.NOP
    def operator(self, top=False):
        return '-'
    def match(self, op, specials=False):
        return base.MatchResult('-') if isinstance(op, Invert) else None
    def items(self, node, settings=base.SETTINGS, filtered=True):
        return iter((('-', node),))
    def keys(self, node, settings=base.SETTINGS):
        yield '-'
    def values(self, node, settings=base.SETTINGS):
        yield node

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        return engine.removes(ops, node, val, settings=settings)

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        assert val is not base.ANY, 'Value required'
        return engine.updates(ops, node, val, settings=settings)


# engine and wrappers both import this module, so they can only be bound
# once the classes above exist.
from . import engine  # noqa: E402
from . import wrappers  # noqa: E402
