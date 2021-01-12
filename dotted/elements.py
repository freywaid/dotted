"""
"""
import collections.abc
import contextlib
import copy
import functools
import itertools
import pyparsing as pp
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
        return ''


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
        return ( v for v in vals if self.value == v )


class Numeric(Const):
    def is_int(self):
        try:
            return str(self.value) == str(int(self.value))
        except (ValueError, TypeError):
            return False
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
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, (Const, Special) if specials else Const)


class Wildcard(Pattern):
    def __repr__(self):
        return f'*'
    def matches(self, vals):
        return iter(vals)


class WildcardFirst(Wildcard):
    def __repr__(self):
        return f'*?'
    def matches(self, vals):
        v = next(iter(vals), _marker)
        return iter(() if v is _marker else (v,))


class Regex(Pattern):
    @property
    def pattern(self):
        return re.compile(self.value)
    def __repr__(self):
        return f'/{self.value}/'
    def matches(self, vals):
        iterable = ( self.pattern.fullmatch(v) for v in vals )
        return ( m[0] for m in iterable if m )


class RegexFirst(Regex):
    def __repr__(self):
        return f'/{self.value}/?'
    def matches(self, vals):
        iterable = super().matches(vals)
        v = next(iterable, _marker)
        return iter(() if v is _marker else (v,))


class Special(Op):
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, Special)
    def matches(self, vals):
        return ( v for v in vals if v == self.value )


class Appender(Special):
    def __repr__(self):
        return '+'
    def matchable(self, op, specials=False):
        return isinstance(op, Appender)
    def matches(self, vals):
        return ( v for v in vals if self.value in v )


class AppenderUnique(Appender):
    def __repr__(self):
        return '+?'



#
#
#
def itemof(node, val):
    return val if isinstance(node, (str, bytes)) else node.__class__([val])


class Key(Op):
    @classmethod
    def concrete(cls, val):
        import numbers
        if isinstance(val, numbers.Number):
            return cls(NumericQuoted(val))
        return cls(Word(val))
    @property
    def op(self):
        return self.args[0]
    def is_pattern(self):
        return isinstance(self.op, Pattern)
    def __repr__(self):
        return f'{self.op}'
    def operator(self, top=False):
        return str(self) if top else '.' + str(self)
    def keys(self, node):
        if not hasattr(node, 'keys'):
            return ()
        return self.op.matches(node.keys())
    def items(self, node):
        for k in self.keys(node):
            try:
                yield k, node[k]
            except TypeError:
                pass
    def values(self, node):
        return ( v for _,v in self.items(node) )
    def is_empty(self, node):
        return not tuple(self.keys(node))
    def default(self):
        if self.is_pattern():
            return {}
        return {self.op.value: None}

    def match(self, op, specials=False):
        if not self.op.matchable(op.op, specials):
            return None
        val = next(self.op.matches((op.op.value,)), _marker)
        return None if val is _marker else Match(val)

    def update(self, node, key, val):
        node[key] = self.default() if val is ANY else val
        return node
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)
        for k in self.keys(node):
            node = self.update(node, k, val)
        return node

    def pop(self, node, key):
        node.pop(key, None)
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
        return f'[{self.op}]'
    def operator(self, top=False):
        return str(self)
    def keys(self, node):
        if hasattr(node, 'keys'):
            return super().keys(node)
        if self.is_pattern():
            return self.op.matches(idx for idx,_ in enumerate(node))
        try:
            _ = node[self.op.value]
            return (self.op.value,)
        except (KeyError, IndexError):
            return ()
    def default(self):
        if isinstance(self.op, Numeric) and self.op.is_int():
            return []
        return super().default()

    def update(self, node, key, val):
        if hasattr(node, 'keys'):
            return super().update(node, key, val)
        if len(node) <= key:
            node += itemof(node, val)
            return node
        if node[key] == val:
            return node
        try:
            node[key] = self.default() if val is ANY else val
        except TypeError:
            node = node[:key] + itemof(node, val) + node[key+1:]
        return node
    def upsert(self, node, val):
        return super().upsert(node, val)

    def pop(self, node, key):
        if hasattr(node, 'keys'):
            return super().pop(node, key)
        if hasattr(node, 'pop'):
            node.pop(key)
            return node
        return node.__class__(v for i,v in enumerated(node) if i != key)
    def remove(self, node, val):
        if hasattr(node, 'keys'):
            return super().remove(node, val)
        if val is ANY:
            if hasattr(node, 'pop'):
                popped = 0
                for k in list(self.keys(node)):
                    node.pop(k - popped)
                    popped += 1
                return node
            keys = tuple(self.keys(node))
            return node.__class__(v for i,v in enumerated(node) if i not in keys)
        if hasattr(node, 'remove'):
            try:
                node.remove(val)
            except (ValueError, KeyError):
                pass
            return node
        try:
            idx = node.index(val)
            node = node[:idx] + node[idx+1:]
        except ValueError:
            pass
        return node


class SlotSpecial(Slot):
    @classmethod
    def concrete(cls, val):
        return cls(val)
    def default(self):
        return []
    def keys(self, node):
        return (-1,)
    def items(self, node):
        for k in self.keys(node):
            try:
                yield k, node[k]
            except TypeError:
                pass
    def values(self, node):
        return ( v for _,v in self.items(node) )
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


class Slice(Op):
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
    def keys(self, node):
        return (self.slice(node),)
    def items(self, node):
        return ( (k,node[k]) for k in self.keys(node) )
    def values(self, node):
        return ( v for _,v in self.items(node) )
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
            return self.pop(self, node, self.slice(node))
        key = self.slice(node)
        if node[key] == val:
            return self.pop(self, node, key)
        return node


class Invert(Op):
    @classmethod
    def concrete(cls, val):
        return cls(val)
    def __repr__(self):
        return '-'
    def is_pattern(self):
        return False
    @property
    def op(self):
        return None
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
        self.transforms = tuple( tuple(r) for r in results.get('transforms', ()) )
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
    for k,v in cur.items(node):
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
