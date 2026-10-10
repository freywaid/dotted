"""
Dotted result model and transform registry.
"""
import itertools

from . import base
from . import predicates
from . import utils
from .access import Attr, Invert, Key, Slot
from .matchers import Const
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
        self.transforms = tuple(results.get('transforms', ()))
        guard_raw = results.get('guard', ())
        if guard_raw:
            op, val = guard_raw
            self.guard = val
            self.guard_op = predicates.PRED_OPS.get(op, predicates.EQ)
        else:
            self.guard = None
            self.guard_op = predicates.EQ
        self._hash = None

    @property
    def guard_negate(self):
        """
        Backward compat: True when guard_op is NE.
        """
        return self.guard_op is predicates.NE

    def guard_matches(self, val, root=None, parents=()):
        """
        True if val passes the template-level guard (or if no guard is set).
        A guard reference resolves against root, with ^ the value itself
        and ^^ and up its parents (see guard_passes).
        """
        if self.guard is None:
            return True
        return guard_passes(self.guard_op, val, self.guard, root, val, parents)

    def assemble(self, start=0, pedantic=False):
        return assemble(self, start, pedantic=pedantic, transforms=self.transforms)

    def written(self):
        """
        This path as assemble() writes it, both as the string and parsed.
        The parsed form leaves out a trailing [] that assemble() drops, so
        it is what the string says without having to parse it.
        """
        parts, dropped = render(self.ops)
        for t in self.transforms:
            parts.append('|' + t.operator())
        if not dropped:
            return ''.join(parts), self
        return ''.join(parts), Dotted({'ops': self.ops[:-1], 'transforms': self.transforms})
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)}, {list(self.transforms)})'
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
            try:
                self._hash = hash((self.ops, self.transforms, self.guard, self.guard_op))
            except TypeError:
                self._hash = hash((self.ops, Dotted._hashable(self.transforms), self.guard, self.guard_op))
        return self._hash
    def __len__(self):
        return len(self.ops)
    def __iter__(self):
        return iter(self.ops)
    def __eq__(self, ops):
        return (self.ops == ops.ops and self.transforms == ops.transforms
                and self.guard == ops.guard and self.guard_op == ops.guard_op)
    def __getitem__(self, key):
        return self.ops[key]
    def resolve(self, bindings, partial=False):
        """
        Return a new Dotted with all $N resolved in ops, transforms, and guard.
        """
        new_ops = tuple(op.resolve(bindings, partial) for op in self.ops)
        new_transforms = tuple(t.resolve(bindings, partial) for t in self.transforms)
        new_guard = (
            self.guard.resolve(bindings, partial)
            if self.guard is not None and hasattr(self.guard, 'resolve')
            else self.guard)
        if (all(no is oo for no, oo in zip(new_ops, self.ops))
                and all(nt is ot for nt, ot in zip(new_transforms, self.transforms))
                and new_guard is self.guard):
            return self
        guard_raw = (self.guard_op.op, new_guard) if new_guard is not None else ()
        return Dotted({'ops': new_ops, 'transforms': new_transforms, 'guard': guard_raw})

    def apply(self, val, root=None, parents=()):
        """
        val through this path's transforms (see apply_transforms).
        """
        return apply_transforms(val, self.transforms, root, parents)

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
        return needs_parents(self.ops, self.transforms, self.guard)

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
        if self.guard is not None or self.needs_parents:
            return None
        chain = []
        for op in self.ops:
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


def needs_parents(ops, transforms=(), guard=None):
    """
    True if a reference needs _parents tracking during traversal: in an
    access position, a relative reference with depth >= 2 (parent or
    higher); in a guard, filter or transform, where ^ is the value under
    test, any reference, since its parents are the ancestors.
    """
    for op in ops:
        # the matcher in the access position, when the op has one
        # (Invert's op is the NOP sentinel, not a matcher)
        matcher = getattr(op.most_inner, 'op', None)
        if isinstance(matcher, base.Op) and matcher.is_reference():
            if matcher.depth >= 2:
                return True
            continue
        if op.is_reference():
            return True
    if any(_reaches_above(t.params) for t in transforms):
        return True
    return guard is not None and _reaches_above((guard,))


def apply_transforms(val, transforms, root=None, parents=()):
    """
    Apply a sequence of transforms to a value. A reference in a transform
    argument resolves as the transform runs: $$(path) against root,
    $$(^path) against the value the transform receives, $$(^^path) and up
    against parents, nearest first. Raises UnresolvedReference when one
    does not resolve.
    """
    for t in transforms:
        fn = Dotted._registry[t.name]
        val = fn(val, *t.arguments(root, val, parents))
    return val


def guard_matcher(guard, root=None, node=None, parents=()):
    """
    The match op a guard or filter value compares with: the value as
    parsed, or a reference resolved against root, node and parents, node
    being the value under test (so $$(^x) looks x up in it). A reference
    path that is a pattern becomes a value group of what it resolves to.
    Raises UnresolvedReference when the reference does not resolve.
    """
    if not (hasattr(guard, 'is_reference') and guard.is_reference()):
        return guard
    val = guard.resolve_ref(root, node=node, parents=parents)
    if not guard.is_pattern():
        return Const(val)
    from . import containers
    return containers.ValueGroup(*(Const(v) for v in val))


def guard_passes(pred_op, val, guard, root=None, node=None, parents=()):
    """
    True if val satisfies pred_op against guard (see guard_matcher); a
    guard reference that does not resolve passes nothing.
    """
    try:
        matcher = guard_matcher(guard, root, node, parents)
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
