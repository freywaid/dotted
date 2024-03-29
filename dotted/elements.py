"""
"""
import collections.abc
import contextlib
import copy
import functools
import itertools
import pyparsing as pp
import types
import re


_marker = object()
ANY = _marker


class Match:
    def __init__(self, val):
        self.val = val
    def __bool__(self):
        return True


class Op:
    def __init__(self, *args, **kwargs):
        if len(args) == 3 and isinstance(args[2], pp.ParseResults):
            self.args = tuple(args[2].asList())
            self.parsed = args
        else:
            self.args = tuple(args)
            self.parsed = kwargs.get('parsed', ())
    def __repr__(self):
        return f'{self.__class__.__name__}:{self.args}'
    def __hash__(self):
        return hash(self.args)
    def __eq__(self, op):
        return self.__class__ == op.__class__ and self.args == op.args
    def scrub(self, node):
        return node
    def is_slice(self):
        return False


class MetaNOP(type):
    def __repr__(cls):
        return '<NOP>'
    @property
    def value(cls):
        return cls


class NOP(metaclass=MetaNOP):
    @classmethod
    def matchable(cls, op, specials=False):
        return False
    @classmethod
    def matches(cls, vals):
        return ()
    def is_slice(self):
        return False


class Const(Op):
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, Const)
    def matches(self, vals):
        return (v for v in vals if self.value == v)


class Numeric(Const):
    def is_int(self):
        try:
            return str(self.args[0]) == str(int(self.args[0]))
        except (ValueError, TypeError):
            return False
    @property
    def value(self):
        return int(self.args[0]) if self.is_int() else float(self.args[0])
    def __repr__(self):
        return f'{self.value}'


class NumericQuoted(Numeric):
    def __repr__(self):
        if self.is_int():
            return super().__repr__()
        return f"#'{str(self.value)}'"


class Word(Const):
    def __repr__(self):
        return f'{self.value}'


class String(Const):
    def __repr__(self):
        return f'{repr(self.value)}'


class Pattern(Op):
    def __repr__(self):
        return str(self.value)
    def matchable(self, op, specials=False):
        raise NotImplementedError


class Wildcard(Pattern):
    @property
    def value(self):
        return '*'
    def matches(self, vals):
        return iter(v for v in vals if v is not NOP)
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or specials


class WildcardFirst(Wildcard):
    @property
    def value(self):
        return '*?'
    def matches(self, vals):
        v = next(super().matches(vals), _marker)
        return iter(() if v is _marker else (v,))
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or \
            (specials and isinstance(op, (Special, WildcardFirst, RegexFirst)))


class Regex(Pattern):
    @property
    def value(self):
        return f'/{self.args[0]}/'
    @property
    def pattern(self):
        return re.compile(self.args[0])
    def matches(self, vals):
        vals = (v for v in vals if v is not NOP)
        vals = {v if isinstance(v, (str, bytes)) else str(v): v for v in vals}
        iterable = (self.pattern.fullmatch(v) for v in vals)
        # we want to regex match numerics as strings but return numerics
        # unless they were transformed, of course
        for m in iterable:
            if not m:
                continue
            if m[0] != m.string:
                yield m[0]
            else:
                yield vals[m.string]
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or (specials and isinstance(op, (Special, Regex)))


class RegexFirst(Regex):
    @property
    def value(self):
        return f'/{self.args[0]}/?'
    def matches(self, vals):
        iterable = super().matches(vals)
        v = next(iterable, _marker)
        return iter(() if v is _marker else (v,))
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or (specials and isinstance(op, (Special, RegexFirst)))


class Special(Op):
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, Special)
    def matches(self, vals):
        return (v for v in vals if v == self.value)


class Appender(Special):
    @property
    def value(self):
        return '+'
    def matchable(self, op, specials=False):
        return isinstance(op, Appender)
    def matches(self, vals):
        return (v for v in vals if self.value in v)


class AppenderUnique(Appender):
    @property
    def value(self):
        return '+?'


class FilterOp(Op):
    def is_pattern(self):
        return False

    def filtered(self, items):
        raise NotImplementedError

    def matchable(self, op):
        raise NotImplementedError

    def match(self, op):
        raise NotImplementedError


