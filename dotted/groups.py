"""
"""
import pyparsing as pp

from . import base
from . import wrappers
from . import engine
from .access import Key, Attr, Slot, SlotSpecial, Invert

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

    def is_pattern(self):
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

    def default(self):
        """
        Derive default from the first branch's first op, so auto-creation works
        (e.g. slot group [(*&filter#, +)] defaults to []).
        """
        for branch in base.branches_only(self.branches):
            if branch:
                first_op = branch[0]
                # Unwrap NopWrap/ValueGuard to find the underlying op
                inner = first_op
                while isinstance(inner, (wrappers.NopWrap, wrappers.ValueGuard)):
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

    def operator(self, top=False):
        return self._render(top)

    def _render(self, top=True):
        """
        Render the group as a string. Subclasses override this.
        When top=False (mid-path), branch-leading Keys get a '.' prefix.
        """
        return repr(self)


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
    def leaf_op(self):
        """
        Recurse into the first non-cut branch.
        """
        for branch in base.branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Union of excluded keys across all non-cut branches.
        """
        excluded = set()
        for branch in base.branches_only(self.branches):
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

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
        softcut_paths = []
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
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, kwargs=frame.kwargs))
            found = False
            for path, val in engine.process(stack, use_paths):
                if path is base.CUT_SENTINEL:
                    break
                if softcut_paths and path and base.path_overlaps(softcut_paths, path):
                    continue
                found = True
                if is_softcut and path:
                    softcut_paths.append(path)
                results.append((path if paths else None, val))
            stack.pop_level()
            if not found:
                continue
            if marker is base.BRANCH_CUT:
                results.append((base.CUT_SENTINEL, None))
                return results
        return results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        matched_any = False
        br = self.branches
        softcut_paths = []
        for i in range(len(br)):
            item = br[i]
            if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in engine.walk(branch_ops, node, paths=True, **kwargs):
                if path is base.CUT_SENTINEL:
                    break
                if softcut_paths and path and base.path_overlaps(softcut_paths, path):
                    continue
                paths.append(path)
            if not paths:
                continue
            matched_any = True
            if marker is base.BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                branch_nop = nop or any(isinstance(op, wrappers.NopWrap) for op in item)
                for path in paths:
                    node = engine.updates(list(path), node, val, has_defaults, _path, branch_nop, **kwargs)
            else:
                node = engine.updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
            if marker is base.BRANCH_CUT:
                return node
        if not matched_any:
            return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop, **kwargs)
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        br = self.branches
        softcut_paths = []
        for i in range(len(br)):
            item = br[i]
            if item in (base.BRANCH_CUT, base.BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in engine.walk(branch_ops, node, paths=True, **kwargs):
                if path is base.CUT_SENTINEL:
                    break
                if softcut_paths and path and base.path_overlaps(softcut_paths, path):
                    continue
                paths.append(path)
            if not paths:
                continue
            if marker is base.BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                for path in paths:
                    node = engine.removes(list(path), node, val, **kwargs)
            else:
                node = engine.removes(branch_ops, node, val, **kwargs)
            if marker is base.BRANCH_CUT:
                return node
        return node


class OpGroupFirst(OpGroup):
    """
    First-match operation group - returns only first matching value across all branches.
    """
    def leaf_op(self):
        """
        Recurse into the first non-cut branch.
        """
        for branch in base.branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Union of excluded keys across all non-cut branches.
        """
        excluded = set()
        for branch in base.branches_only(self.branches):
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

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
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, kwargs=frame.kwargs))
            for pair in engine.process(stack, paths):
                stack.pop_level()
                return [pair]
            stack.pop_level()
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        for branch in base.branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if base.has_any(engine.gets(branch_ops, node, **kwargs)):
                return engine.updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
        return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        for branch in base.branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if base.has_any(engine.gets(branch_ops, node, **kwargs)):
                return engine.removes(branch_ops, node, val, **kwargs)
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

    def excluded_keys(self, node):
        """
        Union of excluded keys across all branches of the conjunction.
        """
        excluded = set()
        for branch in self.branches:
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

    def _render(self, top=True):
        branch_strs = []
        for branch in self.branches:
            branch_strs.append(''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(branch)))
        return '(' + '&'.join(branch_strs) + ')'

    def __repr__(self):
        return self._render(top=True)

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
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, kwargs=frame.kwargs))
            branch_results = list(engine.process(stack, paths))
            stack.pop_level()
            if not branch_results:
                return ()
            all_results.extend(branch_results)
        return all_results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not _can_update_conjunctive_branch(branch_ops, node):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = engine.updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not base.has_any(engine.gets(branch_ops, node, **kwargs)):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = engine.removes(branch_ops, node, val, **kwargs)
        return node




