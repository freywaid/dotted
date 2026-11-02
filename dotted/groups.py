"""
"""
from . import base
from . import matchers
from . import wrappers
from . import engine
from . import results
from .access import AccessOp, Key, Attr, Slot, SlotSpecial


def leading_keys(path):
    """
    The keys of the plain constant accesses that path starts with, as a
    hashable tuple. Stops at the first segment that is anything else.
    """
    keys = []
    for op in path:
        if not isinstance(op, AccessOp) or not isinstance(op.op, matchers.Const):
            break
        key = op.op.value
        try:
            hash(key)
        except TypeError:
            break
        keys.append(key)
    return tuple(keys)


class SoftcutPaths:
    """
    The paths yielded by soft-cut branches, answering whether a later path
    overlaps any of them (one is a prefix of the other).

    Overlap is decided by base.path_overlaps. To avoid comparing against
    every stored path, each path is indexed by its leading keys (see
    leading_keys). Two paths can only overlap if, over the leading keys they
    both have, the keys are equal. So a few lookups find the candidates and
    path_overlaps confirms them.
    """
    def __init__(self):
        self.count = 0
        # leading keys -> paths with exactly those leading keys
        self.by_keys = {}
        # keys -> paths whose leading keys extend them
        self.by_prefix = {}

    def __len__(self):
        return self.count

    def add(self, path):
        self.count += 1
        keys = leading_keys(path)
        self.by_keys.setdefault(keys, []).append(path)
        for n in range(len(keys)):
            self.by_prefix.setdefault(keys[:n], []).append(path)

    def extend(self, paths):
        for path in paths:
            self.add(path)

    def overlaps(self, path):
        keys = leading_keys(path)
        # stored paths with no more leading keys than this one
        for n in range(len(keys) + 1):
            found = self.by_keys.get(keys[:n])
            if found and base.path_overlaps(found, path):
                return True
        # stored paths with more
        found = self.by_prefix.get(keys)
        return bool(found) and base.path_overlaps(found, path)