class FilterKeyValue(FilterOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.kv = tuple((k, v) for k, v in self.args)
    def __hash__(self):
        return hash(self.kv)
    def __repr__(self):
        return ','.join(f'{k}={v}' for k, v in self.kv)

    def is_filtered(self, node):
        if not hasattr(node, 'keys'):
            return False
        # disjunctive evaluation
        for k, v in self.kv:
            for km in k.matches(node.keys()):
                for vm in v.matches((node[km],)):
                    return True
        return False

    def filtered(self, items):
        return (item for item in items if self.is_filtered(item))

    def matchable(self, op):
        return isinstance(op, FilterKeyValue)

    def match(self, op):
        if not self.matchable(op):
            return None
        r = ()
        for k, v in self.kv:
            found = False
            for ik, iv in op.kv:
                if not k.matchable(ik) or not v.matchable(iv):
                    continue
                mk = next(k.matches((ik.value,)), _marker)
                mv = next(v.matches((iv.value,)), _marker)
                if _marker in (mk, mv):
                    continue
                if (mk, mv) not in r:
                    r += ((mk, mv),)
                found = True
            if not found:
                return None
        return type(op)(*r)


class FilterKeyValueFirst(FilterKeyValue):
    def matchable(self, op):
        return isinstance(op, FilterKeyValueFirst)

    def filtered(self, items):
        for item in super().filtered(items):
            yield item
            break


#
#
#
def itemof(node, val):
    return val if isinstance(node, (str, bytes)) else node.__class__([val])



class CmdOp(Op):
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
            results += (Match(m),)
        return results

    def filtered(self, items):
        for f in self.filters:
            items = f.filtered(items)
        return items


class Empty(CmdOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.args

    def __repr__(self):
        return '.'.join(repr(f) for f in self.filters)

    def is_pattern(self):
        return False

    def operator(self, top=False):
        return self.__repr__()

    def values(self, node):
        return self.filtered((node,))

    def default(self):
        return ''

    def match(self, op):
        if not isinstance(op, Empty):
            return None
        m = super().match(op)
        if m is None:
            return m
        return (Match(''),) + m


class Key(CmdOp):
    @classmethod
    def concrete(cls, val):
        import numbers
        if isinstance(val, numbers.Number):
            return cls(NumericQuoted(val))
        return cls(Word(val))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.op = self.args[0]
        self.filters = self.args[1:]

    def is_pattern(self):
        return isinstance(self.op, Pattern)

    def __repr__(self):
        return '.'.join(repr(a) for a in self.args)

    def operator(self, top=False):
        iterable = itertools.chain((quote(self.op.value),), (repr(f) for f in self.filters))
        s = '.'.join(iterable)
        if top:
            return s
        return '.' + s

    def _items(self, node, keys):
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
            for v in self.filtered(_values()):
                yield (curkey, v)

        return _items()

    def items(self, node):
        if not hasattr(node, 'keys'):
            return ()
        return self._items(node, self.op.matches(node.keys()))

    def keys(self, node):
        return (k for k, _ in self.items(node))

    def values(self, node):
        return (v for _, v in self.items(node))

    def is_empty(self, node):
        return not tuple(self.keys(node))

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
        val = next(self.op.matches((op.op.value,)), _marker)
        if val is _marker:
            return None
        results += (Match(val),)
        return results

    def update(self, node, key, val):
        val = self.default() if val is ANY else val
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
    def remove(self, node, val):
        for k,v in self.items(node):
            if val is ANY or v == val:
                return self.pop(node, k)
        return node


class Attr(Key):
    @classmethod
    def concrete(cls, val):
        return cls(Word(val))

    def __repr__(self):
        return '@' + '.'.join(repr(a) for a in self.args)

    def operator(self, top=False):
        iterable = itertools.chain((quote(self.op.value),), (repr(f) for f in self.filters))
        return '@' + '.'.join(iterable)

    def _items(self, node, keys):
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
            for v in self.filtered(_values()):
                yield (curkey, v)

        return _items()

    def items(self, node):
        try:
            keys = node.__dict__.keys()
        except AttributeError:
            keys = ()
        return self._items(node, self.op.matches(keys))

    def default(self):
        o = types.SimpleNamespae()
        if self.is_pattern():
            return o
        if not self.filters:
            setattr(o, self.op.value, None)
            return o
        setattr(o, self.op.value, types.SimpleNamespace())
        return o

    def update(self, node, key, val):
        val = self.default() if val is ANY else val
        setattr(node, key, val)
        return node
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)
        keys = tuple(self.keys(node))
        iterable = ((k, getattr(node, k)) for k in node if k not in keys)
        items = itertools.chain(iterable, ((k, val) for k in keys))
        for k, v in items:
            setattr(node, k, v)
        return node

    def pop(self, node, key):
        try:
            delattr(node, key)
        except AttributeError:
            return node
    def remove(self, node, val):
        for k,v in self.items(node):
            if val is ANY or v == val:
                return self.pop(node, k)
        return node


class Slot(Key):
    @classmethod
    def concrete(cls, val):
        import numbers
        if isinstance(val, numbers.Number):
            return cls(Numeric(val))
        return String(val)

    def __repr__(self):
        return '[' + super().__repr__()  + ']'

    def operator(self, top=False):
        iterable = (repr(a) for a in self.filters)
        if self.op is not None:
            iterable = itertools.chain((quote(self.op.value, as_key=False),), iterable)
        return '[' + '.'.join(iterable) + ']'

    def items(self, node):
        if hasattr(node, 'keys'):
            return super().items(node)

        if self.is_pattern():
            keys = self.op.matches(idx for idx, _ in enumerate(node))
        else:
            keys = (self.op.value,)

        return self._items(node, keys)

    def default(self):
        if isinstance(self.op, Numeric) and self.op.is_int():
            return []
        return super().default()

    def update(self, node, key, val):
        if hasattr(node, 'keys'):
            return super().update(node, key, val)
        val = self.default() if val is ANY else val
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
        val = self.default() if val is ANY else val
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
            for k in node:
                if k in update_keys:
                    yield val
                else:
                    yield node[k]

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
        return type(node)(v for i,v in enumerated(node) if i != key)
    def remove(self, node, val):
        if hasattr(node, 'keys'):
            return super().remove(node, val)
        keys = tuple(self.keys(node))
        if val is ANY:
            if hasattr(node, '__delitem__'):
                for k in reversed(keys):
                    del node[k]
                return node
            return node.__class__(v for i, v in enumerated(node) if i not in keys)
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
    def concrete(cls, val):
        return cls(val)
    def default(self):
        return []

    def items(self, node):
        try:
            yield -1, node[-1]
        except TypeError:
            pass

    def is_empty(self, node):
        return True

    def update(self, node, key, val):
        if not isinstance(key, str):
            return super().update(node, key, val)
        if key == '+' or (key == '+?' and val not in node):
            node += itemof(node, val)
        return node
    def upsert(self, node, val):
        return self.update(node, self.op.value, val)

    def pop(self, node, key):
        if isinstance(key, str):
            return None
        return node
    def remove(self, node, val):
        return node


class SliceFilter(CmdOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.args

    def is_pattern(self):
        return False

    def match(self, op, specials=False):
        if not isinstance(op, SliceFilter):
            return None
        return super().match(op)

    def values(self, node):
        return (type(node)(self.filtered(node)),)
    def items(self, node):
        return ((None, self.values(node)),)
    def keys(self, node):
        return (k for k, _ in self.items(node))

    def _items(self, node):
        curidx = None
        def _items():
            nonlocal curidx
            for idx, item in enumerate(node):
                curidx = idx
                yield item

        for v in self.filtered(_items()):
            yield (curidx, v)

    def upsert(self, node, val):
        return self.update(node, None, val)

    def update(self, node, key, val):
        raise RuntimeError('Updates not supported for slice filtering')

    def remove(self, node, val):
        removes = [idx for idx, _ in self._items(node)]

        if not removes:
            return node

        def _build():
            iterable = (v for idx, v in enumerate(node) if idx not in removes)
            return type(node)(iterable)

        # if we're removing by value, then we need to see _if_ new list will equal
        new = None
        if val is not ANY:
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
        return self.remove(node, ANY)


class Slice(CmdOp):
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

    def keys(self, node):
        return (self.slice(node),)
    def items(self, node):
        for k in self.keys(node):
            try:
                yield (k, node[k])
            except (TypeError, KeyError, IndexError):
                pass
    def values(self, node):
        return (v for _, v in self.items(node))
    def is_empty(self, node):
        return not node[self.slice(node)]
    def default(self):
        return []
    def match(self, op, specials=False):
        if not isinstance(op, Slice):
            return None
        if self.cardinality() < op.cardinality():
            return None
        return Match(op.slice())
    def update(self, node, key, val):
        if node[key] == val:
            return node
        try:
            node[key] = self.default() if val is ANY else val
            return node
        except TypeError:
            pass
        r = range(key.start, key.stop, key.step)
        idx = 0
        out = []
        for i,v in enumerate(node):
            if i not in range:
                out.append(v)
                continue
            if idx < len(val):
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
        r = range(key.start, key.stop, key.step)
        iterable = ( v for i,v in enumerate(node) if i not in r )
        if hasattr(node, 'join'):
            return node.__class__().join(iterable)
        return node.__class__(iterable)
    def remove(self, node, val):
        if val is ANY:
            return self.pop(node, self.slice(node))
        key = self.slice(node)
        if node[key] == val:
            return self.pop(node, key)
        return node


class Invert(CmdOp):
    @classmethod
    def concrete(cls, val):
        return cls(val)
    def __repr__(self):
        return '-'
    def is_pattern(self):
        return False
    @property
    def op(self):
        return NOP
    def operator(self, top=False):
        return '-'
    def match(self, op, specials=False):
        return Match('-') if isinstance(op, Invert) else None
    def items(self, node):
        yield ('-', node)
    def keys(self, node):
        yield '-'
    def values(self, node):
        yield node


#
#
#
class rdoc(str):
    def expandtabs(*args, **kwargs):
        title = 'Supported transforms\n\n'
        return title + '\n'.join(f'{name}\t{fn.__doc__ or ""}' for name,fn in Dotted._registry.items())


class Dotted:
    _registry = {}

    def registry(self):
        return self._registry

    @classmethod
    def register(cls, name, fn):
        cls._registry[name] = fn

    def __init__(self, results):
        self.ops = tuple(results['ops'])
        self.transforms = tuple(tuple(r) for r in results.get('transforms', ()))

    def assemble(self, start=0):
        return assemble(self, start)
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)}, {list(self.transforms)})'
    def __hash__(self):
        return hash((self.ops, self.transforms))
    def __len__(self):
        return len(self.ops)
    def __iter__(self):
        return iter(self.ops)
    def __eq__(self, ops):
        return self.ops == ops.ops and self.transforms == ops.transforms
    def __getitem__(self, key):
        return self.ops[key]
    def apply(self, val):
        for name,*args in self.transforms:
            fn = self._registry[name]
            val = fn(val, *args)
        return val

