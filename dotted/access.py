"""
"""
import functools
import itertools
import re
import types

from . import base, match
from .base import Op
from .utils import is_dict_like, is_list_like, is_set_like, is_terminal


def itemof(node, val):
    return val if isinstance(node, (str, bytes)) else node.__class__([val])


# ---- quoting utilities ----

_RESERVED = frozenset('.[]*:|+?/=,@&()!~#{}')
_NEEDS_QUOTE = _RESERVED | frozenset(' \t\n\r')

_NUMERIC_RE = re.compile(
    r'[-]?0[xX][0-9a-fA-F]+$'           # hex
    r'|[-]?0[oO][0-7]+$'                # octal
    r'|[-]?0[bB][01]+$'                 # binary
    r'|[-]?[0-9][0-9_]*[eE][+-]?[0-9]+$' # scientific notation
    r'|[-]?[0-9]+(?:_[0-9]+)+$'         # underscore separators
    r'|[-]?[0-9]+$'                     # plain integers
)


def _needs_quoting(s):
    """
    Return True if a string key must be quoted in dotted notation.
    """
    if not s:
        return True
    # match.Numeric forms (integers, scientific notation, underscore separators)
    # are handled by the grammar and don't need quoting, even if they
    # contain reserved characters like '+' in '1e+10'.
    if s[0].isdigit() or (len(s) > 1 and s[0] == '-' and s[1].isdigit()):
        return not _NUMERIC_RE.match(s)
    if any(c in _NEEDS_QUOTE for c in s):
        return True
    return False


def _is_numeric_str(s):
    """
    Return True if s is a string that parses as an integer.
    """
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def _quote_str(s):
    """
    Wrap a string in single quotes, escaping backslashes and single quotes.
    """
    s = s.replace('\\', '\\\\').replace("'", "\\'")
    return f"'{s}'"


def quote(key, as_key=True):
    """
    Quote a key for use in a dotted notation path string.

    For raw string keys, wraps in double quotes if the key contains
    reserved characters or whitespace.
    """
    if isinstance(key, str):
        if _needs_quoting(key):
            return _quote_str(key)
        return key
    elif isinstance(key, int):
        return str(key)
    elif isinstance(key, float):
        s = str(key)
        if '.' not in s:
            return s
        if as_key:
            return f"#'{s}'"
        return s
    elif isinstance(key, Op):
        return str(key)
    else:
        raise NotImplementedError


def normalize(key, as_key=True):
    """
    Convert a raw Python key to its dotted normal form representation.

    Like quote(), but also quotes string keys that look numeric so they
    round-trip correctly through pack/unpack (preserving string vs int type).
    """
    if isinstance(key, str) and _is_numeric_str(key):
        return _quote_str(key)
    return quote(key, as_key=as_key)


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

    def filtered(self, items):
        for f in self.filters:
            items = f.filtered(items)
        return items

    def keys(self, node, **kwargs):
        return (k for k, _ in self.items(node, **kwargs))

    def values(self, node, **kwargs):
        return (v for _, v in self.items(node, **kwargs))

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        from . import elements
        if not ops:
            if nop:
                return node
            if kwargs.get('strict') and not any(True for _ in self.items(node, **kwargs)):
                return node
            return self.upsert(node, val)
        if self.is_empty(node) and not has_defaults:
            if nop or isinstance(ops[0], elements.NopWrap):
                return node
            built = elements.updates(ops, elements.build_default(ops), val, True, _path, nop, **kwargs)
            return self.upsert(node, built)
        pass_nop = nop and not nop_from_unwrap
        for k, v in self.items(node, **kwargs):
            if v is None:
                v = elements.build_default(ops)
            node = self.update(node, k, elements.updates(ops, v, val, has_defaults, _path + [(self, k)], pass_nop, **kwargs))
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        from . import elements
        if not ops:
            if nop:
                return node
            if kwargs.get('strict') and not any(True for _ in self.items(node, **kwargs)):
                return node
            return self.remove(node, val)
        for k, v in self.items(node, **kwargs):
            node = self.update(node, k, elements.removes(ops, v, val, nop=False, **kwargs))
        return node