class OpGroup(base.TraversalOp):
    """
    Base class for all operation groups (disjunction, conjunction, negation, first-match).

    branches is a sequence of (branch_tuple, base.BRANCH_CUT/base.BRANCH_SOFTCUT?, branch_tuple, ...).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # args may contain base.BRANCH_CUT/base.BRANCH_SOFTCUT; normalize branch tuples
        out = []
        for x in self.args:
            if x in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                out.append(x)
            else:
                b = tuple(x) if isinstance(x, (list, tuple)) else (x,)
                out.append(b)
        self.branches = tuple(out)
        self.args = self.branches

    def __hash__(self):
        return hash(self.branches)

    def __eq__(self, other):
        # spelled out, not inherited: a compiled class that defines
        # __hash__ alone compares by identity (CPython inherits
        # tp_richcompare only together with tp_hash)
        return self.__class__ == other.__class__ and self.branches == other.branches

    def is_pattern(self):
        return True

    def is_variadic(self):
        return True

    def is_template(self):
        """
        True if any branch contains a template op.
        """
        for branch in base.branches_only(self.branches):
            for op in branch:
                if hasattr(op, 'is_template') and op.is_template():
                    return True
        return False

    def is_reference(self):
        """
        True if any branch contains an internal reference.
        """
        for branch in base.branches_only(self.branches):
            for op in branch:
                if hasattr(op, 'is_reference') and op.is_reference():
                    return True
        return False

    def default(self):
        """
        Derive default from the first branch's first op, so auto-creation works
        (e.g. slot group [(*&filter#, +)] defaults to []).
        """
        for branch in base.branches_only(self.branches):
            if branch:
                first_op = branch[0]
                # Unwrap any Wrap layer to find the underlying op
                inner = first_op
                while isinstance(inner, wrappers.Wrap):
                    inner = inner.inner
                # Slot groups operate on lists
                if isinstance(inner, Slot):
                    return []
                if hasattr(first_op, 'default'):
                    return first_op.default()
        return {}

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in all branches.
        """
        new_branches = []
        changed = False
        for item in self.branches:
            if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                new_branches.append(item)
                continue
            new_branch = tuple(
                op.resolve(bindings, partial) for op in item)
            if not all(nb is ob for nb, ob in zip(new_branch, item)):
                changed = True
            new_branches.append(new_branch)
        if not changed:
            return self
        return type(self)(*new_branches)

    def to_branches(self):
        return [self]

    def to_opgroup(self, cut_after=None):
        return self

    def nop_segments(self):
        """
        This group with a ~ on every op of every branch, what ~ on the
        group means: ~(a.b) is (~a.~b), and the ops after the group are
        not held back.
        """
        return type(self)(*(
            item if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT) else tuple(wrappers.NopWrap(op) for op in item)
            for item in self.branches))

    def operator(self, top=False):
        return self._render(top)

    def do_match(self, rest_pats, path_ops, partial):
        """
        Match a concrete path against this group: the path matches if it
        matches any branch followed by the remaining pattern ops.  A single
        concrete path can only be produced by one branch at a time, so
        disjunction, first-match, and conjunction all reduce to "any branch
        matches"; cut markers don't constrain matching either.

        The group is one pattern segment: it contributes a single capture —
        the path segments its branch consumed, assembled — flagged as a
        pattern match (like Recursive).
        """
        for branch in base.branches_only(self.branches):
            branch_ops = list(branch)
            for n in range(len(path_ops) + 1):
                consumed = base.match_ops(branch_ops, path_ops[:n], False)
                if consumed is None:
                    continue
                rest = base.match_ops(list(rest_pats), path_ops[n:], partial)
                if rest is None:
                    continue
                combined = results.assemble(path_ops[:n])
                return [(combined, True)] + rest
        return None

    def do_match_path(self, pats, rest_path, partial):
        """
        Match when this group appears on the *path* side.  The path
        denotes every expansion of the group's branches, so the pattern
        must cover all of them (subsumption): each branch is spliced
        into the path in the group's place and matched; one failing
        branch fails the whole match.

        The group captures as itself — one capture — via an aligned
        decomposition (a pattern prefix covering every branch), or
        assembled into a variadic pattern segment's capture when the
        pattern crosses the group boundary; only when neither
        decomposition exists do captures fall back to the first
        branch's expansion.
        """
        fallback = self._verify_covered(pats, rest_path, partial)
        if fallback is None:
            return None
        r = self._aligned_captures(pats, rest_path, partial)
        if r is not None:
            return r
        if pats and pats[0].is_variadic():
            r = pats[0].do_match(pats[1:], [self] + list(rest_path), partial)
            if r is not None:
                return r
        return fallback

    def _verify_covered(self, pats, rest_path, partial):
        """
        Subsumption check over expansions: splice each branch into the
        path and match.  Returns the first branch's result (the capture
        fallback) or None when any branch is uncovered.
        """
        first = base.marker
        for branch in base.branches_only(self.branches):
            r = base.match_ops(pats, list(branch) + list(rest_path), partial)
            if r is None:
                return None
            if first is base.marker:
                first = r
        if first is base.marker:
            return base.match_ops(pats, list(rest_path), partial)
        return first

    def _aligned_captures(self, pats, rest_path, partial):
        """
        Group-as-itself captures: find a pattern prefix that covers the
        branch set exactly (or up to a partial tail when the pattern
        ends inside the group); the group then captures as one segment
        and the remaining pattern matches the remaining path.
        """
        for k in range(1, len(pats) + 1):
            head = list(pats[:k])
            tail_partial = partial and k == len(pats) and not rest_path
            if not self._covers_branches(head, tail_partial):
                continue
            rest = base.match_ops(list(pats[k:]), list(rest_path), partial)
            if rest is None:
                continue
            is_pat = any(p.is_pattern() for p in head)
            return [(results.assemble([self]), is_pat)] + rest
        return None

    def _covers_branches(self, head_pats, tail_partial=False):
        """
        True if head_pats covers every branch of this group.
        """
        return all(base.match_ops(head_pats, list(b), tail_partial) is not None
                   for b in base.branches_only(self.branches))

    def covered_by(self, matcher):
        """
        A group path op is covered when every segment of every branch is.
        """
        return all(op.covered_by(matcher)
                   for branch in base.branches_only(self.branches)
                   for op in branch)

    def _render(self, top=True):
        """
        Render the group as a string. Subclasses override this.
        When top=False (mid-path), branch-leading Keys get a '.' prefix.
        """
        return repr(self)

    def as_attrs(self):
        """
        Promote unresolved bare identifiers (parsed as Keys) to Attrs in all
        branches.  Subclasses override to handle cut/softcut markers.
        """
        return type(self)(*[_attr_branch(b) for b in self.branches])


class OpGroupOr(OpGroup):
    """
    Disjunction: branches from a common point, yields all matches from all branches.

    This enables syntax like:
        a(.b,[])     - from a, get both a.b and a[]
        a(.b#, .c)   - from a, first branch that matches wins (cut); if .b matches, stop
        a(.b##, .c)  - soft cut: like hard cut but later branches still run for keys not covered

    base.BRANCH_CUT in the sequence means: after yielding from the previous branch, yield base.CUT_SENTINEL and stop.
    base.BRANCH_SOFTCUT in the sequence means: later branches skip keys already yielded by this branch.
    """
    def as_attrs(self):
        new_branches = []
        for b in self.branches:
            if isinstance(b, tuple):
                new_branches.append(_attr_branch(b))
            else:
                new_branches.append(b)  # cut markers pass through
        return OpGroupOr(*new_branches)

    def leaf_op(self):
        """
        Recurse into the first non-cut branch.
        """
        for branch in base.branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def _render(self, top=True):
        parts = []
        for item in self.branches:
            if item is base.BRANCH_CUT:
                if parts:
                    parts[-1] += '#'
            elif item is base.BRANCH_SOFTCUT:
                if parts:
                    parts[-1] += '##'
            else:
                s = ''.join(op.operator(top=(top and j == 0)) for j, op in enumerate(item))
                parts.append(s)
        return '(' + ','.join(parts) + ')'

    def __repr__(self):
        return self._render(top=True)

    def _next_marker(self, i):
        """
        Return the marker (base.BRANCH_CUT or base.BRANCH_SOFTCUT) following branch at index i, or None.
        """
        br = self.branches
        if i >= len(br) - 1:
            return None
        nxt = br[i + 1]
        if nxt in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
            return nxt
        return None

    def push_children(self, stack, frame, paths):
        """
        Process branches sequentially via base.DepthStack levels.
        Handles cut, softcut, and path overlap filtering.
        """
        br = self.branches
        softcut_paths = SoftcutPaths()
        results = []
        for i in range(len(br)):
            item = br[i]
            if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                continue
            branch_ops = tuple(item) + tuple(frame.ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            is_softcut = marker is base.BRANCH_SOFTCUT
            use_paths = paths or bool(softcut_paths) or is_softcut
            stack.push_level()
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, settings=frame.settings))
            found = False
            for path, val in engine.process(stack, use_paths):
                if path is base.CUT_SENTINEL:
                    break
                if softcut_paths and path and softcut_paths.overlaps(path):
                    continue
                found = True
                if is_softcut and path:
                    softcut_paths.add(path)
                results.append((path if paths else None, val))
            stack.pop_level()
            if not found:
                continue
            if marker is base.BRANCH_CUT:
                results.append((base.CUT_SENTINEL, None))
                return results
        return results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        matched_any = False
        br = self.branches
        softcut_paths = SoftcutPaths()
        for i in range(len(br)):
            item = br[i]
            if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in engine.walk(branch_ops, node, paths=True, settings=settings):
                if path is base.CUT_SENTINEL:
                    break
                if softcut_paths and path and softcut_paths.overlaps(path):
                    continue
                paths.append(path)
            if not paths and not appends(item):
                continue
            matched_any = True
            if marker is base.BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths and paths:
                branch_nop = nop or any(isinstance(op, wrappers.NopWrap) for op in item)
                for path in paths:
                    node = engine.updates(list(path), node, val, has_defaults, _path, branch_nop, settings=settings)
            else:
                node = engine.updates(branch_ops, node, val, has_defaults, _path, nop, settings=settings)
            if marker is base.BRANCH_CUT:
                return node
        if not matched_any:
            return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop, settings=settings)
        return node

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        br = self.branches
        softcut_paths = SoftcutPaths()
        for i in range(len(br)):
            item = br[i]
            if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in engine.walk(branch_ops, node, paths=True, settings=settings):
                if path is base.CUT_SENTINEL:
                    break
                if softcut_paths and path and softcut_paths.overlaps(path):
                    continue
                paths.append(path)
            if not paths:
                continue
            if marker is base.BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                # the walked paths lose their ~, so a branch with one is held back whole, as in do_update
                if not (nop or any(isinstance(op, wrappers.NopWrap) for op in item)):
                    for path in paths:
                        node = engine.removes(list(path), node, val, settings=settings)
            else:
                node = engine.removes(branch_ops, node, val, settings=settings)
            if marker is base.BRANCH_CUT:
                return node
        return node