class OpGroupNot(OpGroup):
    """
    Negation of operation sequences - returns values from paths NOT matching the inner pattern.

    This enables syntax like:
        a(!.b)       - from a, get all keys except b
        a(!(.b,.c))  - from a, get all keys except b and c

    Works by using the inner op's items(filtered=False) to enumerate all items via the
    proper data-access layer, then excluding keys that match the inner pattern.

    TODO: `!*` always evaluates to empty — the parser should recognize this and emit
    something that evaluates to empty directly, rather than going through negation.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # For negation, we have a single inner expression to negate
        self.inner = self.branches[0] if self.branches else ()

    def leaf_op(self):
        """
        Recurse into the inner pattern.
        """
        if self.inner:
            return self.inner[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Delegate to the inner pattern.
        """
        if self.inner:
            return self.inner[0].excluded_keys(node)
        return set()

    def _render(self, top=True):
        inner_str = ''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(self.inner))
        return f'(!{inner_str})'

    def __repr__(self):
        return self._render(top=True)

    def _not_items(self, node, **kwargs):
        """
        Yield (key, value) pairs for keys NOT excluded by the inner pattern.

        Uses items(filtered=False) on the leaf op to enumerate all items via the
        proper data-access layer, then subtracts keys matched by the inner pattern.
        """
        inner = self.inner
        if not inner:
            return
        first_op = inner[0]
        leaf = first_op.leaf_op()
        excluded = first_op.excluded_keys(node)
        for k, v in leaf.items(node, filtered=False, **kwargs):
            if k not in excluded:
                yield (k, v)

    def push_children(self, stack, frame, paths):
        inner = self.inner
        if not inner:
            return ()
        leaf = inner[0].leaf_op()
        children = list(self._not_items(frame.node, **(frame.kwargs or {})))
        for k, v in reversed(children):
            cp = frame.prefix + (leaf.concrete(k),) if paths else frame.prefix
            stack.push(base.Frame(frame.ops, v, cp, kwargs=frame.kwargs))
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        inner = self.inner
        if not inner:
            return node
        leaf = inner[0].leaf_op()
        remaining_ops = list(inner[1:]) + list(ops)
        for k, v in self._not_items(node, **kwargs):
            if remaining_ops:
                node = leaf.update(node, k, engine.updates(remaining_ops, v, val, has_defaults, _path + [(leaf, k)], nop, **kwargs))
            else:
                node = leaf.update(node, k, val)
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        inner = self.inner
        if not inner:
            return node
        leaf = inner[0].leaf_op()
        remaining_ops = list(inner[1:]) + list(ops)
        items = list(self._not_items(node, **kwargs))
        for k, v in reversed(items):
            if remaining_ops:
                node = leaf.update(node, k, engine.removes(remaining_ops, v, val, **kwargs))
            else:
                node = leaf.pop(node, k)
        return node


# =============================================================================
# Parse actions — called by grammar.py to construct OpGroups from parse results
# =============================================================================


def _to_branch(item):
    """
    Convert a parse result item (op_seq Group or OpGroup) to a branch tuple.
    """
    if isinstance(item, OpGroup):
        return (item,)
    if isinstance(item, (list, tuple, pp.ParseResults)):
        return tuple(item)
    return (item,)


def inner_not_action(t):
    """
    Parse action for unified negation: ! atom.
    The atom is either an OpGroup (from grouped expression) or an op_seq.
    OpGroupNot takes a single branch as its inner pattern.
    """
    item = t[0]
    branch = _to_branch(item)
    return OpGroupNot(branch)


def inner_and_action(t):
    """
    Parse action for unified conjunction: atom & atom & ...
    """
    branches = [_to_branch(item) for item in t]
    return OpGroupAnd(*branches)


def inner_or_action(t):
    """
    Parse action for unified disjunction: term , term , ...
    Each term is a Group containing [inner_and_result, optional_cut_marker].
    If there's only one term with no cut marker, pass through without wrapping.
    """
    terms = list(t)
    # Single term, no cut marker — pass through (don't wrap in OpGroupOr)
    if len(terms) == 1 and len(terms[0]) == 1:
        return terms[0][0]
    out = []
    for term in terms:
        item = term[0]
        out.append(_to_branch(item))
        if len(term) >= 2:
            if term[1] == '##':
                out.append(base.BRANCH_SOFTCUT)
            elif term[1] == '#':
                out.append(base.BRANCH_CUT)
    return OpGroupOr(*out)