class SimpleOp(BaseOp):
    """
    Base for ops with items()/concrete() that share the standard walk pattern.
    """

    def push_children(self, stack, frame, paths):
        """
        Push matching children onto the traversal stack.
        Reverse order so first match is popped first (LIFO).

        NOTE: We tried skipping list()+reversed() for concrete (non-pattern)
        keys since they match at most one item.  Benchmarked as neutral —
        the is_pattern() check costs roughly what the single-item reversal
        saves.  We also tried removing the ``or {}`` guard on kwargs
        (always passing a dict instead of None).  Also neutral.  Keeping
        the simple uniform path for clarity.
        """
        children = list(self.items(frame.node, **(frame.kwargs or {})))
        for k, v in reversed(children):
            cp = frame.prefix + (self.concrete(k),) if paths else frame.prefix
            stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
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

    def is_empty(self, node):
        return False

    def operator(self, top=False):
        return self.__repr__()

    def items(self, node, **kwargs):
        """
        Yield the root as a single item with empty key.
        """
        for v in self.filtered((node,)):
            yield ('', v)

    def keys(self, node, **kwargs):
        """
        Yield empty string as the 'key' for root.
        """
        return (k for k, _ in self.items(node, **kwargs))

    def values(self, node, **kwargs):
        return self.filtered((node,))

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
    pass


class Key(AccessOp):
    @classmethod
    def concrete(cls, val):
        """
        Return a concrete Key op for the given key value.
        """
        return cls._concrete_cached(type(val), val)

    @classmethod
    @functools.lru_cache()
    def _concrete_cached(cls, _type, val):
        import numbers
        if isinstance(val, numbers.Number):
            return cls(match.NumericQuoted(val))
        return cls(match.Word(val))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.op = self.args[0]
        self.filters = self.args[1:]

    def is_pattern(self):
        return isinstance(self.op, match.Pattern)

    def __repr__(self):
        return '.'.join(repr(a) for a in self.args)

    def operator(self, top=False):
        q = normalize(self.op.value) if isinstance(self.op, (match.Word, match.String)) else repr(self.op)
        iterable = itertools.chain((q,), (repr(f) for f in self.filters))
        s = '.'.join(iterable)
        if top:
            return s
        return '.' + s

    def _items(self, node, keys, filtered=True):
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
            values = self.filtered(_values()) if filtered else _values()
            for v in values:
                yield (curkey, v)

        return _items()

    def items(self, node, filtered=True, **kwargs):
        # Dict-like: use key matching
        if hasattr(node, 'keys'):
            keys = self.op.matches(node.keys()) if filtered else node.keys()
            return self._items(node, keys, filtered)
        # In strict mode, numeric keys never coerce to list indices
        if kwargs.get('strict'):
            return ()
        # Key only handles lists with concrete numeric keys
        if not hasattr(node, '__getitem__'):
            return ()
        if not isinstance(self.op, match.Const):
            return ()
        key = self.op.value
        if not isinstance(key, int):
            return ()
        if not filtered:
            return self._items(node, range(len(node)), filtered=False)
        # Treat as sequence index
        try:
            return iter([(key, node[key])])
        except (IndexError, TypeError):
            return ()

    def is_empty(self, node):
        return not any(True for _ in self.keys(node))

    def default(self):
        if self.is_pattern():
            return {}
        if not self.filters:
            return {self.op.value: None}
        return {self.op.value: {}}

    def match(self, op, specials=False):
        if not isinstance(op, Key):
            return None
        if not self.op.matchable(op.op, specials):
            return None

        results = super().match(op)
        if results is None:
            return None

        # match key
        val = next(self.op.matches((op.op.value,)), base._marker)
        if val is base._marker:
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
    def remove(self, node, val, **kwargs):
        to_remove = [k for k, v in self.items(node, **kwargs) if val is base.ANY or v == val]
        for k in to_remove:
            node = self.pop(node, k)
        return node