class OpGroupFirst(OpGroup):
    """
    First-match operation group - returns only first matching value across all branches.
    """
    def as_attrs(self):
        new_branches = []
        for b in self.branches:
            if isinstance(b, tuple):
                new_branches.append(_attr_branch(b))
            else:
                new_branches.append(b)
        return OpGroupFirst(*new_branches)

    def leaf_op(self):
        """
        Recurse into the first non-cut branch.
        """
        for branch in base.branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def _render(self, top=True):
        branch_strs = [''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(b)) for b in base.branches_only(self.branches)]
        return '(' + ','.join(branch_strs) + ')?'

    def __repr__(self):
        return self._render(top=True)

    def push_children(self, stack, frame, paths):
        """
        Try branches in order, return first result found.
        """
        for branch in base.branches_only(self.branches):
            branch_ops = tuple(branch) + tuple(frame.ops)
            if not branch_ops:
                continue
            stack.push_level()
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, settings=frame.settings))
            for pair in engine.process(stack, paths):
                stack.pop_level()
                return [pair]
            stack.pop_level()
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        for branch in base.branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if appends(branch) or base.has_any(engine.gets(branch_ops, node, settings=settings)):
                return engine.updates(branch_ops, node, val, has_defaults, _path, nop, settings=settings)
        return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop, settings=settings)

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        for branch in base.branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if base.has_any(engine.gets(branch_ops, node, settings=settings)):
                return engine.removes(branch_ops, node, val, settings=settings)
        return node