Dotted.registry.__doc__ = rdoc()


def quote(key, as_key=True):
    if isinstance(key, str):
        try:
            int(key)
            s = repr(key)
        except ValueError:
            s = key
    elif isinstance(key, int):
        s = str(key)
    elif isinstance(key, float):
        if as_key:
            s = f"#'{key}'"
        else:
            s = str(key)
    elif isinstance(key, Op):
        return str(key)
    else:
        raise NotImplementedError
    return s


def assemble(ops, start=0):
    def _gen():
        top = True
        for op in itertools.islice(ops, start, None):
            yield op.operator(top)
            if not isinstance(op, Invert):
                top = False
    return ''.join(_gen())


def transform(name):
    """
    Transform decorator
    """
    def _fn(fn):
        Dotted.register(name, fn)
        return fn
    return _fn


def build_default(ops):
    cur, *ops = ops
    built = cur.default()
    if not ops:
        return built
    return cur.upsert(built, build_default(ops))


def build(ops, node, deepcopy=True):
    cur, *ops = ops
    built = node.__class__()
    for k,v in cur.items(node):
        if not ops:
            built = cur.update(built, k, copy.deepcopy(v) if deepcopy else v)
        else:
            built = cur.update(built, k, build(ops, v, deepcopy=deepcopy))
    return built or build_default([cur]+ops)


