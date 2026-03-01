"""
Wrapper ops that decorate access ops with extra behavior.

Wrap           — abstract base; delegates all access-op methods to .inner
NopWrap        — match but don't mutate (~prefix)
ValueGuard     — key=value / [slot]=value direct value test
TypeRestriction — :type / :!type node-type constraint
FilterWrap     — &filter value-level predicate
"""
from . import base
from . import access
from . import predicates
from . import utypes


class Wrap(base.TraversalOp):
    """
    Abstract base for ops that wrap another op; use .inner to get the wrapped op.
    Provides default delegation for all access-op methods.
    """

    inner = None  # subclasses set in __init__

    def is_pattern(self):
        return self.inner.is_pattern() if hasattr(self.inner, 'is_pattern') else False

    def is_template(self):
        """
        Delegate to inner op.
        """
        return self.inner.is_template() if hasattr(self.inner, 'is_template') else False

    def is_reference(self):
        """
        Delegate to inner op.
        """
        return self.inner.is_reference() if hasattr(self.inner, 'is_reference') else False

    def default(self):
        return self.inner.default() if hasattr(self.inner, 'default') else {}

    def upsert(self, node, val):
        return self.inner.upsert(node, val) if hasattr(self.inner, 'upsert') else val

    def items(self, node, **kwargs):
        return self.inner.items(node, **kwargs) if hasattr(self.inner, 'items') else iter(())

    def values(self, node, **kwargs):
        return self.inner.values(node, **kwargs) if hasattr(self.inner, 'values') else iter(())

    def is_empty(self, node):
        return self.inner.is_empty(node) if hasattr(self.inner, 'is_empty') else True

    def update(self, node, key, val):
        return self.inner.update(node, key, val) if hasattr(self.inner, 'update') else node

    def match(self, op, specials=False):
        if not hasattr(self.inner, 'match'):
            return None
        try:
            return self.inner.match(op, specials=specials)
        except TypeError:
            return self.inner.match(op)

    def concrete(self, val):
        return self.inner.concrete(val)

    def pop(self, node, key):
        return self.inner.pop(node, key)

    def remove(self, node, val, **kwargs):
        return self.inner.remove(node, val, **kwargs)

    def push_children(self, stack, frame, paths):
        return self.inner.push_children(stack, frame, paths)


