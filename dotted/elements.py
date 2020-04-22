"""
"""
import collections.abc
import copy
import functools
import pyparsing as pp
import re


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
    def match_op(cls, op):
        return None

class Const(Op):
    @property
    def value(self):
        return self.args[0]
    def match(self, val):
        return Match(val) if self.value == val else None
    def match_op(self, op):
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

class Appender(Const):
    def __repr__(self):
        return '+'

class AppenderIf(Const):
    def __repr__(self):
        return '+?'

class Pattern(Op):
    pass

class Wildcard(Pattern):
    def match(self, val):
        return Match(val)
    def match_op(self, op):
        if not isinstance(op, Const):
            return None
        return self.match(op.value)
    def __repr__(self):
        return f'*'

class Regex(Pattern):
    @property
    def pattern(self):
        return re.compile(self.args[0])
    def match(self, val):
        m = self.pattern.fullmatch(val)
        return Match(m[0]) if m else None
    def match_op(self, op):
        if not isinstance(op, Const):
            return None
        return self.match(op.value)
    def __repr__(self):
        return f'/{self.args[0]}/'


#
#
#
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
    def default_keys(self):
        if self.is_pattern():
            return ()
        return (self.op.value,)
    def default(self):
        if self.is_pattern():
            return {}
        return {self.op.value: None}
    def match(self, op):
        m = self.op.match_op(op.op)
        return m
    def add(self, node, key, val):
        node[key] = val
        return node
    def update(self, node, val):
        keys = self.keys(node) if self.is_pattern() else self.default_keys()
        for k in keys:
            node[k] = val
        return node
    def remove(self, node):
        for k in list(self.keys(node)):
            del node[k]


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
    def default_keys(self):
        if self.is_pattern():
            return ()
        return (self.op.value,)
    def default(self):
        if isinstance(self.op, Integer):
            return []
        return super().default()
    def add(self, node, key, val):
        if isinstance(node, dict):
            return super().add(node, key, val)
        node += val if isinstance(node, (str, bytes)) else node.__class__([val])
        return node
    def remove(self, node):
        if isinstance(node, dict):
            return super().remove(node)
        popped = 0
        for k in list(self.keys(node)):
            node.pop(k - popped)
            popped += 1


class SlotAppend(Op):
    @classmethod
    def concrete(cls, val):
        return cls(val)
    def is_pattern(self):
        return False
    @property
    def op(self):
        return None
    def __repr__(self):
        return self.args[0]
    def operator(self, top=False):
        return str(self)
    def default(self):
         return []
    def default_keys(self):
        return ('+',)
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
    def match(self, op):
        return None
    def remove(self, node):
        pass
    def add(self, node, key, val):
        if '+' in key:
            node += val if isinstance(node, (str, bytes)) else node.__class__([val])
        else:
            node[key] = val
        return node
    def update(self, node, val):
        if self.args[0] == '+?' and val in node:
            return node
        node += val if isinstance(node, (str, bytes)) else node.__class__([val])
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
    def default_keys(self):
        return (slice(0,1),)
    def default(self):
        return []
    def match(self, op):
        if not isinstance(op, Slice):
            return None
        return Match(op.slice) if self.slice == op.slice else None
    def add(self, node, key, val):
        node += val if isinstance(node, (str, bytes)) else node.__class__([val])
        return node
    def update(self, node, val):
        node[self.slice(node)] = val
        return node
    def remove(self, node):
        del node[self.slice(node)]

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
    for k in cur.default_keys():
        built = cur.add(built, k, build_default(ops))
    return built


def build(ops, node, deepcopy=True):
    cur, *ops = ops
    built = node.__class__()
    for k,v in cur.items(node):
        if not ops:
            built = cur.add(built, k, copy.deepcopy(v) if deepcopy else v)
        else:
            built = cur.add(built, k, build(ops, v, deepcopy=deepcopy))
    if not built:
        built = build_default([cur]+ops)
    return built


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
        return cur.update(node, val)
    if cur.is_empty(node) and not has_defaults:
        built = updates(ops, build_default(ops), val, True)
        return cur.update(node, built)
    for v in cur.values(node):
        updates(ops, v, val, has_defaults)
    return node


def removes(ops, node):
    cur, *ops = ops
    if not ops:
        cur.remove(node)
        return node
    for v in cur.values(node):
        removes(ops, v)
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
