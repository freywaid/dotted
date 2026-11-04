"""
Traversal engine for dotted path operations.

Core traversal functions (walk, gets, updates, removes, expands).
"""
import copy

from . import base
from . import matchers
from . import wrappers
from .access import Attr, Slot
from . import results
from .results import Dotted


def _needs_parents(ops):
    """
    True if any op in the chain is a relative reference with depth >= 2
    (parent or higher), requiring _parents tracking during traversal.
    A parsed path remembers the answer.
    """
    if isinstance(ops, Dotted):
        return ops.needs_parents
    return results.needs_parents(ops)


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


def build(ops, node, deepcopy=True, strict=False, settings=None, resolver=None):
    if settings is None:
        settings = base.Settings(None, strict, resolver=resolver)
    cur, *ops = ops
    built = node.__class__()
    for k,v in cur.items(node, settings=settings):
        if not ops:
            # stdlib copy, not utils.deepcopy: copium gets slow at small
            # copies like these after a large one (percolab/copium#54)
            built = cur.update(built, k, copy.deepcopy(v) if deepcopy else v)
        else:
            built = cur.update(built, k, build(ops, v, deepcopy, strict, settings))
    return built or build_default([cur]+ops)


SIMPLE_BAIL = object()

# simple_get's read of a list or tuple: an index
SEQUENCE = object()


def item_get(node, key, default):
    """
    The get() of a dict-like that has keys() but no get() of its own.
    """
    try:
        return node[key]
    except (KeyError, IndexError, TypeError):
        return default


def seeded_reads():
    """
    The reads simple_get knows before it sees a node.
    """
    return {dict: dict.get, list: SEQUENCE, tuple: SEQUENCE}


# type -> how simple_get reads a node of it, decided once per type
READS = seeded_reads()
TYPE_CACHE = True


def set_type_cache(enable=True):
    """
    Enable/disable the per-type cache behind the get() fast path, which
    decides once per type how a node of it is read (escape hatch: when
    off, every node's type is looked at afresh). On by default. Toggling
    discards what is cached. Returns the previous setting.
    """
    global TYPE_CACHE
    prev = TYPE_CACHE
    TYPE_CACHE = bool(enable)
    READS.clear()
    if TYPE_CACHE:
        READS.update(seeded_reads())
    return prev


def read_of(t):
    """
    How simple_get reads a node of type t, by its interface, as Key and
    Slot duck-type it: a dict-like's unbound get(), SEQUENCE for a list or
    tuple, None for the full traversal. Cached when the type cache is on.
    """
    if hasattr(t, 'keys'):
        read = getattr(t, 'get', None) or item_get
    elif issubclass(t, (list, tuple)):
        read = SEQUENCE
    else:
        read = None
    if TYPE_CACHE:
        READS[t] = read
    return read


def simple_get(chain, node, strict=False):
    """
    Fast path for simple paths (see Dotted.simple_chain): follow literal
    keys with direct dict/list/attr access, skipping walk() entirely.
    Returns the found value, base.marker when the path misses, or
    SIMPLE_BAIL when a node's type falls outside the fast path — the
    caller then falls back to the full traversal.
    """
    # locals, not a module global's lookup per step
    reads = READS
    sequence = SEQUENCE
    marker = base.marker
    for kind, key in chain:
        if kind == 'attr':
            node = getattr(node, key, marker)
            if node is marker:
                return marker
            continue
        t = type(node)
        # READS is empty while the type cache is off
        try:
            read = reads[t]
        except KeyError:
            read = read_of(t)
        # custom containers, None, etc: full traversal
        if read is None:
            return SIMPLE_BAIL
        if read is not sequence:
            # strict: Slot never coerces to dict keys
            if strict and kind == 'slot':
                return marker
            try:
                node = read(node, key, marker)
            except TypeError:
                return marker
            if node is marker:
                return marker
            continue
        # strict: Key never coerces to sequence indices
        if strict and kind == 'key':
            return marker
        if type(key) is not int:
            return marker
        try:
            node = node[key]
        except IndexError:
            return marker
    return node