def inner_to_opgroup(parsed_result):
    """
    Parse action: convert (inner_expr) to OpGroup.
    Unwraps single-branch OpGroupOr containing a sole OpGroupAnd/OpGroupNot,
    since the OpGroupOr wrapper is redundant in that case.
    Does NOT unwrap OpGroupNot or OpGroupAnd — those carry semantic meaning.
    """
    items = list(parsed_result)
    if not items:
        return OpGroupOr()
    # If there's a single OpGroup result, use it directly
    if len(items) == 1 and isinstance(items[0], OpGroup):
        inner = items[0]
        # Only unwrap redundant OpGroupOr wrapping a single OpGroupAnd/OpGroupNot
        if isinstance(inner, OpGroupOr):
            branches = list(base.branches_only(inner.branches))
            if (len(branches) == 1 and isinstance(branches[0], tuple)
                    and len(branches[0]) == 1
                    and isinstance(branches[0][0], (OpGroupAnd, OpGroupNot))):
                return branches[0][0]
        return inner
    # Multiple items or single non-OpGroup: treat as a single branch (op_seq)
    # This handles cases like (name.first) where inner_expr flattens to [name, first]
    branch = tuple(items)
    return OpGroupOr(branch)


def inner_to_opgroup_first(parsed_result):
    """
    Parse action: convert (inner_expr)? to OpGroupFirst.
    """
    return OpGroupFirst(*inner_to_opgroup(parsed_result).branches)


def slot_to_opgroup(parsed_result):
    """
    Convert slot grouping [(*&filter, +)] or [(*&filter#, +)] to OpGroup.
    Each slot item becomes a branch; # inserts base.BRANCH_CUT after that branch.
    Parse result items may be ParseResults (from Group), so unwrap to get Slot/SlotSpecial/NopWrap.
    """
    _slot_types = (Slot, SlotSpecial, wrappers.NopWrap, wrappers.FilterWrap)
    out = []
    for item in parsed_result:
        if isinstance(item, _slot_types):
            out.append((item,))
            continue
        if not (isinstance(item, (list, tuple, pp.ParseResults)) and len(item) >= 1):
            continue
        first = item[0]
        while isinstance(first, (list, tuple, pp.ParseResults)) and len(first) == 1:
            first = first[0]
        if isinstance(first, _slot_types):
            out.append((first,))
            if len(item) >= 2 and item[1] == '##':
                out.append(base.BRANCH_SOFTCUT)
            elif len(item) >= 2 and item[1] == '#':
                out.append(base.BRANCH_CUT)
    return OpGroupOr(*out)


def slot_to_opgroup_first(parsed_result):
    """
    Convert slot grouping [(*&filter, +)?] to OpGroupFirst.
    """
    return OpGroupFirst(*slot_to_opgroup(parsed_result).branches)


def _attr_branch(branch):
    """
    Convert leading Key to Attr in a branch tuple.
    Only converts exact Key (not Attr or other subclasses).
    """
    if isinstance(branch, tuple) and branch and type(branch[0]) is Key:
        return (Attr(*branch[0].args),) + branch[1:]
    return branch


def attr_transform_opgroup(group):
    """
    Transform an OpGroup, converting leading Keys to Attrs in all branches.
    Used by @(group) syntax to infer attribute access for bare keys.
    """
    if isinstance(group, OpGroupOr):
        new_branches = []
        for b in group.branches:
            if isinstance(b, tuple):
                new_branches.append(_attr_branch(b))
            else:
                new_branches.append(b)  # cut markers pass through
        return OpGroupOr(*new_branches)
    if isinstance(group, OpGroupFirst):
        new_branches = []
        for b in group.branches:
            if isinstance(b, tuple):
                new_branches.append(_attr_branch(b))
            else:
                new_branches.append(b)
        return OpGroupFirst(*new_branches)
    if isinstance(group, OpGroupAnd):
        return OpGroupAnd(*[_attr_branch(b) for b in group.branches])
    if isinstance(group, OpGroupNot):
        return OpGroupNot(*[_attr_branch(b) for b in group.branches])
    # Single Key not wrapped in OpGroup (e.g. @(a) with single item)
    if type(group) is Key:
        return Attr(*group.args)
    return group



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


def _disjunction_fallback(cur, ops, node, val, has_defaults, _path, nop, **kwargs):
    """
    When nothing matches in disjunction: update first concrete path (last to first).
    """
    for branch in reversed(list(base.branches_only(cur.branches))):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if _is_concrete_path(branch_ops):
            return engine.updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
    return node
