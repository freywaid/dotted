"""
"""
import collections
import collections.abc
import contextlib
import copy
import functools
import itertools
import pyparsing as pp
import types
import re


_marker = object()
ANY = _marker

# When a branch with cut (#) matches, we yield this after its results; consumer stops.
_CUT_SENTINEL = object()
# Structural marker: in OpGroup.branches, means "after previous branch, emit _CUT_SENTINEL and stop".
_BRANCH_CUT = object()
# Structural marker: soft cut (##) — after previous branch, suppress later branches for keys already yielded.
_BRANCH_SOFTCUT = object()

# Generator safety (data): keep lazy things lazy as long as possible. Avoid needlessly consuming
# the user's data when it is a generator/iterator (e.g. a sequence at some path, or values from
# .items()/.values()). Only materialize (list/tuple) when we must iterate multiple times or
# mutate-during-iterate. Use _has_any(gen) for "any match?", any(True for _ in gen) for "is empty?",
# next(gen, sentinel) when only the first item is needed. Keeps get_multi(obj, path_iterator)
# lazy-in/lazy-out and avoids pulling large or infinite streams into memory.


def _branches_only(branches):
    """
    Yield branch tuples from OpGroup.branches, skipping _BRANCH_CUT and _BRANCH_SOFTCUT.
    """
    for b in branches:
        if b not in (_BRANCH_CUT, _BRANCH_SOFTCUT):
            yield b


def _path_overlaps(softcut_paths, path):
    """
    Return True if path overlaps with any softcut path — i.e. one is a prefix of the other.
    """
    for sp in softcut_paths:
        n = min(len(sp), len(path))
        if all(sp[j].match(path[j], specials=True) for j in range(n)):
            return True
    return False


def _has_any(gen):
    """Return True if gen yields at least one item, without consuming the rest."""
    return any(True for _ in gen)


from .utils import is_dict_like, is_list_like, is_set_like, is_terminal


class MatchResult:
    def __init__(self, val):
        self.val = val
    def __bool__(self):
        return True



class Op:
    def __init__(self, *args, **kwargs):
        if len(args) == 3 and isinstance(args[2], pp.ParseResults):
            self.args = tuple(args[2].as_list())
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
    def is_recursive(self):
        return False
    def is_slice(self):
        return False


class MetaNOP(type):
    def __repr__(cls):
        return '<NOP>'
    @property
    def value(cls):
        return cls


class NOP(metaclass=MetaNOP):
    @classmethod
    def matchable(cls, op, specials=False):
        return False
    @classmethod
    def matches(cls, vals):
        return ()
    def is_slice(self):
        return False


class Frame:
    """
    Stack frame for the traversal engine.
    """
    __slots__ = ('ops', 'node', 'prefix', 'depth', 'seen_paths')

    def __init__(self, ops, node, prefix, depth=0, seen_paths=None):
        self.ops = ops
        self.node = node
        self.prefix = prefix
        self.depth = depth
        self.seen_paths = seen_paths


class DepthStack:
    """
    Stack of substacks, indexed by depth. Each depth level is a deque.
    OpGroups push a new level for branch isolation; simple ops push
    onto the current level.
    """
    __slots__ = ('_stacks', 'level')

    def __init__(self):
        self._stacks = collections.defaultdict(collections.deque)
        self.level = 0

    def push(self, frame):
        self._stacks[self.level].append(frame)

    def pop(self):
        return self._stacks[self.level].pop()

    def push_level(self):
        self.level += 1

    def pop_level(self):
        del self._stacks[self.level]
        self.level -= 1

    @property
    def current(self):
        return self._stacks[self.level]

    def __bool__(self):
        return bool(self._stacks)


class TraversalOp(Op):
    """
    Base for all ops that participate in traversal (walk/update/remove).
    Base class for ops that participate in stack-based traversal.
    Subclasses must implement push_children(stack, frame, paths).
    """
    def to_branches(self):
        return [tuple([self])]

    def leaf_op(self):
        """
        Find the leaf traversal op for data-access (items, keys, update, pop).
        Simple ops return self; groups recurse into their first branch.
        """
        return self

    def excluded_keys(self, node):
        """
        Collect the set of keys excluded by a negation pattern's first op.
        Simple ops return their own keys; groups recurse into branches.
        """
        return set(self.keys(node))


class MatchOp(Op):
    """
    Base for ops that match values/keys (Const, Pattern, Special, Filter).
    These are used by TraversalOps for pattern matching but never appear
    directly in the ops list processed by the engine.
    """
    def to_branches(self):
        return [tuple([Key(self)])]


class Const(MatchOp):
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, Const)
    def matches(self, vals):
        return (v for v in vals if self.value == v)


class Numeric(Const):
    def is_int(self):
        try:
            return str(self.args[0]) == str(int(self.args[0]))
        except (ValueError, TypeError):
            return False
    @property
    def value(self):
        return int(self.args[0]) if self.is_int() else float(self.args[0])
    def __repr__(self):
        return f'{self.value}'


class NumericExtended(Numeric):
    """
    Numeric from extended literal forms: scientific notation (1e10, 1e-12),
    underscore separators (1_000), hex (0x1F), octal (0o17), binary (0b1010).
    Stores the converted numeric value (int when possible, float otherwise).
    """
    def __init__(self, *args, **kwargs):
        if len(args) == 3 and isinstance(args[2], pp.ParseResults):
            raw = args[2][0]
            stripped = raw.lstrip('-')
            if len(stripped) > 1 and stripped[0] == '0' and stripped[1] in 'xXoObB':
                val = int(raw, 0)
            else:
                f = float(raw.replace('_', ''))
                val = int(f) if f == int(f) else f
            super().__init__(args[0], args[1], pp.ParseResults([val]), **kwargs)
        else:
            super().__init__(*args, **kwargs)


class NumericQuoted(Numeric):
    def __repr__(self):
        if self.is_int():
            return super().__repr__()
        return f"#'{str(self.value)}'"


class Word(Const):
    def __repr__(self):
        return f'{self.value}'


class String(Const):
    def __repr__(self):
        return f'{repr(self.value)}'


class Bytes(Const):
    """
    Byte string literal: b"..." or b'...'
    """
    @property
    def value(self):
        return self.args[0].encode() if isinstance(self.args[0], str) else self.args[0]
    def __repr__(self):
        return repr(self.value)


class Boolean(Const):
    """
    Wrapper for True/False in filter values
    """
    @property
    def value(self):
        return self.args[0] == 'True'
    def __repr__(self):
        return str(self.value)


class NoneValue(Const):
    """
    Wrapper for None in filter values
    """
    @property
    def value(self):
        return None
    def matches(self, vals):
        return (v for v in vals if v is None)
    def __repr__(self):
        return 'None'


class Pattern(MatchOp):
    def __repr__(self):
        return str(self.value)
    def matchable(self, op, specials=False):
        raise NotImplementedError


class Wildcard(Pattern):
    @property
    def value(self):
        return '*'
    def matches(self, vals):
        return iter(v for v in vals if v is not NOP)
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or specials


class WildcardFirst(Wildcard):
    @property
    def value(self):
        return '*?'
    def matches(self, vals):
        v = next(super().matches(vals), _marker)
        return iter(() if v is _marker else (v,))
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or \
            (specials and isinstance(op, (Special, WildcardFirst, RegexFirst)))