class NopWrap(Wrap):
    """
    Wraps a path segment so that update/remove matches but does not mutate.
    Use ~ prefix: ~a.b, .~a, ~(name.first), [~*&filter], @~a, ~@a.
    """

    def __init__(self, inner, *args, **kwargs):
        super().__init__(inner, *args, **kwargs)
        self.inner = inner

    def __repr__(self):
        return f'~{self.inner!r}'

    def __hash__(self):
        return hash(('nop', self.inner))

    def __eq__(self, other):
        return isinstance(other, NopWrap) and self.inner == other.inner

    def operator(self, top=False):
        s = self.inner.operator(top)
        # Empty slice: canonical ~[]
        if s == '[]':
            return '~[]'
        # Slots: always [~stuff] (tilde inside brackets)
        if s.startswith('['):
            return '[~' + s[1:]
        if top:
            return '~' + s
        if s.startswith('.'):
            return '.~' + s[1:]
        if s.startswith('@'):
            return '@~' + s[1:]
        return '~' + s

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in inner op.
        """
        new_inner = self.inner.resolve(bindings, partial)
        if new_inner is self.inner:
            return self
        return NopWrap(new_inner)

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        return self.inner.do_update(ops, node, val, has_defaults, _path, nop=True, nop_from_unwrap=True, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        return self.inner.do_remove(ops, node, val, nop=True, **kwargs)


def _guard_repr(guard):
    """
    Produce the repr string for a guard value (the RHS of key=value).
    """
    return repr(guard)


class ValueGuard(Wrap):
    """
    Wraps Key/Slot with a direct value test: key=value, [slot]=value.
    Optionally applies transforms before the guard check: key|int=7.
    """

    def __init__(self, inner, guard, negate=False, transforms=(), pred_op=None, *args, **kwargs):
        super().__init__(inner, *args, **kwargs)
        self.inner = inner    # Key or Slot
        self.guard = guard    # value op (matchers.Numeric, matchers.String, matchers.Wildcard, matchers.Regex, etc.)
        if pred_op is not None:
            self.pred_op = pred_op
        elif negate:
            self.pred_op = predicates.NE
        else:
            self.pred_op = predicates.EQ
        self.transforms = tuple(transforms)

    @property
    def negate(self):
        """
        Backward compat: True when pred_op is NE.
        """
        return self.pred_op is predicates.NE

    def __repr__(self):
        return self.operator(top=True)

    def __hash__(self):
        return hash(('guard', self.inner, self.guard, self.pred_op, self.transforms))

    def __eq__(self, other):
        return (isinstance(other, ValueGuard) and self.inner == other.inner
                and self.guard == other.guard and self.pred_op == other.pred_op
                and self.transforms == other.transforms)

    def _guard_matches(self, val):
        """
        True if val matches the guard value (after applying transforms).
        """
        if self.transforms:
            from .results import apply_transforms
            val = apply_transforms(val, self.transforms)
        return any(True for _ in self.pred_op.matches((val,), self.guard))

    def _transforms_operator(self):
        """
        Render the |transform suffix for operator/assemble.
        """
        if not self.transforms:
            return ''
        return ''.join('|' + t.operator() for t in self.transforms)

    def operator(self, top=False):
        return self.inner.operator(top) + self._transforms_operator() + self.pred_op.op + _guard_repr(self.guard)

    def is_pattern(self):
        return self.inner.is_pattern()

    def is_recursive(self):
        return self.inner.is_recursive()

    def default(self):
        return self.inner.default()

    def is_empty(self, node):
        return self.inner.is_empty(node)

    def values(self, node, **kwargs):
        return (v for v in self.inner.values(node, **kwargs) if self._guard_matches(v))

    def items(self, node, **kwargs):
        return ((k, v) for k, v in self.inner.items(node, **kwargs) if self._guard_matches(v))

    def keys(self, node, **kwargs):
        return (k for k, v in self.inner.items(node, **kwargs) if self._guard_matches(v))

    def upsert(self, node, val):
        # Only update entries where guard matches
        matched_keys = set(self.keys(node))
        if not matched_keys:
            return node
        # Delegate per-key update to inner
        for k in matched_keys:
            node = self.inner.update(node, k, val)
        return node

    def update(self, node, key, val):
        return self.inner.update(node, key, val)

    def remove(self, node, val, **kwargs):
        # Only remove entries where guard matches
        to_remove = [(k, v) for k, v in self.inner.items(node, **kwargs) if self._guard_matches(v)]
        for k, v in reversed(to_remove):
            if val is base.ANY or v == val:
                node = self.inner.pop(node, k)
        return node

    def pop(self, node, key):
        return self.inner.pop(node, key)

    def concrete(self, val):
        return self.inner.concrete(val)

    def match(self, op, specials=False):
        # Guard ignored for structural matching — delegate to inner
        try:
            return self.inner.match(op, specials=specials)
        except TypeError:
            return self.inner.match(op)

    def push_children(self, stack, frame, paths):
        """
        Non-recursive: items() already filters by guard, push onto stack.
        Recursive: collect matches from inner, filter by guard, push survivors.
        """
        if not self.inner.is_recursive():
            children = list(self.items(frame.node, **(frame.kwargs or {})))
            for k, v in reversed(children):
                cp = frame.prefix + (self.concrete(k),) if paths else frame.prefix
                stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
            return ()
        matches = [(cp, v) for cp, v in self.inner._collect_matches(
            frame.node, paths, prefix=frame.prefix, **(frame.kwargs or {})) if self._guard_matches(v)]
        for cp, v in reversed(matches):
            stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
        return ()

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in inner, guard, and transforms.
        """
        new_inner = self.inner.resolve(bindings, partial)
        new_guard = self.guard.resolve(bindings, partial) if hasattr(self.guard, 'resolve') else self.guard
        new_transforms = tuple(
            t.resolve(bindings, partial) for t in self.transforms)
        if (new_inner is self.inner and new_guard is self.guard
                and all(nt is ot for nt, ot in zip(new_transforms, self.transforms))):
            return self
        return ValueGuard(new_inner, new_guard, pred_op=self.pred_op, transforms=new_transforms)

    def do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs):
        if self.inner.is_recursive():
            return self.inner._update_recursive(
                ops, node, val, has_defaults, _path, nop, guard=self._guard_matches, **kwargs)
        return access.BaseOp.do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        if self.inner.is_recursive():
            return self.inner._remove_recursive(
                ops, node, val, nop, guard=self._guard_matches, **kwargs)
        return access.BaseOp.do_remove(self, ops, node, val, nop, **kwargs)