class OpGroupAnd(OpGroup):
    """
    Conjunction of operation sequences - returns values only if ALL branches match.

    This enables syntax like:
        a(.b&.c)     - from a, get a.b and a.c only if both exist
        x(.a.i&.b.k) - from x, get both only if both paths exist

    If any branch fails to match, returns nothing.
    """
    def leaf_op(self):
        """
        Recurse into the first branch.
        """
        if self.branches:
            return self.branches[0][0].leaf_op()
        return self

    def _render(self, top=True):
        branch_strs = []
        for branch in self.branches:
            branch_strs.append(''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(branch)))
        return '(' + '&'.join(branch_strs) + ')'

    def __repr__(self):
        return self._render(top=True)

    def _verify_covered(self, pats, rest_path, partial):
        """
        A conjunction's expansions are the intersection of its
        branches', so a pattern covering any single branch covers the
        whole (sufficient, conservatively incomplete).
        """
        for branch in base.branches_only(self.branches):
            r = base.match_ops(pats, list(branch) + list(rest_path), partial)
            if r is not None:
                return r
        return None

    def _covers_branches(self, head_pats, tail_partial=False):
        """
        Any single covered branch suffices for a conjunction.
        """
        return any(base.match_ops(head_pats, list(b), tail_partial) is not None
                   for b in base.branches_only(self.branches))

    def push_children(self, stack, frame, paths):
        """
        All branches must match. Collect results per branch;
        if any branch is empty, return nothing.
        """
        all_results = []
        for branch in self.branches:
            branch_ops = tuple(branch) + tuple(frame.ops)
            if not branch_ops:
                continue
            stack.push_level()
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, settings=frame.settings))
            branch_results = list(engine.process(stack, paths))
            stack.pop_level()
            if not branch_results:
                return ()
            all_results.extend(branch_results)
        return all_results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not _can_update_conjunctive_branch(branch_ops, node):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = engine.updates(branch_ops, node, val, has_defaults, _path, nop, settings=settings)
        return node

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not base.has_any(engine.gets(branch_ops, node, settings=settings)):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = engine.removes(branch_ops, node, val, settings=settings)
        return node