class Regex(Pattern):
    @property
    def value(self):
        return f'/{self.args[0]}/'
    @property
    def pattern(self):
        return re.compile(self.args[0])
    def matches(self, vals):
        vals = (v for v in vals if v is not NOP)
        vals = {v if isinstance(v, (str, bytes)) else str(v): v for v in vals}
        iterable = (self.pattern.fullmatch(v) for v in vals)
        # we want to regex match numerics as strings but return numerics
        # unless they were transformed, of course
        for m in iterable:
            if not m:
                continue
            if m[0] != m.string:
                yield m[0]
            else:
                yield vals[m.string]
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or (specials and isinstance(op, (Special, Regex)))


class RegexFirst(Regex):
    @property
    def value(self):
        return f'/{self.args[0]}/?'
    def matches(self, vals):
        iterable = super().matches(vals)
        v = next(iterable, _marker)
        return iter(() if v is _marker else (v,))
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or (specials and isinstance(op, (Special, RegexFirst)))


class Special(MatchOp):
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, Special)
    def matches(self, vals):
        return (v for v in vals if v == self.value)


class Appender(Special):
    @property
    def value(self):
        return '+'
    def matchable(self, op, specials=False):
        return isinstance(op, Appender)
    def matches(self, vals):
        return (v for v in vals if self.value in v)


class AppenderUnique(Appender):
    @property
    def value(self):
        return '+?'


class FilterOp(MatchOp):
    def is_pattern(self):
        return False

    def filtered(self, items):
        raise NotImplementedError

    def matchable(self, op):
        raise NotImplementedError

    def match(self, op):
        raise NotImplementedError


class FilterKey(MatchOp):
    """
    Represents a dotted path in a filter key, e.g. user.id or config.db.host
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # args comes as a single list from pyparsing Group, flatten it
        if len(self.args) == 1 and isinstance(self.args[0], (list, tuple)):
            self.parts = tuple(self.args[0])
        else:
            self.parts = tuple(self.args)
        # Override args with tuple of parts for proper hashing
        self.args = self.parts
        # For simple keys (single part), expose value for backwards compat
        self.value = self.parts[0].value if len(self.parts) == 1 else None

    def __repr__(self):
        out = []
        for i, p in enumerate(self.parts):
            if i and not isinstance(p, (Slot, Slice)) and not isinstance(self.parts[i - 1], (Slot, Slice)):
                out.append('.')
            out.append(repr(p))
        return ''.join(out)

    def __hash__(self):
        return hash(self.parts)

    def is_dotted(self):
        return len(self.parts) > 1

    def get_values(self, node):
        """Get all values from node matching this key, traversing dotted path if needed.
        Supports slot parts (e.g. tags[*]) to yield each element of a list for matching.
        Yields (value, True) for each match, or (None, False) if no matches."""
        yield from self._get_values(node, list(self.parts))

    def _get_values(self, node, parts):
        if not parts:
            yield node, True
            return
        part = parts[0]
        rest = parts[1:]
        if isinstance(part, Slot):
            try:
                for v in part.values(node):
                    yield from self._get_values(v, rest)
            except (TypeError, AttributeError):
                yield None, False
            return
        if isinstance(part, Slice):
            try:
                s = part.slice(node)
                val = node[s]
                yield from self._get_values(val, rest)
            except (TypeError, AttributeError, KeyError, IndexError):
                yield None, False
            return
        # Key-like part (Word, Wildcard, etc.)
        if not hasattr(node, 'keys'):
            yield None, False
            return
        if len(parts) == 1 and not isinstance(part, Slot):
            # Simple key - yield all matching values (existing single-key behavior)
            found_any = False
            for km in part.matches(node.keys()):
                yield node[km], True
                found_any = True
            if not found_any:
                yield None, False
            return
        # Dotted path - traverse (first match only at each key-like level)
        found = False
        for km in part.matches(node.keys()):
            child = node[km]
            yield from self._get_values(child, rest)
            found = True
            break
        if not found:
            yield None, False

    def get_value(self, node):
        """
        Get first matching value from node (backwards compat)
        """
        for val, found in self.get_values(node):
            return val, found
        return None, False

    def matches(self, keys):
        """
        For simple keys, delegate to the inner part's matches
        """
        if not self.is_dotted():
            return self.parts[0].matches(keys)
        return ()

    def matchable(self, op):
        """
        Check if this filter key can match another
        """
        if not isinstance(op, FilterKey):
            return False
        if len(self.parts) != len(op.parts):
            return False
        for sp, op_p in zip(self.parts, op.parts):
            if not sp.matchable(op_p):
                return False
        return True

    def match(self, op):
        """
        Match against another filter key
        """
        if not self.matchable(op):
            return None
        result = []
        for sp, op_p in zip(self.parts, op.parts):
            m = sp.match(op_p)
            if m is None:
                return None
            result.append(m)
        return '.'.join(str(r) for r in result)


class FilterKeyValue(FilterOp):
    """
    Single key=value filter comparison
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # args is a single group containing [key, value]
        if len(self.args) == 1 and hasattr(self.args[0], '__iter__') and not isinstance(self.args[0], (str, bytes)):
            # New format: single (key, value) pair from grammar
            items = list(self.args[0])
            self.key = items[0]
            self.val = items[1]
        else:
            # Legacy format: direct key, value args
            self.key = self.args[0]
            self.val = self.args[1]

    def __hash__(self):
        return hash((self.key, self.val))

    def __repr__(self):
        return f'{self.key}={self.val}'

    def is_filtered(self, node):
        if not hasattr(node, 'keys'):
            return False
        for val, found in self.key.get_values(node):
            if found:
                for vm in self.val.matches((val,)):
                    return True
        return False

    def filtered(self, items):
        return (item for item in items if self.is_filtered(item))

    def matchable(self, op):
        if isinstance(op, FilterKeyValue):
            return True
        # A single comparison can match against an OR if any child matches
        if isinstance(op, FilterOr):
            return any(self.matchable(f) for f in op.filters)
        return False

    def match(self, op):
        if isinstance(op, FilterOr):
            # Match against any of the OR's filters
            for f in op.filters:
                m = self.match(f)
                if m is not None:
                    return m
            return None
        if not isinstance(op, FilterKeyValue):
            return None
        if not self.key.matchable(op.key) or not self.val.matchable(op.val):
            return None
        mk = next(self.key.matches((op.key.value,)), _marker)
        mv = next(self.val.matches((op.val.value,)), _marker)
        if _marker in (mk, mv):
            return None
        return type(op)(op.key, op.val)


class FilterKeyValueNot(FilterOp):
    """
    Single key!=value filter (same semantics as !(key=value), but repr is key!=val for clean reassembly).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if len(self.args) == 1 and hasattr(self.args[0], '__iter__') and not isinstance(self.args[0], (str, bytes)):
            items = list(self.args[0])
            self.key = items[0]
            self.val = items[1]
        else:
            self.key = self.args[0]
            self.val = self.args[1]

    def __hash__(self):
        return hash(('!=', self.key, self.val))

    def __repr__(self):
        return f'{self.key}!={self.val}'

    def _eq_filtered(self, node):
        """True if node matches key=value (same logic as FilterKeyValue.is_filtered)."""
        if not hasattr(node, 'keys'):
            return False
        for val, found in self.key.get_values(node):
            if found:
                for vm in self.val.matches((val,)):
                    return True
        return False

    def is_filtered(self, node):
        return not self._eq_filtered(node)

    def filtered(self, items):
        return (item for item in items if self.is_filtered(item))

    def matchable(self, op):
        if isinstance(op, FilterKeyValueNot):
            return True
        if isinstance(op, FilterOr):
            return any(self.matchable(f) for f in op.filters)
        return False

    def match(self, op):
        if isinstance(op, FilterOr):
            for f in op.filters:
                m = self.match(f)
                if m is not None:
                    return m
            return None
        if not isinstance(op, FilterKeyValueNot):
            return None
        if not self.key.matchable(op.key) or not self.val.matchable(op.val):
            return None
        mk = next(self.key.matches((op.key.value,)), _marker)
        mv = next(self.val.matches((op.val.value,)), _marker)
        if _marker in (mk, mv):
            return None
        return type(op)(op.key, op.val)


class FilterGroup(FilterOp):
    """
    Parenthesized group of filter expressions
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = self.args[0] if self.args else None

    def __hash__(self):
        return hash(self.inner)

    def __repr__(self):
        return f'({self.inner})'

    def is_filtered(self, node):
        return self.inner.is_filtered(node) if self.inner else True

    def filtered(self, items):
        return self.inner.filtered(items) if self.inner else items

    def matchable(self, op):
        return isinstance(op, FilterGroup) and self.inner.matchable(op.inner)

    def match(self, op):
        if not self.matchable(op):
            return None
        return self.inner.match(op.inner)


