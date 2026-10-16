"""
Dotted result model and transform registry.
"""
import itertools

from . import base
from . import predicates
from . import utils
from . import wrappers
from .access import Attr, Invert, Key, Slot
from .matchers import Const, ResolvedValue
from .utils import lazyprop


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
        self._hash = None

    @property
    def transforms(self):
        """
        The transforms on the last op, a.b|int, or () — ops own their
        transforms; this is the trailing ones as a path shows them.
        """
        return self.ops[-1].transforms if self.ops and isinstance(self.ops[-1], wrappers.TransformWrap) else ()

    def segments(self):
        """
        The plain value of each segment, wrappers aside: ('$amount',
        'value') for $amount.value|sub:1, whatever guards, filters,
        transforms or ~ sit on them. A segment with no single value, a
        pattern, group or recursive op, is given as its text.
        """
        out = []
        for op in self.ops:
            inner = op.most_inner
            matcher = getattr(inner, 'op', None)
            if isinstance(matcher, Const):
                out.append(matcher.value)
                continue
            out.append(render([inner])[0][0])
        return tuple(out)

    def folded(self, runs):
        """
        This path with runs of segments folded into pre-resolved keys, as
        a resolver's fold() asks (see base.Resolver): runs are (start,
        stop, value) triples, and ops[start:stop] become one op whose key
        is the value, keeping the last op's decorations. An inner op of a
        run may carry none; overlapping runs are an error.
        """
        out = []
        at = 0
        for start, stop, value in sorted(runs, key=lambda run: run[0]):
            if start < at or stop <= start:
                raise ValueError(f'fold {(start, stop)} overlaps another or is empty')
            out.extend(self.ops[at:start])
            out.append(_folded(self.ops[start:stop], value))
            at = stop
        out.extend(self.ops[at:])
        return Dotted({'ops': tuple(out)})

    def assemble(self, start=0, pedantic=False):
        return assemble(self, start, pedantic=pedantic)

    def written(self):
        """
        This path as assemble() writes it, both as the string and parsed.
        The parsed form leaves out a trailing [] that assemble() drops, so
        it is what the string says without having to parse it.
        """
        parts, dropped = render(self.ops)
        if not dropped:
            return ''.join(parts), self
        return ''.join(parts), Dotted({'ops': self.ops[:-1]})
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)})'
    @staticmethod
    def _hashable(obj):
        """
        Recursively convert unhashable types to hashable equivalents.
        """
        if utils.is_list_like(obj):
            return tuple(Dotted._hashable(x) for x in obj)
        if utils.is_set_like(obj):
            return frozenset(Dotted._hashable(x) for x in obj)
        if utils.is_dict_like(obj):
            if hasattr(obj, 'items') and callable(obj.items):
                iterable = obj.items()
            else:
                iterable = ((k, obj[k]) for k in obj)
            return tuple(sorted((k, Dotted._hashable(v)) for k, v in iterable))
        return obj
    def __hash__(self):
        if self._hash is None:
            # not hash(self.ops): a Dotted must not collide with its own ops tuple
            self._hash = hash(('dotted', self.ops))
        return self._hash
    def __len__(self):
        return len(self.ops)
    def __iter__(self):
        return iter(self.ops)
    def __eq__(self, ops):
        return isinstance(ops, Dotted) and self.ops == ops.ops
    def __getitem__(self, key):
        if isinstance(key, slice):
            # a slice is a path too: ops[1:] is the path past its head
            return Dotted({'ops': self.ops[key]})
        return self.ops[key]
    def resolve(self, bindings, partial=False):
        """
        Return a new Dotted with all $N resolved in the ops.
        """
        new_ops = tuple(op.resolve(bindings, partial) for op in self.ops)
        if all(no is oo for no, oo in zip(new_ops, self.ops)):
            return self
        return Dotted({'ops': new_ops})

    def apply(self, val, settings=base.SETTINGS, parents=()):
        """
        val through the trailing transforms (see apply_transforms).
        """
        return apply_transforms(val, self.transforms, settings, parents)

    @lazyprop
    def variadic(self):
        """
        True if any op can consume a variable number of path segments when
        matching (recursive ops, groups). Computed once per parsed path.
        """
        return any(op.is_variadic() for op in self.ops)

    @lazyprop
    def needs_parents(self):
        """
        True if a reference needs the traversal to track parents: one to a
        parent or higher in an access position, or one in a transform or
        guard reaching the node holding the value or above. Computed once
        per parsed path.
        """
        return needs_parents(self.ops)

    @lazyprop
    def simple_chain(self):
        """
        Tuple of (kind, key) pairs when the access chain is simple — a plain
        chain of concrete Key/Attr/Slot accesses with no patterns,
        substitutions, references, guards, or filters — else None. Transforms
        are permitted (they apply after the lookup). Computed once and
        cached; drives the fast path in get() that skips walk().
        kind is 'key', 'attr', or 'slot'.
        """
        if self.needs_parents:
            return None
        ops = self.ops
        if ops and isinstance(ops[-1], wrappers.TransformWrap):
            # trailing transforms apply after the lookup (see get)
            ops = ops[:-1] + (ops[-1].inner,)
        chain = []
        for op in ops:
            # exact types: subclasses like SlotSpecial have different semantics
            if type(op) not in (Key, Attr, Slot):
                return None
            if not isinstance(op.op, Const):
                return None
            kind = 'attr' if isinstance(op, Attr) else 'slot' if isinstance(op, Slot) else 'key'
            chain.append((kind, op.op.value))
        return tuple(chain)