class Attr(Key):
    @classmethod
    @functools.lru_cache()
    def concrete(cls, val):
        return cls(match.Word(val))

    def __repr__(self):
        return '@' + '.'.join(repr(a) for a in self.args)

    def operator(self, top=False):
        q = normalize(self.op.value) if isinstance(self.op, (match.Word, match.String)) else repr(self.op)
        iterable = itertools.chain((q,), (repr(f) for f in self.filters))
        return '@' + '.'.join(iterable)

    def _items(self, node, keys, filtered=True):
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
            values = self.filtered(_values()) if filtered else _values()
            for v in values:
                yield (curkey, v)

        return _items()

    def items(self, node, filtered=True, **kwargs):
        # Try __dict__ first (normal objects), then _fields (namedtuple)
        try:
            all_keys = node.__dict__.keys()
        except AttributeError:
            all_keys = getattr(node, '_fields', ())
        keys = self.op.matches(all_keys) if filtered else all_keys
        return self._items(node, keys, filtered)

    def default(self):
        o = types.SimpleNamespace()
        if self.is_pattern():
            return o
        if not self.filters:
            setattr(o, self.op.value, None)
            return o
        setattr(o, self.op.value, types.SimpleNamespace())
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
        # Try dataclasses.replace for frozen dataclass
        import dataclasses
        if dataclasses.is_dataclass(node):
            return dataclasses.replace(node, **{key: val})
        raise AttributeError(f"Cannot set attribute '{key}' on {type(node).__name__}")
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)
        keys = tuple(self.keys(node))
        # Try mutable update first
        try:
            node_keys = node.__dict__.keys()
        except AttributeError:
            node_keys = getattr(node, '_fields', ())
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
        import dataclasses
        if dataclasses.is_dataclass(node):
            return dataclasses.replace(node, **updates)
        raise AttributeError(f"Cannot set attributes on {type(node).__name__}")

    def pop(self, node, key):
        try:
            delattr(node, key)
        except AttributeError:
            pass
        return node

    def remove(self, node, val, **kwargs):
        to_remove = [k for k, v in self.items(node, **kwargs) if val is base.ANY or v == val]
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
        import numbers
        if isinstance(val, numbers.Number):
            return cls(match.Numeric(val))
        return match.String(val)

    def __repr__(self):
        return '[' + super().__repr__()  + ']'

    def operator(self, top=False):
        iterable = (repr(a) for a in self.filters)
        if self.op is not None:
            if isinstance(self.op, (match.Word, match.String)):
                q = normalize(self.op.value, as_key=False)
            elif isinstance(self.op, (match.Numeric, match.NumericQuoted)):
                q = repr(self.op)
            else:
                q = self.op.value
            iterable = itertools.chain((q,), iterable)
        return '[' + '.'.join(iterable) + ']'

    def items(self, node, filtered=True, **kwargs):
        if hasattr(node, 'keys'):
            if kwargs.get('strict'):
                return ()
            return super().items(node, filtered, **kwargs)

        if not hasattr(node, '__getitem__'):
            return ()

        if not filtered:
            keys = range(len(node))
        elif self.is_pattern():
            keys = self.op.matches(idx for idx, _ in enumerate(node))
        else:
            keys = (self.op.value,)

        return self._items(node, keys, filtered)

    def default(self):
        if isinstance(self.op, match.Numeric) and self.op.is_int():
            return []
        return super().default()

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

    def items(self, node, **kwargs):
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

    @classmethod
    def concrete(cls, val):
        return Slot.concrete(val)

    def is_pattern(self):
        return False

    def is_empty(self, node):
        return not node

    def match(self, op, specials=False):
        if not isinstance(op, SliceFilter):
            return None
        return super().match(op)

    def items(self, node, **kwargs):
        for idx, v in enumerate(node):
            if any(True for _ in self.filtered((v,))):
                yield (idx, v)

    def upsert(self, node, val):
        return self.update(node, None, val)

    def update(self, node, key, val):
        raise RuntimeError('Updates not supported for slice filtering')

    def remove(self, node, val, **kwargs):
        removes = [idx for idx, _ in self.items(node, **kwargs)]

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
        filtered = type(frame.node)(self.filtered(frame.node))
        cp = frame.prefix + (Slice.concrete(slice(None)),) if paths else frame.prefix
        stack.push(base.Frame(frame.ops, filtered, cp, kwargs=frame.kwargs))
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
            return quote(a, False) if a is not None else ''
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

    def keys(self, node, **kwargs):
        return (self.slice(node),)
    def items(self, node, **kwargs):
        for k in self.keys(node, **kwargs):
            try:
                yield (k, node[k])
            except (TypeError, KeyError, IndexError):
                pass
    def values(self, node, **kwargs):
        return (v for _, v in self.items(node, **kwargs))
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
    def items(self, node, **kwargs):
        yield ('-', node)
    def keys(self, node, **kwargs):
        yield '-'
    def values(self, node, **kwargs):
        yield node

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        from . import elements
        return elements.removes(ops, node, val, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        from . import elements
        assert val is not base.ANY, 'Value required'
        return elements.updates(ops, node, val, **kwargs)
