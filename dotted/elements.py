"""
"""
import copy
import itertools
import pyparsing as pp

from . import base, match, access, recursive
from .base import (  # noqa: F401 — re-export
    Op, TraversalOp, MatchOp, MatchResult,
    MetaNOP, NOP, Frame, DepthStack,
    ANY, _marker, _CUT_SENTINEL, _BRANCH_CUT, _BRANCH_SOFTCUT,
    _branches_only, _path_overlaps, _has_any,
)
from .match import (  # noqa: F401 — re-export
    Const, Numeric, NumericExtended, NumericQuoted,
    Word, String, Bytes, Boolean, NoneValue,
    Pattern, Wildcard, WildcardFirst,
    Regex, RegexFirst,
    Special, Appender, AppenderUnique,
)
from .access import (  # noqa: F401 — re-export
    BaseOp, SimpleOp, Empty, AccessOp,
    Key, Attr, Slot, SlotSpecial,
    SliceFilter, Slice, Invert,
    itemof, quote, normalize,
    _RESERVED, _NEEDS_QUOTE, _NUMERIC_RE,
    _needs_quoting, _is_numeric_str, _quote_str,
)
from .recursive import (  # noqa: F401 — re-export
    Recursive, RecursiveFirst,
)
from .utypes import _TYPE_REGISTRY  # used by TypeRestriction._type_suffix
from .utils import is_dict_like, is_list_like, is_set_like, is_terminal

from . import filters
from .filters import (  # noqa: F401 — re-export
    FilterOp, FilterKey, FilterKeyValue, FilterKeyValueNot,
    FilterGroup, FilterAnd, FilterOr,
    FilterKeyValueFirst, FilterNot,
)

