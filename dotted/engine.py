"""
Traversal engine for dotted path operations.

Core traversal functions (walk, gets, updates, removes, expands).
"""
import copy

from . import base
from . import matchers
from . import wrappers
from .access import Attr, Slot
from .results import Dotted


def _needs_parents(ops):
    """
    True if any op in the chain is a relative reference with depth >= 2
    (parent or higher), requiring _parents tracking during traversal.
    """
    for op in ops:
        inner = op.most_inner
        if (hasattr(inner, 'is_reference') and inner.is_reference()
                and inner.op.depth >= 2):
            return True
    return False


def build_default(ops):
    cur, *ops = ops
    if not ops:
        # At leaf - for numeric Slot, populate index with None
        if isinstance(cur, Slot) and isinstance(cur.op, matchers.Numeric) and cur.op.is_int():
            idx = cur.op.value
            return [None] * (idx + 1)
        return cur.default()
    built = cur.default()
    return cur.upsert(built, build_default(ops))


def build(ops, node, deepcopy=True, **kwargs):
    cur, *ops = ops
    built = node.__class__()
    for k,v in cur.items(node, **kwargs):
        if not ops:
            built = cur.update(built, k, copy.deepcopy(v) if deepcopy else v)
        else:
            built = cur.update(built, k, build(ops, v, deepcopy=deepcopy, **kwargs))
    return built or build_default([cur]+ops)


SIMPLE_BAIL = object()


def simple_get(chain, node, strict=False):
    """
    Fast path for simple paths (see Dotted.simple_chain): follow literal
    keys with direct dict/list/attr access, skipping walk() entirely.
    Returns the found value, base.marker when the path misses, or
    SIMPLE_BAIL when a node's type falls outside the fast path — the
    caller then falls back to the full traversal.
    """
    for kind, key in chain:
        if kind == 'attr':
            node = getattr(node, key, base.marker)
            if node is base.marker:
                return base.marker
            continue
        t = type(node)
        if t is dict:
            # strict: Slot never coerces to dict keys
            if strict and kind == 'slot':
                return base.marker
            node = node.get(key, base.marker)
            if node is base.marker:
                return base.marker
            continue
        if t is list or t is tuple:
            # strict: Key never coerces to sequence indices
            if strict and kind == 'key':
                return base.marker
            if type(key) is not int:
                return base.marker
            try:
                node = node[key]
            except IndexError:
                return base.marker
            continue
        # dict subclasses, custom containers, None, etc: full traversal
        return SIMPLE_BAIL
    return node


def iter_until_cut(gen):
    """
    Consume a get generator until base.CUT_SENTINEL; yield values, stop on sentinel.
    """
    for x in gen:
        if x is base.CUT_SENTINEL:
            return
        yield x


def process(stack, paths):
    """
    Process frames at the current depth level and any nested levels
    pushed by ops within it. Yields (path, value) results.
    """
    level = stack.level
    while stack.level >= level and stack.current:
        frame = stack.pop()
        if not frame.ops:
            yield (frame.prefix if paths else None, frame.node)
            continue
        op = frame.ops[0]
        frame.ops = frame.ops[1:]
        yield from op.push_children(stack, frame, paths)


def walk(ops, node, paths=True, **kwargs):
    """
    Yield (path_tuple, value) for all matches.
    path_tuple is a tuple of concrete ops when paths=True, None when paths=False.
    """
    kwargs.setdefault('_root', node)
    if '_parents' not in kwargs:
        kwargs['_parents'] = () if _needs_parents(ops) else None
    stack = base.DepthStack()
    stack.push(base.Frame(tuple(ops), node, (), kwargs=kwargs or None))
    yield from process(stack, paths)


def gets(ops, node, **kwargs):
    """
    Yield values for all matches. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, paths=False, **kwargs):
        if path is base.CUT_SENTINEL:
            yield base.CUT_SENTINEL
        else:
            yield val


def _is_container(obj):
    """
    Check if object can be used as a container for dotted updates.
    """
    if obj is None:
        return False
    # Dict-like, sequence-like, or has attributes
    return (hasattr(obj, 'keys') or hasattr(obj, '__len__') or
            hasattr(obj, '__iter__') or hasattr(obj, '__dict__') or
            hasattr(type(obj), '__slots__'))


def _format_path(segments):
    """
    Consume an iterable of (op, k) segments and assemble the path string for error messages.
    Uses position and op type to decide .key, @attr, [k] and no leading dot on first segment.
    """
    result = []
    for i, (op, k) in enumerate(segments):
        cur = op.inner if isinstance(op, wrappers.Wrap) else op
        first = i == 0
        if isinstance(cur, Attr):
            result.append('@' + str(k))
        elif isinstance(cur, Slot):
            result.append(f'[{k}]')
        elif isinstance(k, int):
            # Assume int => slot (bracket index) when op type is unknown
            result.append(f'[{k}]')
        else:
            # Key or other key-like
            result.append('.' + str(k) if not first else str(k))
    return ''.join(result)


def updates(ops, node, val, has_defaults=False, _path=None, nop=False, **kwargs):
    kwargs.setdefault('_root', node)
    if '_parents' not in kwargs:
        kwargs['_parents'] = () if _needs_parents(ops) else None
    if _path is None:
        _path = []
    if not has_defaults and not _is_container(node):
        path_str = _format_path(_path)
        location = f" at '{path_str}'" if path_str else ""
        raise TypeError(
            f"Cannot update {type(node).__name__}{location} - "
            "use a dict, list, or other container"
        )
    cur, *ops = ops
    return cur.do_update(ops, node, val, has_defaults, _path, nop, **kwargs)


def removes(ops, node, val=base.ANY, nop=False, **kwargs):
    kwargs.setdefault('_root', node)
    if '_parents' not in kwargs:
        kwargs['_parents'] = () if _needs_parents(ops) else None
    cur, *ops = ops
    return cur.do_remove(ops, node, val, nop, **kwargs)


def expands(ops, node, **kwargs):
    """
    Yield Dotted objects for all matched paths. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, paths=True, **kwargs):
        if path is base.CUT_SENTINEL:
            return
        yield Dotted({'ops': path, 'transforms': ops.transforms})
