"""
"""
from . import base
from . import match
from .access import BaseOp, Key, Attr, normalize


class Recursive(BaseOp):
    """
    Recursive traversal operator. Matches a pattern at each level and recurses
    into matched values.

    *key        = follow key chains (inner = match.Word('key'))
    **          = recursive dict-key wildcard (inner = match.Wildcard())
    */re/       = recursive regex (inner = match.Regex('re'))
    *(*, [*])   = recurse through dict keys and list slots
    *(*, @*)    = recurse through dict keys and attributes
    *(*, [*], @*) = recurse through all accessor types
    """

    def __init__(self, inner, *args, accessors=None, depth_start=None, depth_stop=None, depth_step=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = inner          # match.Pattern op: match.Wildcard, match.Word, match.Regex, etc.
        self.accessors = accessors  # None = dict-key only, or branches tuple with cuts
        self.depth_start = depth_start
        self.depth_stop = depth_stop
        self.depth_step = depth_step

    def _render_accessors(self):
        """
        Render accessor branches including cut markers.
        """
        parts = []
        for item in self.accessors:
            if item is base.BRANCH_CUT:
                parts[-1] += '#'
            elif item is base.BRANCH_SOFTCUT:
                parts[-1] += '##'
            else:
                parts.append(item[0].operator(top=True))
        return ', '.join(parts)

    def __repr__(self):
        if self.accessors is not None:
            return f'*({self._render_accessors()})'
        if isinstance(self.inner, match.Wildcard):
            return f'**'
        return f'*{self.inner!r}'

    def __hash__(self):
        return hash(('recursive', self.inner, self.accessors, self.depth_start, self.depth_stop, self.depth_step, self.filters))

    def __eq__(self, other):
        return (isinstance(other, Recursive) and self.inner == other.inner
                and self.accessors == other.accessors
                and self.depth_start == other.depth_start
                and self.depth_stop == other.depth_stop
                and self.depth_step == other.depth_step
                and self.filters == other.filters)

    def is_pattern(self):
        return True

    def is_recursive(self):
        return True

    def operator(self, top=False):
        if self.accessors is not None:
            s = f'*({self._render_accessors()})'
        elif isinstance(self.inner, match.Wildcard):
            s = '**'
        else:
            q = normalize(self.inner.value) if isinstance(self.inner, (match.Word, match.String, match.Numeric, match.NumericQuoted)) else repr(self.inner)
            s = f'*{q}'
        # Depth slice
        if self.depth_start is not None or self.depth_stop is not None or self.depth_step is not None:
            s += ':' + ('' if self.depth_start is None else str(self.depth_start))
            if self.depth_stop is not None or self.depth_step is not None:
                s += ':' + ('' if self.depth_stop is None else str(self.depth_stop))
            if self.depth_step is not None:
                s += ':' + str(self.depth_step)
        # Filters
        for f in self.filters:
            s += f'&{f!r}'
        if not top:
            s = '.' + s
        return s

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in inner match op and accessor branches.
        """
        new_inner = self.inner.resolve(bindings, partial) if hasattr(self.inner, 'resolve') else self.inner
        new_accessors = None
        acc_changed = False
        if self.accessors is not None:
            new_accessors = []
            for item in self.accessors:
                if item is base.BRANCH_CUT or item is base.BRANCH_SOFTCUT:
                    new_accessors.append(item)
                    continue
                new_branch = tuple(
                    op.resolve(bindings, partial) for op in item)
                if not all(nb is ob for nb, ob in zip(new_branch, item)):
                    acc_changed = True
                new_accessors.append(new_branch)
            new_accessors = tuple(new_accessors)
        if new_inner is self.inner and not acc_changed:
            return self
        return Recursive(
            new_inner, accessors=new_accessors if new_accessors is not None else self.accessors,
            depth_start=self.depth_start, depth_stop=self.depth_stop, depth_step=self.depth_step)

    def match(self, op, specials=False):
        return self.inner.matchable(op, specials=specials)

    def _effective_branches(self):
        """
        Return accessor branches that drive recursion.
        Default (no explicit accessors): single branch with Key(self.inner).
        """
        if self.accessors is not None:
            return self.accessors
        return ((Key(self.inner),),)

    def _iter_node(self, node, **kwargs):
        """
        Yield (accessor, key, value) for all matching accessors on this node.
        Respects hard cuts: if a cut-marked branch matched, stop.
        """
        branches = self._effective_branches()
        i = 0
        while i < len(branches):
            item = branches[i]
            if item is base.BRANCH_CUT or item is base.BRANCH_SOFTCUT:
                i += 1
                continue
            acc = item[0]
            matched = False
            for k, v in acc.items(node, **kwargs):
                matched = True
                yield acc, k, v
            if matched and i + 1 < len(branches) and branches[i + 1] is base.BRANCH_CUT:
                break
            i += 1

    def _has_negative_depth(self):
        return ((self.depth_start is not None and self.depth_start < 0) or
                (self.depth_stop is not None and self.depth_stop < 0))

    def in_depth_range(self, depth, max_dtl=0):
        """
        Check if depth is within the configured depth range.

        Args:
            depth: current depth (0-based from first keys)
            max_dtl: max depth to leaf (0 = leaf, 1 = parent of leaf, etc.)
        """
        start = self.depth_start
        stop = self.depth_stop
        step = self.depth_step

        # No depth params: all depths
        if start is None and stop is None and step is None:
            return True

        # Convert negative indices using max_dtl
        # -1 = leaf (max_dtl == 0), -2 = penultimate (max_dtl == 1), -N = max_dtl == N-1
        if start is not None and start < 0:
            if max_dtl != abs(start) - 1:
                return False
            # For exact negative (no stop), just check this one
            if stop is None and step is None:
                return True
            start = depth  # matches, so convert to positive for range check

        orig_stop = self.depth_stop
        if stop is not None and stop < 0:
            # negative stop: -2 means penultimate, so exclude nodes closer to leaf
            # -2 requires max_dtl >= 1 (at least penultimate)
            if max_dtl < abs(stop) - 1:
                return False
            stop = None  # effectively no upper bound on depth

        # Exact depth (only start specified, and stop wasn't converted from negative)
        if stop is None and step is None and orig_stop is None:
            return depth == start

        # Range check
        eff_start = start if start is not None else 0
        if step is not None:
            if stop is not None:
                return depth in range(eff_start, stop + 1, step)
            # No stop with step: check start and step
            return depth >= eff_start and (depth - eff_start) % step == 0
        # No step
        if stop is not None:
            return eff_start <= depth <= stop
        return depth >= eff_start

    def _max_depth_to_leaf(self, node, seen=frozenset(), **kwargs):
        """
        Compute max depth to leaf from this node (structural, ignores filters/depth range).
        Uses seen (frozenset of ids) to prevent infinite recursion on self-similar values.
        """
        node_id = id(node)
        if node_id in seen:
            return 0
        seen = seen | {node_id}
        items = list(self._iter_node(node, **kwargs))
        if not items:
            return 0
        child_depths = [self._max_depth_to_leaf(v, seen, **kwargs) for _, _, v in items]
        return max(child_depths) + 1 if child_depths else 0

    def _collect_matches(self, node, paths, depth=0, prefix=(), seen=frozenset(), **kwargs):
        """
        Yield (prefix, value) for all nodes matching the recursive pattern.

        Traverses the tree depth-first, yielding matches at each level
        before recursing into children (parent-before-children ordering).
        Uses seen (frozenset of ids) to prevent infinite recursion on self-similar values.
        """
        node_id = id(node)
        if node_id in seen:
            return
        seen = seen | {node_id}
        items = list(self._iter_node(node, **kwargs))
        if not items:
            return

        for acc, k, v in items:
            cp = prefix + (acc.concrete(k),) if paths else prefix
            if not any(True for _ in self.filtered((v,))):
                yield from self._collect_matches(v, paths, depth + 1, cp, seen, **kwargs)
                continue
            max_dtl = self._max_depth_to_leaf(v, seen=frozenset(), **kwargs) if self._has_negative_depth() else 0
            if not self.in_depth_range(depth, max_dtl):
                yield from self._collect_matches(v, paths, depth + 1, cp, seen, **kwargs)
                continue
            yield (cp, v)
            yield from self._collect_matches(v, paths, depth + 1, cp, seen, **kwargs)

    def push_children(self, stack, frame, paths):
        matches = list(self._collect_matches(frame.node, paths, prefix=frame.prefix, **(frame.kwargs or {})))
        for cp, v in reversed(matches):
            stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
        return ()

    def _assign(self, acc, node, k, v):
        """
        Assign value v to key k on node using the appropriate accessor method.
        """
        if isinstance(acc, Attr):
            return acc.update(node, k, v)
        node[k] = v
        return node

    def _update_recursive(self, ops, node, val, has_defaults, _path, nop, depth=0, guard=None, seen=frozenset(), **kwargs):
        from . import engine
        node_id = id(node)
        if node_id in seen:
            return node
        seen = seen | {node_id}
        items = list(self._iter_node(node, **kwargs))
        if not items:
            return node

        for acc, k, v in items:
            # Recurse first (bottom-up)
            v = self._update_recursive(ops, v, val, has_defaults, _path, nop, depth + 1, guard, seen, **kwargs)
            node = self._assign(acc, node, k, v)
            if not any(True for _ in self.filtered((v,))):
                continue
            max_dtl = self._max_depth_to_leaf(v) if self._has_negative_depth() else 0
            if not self.in_depth_range(depth, max_dtl):
                continue
            if guard and not guard(v):
                continue
            if ops:
                node = self._assign(acc, node, k, engine.updates(ops, v, val, has_defaults, _path + [(self, k)], nop, **kwargs))
            elif not nop:
                node = self._assign(acc, node, k, val)
        return node

    def do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs):
        return self._update_recursive(ops, node, val, has_defaults, _path, nop, **kwargs)

    def _remove_recursive(self, ops, node, val, nop, depth=0, guard=None, seen=frozenset(), **kwargs):
        from . import engine
        node_id = id(node)
        if node_id in seen:
            return node
        seen = seen | {node_id}
        items = list(self._iter_node(node, **kwargs))
        if not items:
            return node

        to_remove = []
        for acc, k, v in items:
            # Recurse first (bottom-up)
            v = self._remove_recursive(ops, v, val, nop, depth + 1, guard, seen, **kwargs)
            node = self._assign(acc, node, k, v)
            if not any(True for _ in self.filtered((v,))):
                continue
            max_dtl = self._max_depth_to_leaf(v) if self._has_negative_depth() else 0
            if not self.in_depth_range(depth, max_dtl):
                continue
            if guard and not guard(v):
                continue
            if ops:
                node = self._assign(acc, node, k, engine.removes(ops, v, val, nop=False, **kwargs))
            elif not nop and (val is base.ANY or v == val):
                to_remove.append((acc, k))
        for acc, k in reversed(to_remove):
            if isinstance(acc, Attr):
                node = acc.pop(node, k)
            else:
                del node[k]
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        return self._remove_recursive(ops, node, val, nop, **kwargs)


class RecursiveFirst(Recursive):
    """
    First-match variant of Recursive -- yields only the first result.
    """

    def __repr__(self):
        return super().__repr__() + '?'

    def operator(self, top=False):
        s = super().operator(top)
        # Insert ? before filters if present, otherwise append
        return s + '?'

    def push_children(self, stack, frame, paths):
        for cp, v in self._collect_matches(frame.node, paths, prefix=frame.prefix, **(frame.kwargs or {})):
            stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
            return ()
        return ()