class FilterAnd(FilterOp):
    """
    Conjunction of filter expressions (all must match)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = tuple(self.args)

    def __hash__(self):
        return hash(self.filters)

    def __repr__(self):
        return '&'.join(str(f) for f in self.filters)

    def is_filtered(self, node):
        return all(f.is_filtered(node) for f in self.filters)

    def filtered(self, items):
        for f in self.filters:
            items = f.filtered(items)
        return items

    def matchable(self, op):
        if not isinstance(op, FilterAnd):
            return False
        return len(self.filters) == len(op.filters)

    def match(self, op):
        if not self.matchable(op):
            return None
        results = []
        for sf, of in zip(self.filters, op.filters):
            if not sf.matchable(of):
                return None
            m = sf.match(of)
            if m is None:
                return None
            results.append(m)
        return FilterAnd(*results)


class FilterOr(FilterOp):
    """
    Disjunction of filter expressions (any must match)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = tuple(self.args)

    def __hash__(self):
        return hash(self.filters)

    def __repr__(self):
        return ','.join(str(f) for f in self.filters)

    def is_filtered(self, node):
        return any(f.is_filtered(node) for f in self.filters)

    def filtered(self, items):
        # For OR, we need to collect all items that match any filter
        items = list(items)  # Need to iterate multiple times
        seen = set()
        for f in self.filters:
            for item in f.filtered(items):
                item_id = id(item)
                if item_id not in seen:
                    seen.add(item_id)
                    yield item


class FilterKeyValueFirst(FilterOp):
    """
    First-match wrapper for any filter expression
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = self.args[0] if self.args else None

    def __hash__(self):
        return hash(self.inner)

    def __repr__(self):
        return f'{self.inner}?'

    def is_filtered(self, node):
        return self.inner.is_filtered(node) if self.inner else True

    def filtered(self, items):
        if self.inner:
            for item in self.inner.filtered(items):
                yield item
                break

    def matchable(self, op):
        return isinstance(op, FilterKeyValueFirst)

    def match(self, op):
        if not self.matchable(op):
            return None
        if self.inner and op.inner:
            return self.inner.match(op.inner)
        return None


class FilterNot(FilterOp):
    """
    Negation of a filter expression - matches items that DON'T pass the inner filter.

    Examples:
        [!status="active"]           - items where status != "active"
        [!(a=1&b=2)]                 - items that don't match both conditions
        [status="active"&!role="admin"]  - active non-admins
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = self.args[0] if self.args else None

    def __hash__(self):
        return hash(('not', self.inner))

    def __repr__(self):
        return f'!{self.inner}'

    def is_filtered(self, node):
        if self.inner is None:
            return False
        return not self.inner.is_filtered(node)

    def filtered(self, items):
        if self.inner is None:
            return
        yield from (item for item in items if not self.inner.is_filtered(item))

    def matchable(self, op):
        if not isinstance(op, FilterNot):
            return False
        if self.inner is None or op.inner is None:
            return False
        return self.inner.matchable(op.inner)

    def match(self, op):
        if not self.matchable(op):
            return None
        return self.inner.match(op.inner)