def gets(ops, node):
    cur, *ops = ops
    if isinstance(cur, Invert):
        yield from gets(ops, node)
        return
    values = cur.values(node)
    if not ops:
        yield from values
        return
    for v in values:
        yield from gets(ops, v)


def updates(ops, node, val, has_defaults=False):
    cur, *ops = ops
    if isinstance(cur, Invert):
        return removes(ops, node, val)
    if not ops:
        return cur.upsert(node, val)
    if cur.is_empty(node) and not has_defaults:
        built = updates(ops, build_default(ops), val, True)
        return cur.upsert(node, built)
    for k, v in cur.items(node):
        node = cur.update(node, k, updates(ops, v, val, has_defaults))
    return node


def removes(ops, node, val=ANY):
    cur, *ops = ops
    if isinstance(cur, Invert):
        assert val is not ANY, 'Value required'
        return updates(ops, node, val)
    if not ops:
        return cur.remove(node, val)
    for k,v in cur.items(node):
        node = cur.update(node, k, removes(ops, v, val))
    return node


def expands(ops, node):
    def _expands(ops, node):
        cur, *ops = ops
        if not ops:
            yield from ( (cur.concrete(k),) for k in cur.keys(node) )
            return
        for k,v in cur.items(node):
            for m in _expands(ops, v):
                yield (cur.concrete(k),) + m
    return ( Dotted({'ops': r, 'transforms': ops.transforms}) for r \
            in _expands(ops, node) )

# default transforms
from . import transforms