class OpGroupNot(OpGroup):
    """
    Negation of a path - yields what the inner path does NOT reach.

    !P is the complement of the whole path P: the largest subtrees that
    hold no match of P. So on {'a': {'x': 1, 'y': 2}, 'c': 3}:
        !a           - c
        !(a.x)       - a.y and c; a itself holds a match, so it is split
        !(a.*)       - c; everything in a is matched
        a(!.x)       - from a, every key but x

    The complement is taken from what P actually matches, so a cut, a
    first-match or a conjunction in P counts only the matches it lets
    through. (!a).b is the old reading: every key but a, then .b.

    Its matches lie at any depth, so a guard, filter or type after it
    reaches them as it does those of **: through _collect_matches,
    _update_recursive and _remove_recursive.

    TODO: `!*` always evaluates to empty — the parser should recognize this and emit
    something that evaluates to empty directly, rather than going through negation.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # For negation, we have a single inner expression to negate
        self.inner = self.branches[0] if self.branches else ()

    def is_recursive(self):
        """
        True: the complement's matches lie at any depth.
        """
        return True

    def nop_segments(self):
        """
        None: a ~ on a negation holds back the complement's writes as
        they are made, in _update_recursive and _remove_recursive.
        """
        return None

    def leaf_op(self):
        """
        Recurse into the inner pattern.
        """
        if self.inner:
            return self.inner[0].leaf_op()
        return self

    def _render(self, top=True):
        inner_str = ''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(self.inner))
        return f'(!{inner_str})'

    def __repr__(self):
        return self._render(top=True)

    def _matched(self, node, settings):
        """
        The paths, relative to node, of the inner pattern's matches, up to
        a cut, each segment unwrapped to its access op.
        """
        out = []
        for path, _ in engine.walk(self.inner, node, True, settings=settings):
            if path is base.CUT_SENTINEL:
                break
            out.append(tuple(seg.most_inner for seg in path))
        return out

    def _complement(self, node, settings):
        """
        Yield (path, value, settings) for each largest subtree of node that
        holds no match of the inner pattern; path is relative to node.
        """
        if not self.inner:
            return
        yield from self._split(node, self._matched(node, settings), self.inner[0].leaf_op(), (), settings)

    def _split(self, node, matched, acc, prefix, settings):
        """
        The children of node, enumerated by acc, less the matched paths:
        a child no path reaches is yielded whole, one a path ends at is
        dropped, and one a path runs through is split in turn.
        """
        below = settings.below(node)
        for k, v in acc.items(node, filtered=False, settings=settings):
            seg = acc.concrete(k)
            rest = [p[1:] for p in matched if p[0] == seg]
            if not rest:
                yield prefix + (seg,), v, below
                continue
            if any(not p for p in rest):
                continue
            yield from self._split(v, rest, rest[0][0], prefix + (seg,), below)

    def _collect_matches(self, node, paths, prefix=(), settings=base.SETTINGS):
        """
        Yield (prefix, value, terminal) for each subtree of the complement,
        as Recursive does for a guard wrapping it.
        """
        for cp, v, _ in self._complement(node, settings):
            yield (prefix + cp if paths else prefix), v, False

    def push_children(self, stack, frame, paths):
        found = list(self._complement(frame.node, frame.settings))
        for cp, v, below in reversed(found):
            stack.push(base.Frame(frame.ops, v, frame.prefix + cp if paths else frame.prefix, settings=below))
        return ()

    # Data access for a filter or transform wrapping a negation: the
    # complement's items one level down.

    def _leaf(self):
        """
        The data-access op of the negated branch.
        """
        return self.inner[0].leaf_op()

    def _not_items(self, node, settings=base.SETTINGS):
        """
        Yield (key, value) for each subtree of the complement that is a
        child of node.
        """
        children = [cp[0] for cp, _, _ in self._complement(node, settings) if len(cp) == 1]
        leaf = self._leaf()
        for k, v in leaf.items(node, filtered=False, settings=settings):
            if leaf.concrete(k) in children:
                yield k, v

    def items(self, node, settings=base.SETTINGS, filtered=True):
        if not filtered:
            return self._leaf().items(node, filtered=False, settings=settings)
        return self._not_items(node, settings=settings)

    def keys(self, node, settings=base.SETTINGS):
        return (k for k, _ in self._not_items(node, settings=settings))

    def values(self, node, settings=base.SETTINGS):
        return (v for _, v in self._not_items(node, settings=settings))

    def is_empty(self, node):
        return not any(True for _ in self._complement(node, base.SETTINGS))

    def update(self, node, key, val):
        return self._leaf().update(node, key, val)

    def upsert(self, node, val):
        return self._update_recursive((), node, val, False, [], False)

    def pop(self, node, key):
        return self._leaf().pop(node, key)

    def remove(self, node, val, settings=base.SETTINGS):
        return self._remove_recursive((), node, val, False, settings=settings)

    def concrete(self, val):
        return self._leaf().concrete(val)

    def default(self):
        return self._leaf().default()

    def do_match(self, rest_pats, path_ops, partial):
        """
        Match the segments of one subtree of the complement: they follow
        the inner branch op by op until one does not match, and that one
        ends the subtree; then continue with the remaining ops. A path
        the whole branch matches is in the negated pattern, not out of it.
        """
        inner = self.inner
        for i, kop in enumerate(path_ops[:len(inner)]):
            if base.match_ops([inner[i]], [kop], False) is not None:
                continue
            rest = base.match_ops(rest_pats, path_ops[i + 1:], partial)
            if rest is None:
                return None
            return [(getattr(getattr(seg, 'op', seg), 'value', seg), True) for seg in path_ops[:i + 1]] + rest
        return None

    def do_match_path(self, pats, rest_path, partial):
        """
        Match when this negation appears on the *path* side.  It denotes
        every segment its inner pattern excludes — an open set — so it
        is covered only by an identical negation or by a bare wildcard
        segment.
        """
        if not pats:
            return [] if partial else None
        head = pats[0]
        if head != self and not self._wildcard_covers(head):
            return None
        rest = base.match_ops(pats[1:], rest_path, partial)
        if rest is None:
            return None
        return [(results.assemble([self]), True)] + rest

    def _wildcard_covers(self, head):
        """
        True if pattern op *head* is a bare single-segment wildcard and
        this negation excludes only single segments.
        """
        if any(len(b) != 1 for b in base.branches_only(self.branches)):
            return False
        return isinstance(getattr(head, 'op', None), matchers.Wildcard)

    def covered_by(self, matcher):
        """
        A negation denotes an open set of segments; only a wildcard
        covers them all.
        """
        return isinstance(matcher, matchers.Wildcard)

    def _update_recursive(self, ops, node, val, has_defaults, _path, nop, guard=None, settings=base.SETTINGS, nop_from_unwrap=False):
        """
        Write each subtree of the complement that passes guard: val at a
        leaf, else through the ops after the negation.
        """
        if not ops and nop:
            return node
        # a ~ on this op holds back only this op, not the ops after it
        pass_nop = nop and not nop_from_unwrap
        for cp, v, _ in list(self._complement(node, settings)):
            if guard and not guard(v):
                continue
            node = engine.updates(cp + tuple(ops), node, val, has_defaults, _path, pass_nop, settings=settings)
        return node

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        return self._update_recursive(ops, node, val, has_defaults, _path, nop, settings=settings, nop_from_unwrap=nop_from_unwrap)

    def _remove_recursive(self, ops, node, val, nop, guard=None, settings=base.SETTINGS):
        """
        Remove each subtree of the complement that passes guard (and
        equals val), or remove through the ops after the negation; last
        first, so a removal does not shift the slots still to come.
        """
        if not ops and nop:
            return node
        for cp, v, _ in reversed(list(self._complement(node, settings))):
            if guard and not guard(v):
                continue
            node = engine.removes(cp + tuple(ops), node, val, nop=False, settings=settings)
        return node

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        return self._remove_recursive(ops, node, val, nop, settings=settings)


# =============================================================================
# Parse actions — called by grammar.py to construct OpGroups from parse results
# =============================================================================


class Pack(base.TraversalOp):
    """
    A packed group, (a,b;) or (;a,b): the group's matches as one tuple.
    The group keeps its own kind, so (a,b;) packs whatever branches
    match, (a&b;) packs both or nothing, (a#,b;) packs what the cut
    lets through, and (a.*;) packs a one-branch group; no matches pack
    to (). Yielding one value, it is not a pattern, and a transform
    after it applies to the tuple once: (a.*;)|sum. The group is run
    from the node the pack sits at, so a[*].(b.*;) packs per a. Writes
    through a pack raise. Not a Wrap: the pack is the op, the group
    its argument, so an unwrapping stops here.
    """

    def __init__(self, inner):
        super().__init__(inner)
        self.inner = inner

    def __repr__(self):
        return self.operator(top=True)

    def is_template(self):
        return self.inner.is_template()

    def is_reference(self):
        return self.inner.is_reference()

    def matchable(self, op):
        return op == self

    def match(self, op, specials=False):
        """
        A pack denotes only itself: it matches an equal pack.
        """
        if not self.matchable(op):
            return None
        return base.MatchResult(self)

    def do_match(self, rest_pats, path_ops, partial):
        """
        One segment, matched by equality (see match).
        """
        if not path_ops or not self.match(path_ops[0]):
            return None
        rest = base.match_ops(rest_pats, path_ops[1:], partial)
        if rest is None:
            return None
        return [(self, False)] + rest

    def operator(self, top=False):
        """
        The group's text with the marker before its closing paren:
        (a,b;), (a,b;)?.
        """
        s = self.inner.operator(top)
        i = s.rindex(')')
        return s[:i] + ';' + s[i:]

    def is_pattern(self):
        return False

    def as_attrs(self):
        return Pack(self.inner.as_attrs())

    def resolve(self, bindings, partial=False):
        new_inner = self.inner.resolve(bindings, partial)
        if new_inner is self.inner:
            return self
        return Pack(new_inner)

    def concrete(self, val):
        """
        A pack is its own path segment: a walked path reads back as
        written, (a.*;).
        """
        return self

    def gather(self, node, settings=base.SETTINGS):
        """
        The tuple of the group's matches from node, stopping at a cut.
        """
        return tuple(engine.values((self.inner,), node, settings=settings))

    def items(self, node, settings=base.SETTINGS, filtered=True):
        yield None, self.gather(node, settings)

    def keys(self, node, settings=base.SETTINGS):
        yield None

    def values(self, node, settings=base.SETTINGS):
        yield self.gather(node, settings)

    def is_empty(self, node):
        return False

    def push_children(self, stack, frame, paths):
        """
        One frame, the tuple, with this pack as its path segment; the
        node the group ran from is its parent for ^^ references.
        """
        packed = self.gather(frame.node, frame.settings)
        cp = frame.prefix + (self,) if paths else frame.prefix
        stack.push(base.Frame(frame.ops, packed, cp, settings=frame.settings.below(frame.node)))
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, settings=base.SETTINGS):
        raise ValueError(f'cannot update through a packed group: {self!r}')

    def do_remove(self, ops, node, val, nop, settings=base.SETTINGS):
        raise ValueError(f'cannot remove through a packed group: {self!r}')


def _attr_branch(branch):
    """
    Convert leading Key to Attr in a branch tuple.
    Only converts exact Key (not Attr or other subclasses).
    """
    if isinstance(branch, tuple) and branch and type(branch[0]) is Key:
        return (Attr(*branch[0].args),) + branch[1:]
    return branch


def appends(ops):
    """
    True if ops hold an append slot, [+] or [+?]. Nothing is ever there to
    read, but a write always applies, so in an update a group branch that
    holds one counts as matched.
    """
    return any(isinstance(op.most_inner, SlotSpecial) for op in ops)


def _is_concrete_path(branch_ops):
    """
    Return True if branch_ops represents a concrete path (no wildcards/patterns).
    Concrete paths can be created when missing; wildcard paths cannot.
    """
    for op in branch_ops:
        cur = op.inner if isinstance(op, wrappers.Wrap) else op
        if getattr(cur, 'is_pattern', lambda: False)():
            return False
    return True


def _can_update_conjunctive_branch(branch_ops, node):
    """
    Return True if we can update this conjunctive branch (for OpGroupAnd).
    We can update if: path exists (gets yields something), OR it's a plain
    concrete path we can create. We cannot update if: filter doesn't match
    or path is a wildcard.
    """
    if base.has_any(engine.gets(branch_ops, node)):
        return True
    if not _is_concrete_path(branch_ops):
        return False
    first_op = branch_ops[0]
    cur = first_op.inner if isinstance(first_op, wrappers.Wrap) else first_op
    if isinstance(cur, Key) and getattr(cur, 'filters', ()):
        return False
    return True


def _disjunction_fallback(cur, ops, node, val, has_defaults, _path, nop, settings=base.SETTINGS):
    """
    When nothing matches in disjunction: update first concrete path (last to first).
    """
    for branch in reversed(list(base.branches_only(cur.branches))):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if _is_concrete_path(branch_ops):
            return engine.updates(branch_ops, node, val, has_defaults, _path, nop, settings=settings)
    return node