class OpGroup(base.TraversalOp):
    """
    Base class for all operation groups (disjunction, conjunction, negation, first-match).

    branches is a sequence of (branch_tuple, base._BRANCH_CUT/base._BRANCH_SOFTCUT?, branch_tuple, ...).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # args may contain base._BRANCH_CUT/base._BRANCH_SOFTCUT; normalize branch tuples
        out = []
        for x in self.args:
            if x in (base._BRANCH_CUT, base._BRANCH_SOFTCUT):
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

    def default(self):
        """
        Derive default from the first branch's first op, so auto-creation works
        (e.g. slot group [(*&filter#, +)] defaults to []).
        """
        for branch in base._branches_only(self.branches):
            if branch:
                first_op = branch[0]
                # Unwrap NopWrap/ValueGuard to find the underlying op
                inner = first_op
                while isinstance(inner, (NopWrap, ValueGuard)):
                    inner = inner.inner
                # Slot groups operate on lists
                if isinstance(inner, Slot):
                    return []
                if hasattr(first_op, 'default'):
                    return first_op.default()
        return {}

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

    base._BRANCH_CUT in the sequence means: after yielding from the previous branch, yield base._CUT_SENTINEL and stop.
    base._BRANCH_SOFTCUT in the sequence means: later branches skip keys already yielded by this branch.
    """
    def leaf_op(self):
        """
        Recurse into the first non-cut branch.
        """
        for branch in base._branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Union of excluded keys across all non-cut branches.
        """
        excluded = set()
        for branch in base._branches_only(self.branches):
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

    def _render(self, top=True):
        parts = []
        for item in self.branches:
            if item is base._BRANCH_CUT:
                if parts:
                    parts[-1] += '#'
            elif item is base._BRANCH_SOFTCUT:
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
        Return the marker (base._BRANCH_CUT or base._BRANCH_SOFTCUT) following branch at index i, or None.
        """
        br = self.branches
        if i >= len(br) - 1:
            return None
        nxt = br[i + 1]
        if nxt in (base._BRANCH_CUT, base._BRANCH_SOFTCUT):
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
            if item in (base._BRANCH_CUT, base._BRANCH_SOFTCUT):
                continue
            branch_ops = tuple(item) + tuple(frame.ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            is_softcut = marker is base._BRANCH_SOFTCUT
            use_paths = paths or bool(softcut_paths) or is_softcut
            stack.push_level()
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, kwargs=frame.kwargs))
            found = False
            for path, val in _process(stack, use_paths):
                if path is base._CUT_SENTINEL:
                    break
                if softcut_paths and path and base._path_overlaps(softcut_paths, path):
                    continue
                found = True
                if is_softcut and path:
                    softcut_paths.append(path)
                results.append((path if paths else None, val))
            stack.pop_level()
            if not found:
                continue
            if marker is base._BRANCH_CUT:
                results.append((base._CUT_SENTINEL, None))
                return results
        return results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        matched_any = False
        br = self.branches
        softcut_paths = []
        for i in range(len(br)):
            item = br[i]
            if item in (base._BRANCH_CUT, base._BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in walk(branch_ops, node, paths=True, **kwargs):
                if path is base._CUT_SENTINEL:
                    break
                if softcut_paths and path and base._path_overlaps(softcut_paths, path):
                    continue
                paths.append(path)
            if not paths:
                continue
            matched_any = True
            if marker is base._BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                branch_nop = nop or any(isinstance(op, NopWrap) for op in item)
                for path in paths:
                    node = updates(list(path), node, val, has_defaults, _path, branch_nop, **kwargs)
            else:
                node = updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
            if marker is base._BRANCH_CUT:
                return node
        if not matched_any:
            return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop, **kwargs)
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        br = self.branches
        softcut_paths = []
        for i in range(len(br)):
            item = br[i]
            if item in (base._BRANCH_CUT, base._BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in walk(branch_ops, node, paths=True, **kwargs):
                if path is base._CUT_SENTINEL:
                    break
                if softcut_paths and path and base._path_overlaps(softcut_paths, path):
                    continue
                paths.append(path)
            if not paths:
                continue
            if marker is base._BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                for path in paths:
                    node = removes(list(path), node, val, **kwargs)
            else:
                node = removes(branch_ops, node, val, **kwargs)
            if marker is base._BRANCH_CUT:
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
        for branch in base._branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Union of excluded keys across all non-cut branches.
        """
        excluded = set()
        for branch in base._branches_only(self.branches):
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

    def _render(self, top=True):
        branch_strs = [''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(b)) for b in base._branches_only(self.branches)]
        return '(' + ','.join(branch_strs) + ')?'

    def __repr__(self):
        return self._render(top=True)

    def push_children(self, stack, frame, paths):
        """
        Try branches in order, return first result found.
        """
        for branch in base._branches_only(self.branches):
            branch_ops = tuple(branch) + tuple(frame.ops)
            if not branch_ops:
                continue
            stack.push_level()
            stack.push(base.Frame(branch_ops, frame.node, frame.prefix, kwargs=frame.kwargs))
            for pair in _process(stack, paths):
                stack.pop_level()
                return [pair]
            stack.pop_level()
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False, **kwargs):
        for branch in base._branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if base._has_any(gets(branch_ops, node, **kwargs)):
                return updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
        return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        for branch in base._branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if base._has_any(gets(branch_ops, node, **kwargs)):
                return removes(branch_ops, node, val, **kwargs)
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
            branch_results = list(_process(stack, paths))
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
                node = updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
        return node

    def do_remove(self, ops, node, val, nop, **kwargs):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not base._has_any(gets(branch_ops, node, **kwargs)):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = removes(branch_ops, node, val, **kwargs)
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
                node = leaf.update(node, k, updates(remaining_ops, v, val, has_defaults, _path + [(leaf, k)], nop, **kwargs))
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
                node = leaf.update(node, k, removes(remaining_ops, v, val, **kwargs))
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


def _inner_not_action(t):
    """
    Parse action for unified negation: ! atom.
    The atom is either an OpGroup (from grouped expression) or an op_seq.
    OpGroupNot takes a single branch as its inner pattern.
    """
    item = t[0]
    branch = _to_branch(item)
    return OpGroupNot(branch)


def _inner_and_action(t):
    """
    Parse action for unified conjunction: atom & atom & ...
    """
    branches = [_to_branch(item) for item in t]
    return OpGroupAnd(*branches)


def _inner_or_action(t):
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
                out.append(base._BRANCH_SOFTCUT)
            elif term[1] == '#':
                out.append(base._BRANCH_CUT)
    return OpGroupOr(*out)


def _inner_to_opgroup(parsed_result):
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
            branches = list(base._branches_only(inner.branches))
            if (len(branches) == 1 and isinstance(branches[0], tuple)
                    and len(branches[0]) == 1
                    and isinstance(branches[0][0], (OpGroupAnd, OpGroupNot))):
                return branches[0][0]
        return inner
    # Multiple items or single non-OpGroup: treat as a single branch (op_seq)
    # This handles cases like (name.first) where inner_expr flattens to [name, first]
    branch = tuple(items)
    return OpGroupOr(branch)


def _inner_to_opgroup_first(parsed_result):
    """
    Parse action: convert (inner_expr)? to OpGroupFirst.
    """
    return OpGroupFirst(*_inner_to_opgroup(parsed_result).branches)


def _slot_to_opgroup(parsed_result):
    """
    Convert slot grouping [(*&filter, +)] or [(*&filter#, +)] to OpGroup.
    Each slot item becomes a branch; # inserts base._BRANCH_CUT after that branch.
    Parse result items may be ParseResults (from Group), so unwrap to get Slot/SlotSpecial/NopWrap.
    """
    _slot_types = (Slot, SlotSpecial, NopWrap)
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
                out.append(base._BRANCH_SOFTCUT)
            elif len(item) >= 2 and item[1] == '#':
                out.append(base._BRANCH_CUT)
    return OpGroupOr(*out)


def _slot_to_opgroup_first(parsed_result):
    """
    Convert slot grouping [(*&filter, +)?] to OpGroupFirst.
    """
    return OpGroupFirst(*_slot_to_opgroup(parsed_result).branches)


def _attr_branch(branch):
    """
    Convert leading Key to Attr in a branch tuple.
    Only converts exact Key (not Attr or other subclasses).
    """
    if isinstance(branch, tuple) and branch and type(branch[0]) is Key:
        return (Attr(*branch[0].args),) + branch[1:]
    return branch


def _attr_transform_opgroup(group):
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




class Wrap(base.TraversalOp):
    """
    Abstract base for ops that wrap another op; use .inner to get the wrapped op.
    Provides default delegation for all access-op methods.
    """

    inner = None  # subclasses set in __init__

    def is_pattern(self):
        return self.inner.is_pattern() if hasattr(self.inner, 'is_pattern') else False

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
    """

    def __init__(self, inner, guard, negate=False, *args, **kwargs):
        super().__init__(inner, *args, **kwargs)
        self.inner = inner    # Key or Slot
        self.guard = guard    # value op (match.Numeric, match.String, match.Wildcard, match.Regex, etc.)
        self.negate = negate

    def __repr__(self):
        eq = '!=' if self.negate else '='
        return f'{self.inner!r}{eq}{self.guard!r}'

    def __hash__(self):
        return hash(('guard', self.inner, self.guard, self.negate))

    def __eq__(self, other):
        return (isinstance(other, ValueGuard) and self.inner == other.inner
                and self.guard == other.guard and self.negate == other.negate)

    def _guard_matches(self, val):
        """
        True if val matches the guard value.
        """
        matched = any(True for _ in self.guard.matches((val,)))
        return not matched if self.negate else matched

    def operator(self, top=False):
        eq = '!=' if self.negate else '='
        return self.inner.operator(top) + eq + _guard_repr(self.guard)

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

    def do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs):
        if self.inner.is_recursive():
            return self.inner._update_recursive(
                ops, node, val, has_defaults, _path, nop, guard=self._guard_matches, **kwargs)
        return BaseOp.do_update(self, ops, node, val, has_defaults, _path, nop, **kwargs)

    def do_remove(self, ops, node, val, nop, **kwargs):
        if self.inner.is_recursive():
            return self.inner._remove_recursive(
                ops, node, val, nop, guard=self._guard_matches, **kwargs)
        return BaseOp.do_remove(self, ops, node, val, nop, **kwargs)


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
        _reverse = {v: k for k, v in _TYPE_REGISTRY.items()}
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
        self.transforms = tuple(tuple(r) for r in results.get('transforms', ()))

    def assemble(self, start=0, pedantic=False):
        return assemble(self, start, pedantic=pedantic)
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)}, {list(self.transforms)})'
    @staticmethod
    def _hashable(obj):
        """
        Recursively convert unhashable types to hashable equivalents.
        """
        if is_list_like(obj):
            return tuple(Dotted._hashable(x) for x in obj)
        if is_set_like(obj):
            return frozenset(Dotted._hashable(x) for x in obj)
        if is_dict_like(obj):
            if hasattr(obj, 'items') and callable(obj.items):
                iterable = obj.items()
            else:
                iterable = ((k, obj[k]) for k in obj)
            return tuple(sorted((k, Dotted._hashable(v)) for k, v in iterable))
        return obj
    def __hash__(self):
        try:
            return hash((self.ops, self.transforms))
        except TypeError:
            return hash((self.ops, Dotted._hashable(self.transforms)))
    def __len__(self):
        return len(self.ops)
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


def assemble(ops, start=0, pedantic=False):
    """
    Reassemble ops into a dotted notation string.

    By default, strips a redundant trailing [] (e.g. hello[] -> hello)
    unless it follows another [] (hello[][] is preserved as-is).
    Set pedantic=True to always preserve trailing [].
    """
    parts = []
    top = True
    for op in itertools.islice(ops, start, None):
        parts.append(op.operator(top))
        if not isinstance(op, Invert):
            top = False
    if not pedantic and not top and len(parts) > 1 and parts[-1] == '[]' and parts[-2] != '[]':
        parts.pop()
    return ''.join(parts)


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
    if not ops:
        # At leaf - for numeric Slot, populate index with None
        if isinstance(cur, Slot) and isinstance(cur.op, match.Numeric) and cur.op.is_int():
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




def iter_until_cut(gen):
    """
    Consume a get generator until base._CUT_SENTINEL; yield values, stop on sentinel.
    """
    for x in gen:
        if x is base._CUT_SENTINEL:
            return
        yield x


def _process(stack, paths):
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
    stack = base.DepthStack()
    stack.push(base.Frame(tuple(ops), node, (), kwargs=kwargs or None))
    yield from _process(stack, paths)


def gets(ops, node, **kwargs):
    """
    Yield values for all matches. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, paths=False, **kwargs):
        if path is base._CUT_SENTINEL:
            yield base._CUT_SENTINEL
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
            hasattr(obj, '__iter__') or hasattr(obj, '__dict__'))


def _format_path(segments):
    """
    Consume an iterable of (op, k) segments and assemble the path string for error messages.
    Uses position and op type to decide .key, @attr, [k] and no leading dot on first segment.
    """
    result = []
    for i, (op, k) in enumerate(segments):
        cur = op.inner if isinstance(op, Wrap) else op
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


def _is_concrete_path(branch_ops):
    """
    Return True if branch_ops represents a concrete path (no wildcards/patterns).
    Concrete paths can be created when missing; wildcard paths cannot.
    """
    for op in branch_ops:
        cur = op.inner if isinstance(op, Wrap) else op
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
    if base._has_any(gets(branch_ops, node)):
        return True
    if not _is_concrete_path(branch_ops):
        return False
    first_op = branch_ops[0]
    cur = first_op.inner if isinstance(first_op, Wrap) else first_op
    if isinstance(cur, Key) and getattr(cur, 'filters', ()):
        return False
    return True



def _disjunction_fallback(cur, ops, node, val, has_defaults, _path, nop, **kwargs):
    """
    When nothing matches in disjunction: update first concrete path (last to first).
    """
    for branch in reversed(list(base._branches_only(cur.branches))):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if _is_concrete_path(branch_ops):
            return updates(branch_ops, node, val, has_defaults, _path, nop, **kwargs)
    return node


def updates(ops, node, val, has_defaults=False, _path=None, nop=False, **kwargs):
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
    cur, *ops = ops
    return cur.do_remove(ops, node, val, nop, **kwargs)


def expands(ops, node, **kwargs):
    """
    Yield Dotted objects for all matched paths. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, paths=True, **kwargs):
        if path is base._CUT_SENTINEL:
            return
        yield Dotted({'ops': path, 'transforms': ops.transforms})

# default transforms
from . import transforms