class OpGroup(TraversalOp):
    """
    Base class for all operation groups (disjunction, conjunction, negation, first-match).

    branches is a sequence of (branch_tuple, _BRANCH_CUT/_BRANCH_SOFTCUT?, branch_tuple, ...).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # args may contain _BRANCH_CUT/_BRANCH_SOFTCUT; normalize branch tuples
        out = []
        for x in self.args:
            if x in (_BRANCH_CUT, _BRANCH_SOFTCUT):
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
        for branch in _branches_only(self.branches):
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

    _BRANCH_CUT in the sequence means: after yielding from the previous branch, yield _CUT_SENTINEL and stop.
    _BRANCH_SOFTCUT in the sequence means: later branches skip keys already yielded by this branch.
    """
    def leaf_op(self):
        """
        Recurse into the first non-cut branch.
        """
        for branch in _branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Union of excluded keys across all non-cut branches.
        """
        excluded = set()
        for branch in _branches_only(self.branches):
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

    def _render(self, top=True):
        parts = []
        for item in self.branches:
            if item is _BRANCH_CUT:
                if parts:
                    parts[-1] += '#'
            elif item is _BRANCH_SOFTCUT:
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
        Return the marker (_BRANCH_CUT or _BRANCH_SOFTCUT) following branch at index i, or None.
        """
        br = self.branches
        if i >= len(br) - 1:
            return None
        nxt = br[i + 1]
        if nxt in (_BRANCH_CUT, _BRANCH_SOFTCUT):
            return nxt
        return None

    def push_children(self, stack, frame, paths):
        """
        Process branches sequentially via DepthStack levels.
        Handles cut, softcut, and path overlap filtering.
        """
        br = self.branches
        softcut_paths = []
        results = []
        for i in range(len(br)):
            item = br[i]
            if item in (_BRANCH_CUT, _BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(frame.ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            is_softcut = marker is _BRANCH_SOFTCUT
            use_paths = paths or bool(softcut_paths) or is_softcut
            stack.push_level()
            stack.push(Frame(branch_ops, frame.node, frame.prefix))
            found = False
            for path, val in _process(stack, use_paths):
                if path is _CUT_SENTINEL:
                    break
                if softcut_paths and path and _path_overlaps(softcut_paths, path):
                    continue
                found = True
                if is_softcut and path:
                    softcut_paths.append(path)
                results.append((path if paths else None, val))
            stack.pop_level()
            if not found:
                continue
            if marker is _BRANCH_CUT:
                results.append((_CUT_SENTINEL, None))
                return results
        return results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        matched_any = False
        br = self.branches
        softcut_paths = []
        for i in range(len(br)):
            item = br[i]
            if item in (_BRANCH_CUT, _BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in walk(branch_ops, node, paths=True):
                if path is _CUT_SENTINEL:
                    break
                if softcut_paths and path and _path_overlaps(softcut_paths, path):
                    continue
                paths.append(path)
            if not paths:
                continue
            matched_any = True
            if marker is _BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                branch_nop = nop or any(isinstance(op, NopWrap) for op in item)
                for path in paths:
                    node = updates(list(path), node, val, has_defaults, _path, branch_nop)
            else:
                node = updates(branch_ops, node, val, has_defaults, _path, nop)
            if marker is _BRANCH_CUT:
                return node
        if not matched_any:
            return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop)
        return node

    def do_remove(self, ops, node, val, nop):
        br = self.branches
        softcut_paths = []
        for i in range(len(br)):
            item = br[i]
            if item in (_BRANCH_CUT, _BRANCH_SOFTCUT):
                continue
            branch_ops = list(item) + list(ops)
            if not branch_ops:
                continue
            marker = self._next_marker(i)
            paths = []
            for path, _ in walk(branch_ops, node, paths=True):
                if path is _CUT_SENTINEL:
                    break
                if softcut_paths and path and _path_overlaps(softcut_paths, path):
                    continue
                paths.append(path)
            if not paths:
                continue
            if marker is _BRANCH_SOFTCUT:
                softcut_paths.extend(p for p in paths if p)
            if softcut_paths:
                for path in paths:
                    node = removes(list(path), node, val)
            else:
                node = removes(branch_ops, node, val)
            if marker is _BRANCH_CUT:
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
        for branch in _branches_only(self.branches):
            if branch:
                return branch[0].leaf_op()
        return self

    def excluded_keys(self, node):
        """
        Union of excluded keys across all non-cut branches.
        """
        excluded = set()
        for branch in _branches_only(self.branches):
            if branch:
                excluded.update(branch[0].excluded_keys(node))
        return excluded

    def _render(self, top=True):
        branch_strs = [''.join(op.operator(top=(top and i == 0)) for i, op in enumerate(b)) for b in _branches_only(self.branches)]
        return '(' + ','.join(branch_strs) + ')?'

    def __repr__(self):
        return self._render(top=True)

    def push_children(self, stack, frame, paths):
        """
        Try branches in order, return first result found.
        """
        for branch in _branches_only(self.branches):
            branch_ops = list(branch) + list(frame.ops)
            if not branch_ops:
                continue
            stack.push_level()
            stack.push(Frame(branch_ops, frame.node, frame.prefix))
            for pair in _process(stack, paths):
                stack.pop_level()
                return [pair]
            stack.pop_level()
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        for branch in _branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if _has_any(gets(branch_ops, node)):
                return updates(branch_ops, node, val, has_defaults, _path, nop)
        return _disjunction_fallback(self, ops, node, val, has_defaults, _path, nop)

    def do_remove(self, ops, node, val, nop):
        for branch in _branches_only(self.branches):
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if _has_any(gets(branch_ops, node)):
                return removes(branch_ops, node, val)
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
            branch_ops = list(branch) + list(frame.ops)
            if not branch_ops:
                continue
            stack.push_level()
            stack.push(Frame(branch_ops, frame.node, frame.prefix))
            branch_results = list(_process(stack, paths))
            stack.pop_level()
            if not branch_results:
                return ()
            all_results.extend(branch_results)
        return all_results

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not _can_update_conjunctive_branch(branch_ops, node):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = updates(branch_ops, node, val, has_defaults, _path, nop)
        return node

    def do_remove(self, ops, node, val, nop):
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            if not _has_any(gets(branch_ops, node)):
                return node
        for branch in self.branches:
            branch_ops = list(branch) + list(ops)
            if branch_ops:
                node = removes(branch_ops, node, val)
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

    def _not_items(self, node):
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
        for k, v in leaf.items(node, filtered=False):
            if k not in excluded:
                yield (k, v)

    def push_children(self, stack, frame, paths):
        inner = self.inner
        if not inner:
            return ()
        leaf = inner[0].leaf_op()
        children = list(self._not_items(frame.node))
        for k, v in reversed(children):
            cp = frame.prefix + (leaf.concrete(k),) if paths else frame.prefix
            stack.push(Frame(frame.ops, v, cp))
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        inner = self.inner
        if not inner:
            return node
        leaf = inner[0].leaf_op()
        remaining_ops = list(inner[1:]) + list(ops)
        for k, v in self._not_items(node):
            if remaining_ops:
                node = leaf.update(node, k, updates(remaining_ops, v, val, has_defaults, _path + [(leaf, k)], nop))
            else:
                node = leaf.update(node, k, val)
        return node

    def do_remove(self, ops, node, val, nop):
        inner = self.inner
        if not inner:
            return node
        leaf = inner[0].leaf_op()
        remaining_ops = list(inner[1:]) + list(ops)
        items = list(self._not_items(node))
        for k, v in reversed(items):
            if remaining_ops:
                node = leaf.update(node, k, removes(remaining_ops, v, val))
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
                out.append(_BRANCH_SOFTCUT)
            elif term[1] == '#':
                out.append(_BRANCH_CUT)
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
            branches = list(_branches_only(inner.branches))
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
    Each slot item becomes a branch; # inserts _BRANCH_CUT after that branch.
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
                out.append(_BRANCH_SOFTCUT)
            elif len(item) >= 2 and item[1] == '#':
                out.append(_BRANCH_CUT)
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


#
#
#
def itemof(node, val):
    return val if isinstance(node, (str, bytes)) else node.__class__([val])



class BaseOp(TraversalOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = ()

    def match(self, op):
        results = ()
        for f, of in zip(self.filters, op.filters):
            if not f.matchable(of):
                return None
            m = f.match(of)
            if m is None:
                return None
            results += (MatchResult(m),)
        return results

    def filtered(self, items):
        for f in self.filters:
            items = f.filtered(items)
        return items

    def keys(self, node):
        return (k for k, _ in self.items(node))

    def values(self, node):
        return (v for _, v in self.items(node))

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        if not ops:
            return node if nop else self.upsert(node, val)
        if self.is_empty(node) and not has_defaults:
            if nop or isinstance(ops[0], NopWrap):
                return node
            built = updates(ops, build_default(ops), val, True, _path, nop)
            return self.upsert(node, built)
        pass_nop = nop and not nop_from_unwrap
        for k, v in self.items(node):
            if v is None:
                v = build_default(ops)
            node = self.update(node, k, updates(ops, v, val, has_defaults, _path + [(self, k)], pass_nop))
        return node

    def do_remove(self, ops, node, val, nop):
        if not ops:
            return node if nop else self.remove(node, val)
        for k, v in self.items(node):
            node = self.update(node, k, removes(ops, v, val, nop=False))
        return node



class SimpleOp(BaseOp):
    """
    Base for ops with items()/concrete() that share the standard walk pattern.
    """

    def push_children(self, stack, frame, paths):
        """
        Push matching children onto the traversal stack.
        Reverse order so first match is popped first (LIFO).
        """
        children = list(self.items(frame.node))
        for k, v in reversed(children):
            cp = frame.prefix + (self.concrete(k),) if paths else frame.prefix
            stack.push(Frame(frame.ops, v, cp))
        return ()


class Empty(SimpleOp):
    """
    Represents an empty path - the root of the data structure.

    Examples:
        get(data, '')      → returns data itself
        update(data, '', v) → replaces root with v
        remove(data, '')   → returns None (root removed)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.args

    def __repr__(self):
        return '.'.join(repr(f) for f in self.filters)

    def is_pattern(self):
        return False

    def is_empty(self, node):
        return False

    def operator(self, top=False):
        return self.__repr__()

    def items(self, node):
        """
        Yield the root as a single item with empty key.
        """
        for v in self.filtered((node,)):
            yield ('', v)

    def keys(self, node):
        """
        Yield empty string as the 'key' for root.
        """
        return (k for k, _ in self.items(node))

    def values(self, node):
        return self.filtered((node,))

    def default(self):
        return None

    def update(self, node, key, val):
        """
        Replace root with val.
        """
        return val

    def upsert(self, node, val):
        """
        Replace root with val.
        """
        return val

    def pop(self, node, key):
        """
        Remove root - return None.
        """
        return None

    def remove(self, node, val):
        """
        Remove root if it matches val.
        """
        if val is ANY or node == val:
            return None
        return node

    @classmethod
    def concrete(cls, val):
        """
        Return a concrete Empty op for the given key value.
        For empty path, the key is always ''.
        """
        return cls()

    def match(self, op, specials=False):
        if not isinstance(op, Empty):
            return None
        m = super().match(op)
        if m is None:
            return m
        return (MatchResult(''),) + m


class AccessOp(SimpleOp):
    """
    Base class for the three access operations: Key (.), Attr (@), Slot ([]).

    Access ops are the traversal primitives that actually look up a child
    value from a node.  Modifiers like ! (negation) and ~ (nop) are not
    access ops — they wrap or filter but don't access anything themselves.

    Inside mid-path groups, every branch must begin with an access op
    (explicit form) or inherit one from a prefix (shorthand form).
    """
    pass


class Key(AccessOp):
    @classmethod
    def concrete(cls, val):
        import numbers
        if isinstance(val, numbers.Number):
            return cls(NumericQuoted(val))
        return cls(Word(val))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.op = self.args[0]
        self.filters = self.args[1:]

    def is_pattern(self):
        return isinstance(self.op, Pattern)

    def __repr__(self):
        return '.'.join(repr(a) for a in self.args)

    def operator(self, top=False):
        q = normalize(self.op.value) if isinstance(self.op, (Word, String)) else repr(self.op)
        iterable = itertools.chain((q,), (repr(f) for f in self.filters))
        s = '.'.join(iterable)
        if top:
            return s
        return '.' + s

    def _items(self, node, keys, filtered=True):
        curkey = None

        def _values():
            nonlocal curkey
            for k in keys:
                try:
                    v = node[k]
                except (TypeError, KeyError, IndexError):
                    continue
                curkey = k
                yield v

        def _items():
            values = self.filtered(_values()) if filtered else _values()
            for v in values:
                yield (curkey, v)

        return _items()

    def items(self, node, filtered=True):
        # Dict-like: use key matching
        if hasattr(node, 'keys'):
            keys = self.op.matches(node.keys()) if filtered else node.keys()
            return self._items(node, keys, filtered)
        # Key only handles lists with concrete numeric keys
        if not hasattr(node, '__getitem__'):
            return ()
        if not isinstance(self.op, Const):
            return ()
        key = self.op.value
        if not isinstance(key, int):
            return ()
        if not filtered:
            return self._items(node, range(len(node)), filtered=False)
        # Treat as sequence index
        try:
            return iter([(key, node[key])])
        except (IndexError, TypeError):
            return ()

    def is_empty(self, node):
        return not any(True for _ in self.keys(node))

    def default(self):
        if self.is_pattern():
            return {}
        if not self.filters:
            return {self.op.value: None}
        return {self.op.value: {}}

    def match(self, op, specials=False):
        if not isinstance(op, Key):
            return None
        if not self.op.matchable(op.op, specials):
            return None

        results = super().match(op)
        if results is None:
            return None

        # match key
        val = next(self.op.matches((op.op.value,)), _marker)
        if val is _marker:
            return None
        results += (MatchResult(val),)
        return results

    def update(self, node, key, val):
        val = self.default() if val is ANY else val
        try:
            node[key] = val
            return node
        except TypeError:
            pass
        iterable = ((k, node[k]) for k in node if k != key)
        iterable = itertools.chain(iterable, ((key, val),))
        return type(node)(iterable)
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)

        keys = tuple(self.keys(node))
        iterable = ((k, node[k]) for k in node if k not in keys)
        items = itertools.chain(iterable, ((k, val) for k in keys))
        try:
            for k, v in items:
                node[k] = v
            return node
        except TypeError:
            pass
        return type(node)(items)

    def pop(self, node, key):
        try:
            del node[key]
            return node
        except KeyError:
            return node
        except TypeError:
            pass
        return type(node)((k, v) for k, v in node.items() if k != key)
    def remove(self, node, val):
        to_remove = [k for k, v in self.items(node) if val is ANY or v == val]
        for k in to_remove:
            node = self.pop(node, k)
        return node


class Attr(Key):
    @classmethod
    def concrete(cls, val):
        return cls(Word(val))

    def __repr__(self):
        return '@' + '.'.join(repr(a) for a in self.args)

    def operator(self, top=False):
        q = normalize(self.op.value) if isinstance(self.op, (Word, String)) else repr(self.op)
        iterable = itertools.chain((q,), (repr(f) for f in self.filters))
        return '@' + '.'.join(iterable)

    def _items(self, node, keys, filtered=True):
        curkey = None

        def _values():
            nonlocal curkey
            for k in keys:
                try:
                    v = getattr(node, k)
                except AttributeError:
                    continue
                curkey = k
                yield v

        def _items():
            values = self.filtered(_values()) if filtered else _values()
            for v in values:
                yield (curkey, v)

        return _items()

    def items(self, node, filtered=True):
        # Try __dict__ first (normal objects), then _fields (namedtuple)
        try:
            all_keys = node.__dict__.keys()
        except AttributeError:
            all_keys = getattr(node, '_fields', ())
        keys = self.op.matches(all_keys) if filtered else all_keys
        return self._items(node, keys, filtered)

    def default(self):
        o = types.SimpleNamespace()
        if self.is_pattern():
            return o
        if not self.filters:
            setattr(o, self.op.value, None)
            return o
        setattr(o, self.op.value, types.SimpleNamespace())
        return o

    def update(self, node, key, val):
        val = self.default() if val is ANY else val
        try:
            setattr(node, key, val)
            return node
        except AttributeError:
            pass
        # Try namedtuple _replace
        if hasattr(node, '_replace'):
            return node._replace(**{key: val})
        # Try dataclasses.replace for frozen dataclass
        import dataclasses
        if dataclasses.is_dataclass(node):
            return dataclasses.replace(node, **{key: val})
        raise AttributeError(f"Cannot set attribute '{key}' on {type(node).__name__}")
    def upsert(self, node, val):
        if not self.is_pattern():
            return self.update(node, self.op.value, val)
        keys = tuple(self.keys(node))
        # Try mutable update first
        try:
            node_keys = node.__dict__.keys()
        except AttributeError:
            node_keys = getattr(node, '_fields', ())
        iterable = ((k, getattr(node, k)) for k in node_keys if k not in keys)
        items = list(itertools.chain(iterable, ((k, val) for k in keys)))
        try:
            for k, v in items:
                setattr(node, k, v)
            return node
        except AttributeError:
            pass
        # Immutable: build replacement dict
        updates = {k: val for k in keys}
        if hasattr(node, '_replace'):
            return node._replace(**updates)
        import dataclasses
        if dataclasses.is_dataclass(node):
            return dataclasses.replace(node, **updates)
        raise AttributeError(f"Cannot set attributes on {type(node).__name__}")

    def pop(self, node, key):
        try:
            delattr(node, key)
        except AttributeError:
            pass
        return node

    def remove(self, node, val):
        to_remove = [k for k, v in self.items(node) if val is ANY or v == val]
        for k in to_remove:
            self.pop(node, k)
        return node


class Slot(Key):
    @classmethod
    def concrete(cls, val):
        import numbers
        if isinstance(val, numbers.Number):
            return cls(Numeric(val))
        return String(val)

    def __repr__(self):
        return '[' + super().__repr__()  + ']'

    def operator(self, top=False):
        iterable = (repr(a) for a in self.filters)
        if self.op is not None:
            if isinstance(self.op, (Word, String)):
                q = normalize(self.op.value, as_key=False)
            elif isinstance(self.op, (Numeric, NumericQuoted)):
                q = repr(self.op)
            else:
                q = self.op.value
            iterable = itertools.chain((q,), iterable)
        return '[' + '.'.join(iterable) + ']'

    def items(self, node, filtered=True):
        if hasattr(node, 'keys'):
            return super().items(node, filtered)

        if not filtered:
            keys = range(len(node))
        elif self.is_pattern():
            keys = self.op.matches(idx for idx, _ in enumerate(node))
        else:
            keys = (self.op.value,)

        return self._items(node, keys, filtered)

    def default(self):
        if isinstance(self.op, Numeric) and self.op.is_int():
            return []
        return super().default()

    def update(self, node, key, val):
        if hasattr(node, 'keys'):
            return super().update(node, key, val)
        val = self.default() if val is ANY else val
        if len(node) <= key:
            node += itemof(node, val)
            return node
        try:
            node[key] = val
        except TypeError:
            node = node[:key] + itemof(node, val) + node[key+1:]
        return node

    def upsert(self, node, val):
        if hasattr(node, 'keys'):
            return super().upsert(node, val)
        val = self.default() if val is ANY else val
        if self.is_pattern():
            keys = tuple(self.keys(node))
        else:
            keys = (self.op.value,)
        update_keys = tuple(k for k in keys if k < len(node))
        append_keys = tuple(k for k in keys if k >= len(node))
        try:
            for k in update_keys:
                node[k] = val
            node += type(node)(val for _ in append_keys)
            return node
        except TypeError:
            pass

        def _gen():
            for i, v in enumerate(node):
                if i in update_keys:
                    yield val
                else:
                    yield v

        # Handle different immutable types
        if isinstance(node, str):
            # str() doesn't accept iterables
            node = ''.join(_gen())
            node += ''.join(str(val) for _ in append_keys)
        elif hasattr(node, '_make'):
            # namedtuple - use _make() and ignore appends (fixed structure)
            node = type(node)._make(_gen())
        elif isinstance(node, frozenset):
            # frozenset - use union
            node = frozenset(_gen())
            if append_keys:
                node = node | frozenset([val])
        else:
            node = type(node)(_gen())
            node += type(node)(val for _ in append_keys)
        return node

    def pop(self, node, key):
        if hasattr(node, 'keys'):
            return super().pop(node, key)
        try:
            del node[key]
            return node
        except (KeyError, IndexError):
            return node
        except TypeError:
            pass
        idx = key if key >= 0 else len(node) + key
        return type(node)(v for i,v in enumerate(node) if i != idx)
    def remove(self, node, val):
        if hasattr(node, 'keys'):
            return super().remove(node, val)
        keys = tuple(self.keys(node))
        if val is ANY:
            if hasattr(node, '__delitem__'):
                for k in reversed(keys):
                    del node[k]
                return node
            n = len(node)
            normalized = {k if k >= 0 else n + k for k in keys}
            return node.__class__(v for i, v in enumerate(node) if i not in normalized)
        if hasattr(node, 'remove'):
            try:
                node.remove(val)
            except (ValueError, KeyError):
                pass
            return node
        try:
            if hasattr(node, 'index'):
                idx = node.index(val)
            else:
                idx = next(idx for idx, v in enumerate(node) if val == v)
        except (ValueError, StopIteration):
            return node
        node = node[:idx] + node[idx+1:]
        return node


class SlotSpecial(Slot):
    @classmethod
    def concrete(cls, val):
        return cls(val)
    def default(self):
        return []

    def items(self, node):
        try:
            yield -1, node[-1]
        except (TypeError, IndexError):
            pass

    def is_empty(self, node):
        return True

    def update(self, node, key, val):
        if not isinstance(key, str):
            return super().update(node, key, val)
        if key == '+' or (key == '+?' and val not in node):
            item = itemof(node, val)
            if isinstance(node, frozenset):
                node = node | item
            else:
                try:
                    node += item
                except TypeError:
                    # Immutable sequence - concatenate
                    node = node + item
        return node
    def upsert(self, node, val):
        return self.update(node, self.op.value, val)

    def pop(self, node, key):
        if isinstance(key, str):
            return None
        return node
    def remove(self, node, val):
        return node


class SliceFilter(BaseOp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters = self.args

    @classmethod
    def concrete(cls, val):
        return Slot.concrete(val)

    def is_pattern(self):
        return False

    def is_empty(self, node):
        return not node

    def match(self, op, specials=False):
        if not isinstance(op, SliceFilter):
            return None
        return super().match(op)

    def items(self, node):
        for idx, v in enumerate(node):
            if any(True for _ in self.filtered((v,))):
                yield (idx, v)

    def upsert(self, node, val):
        return self.update(node, None, val)

    def update(self, node, key, val):
        raise RuntimeError('Updates not supported for slice filtering')

    def remove(self, node, val):
        removes = [idx for idx, _ in self.items(node)]

        if not removes:
            return node

        def _build():
            iterable = (v for idx, v in enumerate(node) if idx not in removes)
            return type(node)(iterable)

        # if we're removing by value, then we need to see _if_ new list will equal
        new = None
        if val is not ANY:
            new = _build()
            if new != val:
                return node

        # attempt to mutate
        try:
            for idx in reversed(removes):
                del node[idx]
            return node
        except TypeError:
            pass

        # otherwise we can't mutate, so generate a new one
        return _build() if new is not None else new

    def pop(self, node, key):
        return self.remove(node, ANY)

    def push_children(self, stack, frame, paths):
        """
        Push filtered container onto the stack.
        Path segment is [] (Slice) since the filter narrows the whole collection.
        """
        filtered = type(frame.node)(self.filtered(frame.node))
        cp = frame.prefix + (Slice.concrete(slice(None)),) if paths else frame.prefix
        stack.push(Frame(frame.ops, filtered, cp))
        return ()



class Slice(SimpleOp):
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
            return quote(a, False) if a is not None else ''
        if not self.args or self.args == (None, None, None):
            return '[]'
        start, stop, step = self.args
        m = [s(start), s(stop)]
        if step is not None:
            m += [s(stop)]
        return '[' + ':'.join(m) + ']'
    def is_pattern(self):
        return False
    def is_slice(self):
        return True
    def operator(self, top=False):
        return str(self)
    def slice(self, node=None):
        args = self.args
        if node is not None:
            args = ( len(node) if a == '+' else a for a in self.args )
        return slice(*args)
    def cardinality(self, node=None):
        """
        Calculate cardinality of a slice; don't both dealing with countably infinite
        set arithmetic, instead just pick a suitably large integer
        """
        s = self.slice(node)
        start = s.start or 0
        stop = s.stop or '+'
        step = s.step or 1
        if '+' in (start, step):
            return 0
        if stop == '+':
            return 1 << 64
        return max(0, int((stop - start) / step))

    def keys(self, node):
        return (self.slice(node),)
    def items(self, node):
        for k in self.keys(node):
            try:
                yield (k, node[k])
            except (TypeError, KeyError, IndexError):
                pass
    def values(self, node):
        return (v for _, v in self.items(node))
    def is_empty(self, node):
        return not node[self.slice(node)]
    def default(self):
        return []
    def matchable(self, op):
        return isinstance(op, Slice)

    def match(self, op, specials=False):
        if not isinstance(op, Slice):
            return None
        if self.cardinality() < op.cardinality():
            return None
        return MatchResult(op.slice())
    def update(self, node, key, val):
        if node[key] == val:
            return node
        try:
            node[key] = self.default() if val is ANY else val
            return node
        except TypeError:
            pass
        r = range(*key.indices(len(node)))
        idx = 0
        out = []
        for i,v in enumerate(node):
            if i not in r:
                out.append(v)
                continue
            if idx < len(val):
                out.append(val[idx])
                idx += 1
        # Append remaining val items (handles empty/shorter node)
        while idx < len(val):
            out.append(val[idx])
            idx += 1
        if hasattr(node, 'join'):
            return node.__class__().join(out)
        return node.__class__(out)
    def upsert(self, node, val):
        return self.update(node, self.slice(node), val)

    def pop(self, node, key):
        try:
            del node[key]
            return node
        except TypeError:
            pass
        r = range(*key.indices(len(node)))
        iterable = ( v for i,v in enumerate(node) if i not in r )
        if hasattr(node, 'join'):
            return node.__class__().join(iterable)
        return node.__class__(iterable)
    def remove(self, node, val):
        if val is ANY:
            return self.pop(node, self.slice(node))
        key = self.slice(node)
        if node[key] == val:
            return self.pop(node, key)
        return node


class Invert(SimpleOp):
    @classmethod
    def concrete(cls, val):
        return cls(val)
    def __repr__(self):
        return '-'
    def is_pattern(self):
        return False
    @property
    def op(self):
        return NOP
    def operator(self, top=False):
        return '-'
    def match(self, op, specials=False):
        return MatchResult('-') if isinstance(op, Invert) else None
    def items(self, node):
        yield ('-', node)
    def keys(self, node):
        yield '-'
    def values(self, node):
        yield node

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        return removes(ops, node, val)

    def do_remove(self, ops, node, val, nop):
        assert val is not ANY, 'Value required'
        return updates(ops, node, val)


class Wrap(TraversalOp):
    """Abstract base for ops that wrap another op; use .inner to get the wrapped op."""

    inner = None  # subclasses set in __init__


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

    def is_pattern(self):
        return self.inner.is_pattern() if hasattr(self.inner, 'is_pattern') else False

    def default(self):
        return self.inner.default() if hasattr(self.inner, 'default') else {}

    def upsert(self, node, val):
        return self.inner.upsert(node, val) if hasattr(self.inner, 'upsert') else val

    def items(self, node):
        return self.inner.items(node) if hasattr(self.inner, 'items') else iter(())

    def values(self, node):
        return self.inner.values(node) if hasattr(self.inner, 'values') else iter(())

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

    def push_children(self, stack, frame, paths):
        return self.inner.push_children(stack, frame, paths)

    def do_update(self, ops, node, val, has_defaults, _path, nop, nop_from_unwrap=False):
        return self.inner.do_update(ops, node, val, has_defaults, _path, nop=True, nop_from_unwrap=True)

    def do_remove(self, ops, node, val, nop):
        return self.inner.do_remove(ops, node, val, nop=True)


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
        self.guard = guard    # value op (Numeric, String, Wildcard, Regex, etc.)
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

    def values(self, node):
        return (v for v in self.inner.values(node) if self._guard_matches(v))

    def items(self, node):
        return ((k, v) for k, v in self.inner.items(node) if self._guard_matches(v))

    def keys(self, node):
        return (k for k, v in self.inner.items(node) if self._guard_matches(v))

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

    def remove(self, node, val):
        # Only remove entries where guard matches
        to_remove = [(k, v) for k, v in self.inner.items(node) if self._guard_matches(v)]
        for k, v in reversed(to_remove):
            if val is ANY or v == val:
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
            children = list(self.items(frame.node))
            for k, v in reversed(children):
                cp = frame.prefix + (self.concrete(k),) if paths else frame.prefix
                stack.push(Frame(frame.ops, v, cp))
            return ()
        matches = [(cp, v) for cp, v in self.inner._collect_matches(
            frame.node, paths, prefix=frame.prefix) if self._guard_matches(v)]
        for cp, v in reversed(matches):
            stack.push(Frame(frame.ops, v, cp))
        return ()

    def do_update(self, ops, node, val, has_defaults, _path, nop):
        if self.inner.is_recursive():
            return self.inner._update_recursive(
                ops, node, val, has_defaults, _path, nop, guard=self._guard_matches)
        return BaseOp.do_update(self, ops, node, val, has_defaults, _path, nop)

    def do_remove(self, ops, node, val, nop):
        if self.inner.is_recursive():
            return self.inner._remove_recursive(
                ops, node, val, nop, guard=self._guard_matches)
        return BaseOp.do_remove(self, ops, node, val, nop)


class Recursive(BaseOp):
    """
    Recursive traversal operator. Matches a pattern at each level and recurses
    into matched values. Handles type detection and iteration directly.

    *key    = follow key chains (inner = Word('key'))
    **      = recursive wildcard (inner = Wildcard())
    */re/   = recursive regex (inner = Regex('re'))
    """

    def __init__(self, inner, *args, depth_start=None, depth_stop=None, depth_step=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = inner          # Pattern op: Wildcard, Word, Regex, etc.
        self.depth_start = depth_start
        self.depth_stop = depth_stop
        self.depth_step = depth_step

    def __repr__(self):
        if isinstance(self.inner, Wildcard):
            return f'**'
        return f'*{self.inner!r}'

    def __hash__(self):
        return hash(('recursive', self.inner, self.depth_start, self.depth_stop, self.depth_step, self.filters))

    def __eq__(self, other):
        return (isinstance(other, Recursive) and self.inner == other.inner
                and self.depth_start == other.depth_start
                and self.depth_stop == other.depth_stop
                and self.depth_step == other.depth_step
                and self.filters == other.filters)

    def is_pattern(self):
        return True

    def is_recursive(self):
        return True

    def operator(self, top=False):
        if isinstance(self.inner, Wildcard):
            s = '**'
        else:
            q = normalize(self.inner.value) if isinstance(self.inner, (Word, String, Numeric, NumericQuoted)) else repr(self.inner)
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

    def match(self, op, specials=False):
        return self.inner.matchable(op, specials=specials)

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

    def _matching_keys(self, node):
        """
        Yield (key, value) pairs for keys matching inner pattern in a mapping.
        """
        for k in self.inner.matches(node.keys()):
            yield k, node[k]

    def _max_depth_to_leaf(self, node):
        """
        Compute max depth to leaf from this node (structural, ignores filters/depth range).
        """
        if is_dict_like(node):
            child_depths = [self._max_depth_to_leaf(v) for k, v in self._matching_keys(node)]
            if child_depths:
                return max(child_depths) + 1
        elif is_list_like(node):
            child_depths = [self._max_depth_to_leaf(item) for item in node]
            if child_depths:
                return max(child_depths) + 1
        return 0

    def _collect_matches(self, node, paths, depth=0, prefix=()):
        """
        Yield (prefix, value) for all nodes matching the recursive pattern.

        Traverses the tree depth-first, yielding matches at each level
        before recursing into children (parent-before-children ordering).
        """
        if is_dict_like(node):
            iterable = self._matching_keys(node)
            matched = True
            concrete = Key.concrete
        elif is_list_like(node):
            iterable = enumerate(node)
            matched = any(True for _ in self.inner.matches(range(len(node))))
            concrete = Slot.concrete
        else:
            return

        for k, v in iterable:
            cp = prefix + (concrete(k),) if paths else prefix
            if not matched or not any(True for _ in self.filtered((v,))):
                yield from self._collect_matches(v, paths, depth + 1, cp)
                continue
            max_dtl = self._max_depth_to_leaf(v) if self._has_negative_depth() else 0
            if not self.in_depth_range(depth, max_dtl):
                yield from self._collect_matches(v, paths, depth + 1, cp)
                continue
            yield (cp, v)
            yield from self._collect_matches(v, paths, depth + 1, cp)

    def push_children(self, stack, frame, paths):
        matches = list(self._collect_matches(frame.node, paths, prefix=frame.prefix))
        for cp, v in reversed(matches):
            stack.push(Frame(frame.ops, v, cp))
        return ()

    def _update_recursive(self, ops, node, val, has_defaults, _path, nop, depth=0, guard=None):
        if is_dict_like(node):
            iterable = list(self._matching_keys(node))
            matched = True
        elif is_list_like(node):
            iterable = list(enumerate(node))
            matched = any(True for _ in self.inner.matches(range(len(node))))
        else:
            return node

        for k, v in iterable:
            # Recurse first (bottom-up)
            v = self._update_recursive(ops, v, val, has_defaults, _path, nop, depth + 1, guard)
            node[k] = v
            if not matched:
                continue
            if not any(True for _ in self.filtered((v,))):
                continue
            max_dtl = self._max_depth_to_leaf(v) if self._has_negative_depth() else 0
            if not self.in_depth_range(depth, max_dtl):
                continue
            if guard and not guard(v):
                continue
            if ops:
                node[k] = updates(ops, v, val, has_defaults, _path + [(self, k)], nop)
            elif not nop:
                node[k] = val
        return node

    def do_update(self, ops, node, val, has_defaults, _path, nop):
        return self._update_recursive(ops, node, val, has_defaults, _path, nop)

    def _remove_recursive(self, ops, node, val, nop, depth=0, guard=None):
        if is_dict_like(node):
            iterable = list(self._matching_keys(node))
            matched = True
        elif is_list_like(node):
            iterable = list(enumerate(node))
            matched = any(True for _ in self.inner.matches(range(len(node))))
        else:
            return node

        to_remove = []
        for k, v in iterable:
            # Recurse first (bottom-up)
            v = self._remove_recursive(ops, v, val, nop, depth + 1, guard)
            node[k] = v
            if not matched:
                continue
            if not any(True for _ in self.filtered((v,))):
                continue
            max_dtl = self._max_depth_to_leaf(v) if self._has_negative_depth() else 0
            if not self.in_depth_range(depth, max_dtl):
                continue
            if guard and not guard(v):
                continue
            if ops:
                node[k] = removes(ops, v, val, nop=False)
            elif not nop:
                to_remove.append(k)
        for k in reversed(to_remove):
            del node[k]
        return node

    def do_remove(self, ops, node, val, nop):
        return self._remove_recursive(ops, node, val, nop)


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
        for cp, v in self._collect_matches(frame.node, paths, prefix=frame.prefix):
            stack.push(Frame(frame.ops, v, cp))
            return ()
        return ()


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


_RESERVED = frozenset('.[]*:|+?/=,@&()!~#{}')
_NEEDS_QUOTE = _RESERVED | frozenset(' \t\n\r')


_NUMERIC_RE = re.compile(
    r'[-]?0[xX][0-9a-fA-F]+$'           # hex
    r'|[-]?0[oO][0-7]+$'                # octal
    r'|[-]?0[bB][01]+$'                 # binary
    r'|[-]?[0-9][0-9_]*[eE][+-]?[0-9]+$' # scientific notation
    r'|[-]?[0-9]+(?:_[0-9]+)+$'         # underscore separators
    r'|[-]?[0-9]+$'                     # plain integers
)


def _needs_quoting(s):
    """
    Return True if a string key must be quoted in dotted notation.
    """
    if not s:
        return True
    # Numeric forms (integers, scientific notation, underscore separators)
    # are handled by the grammar and don't need quoting, even if they
    # contain reserved characters like '+' in '1e+10'.
    if s[0].isdigit() or (len(s) > 1 and s[0] == '-' and s[1].isdigit()):
        return not _NUMERIC_RE.match(s)
    if any(c in _NEEDS_QUOTE for c in s):
        return True
    return False


def _is_numeric_str(s):
    """
    Return True if s is a string that parses as an integer.
    """
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def _quote_str(s):
    """
    Wrap a string in single quotes, escaping backslashes and single quotes.
    """
    s = s.replace('\\', '\\\\').replace("'", "\\'")
    return f"'{s}'"


def quote(key, as_key=True):
    """
    Quote a key for use in a dotted notation path string.

    For raw string keys, wraps in double quotes if the key contains
    reserved characters or whitespace.
    """
    if isinstance(key, str):
        if _needs_quoting(key):
            return _quote_str(key)
        return key
    elif isinstance(key, int):
        return str(key)
    elif isinstance(key, float):
        s = str(key)
        if '.' not in s:
            return s
        if as_key:
            return f"#'{s}'"
        return s
    elif isinstance(key, Op):
        return str(key)
    else:
        raise NotImplementedError


def normalize(key, as_key=True):
    """
    Convert a raw Python key to its dotted normal form representation.

    Like quote(), but also quotes string keys that look numeric so they
    round-trip correctly through pack/unpack (preserving string vs int type).
    """
    if isinstance(key, str) and _is_numeric_str(key):
        return _quote_str(key)
    return quote(key, as_key=as_key)


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
        if isinstance(cur, Slot) and isinstance(cur.op, Numeric) and cur.op.is_int():
            idx = cur.op.value
            return [None] * (idx + 1)
        return cur.default()
    built = cur.default()
    return cur.upsert(built, build_default(ops))


def build(ops, node, deepcopy=True):
    cur, *ops = ops
    built = node.__class__()
    for k,v in cur.items(node):
        if not ops:
            built = cur.update(built, k, copy.deepcopy(v) if deepcopy else v)
        else:
            built = cur.update(built, k, build(ops, v, deepcopy=deepcopy))
    return built or build_default([cur]+ops)




def iter_until_cut(gen):
    """
    Consume a get generator until _CUT_SENTINEL; yield values, stop on sentinel.
    """
    for x in gen:
        if x is _CUT_SENTINEL:
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
        op, *rest = frame.ops
        frame.ops = rest
        yield from op.push_children(stack, frame, paths)


def _walk_engine(ops, node, paths):
    """
    Stack-based traversal engine. Single loop, explicit DepthStack.
    """
    stack = DepthStack()
    stack.push(Frame(ops, node, ()))
    yield from _process(stack, paths)


def walk(ops, node, paths=True):
    """
    Yield (path_tuple, value) for all matches.
    path_tuple is a tuple of concrete ops when paths=True, None when paths=False.
    """
    yield from _walk_engine(ops, node, paths)


def gets(ops, node):
    """
    Yield values for all matches. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, paths=False):
        if path is _CUT_SENTINEL:
            yield _CUT_SENTINEL
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
    if _has_any(gets(branch_ops, node)):
        return True
    if not _is_concrete_path(branch_ops):
        return False
    first_op = branch_ops[0]
    cur = first_op.inner if isinstance(first_op, Wrap) else first_op
    if isinstance(cur, Key) and getattr(cur, 'filters', ()):
        return False
    return True



def _disjunction_fallback(cur, ops, node, val, has_defaults, _path, nop):
    """
    When nothing matches in disjunction: update first concrete path (last to first).
    """
    for branch in reversed(list(_branches_only(cur.branches))):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if _is_concrete_path(branch_ops):
            return updates(branch_ops, node, val, has_defaults, _path, nop)
    return node


def updates(ops, node, val, has_defaults=False, _path=None, nop=False):
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
    return cur.do_update(ops, node, val, has_defaults, _path, nop)


def removes(ops, node, val=ANY, nop=False):
    cur, *ops = ops
    return cur.do_remove(ops, node, val, nop)


def expands(ops, node):
    """
    Yield Dotted objects for all matched paths. Thin wrapper around walk().
    """
    for path, val in walk(ops, node, paths=True):
        if path is _CUT_SENTINEL:
            return
        yield Dotted({'ops': path, 'transforms': ops.transforms})

# default transforms
from . import transforms