class TypeRestriction(Wrap):
    """
    Wraps an access op with a node-type constraint.
    :type (positive) or :!type / :!(t1, t2) (negative).
    """

    def __init__(self, inner, *types, negate=False, **kwargs):
        super().__init__(inner, **kwargs)
        self.inner = inner
        self.types = tuple(types)
        self.negate = negate

    def allows(self, node):
        """
        Return True if node's type is allowed by this restriction.
        """
        match = isinstance(node, self.types)
        return not match if self.negate else match

    def _type_suffix(self):
        """
        Render the :type or :!type suffix.
        """
        _reverse = {v: k for k, v in utypes.TYPE_REGISTRY.items()}
        names = [_reverse[t] for t in self.types if t in _reverse]
        if len(names) == 1:
            inner = names[0]
        else:
            inner = f'({", ".join(names)})'
        if self.negate:
            return f':!{inner}'
        return f':{inner}'

    def __repr__(self):
        return f'{self.inner!r}{self._type_suffix()}'

    def __hash__(self):
        return hash(('tr', self.inner, self.types, self.negate))

    def __eq__(self, other):
        return (isinstance(other, TypeRestriction)
                and self.inner == other.inner
                and self.types == other.types
                and self.negate == other.negate)

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in inner op.
        """
        new_inner = self.inner.resolve(bindings, partial)
        if new_inner is self.inner:
            return self
        return TypeRestriction(new_inner, *self.types, negate=self.negate)

    def operator(self, top=False):
        return self.inner.operator(top) + self._type_suffix()

    def items(self, node, **kwargs):
        if not self.allows(node):
            return ()
        return self.inner.items(node, **kwargs)

    def values(self, node, **kwargs):
        if not self.allows(node):
            return ()
        return self.inner.values(node, **kwargs)

    def keys(self, node, **kwargs):
        if not self.allows(node):
            return ()
        return self.inner.keys(node, **kwargs)

    def push_children(self, stack, frame, paths):
        """
        Short-circuit if node type is not allowed; otherwise delegate to inner.
        """
        if not self.allows(frame.node):
            return ()
        return self.inner.push_children(stack, frame, paths)

    def do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs):
        if not self.allows(node):
            return node
        return self.inner.do_update(ops, node, val, has_defaults, _path, nop, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        if not self.allows(node):
            return node
        return self.inner.do_remove(ops, node, val, nop, **kwargs)


class FilterWrap(Wrap):
    """
    Wraps an access op with value filters: key&filter, @attr&filter, [slot&filter].
    Filters are &-separated predicates that narrow which values match.
    """

    def __init__(self, inner, filters, *args, **kwargs):
        super().__init__(inner, *args, **kwargs)
        self.inner = inner
        self.filters = tuple(filters)

    def __repr__(self):
        return self.operator(top=True)

    def __hash__(self):
        return hash(('filter', self.inner, self.filters))

    def __eq__(self, other):
        return (isinstance(other, FilterWrap)
                and self.inner == other.inner
                and self.filters == other.filters)

    def operator(self, top=False):
        s = self.inner.operator(top)
        filter_str = ''.join(f'&{f!r}' for f in self.filters)
        # For slot-like inner ops, insert filter before closing bracket
        if s.endswith(']'):
            return s[:-1] + filter_str + ']'
        return s + filter_str

    def default(self):
        """
        Filters imply the value has structure to check against,
        so upgrade None defaults to empty containers.
        """
        d = self.inner.default()
        if isinstance(d, dict):
            return {k: {} if v is None else v for k, v in d.items()}
        import types as _types
        if isinstance(d, _types.SimpleNamespace):
            for k, v in vars(d).items():
                if v is None:
                    setattr(d, k, _types.SimpleNamespace())
            return d
        return d

    def items(self, node, **kwargs):
        """
        Apply filters to the inner items stream, preserving stream
        semantics (e.g. first-match filters only yield the first hit).
        """
        pairs = list(self.inner.items(node, **kwargs))
        for f in self.filters:
            vals = [v for _, v in pairs]
            filtered_vals = list(f.filtered(iter(vals)))
            # Build a set of object ids that survived filtering.
            # Use a counter to handle duplicate objects correctly.
            surviving = {}
            for v in filtered_vals:
                vid = id(v)
                surviving[vid] = surviving.get(vid, 0) + 1
            new_pairs = []
            for k, v in pairs:
                vid = id(v)
                if surviving.get(vid, 0) > 0:
                    new_pairs.append((k, v))
                    surviving[vid] -= 1
            pairs = new_pairs
        return iter(pairs)

    def values(self, node, **kwargs):
        return (v for _, v in self.items(node, **kwargs))

    def keys(self, node, **kwargs):
        return (k for k, _ in self.items(node, **kwargs))

    def is_empty(self, node):
        return not any(True for _ in self.items(node))

    def upsert(self, node, val):
        matched_keys = [k for k, _ in self.items(node)]
        if not matched_keys:
            return node
        for k in matched_keys:
            node = self.inner.update(node, k, val)
        return node

    def remove(self, node, val, **kwargs):
        to_remove = list(self.items(node, **kwargs))
        for k, v in reversed(to_remove):
            if val is base.ANY or v == val:
                node = self.inner.pop(node, k)
        return node

    def push_children(self, stack, frame, paths):
        """
        Push only children that pass the filter.
        """
        children = list(self.items(frame.node, **(frame.kwargs or {})))
        for k, v in reversed(children):
            cp = frame.prefix + (self.inner.concrete(k),) if paths else frame.prefix
            stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
        return ()

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in inner op and filters.
        """
        new_inner = self.inner.resolve(bindings, partial)
        new_filters = tuple(
            f.resolve(bindings, partial) if hasattr(f, 'resolve') else f
            for f in self.filters)
        if (new_inner is self.inner
                and all(nf is of for nf, of in zip(new_filters, self.filters))):
            return self
        return FilterWrap(new_inner, new_filters)

    def do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs):
        return access.BaseOp.do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        return access.BaseOp.do_remove(self, ops, node, val, nop, **kwargs)

    def match(self, op, specials=False):
        """
        Match inner, then compare filters.
        """
        inner_op = op.inner if isinstance(op, FilterWrap) else op
        try:
            result = self.inner.match(inner_op, specials=specials)
        except TypeError:
            result = self.inner.match(inner_op)
        if result is None:
            return None
        other_filters = op.filters if isinstance(op, FilterWrap) else ()
        for f, of in zip(self.filters, other_filters):
            if not f.matchable(of):
                return None
            m = f.match(of)
            if m is None:
                return None
            result += (base.MatchResult(m),)
        return result