def values(ops, node, strict=False, settings=None, transforms=True, resolver=None, default=None):
    """
    Yield the value of every match, stopping at a cut. The same values as
    iter_until_cut(gets(...)), through one generator. transforms=False
    skips the ops' transforms (apply_transforms=False); default is what an
    aligned (+) match yields when the rest of the path finds nothing.
    """
    for path, val in walk(ops, node, False, strict, settings, transforms, resolver, default):
        if path is base.CUT_SENTINEL:
            return
        yield val


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
        if type(frame) is base.FrameSource:
            source = frame
            frame = next(source.frames, None)
            if frame is None:
                continue
            stack.push(source)
        if frame.pending and not frame.settle():
            continue
        if not frame.ops:
            yield (frame.prefix if paths else None, frame.node)
            continue
        yield from advance(frame, stack, paths)


def advance(frame, stack, paths):
    """
    Take the first op off frame and have it push the frames for its
    matches; the op's own results, if any.
    """
    op = frame.ops[0]
    frame.ops = frame.ops[1:]
    if op.is_aligned():
        return aligned(op, stack, frame, paths)
    return op.push_children(stack, frame, paths)


def aligned(op, stack, frame, paths):
    """
    The results of an aligned (+) op: each of its matches yields what the
    rest of the path finds under it or, when that is nothing, the default
    once, at the match's path followed by the rest of the path as written.
    """
    stack.push_level()
    found = list(op.push_children(stack, frame, paths))
    matches = []
    while stack.current:
        match = stack.pop()
        if type(match) is base.FrameSource:
            matches.extend(match.frames)
            continue
        matches.append(match)
    stack.pop_level()
    for match in matches:
        # processing takes the ops off the frame; the fill's path needs them
        missing = match.prefix + match.ops if paths else None
        stack.push_level()
        stack.push(match)
        results = list(process(stack, paths))
        stack.pop_level()
        found.extend(results)
        if results and results[-1][0] is base.CUT_SENTINEL:
            return found
        if not results:
            found.append((missing, match.settings.default))
    return found


def settings_for(ops, node, strict, settings, transforms=True, resolver=None, default=None):
    """
    The Settings a traversal of `ops` from `node` runs under: the ones
    passed in when this is a traversal within another, else new ones.
    """
    if settings is not None:
        return settings
    return base.Settings(node, strict, () if _needs_parents(ops) else None, transforms, resolver, default)


def walk(ops, node, paths=True, strict=False, settings=None, transforms=True, resolver=None, default=None):
    """
    Yield (path_tuple, value) for all matches.
    path_tuple is a tuple of concrete ops when paths=True, None when paths=False.
    transforms=False skips the ops' transforms (apply_transforms=False);
    resolver is the traversal's, else the process default; default is
    what an aligned (+) match yields when the rest of the path finds nothing.
    """
    settings = settings_for(ops, node, strict, settings, transforms, resolver, default)
    stack = base.DepthStack()
    stack.push(base.Frame(tuple(ops), node, (), settings=settings))
    yield from process(stack, paths)


def gets(ops, node, strict=False, settings=None, resolver=None):
    """
    Yield values for all matches. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, False, strict, settings, True, resolver):
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


def updates(ops, node, val, has_defaults=False, _path=None, nop=False, strict=False, settings=None, transforms=True, resolver=None):
    settings = settings_for(ops, node, strict, settings, transforms, resolver)
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
    return cur.do_update(ops, node, val, has_defaults, _path, nop, settings=settings)


def removes(ops, node, val=base.ANY, nop=False, strict=False, settings=None, resolver=None):
    settings = settings_for(ops, node, strict, settings, True, resolver)
    cur, *ops = ops
    return cur.do_remove(ops, node, val, nop, settings=settings)


def expands(ops, node, strict=False, settings=None, resolver=None):
    """
    Yield Dotted objects for all matched paths. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, True, strict, settings, False, resolver):
        if path is base.CUT_SENTINEL:
            return
        yield Dotted({'ops': path})
