"""
"""
import collections.abc
import copy
import functools
import pyparsing as pp
import re


_marker = object()


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


class MetaNOP(type):
    def __repr__(cls):
        return ''

class NOP(metaclass=MetaNOP):
    @classmethod
    def match(cls, val):
        return None
    @classmethod
    def match_op(cls, op, specials=False):
        return None


class Const(Op):
    @property
    def value(self):
        return self.args[0]
    def match(self, val):
        return Match(val) if self.value == val else None
    def match_op(self, op, specials=False):
        if not isinstance(op, Const):
            return None
        return self.match(op.value)

class Integer(Const):
    @property
    def value(self):
        return int(self.args[0])
    def __repr__(self):
        return f'{self.value}'

class Word(Const):
    def __repr__(self):
        return f'{self.value}'

class String(Const):
    def __repr__(self):
        return f'{repr(self.value)}'


class Pattern(Op):
    pass

class Wildcard(Pattern):
    def match(self, val):
        return Match(val)
    def match_op(self, op, specials=False):
        if isinstance(op, (Const, Special) if specials else Const):
            return self.match(op.value)
        return None
    def __repr__(self):
        return f'*'

class Regex(Pattern):
    @property
    def pattern(self):
        return re.compile(self.args[0])
    def match(self, val):
        m = self.pattern.fullmatch(val)
        return Match(m[0]) if m else None
    def match_op(self, op, specials=False):
        if isinstance(op, (Const, Special) if specials else Const):
            return self.match(op.value)
        return None
    def __repr__(self):
        return f'/{self.args[0]}/'


class Special(Op):
    @property
    def value(self):
        return self.args[0]
    def match(self, val):
        return Match(val) if self.value == val else None
    def match_op(self, op, specials=False):
        if isinstance(op, Special):
            return self.match(op.value)
        return None

class Appender(Special):
    def __repr__(self):
        return '+'

class AppenderIf(Special):
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
        if not isinstance(node, dict):
            return ()
        matches = ( self.op.match(k) for k in node )
        return ( m.val for m in matches if m )
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
        m = self.op.match_op(op.op, specials)
        return m

    def update(self, node, key, val):
        node[key] = val
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
            if v == val:
                return self.pop(node, k)
        return node
    def clear(self, node):
        for k in list(self.keys(node)):
            self.pop(node, k)
        return node


class Slot(Key):
    @classmethod
    def concrete(cls, val):
        return cls(Integer(val) if isinstance(val, int) else String(val))
    def __repr__(self):
        return f'[{self.op}]'
    def operator(self, top=False):
        return str(self)
    def keys(self, node):
        if isinstance(node, dict):
            return super().keys(node)
        if self.is_pattern():
            matches = ( self.op.match(idx) for idx,_ in enumerate(node) )
            return ( m.val for m in matches if m )
        try:
            _ = node[self.op.value]
            return (self.op.value,)
        except (KeyError, IndexError):
            return ()
    def default(self):
        if isinstance(self.op, Integer):
            return []
        return super().default()

    def update(self, node, key, val):
        if isinstance(node, dict):
            return super().update(node, key, val)
        if len(node) <= key:
            node += itemof(node, val)
            return node
        if node[key] == val:
            return node
        try:
            node[key] = val
        except TypeError:
            node = node[:key] + itemof(node, val) + node[key+1:]
        return node
    def upsert(self, node, val):
        return super().upsert(node, val)

    def pop(self, node, key):
        if isinstance(node, dict):
            return super().pop(node, key)
        if hasattr(node, 'pop'):
            node.pop(key)
            return node
        return node.__class__(v for i,v in enumerated(node) if i != key)
    def remove(self, node, val):
        if isinstance(node, dict):
            return super().remove(node, val)
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
    def clear(self, node):
        if isinstance(node, dict):
            return super().clear(node)
        if hasattr(node, 'pop'):
            popped = 0
            for k in list(self.keys(node)):
                node.pop(k - popped)
                popped += 1
            return node
        keys = tuple(self.keys(node))
        return node.__class__(v for i,v in enumerated(node) if i not in keys)


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
    def clear(self, node):
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
        if not self.args:
            return '[]'
        start, stop, step = self.args
        m = [s(start), s(stop)]
        if step is not None:
            m += [s(stop)]
        return '[' + ':'.join(m) + ']'
    def is_pattern(self):
        return False
    def operator(self, top=False):
        return str(self)
    def slice(self, node):
        args = ( len(node) if a == '+' else a for a in self.args )
        return slice(*args)
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
        return Match(op.slice) if self.slice == op.slice else None

    def update(self, node, key, val):
        if node[key] == val:
            return node
        try:
            node[key] = val
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
        key = self.slice(node)
        if node[key] == val:
            return self.pop(self, node, key)
        return node
    def clear(self, node):
        return self.pop(self, node, self.slice(node))


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
    def assemble(self):
        return ''.join(op.operator(idx==0) for idx,op in enumerate(self.ops))
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)}, {list(self.transforms)})'
    def __hash__(self):
        return hash((self.ops, self.transforms))
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
    values = cur.values(node)
    if not ops:
        yield from values
        return
    for v in values:
        yield from gets(ops, v)


def updates(ops, node, val, has_defaults=False):
    cur, *ops = ops
    if not ops:
        return cur.upsert(node, val)
    if cur.is_empty(node) and not has_defaults:
        built = updates(ops, build_default(ops), val, True)
        return cur.upsert(node, built)
    for k,v in cur.items(node):
        node = cur.update(node, k, updates(ops, v, val, has_defaults))
    return node


def removes(ops, node, val=_marker):
    cur, *ops = ops
    if not ops:
        return cur.clear(node) if val is _marker else cur.remove(node, val)
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