Dotted.registry.__doc__ = rdoc()


def _reaches_above(params):
    """
    True if a reference among params reaches the node holding the value
    under test or above (two or more ^), which only tracked parents give.
    """
    return any(getattr(p, 'depth', 0) >= 2 for p in params)


def needs_parents(ops, transforms=()):
    """
    True if a reference needs _parents tracking during traversal: in an
    access position, a relative reference with depth >= 2 (parent or
    higher); in an op's transforms, where ^ is the value, one reaching
    ^^ or above; in a guard or filter, any reference, since its parents
    are the ancestors.
    """
    for op in ops:
        if isinstance(op, wrappers.TransformWrap):
            if any(_reaches_above(t.params) for t in op.transforms):
                return True
            op = op.inner
        # the matcher in the access position, when the op has one
        # (Invert's op is the NOP sentinel, not a matcher)
        matcher = getattr(op.most_inner, 'op', None)
        if isinstance(matcher, base.Op) and matcher.is_reference():
            if matcher.depth >= 2:
                return True
            continue
        if op.is_reference():
            return True
    return any(_reaches_above(t.params) for t in transforms)


def _folded(ops, value):
    """
    One op for the run of ops: the last op's decorations around an access
    whose key is `value`, pre-resolved, so it is not offered to the
    resolver again. The ops before the last must be plain accesses.
    """
    for op in ops[:-1]:
        if type(op) not in (Key, Attr, Slot):
            raise ValueError(f'folded segment {op!r} carries a guard, filter, transform or ~; only the last may')
    return _rewrapped(ops[-1], lambda access: type(access)(ResolvedValue(value)))


def _rewrapped(op, make):
    """
    op with its innermost access op replaced by make(access), every wrap
    around it kept.
    """
    if isinstance(op, wrappers.Wrap):
        return op.rewrap(_rewrapped(op.inner, make))
    return make(op)


def apply_transforms(val, transforms, settings=base.SETTINGS, parents=()):
    """
    Apply a sequence of transforms to a value. A reference in a transform
    argument resolves as the transform runs, through the traversal's
    resolver (see base.Resolver): $$(path) against the root, $$(^path)
    against the value the transform receives, $$(^^path) and up against
    parents, nearest first. Raises UnresolvedReference when one does not
    resolve.
    """
    for t in transforms:
        fn = Dotted._registry[t.name]
        val = fn(val, *t.arguments(settings, val, parents))
    return val


def guard_matcher(guard, settings=base.SETTINGS, node=None, parents=(), position='guards'):
    """
    The match op a guard or filter value compares with: the value as
    parsed, or a reference resolved through the traversal's resolver
    against node and parents, node being the value under test (so
    $$(^x) looks x up in it). A plain value is resolved too when the
    resolver takes literals at position, 'guards' or 'filters'. A reference
    path that is a pattern becomes a value group of what it resolves to.
    Raises UnresolvedReference when the reference does not resolve.
    """
    # container values (ContainerList and kin) are ops without is_reference
    reference = hasattr(guard, 'is_reference') and guard.is_reference()
    if not (reference or (settings.literals(position) and isinstance(guard, Const))):
        return guard
    val = settings.resolve(guard, node, parents)
    if not guard.is_pattern():
        return Const(val)
    from . import containers
    return containers.ValueGroup(*(Const(v) for v in val))


def guard_passes(pred_op, val, guard, settings=base.SETTINGS, node=None, parents=(), position='guards'):
    """
    True if val satisfies pred_op against guard (see guard_matcher); a
    guard reference that does not resolve passes nothing.
    """
    try:
        matcher = guard_matcher(guard, settings, node, parents, position)
    except base.UnresolvedReference:
        return False
    return any(True for _ in pred_op.matches((val,), matcher))


def render(ops, start=0, pedantic=False):
    """
    The text of each op from `start` on, and whether a redundant trailing []
    was dropped (see assemble).
    """
    parts = []
    top = True
    for op in itertools.islice(ops, start, None):
        parts.append(op.operator(top))
        if not isinstance(op, Invert):
            top = False
    dropped = not pedantic and not top and len(parts) > 1 and parts[-1] == '[]' and parts[-2] != '[]'
    if dropped:
        parts.pop()
    return parts, dropped


def assemble(ops, start=0, pedantic=False, transforms=()):
    """
    Reassemble ops into a dotted notation string.

    By default, strips a redundant trailing [] (e.g. hello[] -> hello)
    unless it follows another [] (hello[][] is preserved as-is).
    Set pedantic=True to always preserve trailing [].
    """
    parts, _ = render(ops, start, pedantic)
    for t in transforms:
        parts.append('|' + t.operator())
    return ''.join(parts)
