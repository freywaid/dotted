"""
"""
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


def _branches_only(branches):
    """Yield branch tuples from OpGroup.branches, skipping _BRANCH_CUT."""
    for b in branches:
        if b is not _BRANCH_CUT:
            yield b


class Match:
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


class Const(Op):
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


class Pattern(Op):
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


class Special(Op):
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


class FilterOp(Op):
    def is_pattern(self):
        return False

    def filtered(self, items):
        raise NotImplementedError

    def matchable(self, op):
        raise NotImplementedError

    def match(self, op):
        raise NotImplementedError


class FilterKey(Op):
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
            # For primitives with wildcard key, match the value itself
            if len(self.key.parts) == 1 and isinstance(self.key.parts[0], (Wildcard, WildcardFirst)):
                for _ in self.val.matches((node,)):
                    return True
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
            if len(self.key.parts) == 1 and isinstance(self.key.parts[0], (Wildcard, WildcardFirst)):
                for _ in self.val.matches((node,)):
                    return True
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


#
# Path-level grouping
#
class PathOr(Op):
    """
    Disjunction of path keys - returns tuple of values that exist
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.keys = tuple(self.args)

    def __repr__(self):
        return ','.join(str(k) for k in self.keys)

    def is_pattern(self):
        return True

    def items(self, node):
        for k in self.keys:
            # Handle nested groups and conjunctions
            if hasattr(k, 'items') and callable(k.items):
                yield from k.items(node)
                continue

            # Simple key (Const)
            key_val = getattr(k, 'value', k)
            try:
                if hasattr(node, 'keys') and key_val in node:
                    yield (key_val, node[key_val])
                    continue
                if hasattr(node, '__getitem__') and isinstance(key_val, int):
                    yield (key_val, node[key_val])
            except (KeyError, IndexError, TypeError):
                pass

    def values(self, node):
        return (v for _, v in self.items(node))

    def keys_iter(self, node):
        return (k for k, _ in self.items(node))


class PathAnd(Op):
    """
    Conjunction of path keys - returns tuple only if ALL exist
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.keys = tuple(self.args)

    def __repr__(self):
        return '&'.join(str(k) for k in self.keys)

    def is_pattern(self):
        return True

    def items(self, node):
        is_dict = hasattr(node, 'keys')
        is_seq = hasattr(node, '__getitem__')

        def _get(key_val):
            if is_dict and key_val in node:
                return (key_val, node[key_val])
            if is_seq and isinstance(key_val, int):
                try:
                    return (key_val, node[key_val])
                except (IndexError, TypeError):
                    pass
            return None

        items = tuple(_get(getattr(k, 'value', k)) for k in self.keys)
        if None in items:
            return
        yield from items

    def values(self, node):
        return (v for _, v in self.items(node))

    def keys_iter(self, node):
        return (k for k, _ in self.items(node))


class PathGroup(Op):
    """
    Parenthesized path group expression - treated as a pattern
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = self.args[0] if self.args else None
        self.filters = ()

    def __repr__(self):
        return f'({self.inner})'

    def is_pattern(self):
        return True

    def items(self, node):
        if self.inner:
            yield from self.inner.items(node)

    def values(self, node):
        return (v for _, v in self.items(node))

    def keys(self, node):
        return (k for k, _ in self.items(node))

    def default(self):
        return {}

    @classmethod
    def concrete(cls, val):
        return Key.concrete(val)

    def update(self, node, key, val):
        try:
            node[key] = val
            return node
        except TypeError:
            pass
        return node

    def upsert(self, node, val):
        for k, _ in self.items(node):
            self.update(node, k, val)
        return node

    def pop(self, node, key):
        try:
            del node[key]
        except (KeyError, TypeError):
            pass
        return node

    def remove(self, node, val):
        for k, v in list(self.items(node)):
            if val is ANY or v == val:
                self.pop(node, k)
        return node


class PathGroupFirst(PathGroup):
    """
    First-match path group - returns only first matching value
    """
    def __repr__(self):
        return f'({self.inner})?'

    def items(self, node):
        if self.inner:
            for item in self.inner.items(node):
                yield item
                break


class OpGroup(Op):
    """
    Groups multiple operation sequences that branch from a common point (disjunction).

    This enables syntax like:
        a(.b,[])     - from a, get both a.b and a[]
        a(.b#, .c)   - from a, first branch that matches wins (cut); if .b matches, stop

    branches is a sequence of (branch_tuple, _BRANCH_CUT?, branch_tuple, ...).
    _BRANCH_CUT in the sequence means: after yielding from the previous branch, yield _CUT_SENTINEL and stop.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # args may contain _BRANCH_CUT; normalize branch tuples
        out = []
        for x in self.args:
            if x is _BRANCH_CUT:
                out.append(_BRANCH_CUT)
            else:
                b = tuple(x) if isinstance(x, (list, tuple)) else (x,)
                out.append(b)
        self.branches = tuple(out)
        self.args = self.branches

    def __repr__(self):
        parts = []
        for item in self.branches:
            if item is _BRANCH_CUT:
                if parts:
                    parts[-1] += '#'
            else:
                s = ''.join(op.operator(top=(j == 0)) for j, op in enumerate(item))
                parts.append(s)
        return '(' + ','.join(parts) + ')'

    def __hash__(self):
        return hash(self.branches)

    def is_pattern(self):
        return True

    def operator(self, top=False):
        return self.__repr__()


class OpGroupFirst(OpGroup):
    """
    First-match operation group - returns only first matching value across all branches.
    """
    def __repr__(self):
        branch_strs = [''.join(op.operator(top=(i == 0)) for i, op in enumerate(b)) for b in _branches_only(self.branches)]
        return '(' + ','.join(branch_strs) + ')?'


class OpGroupAnd(OpGroup):
    """
    Conjunction of operation sequences - returns values only if ALL branches match.

    This enables syntax like:
        a(.b&.c)     - from a, get a.b and a.c only if both exist
        x(.a.i&.b.k) - from x, get both only if both paths exist

    If any branch fails to match, returns nothing.
    """
    def __repr__(self):
        branch_strs = []
        for branch in self.branches:
            branch_strs.append(''.join(op.operator(top=(i == 0)) for i, op in enumerate(branch)))
        return '(' + '&'.join(branch_strs) + ')'


class OpGroupNot(OpGroup):
    """
    Negation of operation sequences - returns values from paths NOT matching the inner pattern.

    This enables syntax like:
        a(!.b)       - from a, get all keys except b
        a(!(.b,.c))  - from a, get all keys except b and c

    Works by getting all keys at the current level, then excluding those that match
    the negated pattern.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # For negation, we have a single inner expression to negate
        self.inner = self.branches[0] if self.branches else ()

    def __repr__(self):
        inner_str = ''.join(op.operator(top=(i == 0)) for i, op in enumerate(self.inner))
        return f'(!{inner_str})'


def _key_to_op(key_item):
    """Convert a key item (from path grouping) to a Key operation."""
    if isinstance(key_item, Key):
        return key_item
    if isinstance(key_item, (OpGroup, OpGroupAnd, OpGroupFirst, OpGroupNot)):
        # Already an OpGroup from nested path grouping - shouldn't be wrapped
        return key_item
    # Key expects op with .matches (e.g. Const/Word); wrap raw str
    if isinstance(key_item, str):
        key_item = Const(key_item)
    return Key(key_item)


def _path_or_with_cut(parse_tokens):
    """Parse action for (a#, b): build (PathOr(keys), cut_after)."""
    items = parse_tokens  # list of terms (path_group_or result), not parse_tokens[0]
    keys = []
    cut_after = []
    for item in items:
        if hasattr(item, '__getitem__') and not isinstance(item, (str, bytes)):
            keys.append(item[0])
            cut_after.append(len(item) >= 2 and item[1] == '#')
        else:
            keys.append(item)
            cut_after.append(False)
    return (PathOr(*keys), tuple(cut_after))


def _path_to_opgroup(parsed_result):
    """
    Convert path grouping syntax (a,b) or (a#, b) to OpGroup.
    This makes path grouping syntactic sugar for operation grouping.
    """
    inner = parsed_result[0] if parsed_result else None
    cut_after = None
    if isinstance(inner, tuple) and len(inner) == 2 and isinstance(inner[1], tuple):
        inner, cut_after = inner

    def _to_branches(item):
        """Recursively convert path group items to OpGroup branches."""
        # Handle OpGroup types (from nested path group conversion)
        if isinstance(item, OpGroupAnd):
            # Nested AND: return as marker for special handling
            return [item]
        elif isinstance(item, OpGroupNot):
            # Nested NOT: return as marker
            return [item]
        elif isinstance(item, OpGroup):
            # Nested OpGroup: flatten its branches into ours
            return list(item.branches)
        elif isinstance(item, PathOr):
            # PathOr: each key becomes a branch
            branches = []
            for k in item.keys:
                branches.extend(_to_branches(k))
            return branches
        elif isinstance(item, PathAnd):
            # PathAnd: all keys must exist, convert to OpGroupAnd
            and_branches = [tuple([_key_to_op(k)]) for k in item.keys]
            return [OpGroupAnd(*and_branches)]
        elif isinstance(item, PathNot):
            # PathNot: convert to OpGroupNot
            inner_key = item.inner
            if isinstance(inner_key, OpGroup) and not isinstance(inner_key, (OpGroupAnd, OpGroupNot)):
                # (!(a,b)) - keep OpGroup intact so we can extract all keys to exclude
                return [OpGroupNot(tuple([inner_key]))]
            elif isinstance(inner_key, (OpGroupAnd, OpGroupNot)):
                return [OpGroupNot(*inner_key.branches)]
            return [OpGroupNot(tuple([_key_to_op(inner_key)]))]
        elif isinstance(item, PathGroup):
            # Nested PathGroup: recurse into its inner
            return _to_branches(item.inner) if item.inner else []
        else:
            # Simple key: wrap in a list as a single-op branch
            return [tuple([_key_to_op(item)])]

    if inner is None:
        return OpGroup()

    # If inner is already an OpGroup (from nested path group), return it
    if isinstance(inner, OpGroup):
        return inner

    # Check for PathAnd at the top level
    if isinstance(inner, PathAnd):
        branches = [tuple([_key_to_op(k)]) for k in inner.keys]
        return OpGroupAnd(*branches)

    # Check for PathNot at top level
    if isinstance(inner, PathNot):
        inner_key = inner.inner
        if isinstance(inner_key, OpGroup) and not isinstance(inner_key, (OpGroupAnd, OpGroupNot)):
            # (!(a,b)) - keep OpGroup intact so we can extract all keys to exclude
            return OpGroupNot(tuple([inner_key]))
        elif isinstance(inner_key, (OpGroupAnd, OpGroupNot)):
            return OpGroupNot(*inner_key.branches)
        return OpGroupNot(tuple([_key_to_op(inner_key)]))

    # Convert to branches
    branches = _to_branches(inner)

    # Build final branches, then sequence with _BRANCH_CUT where cut_after[i]
    final_branches = []
    for b in branches:
        if isinstance(b, OpGroupAnd):
            final_branches.append(b)
        elif isinstance(b, OpGroupNot):
            final_branches.append(b)
        elif isinstance(b, tuple):
            final_branches.append(b)
        elif isinstance(b, list):
            final_branches.append(tuple(b))
        else:
            final_branches.append(tuple([_key_to_op(b)]))
    out = []
    for i, fb in enumerate(final_branches):
        out.append(fb)
        if cut_after and i < len(cut_after) and cut_after[i]:
            out.append(_BRANCH_CUT)
    # (a&b) parses as PathOr(PathAnd) -> single OpGroupAnd; return it directly
    if len(out) == 1 and isinstance(out[0], OpGroupAnd):
        return out[0]
    return OpGroup(*out)


def _path_to_opgroup_first(parsed_result):
    """Convert path grouping (a,b)? to OpGroupFirst."""
    opgroup = _path_to_opgroup(parsed_result)
    if isinstance(opgroup, OpGroupAnd):
        # (a&b)? - first match of an AND
        return OpGroupFirst(*opgroup.branches)
    return OpGroupFirst(*opgroup.branches)


def _slot_to_opgroup(parsed_result):
    """
    Convert slot grouping [(*&filter, +)] or [(*&filter#, +)] to OpGroup.
    Each slot item becomes a branch; # inserts _BRANCH_CUT after that branch.
    Parse result items may be ParseResults (from Group), so unwrap to get Slot/SlotSpecial.
    """
    out = []
    for item in parsed_result:
        if isinstance(item, (Slot, SlotSpecial)):
            out.append((item,))
            continue
        if not (isinstance(item, (list, tuple, pp.ParseResults)) and len(item) >= 1):
            continue
        first = item[0]
        while isinstance(first, (list, tuple, pp.ParseResults)) and len(first) == 1:
            first = first[0]
        if isinstance(first, (Slot, SlotSpecial)):
            out.append((first,))
            if len(item) >= 2 and item[1] == '#':
                out.append(_BRANCH_CUT)
    return OpGroup(*out)


def _slot_to_opgroup_first(parsed_result):
    """Convert slot grouping [(*&filter, +)?] to OpGroupFirst."""
    opgroup = _slot_to_opgroup(parsed_result)
    return OpGroupFirst(*opgroup.branches)


class PathNot(Op):
    """
    Negation for path keys - returns all keys EXCEPT those matching the inner expression.

    Examples:
        (!a)      - all keys except 'a'
        (!(a,b))  - all keys except 'a' and 'b'
        (!*)      - no keys (negating wildcard)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner = self.args[0] if self.args else None
        self.filters = ()

    def __repr__(self):
        return f'!{self.inner}'

    def is_pattern(self):
        return True

    def _excluded_keys(self, node):
        """
        Get set of keys to exclude based on inner expression.
        """
        if self.inner is None:
            return set()
        # Handle complex expressions (PathOr, PathAnd, PathGroup, etc.)
        if hasattr(self.inner, 'items') and callable(self.inner.items):
            return set(k for k, _ in self.inner.items(node))
        # Handle patterns (Wildcard, Regex) via matches()
        if hasattr(self.inner, 'matches') and callable(self.inner.matches):
            if hasattr(node, 'keys'):
                return set(self.inner.matches(node.keys()))
            if hasattr(node, '__iter__'):
                return set(self.inner.matches(range(len(node))))
            return set()
        # Simple key (Const)
        key_val = getattr(self.inner, 'value', self.inner)
        if hasattr(node, 'keys') and key_val in node:
            return {key_val}
        if hasattr(node, '__getitem__') and isinstance(key_val, int):
            try:
                node[key_val]  # Check if index exists
                return {key_val}
            except (IndexError, TypeError):
                pass
        return set()

    def items(self, node):
        excluded = self._excluded_keys(node)
        if hasattr(node, 'keys'):
            iterable = ((k, node[k]) for k in node.keys())
        elif hasattr(node, '__iter__') and hasattr(node, '__getitem__'):
            iterable = enumerate(node)
        else:
            raise NotImplementedError

        iterable = ((k, v) for k, v in iterable if k not in excluded)
        yield from iterable

    def values(self, node):
        return (v for _, v in self.items(node))

    def keys_iter(self, node):
        return (k for k, _ in self.items(node))

    def default(self):
        return {}

    @classmethod
    def concrete(cls, val):
        return Key.concrete(val)

    def update(self, node, key, val):
        try:
            node[key] = val
            return node
        except TypeError:
            pass
        return node

    def upsert(self, node, val):
        for k, _ in self.items(node):
            self.update(node, k, val)
        return node

    def pop(self, node, key):
        try:
            del node[key]
        except (KeyError, TypeError):
            pass
        return node

    def remove(self, node, val):
        for k, v in list(self.items(node)):
            if val is ANY or v == val:
                self.pop(node, k)
        return node


#
#
#
def itemof(node, val):
    return val if isinstance(node, (str, bytes)) else node.__class__([val])



class CmdOp(Op):
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
            results += (Match(m),)
        return results

    def filtered(self, items):
        for f in self.filters:
            items = f.filtered(items)
        return items


class Empty(CmdOp):
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
        return (Match(''),) + m


class Key(CmdOp):
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
        iterable = itertools.chain((quote(self.op.value),), (repr(f) for f in self.filters))
        s = '.'.join(iterable)
        if top:
            return s
        return '.' + s

    def _items(self, node, keys):
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
            for v in self.filtered(_values()):
                yield (curkey, v)

        return _items()

    def items(self, node):
        # Dict-like: use key matching
        if hasattr(node, 'keys'):
            return self._items(node, self.op.matches(node.keys()))
        # Not indexable or not a concrete key
        if not hasattr(node, '__getitem__'):
            return ()
        if not isinstance(self.op, Const):
            return ()
        # Only numeric keys work as indices
        key = self.op.value
        if not isinstance(key, int):
            return ()
        # Treat as sequence index
        try:
            return iter([(key, node[key])])
        except (IndexError, TypeError):
            return ()

    def keys(self, node):
        return (k for k, _ in self.items(node))

    def values(self, node):
        return (v for _, v in self.items(node))

    def is_empty(self, node):
        return not tuple(self.keys(node))

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
        results += (Match(val),)
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
        iterable = itertools.chain((quote(self.op.value),), (repr(f) for f in self.filters))
        return '@' + '.'.join(iterable)

    def _items(self, node, keys):
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
            for v in self.filtered(_values()):
                yield (curkey, v)

        return _items()

    def items(self, node):
        # Try __dict__ first (normal objects), then _fields (namedtuple)
        try:
            keys = node.__dict__.keys()
        except AttributeError:
            keys = getattr(node, '_fields', ())
        return self._items(node, self.op.matches(keys))

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
            iterable = itertools.chain((quote(self.op.value, as_key=False),), iterable)
        return '[' + '.'.join(iterable) + ']'

    def items(self, node):
        if hasattr(node, 'keys'):
            return super().items(node)

        if self.is_pattern():
            keys = self.op.matches(idx for idx, _ in enumerate(node))
        else:
            keys = (self.op.value,)

        return self._items(node, keys)

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
        return type(node)(v for i,v in enumerate(node) if i != key)
    def remove(self, node, val):
        if hasattr(node, 'keys'):
            return super().remove(node, val)
        keys = tuple(self.keys(node))
        if val is ANY:
            if hasattr(node, '__delitem__'):
                for k in reversed(keys):
                    del node[k]
                return node
            return node.__class__(v for i, v in enumerate(node) if i not in keys)
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


class SliceFilter(CmdOp):
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

    def values(self, node):
        return (type(node)(self.filtered(node)),)
    def items(self, node):
        return ((None, self.values(node)),)
    def keys(self, node):
        # Return actual indices for expand/pluck
        return (k for k, _ in self._items(node))

    def _items(self, node):
        # Filter while preserving indices
        indexed = list(enumerate(node))
        for idx, v in indexed:
            # Check if this item passes all filters
            if tuple(self.filtered((v,))):
                yield (idx, v)

    def upsert(self, node, val):
        return self.update(node, None, val)

    def update(self, node, key, val):
        raise RuntimeError('Updates not supported for slice filtering')

    def remove(self, node, val):
        removes = [idx for idx, _ in self._items(node)]

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


class Slice(CmdOp):
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
        return Match(op.slice())
    def update(self, node, key, val):
        if node[key] == val:
            return node
        try:
            node[key] = self.default() if val is ANY else val
            return node
        except TypeError:
            pass
        r = range(key.start, key.stop, key.step)
        idx = 0
        out = []
        for i,v in enumerate(node):
            if i not in range:
                out.append(v)
                continue
            if idx < len(val):
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
        r = range(key.start, key.stop, key.step)
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


class Invert(CmdOp):
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
        return Match('-') if isinstance(op, Invert) else None
    def items(self, node):
        yield ('-', node)
    def keys(self, node):
        yield '-'
    def values(self, node):
        yield node


class NopWrap(Op):
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

    def assemble(self, start=0):
        return assemble(self, start)
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)}, {list(self.transforms)})'
    def __hash__(self):
        return hash((self.ops, self.transforms))
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


def quote(key, as_key=True):
    if isinstance(key, str):
        try:
            int(key)
            s = repr(key)
        except ValueError:
            s = key
    elif isinstance(key, int):
        s = str(key)
    elif isinstance(key, float):
        if as_key:
            s = f"#'{key}'"
        else:
            s = str(key)
    elif isinstance(key, Op):
        return str(key)
    else:
        raise NotImplementedError
    return s


def assemble(ops, start=0):
    def _gen():
        top = True
        for op in itertools.islice(ops, start, None):
            yield op.operator(top)
            if not isinstance(op, Invert):
                top = False
    return ''.join(_gen())


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


def _get_all_keys(node):
    """
    Get all keys from a node (dict keys or sequence indices).
    """
    if hasattr(node, 'keys'):
        return set(node.keys())
    if hasattr(node, '__iter__') and hasattr(node, '__getitem__'):
        return set(range(len(node)))
    return None


def _gets_opgroup_first(cur, ops, node):
    """
    OpGroupFirst: yield at most one result from all branches.
    """
    for branch in _branches_only(cur.branches):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        for val in gets(branch_ops, node):
            yield val
            return


def _gets_opgroup_and(cur, ops, node):
    """
    OpGroupAnd: yield all results only if ALL branches have results.
    """
    all_results = []
    for branch in cur.branches:
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        branch_results = list(gets(branch_ops, node))
        if not branch_results:
            return
        all_results.extend(branch_results)
    yield from all_results


def _gets_opgroup_not(cur, ops, node):
    """
    OpGroupNot: yield values for keys NOT matching the inner pattern.
    """
    all_keys = _get_all_keys(node)
    if all_keys is None:
        return

    is_list = not hasattr(node, 'keys') and hasattr(node, '__iter__')

    # Collect excluded keys from all branches
    # For (!(a,b)), structure is: branches = (((a,b),),) where (a,b) is an OpGroup
    # We need to traverse into any nested OpGroups to collect all excluded keys
    excluded_keys = set()

    def collect_excluded(op):
        """Recursively collect keys to exclude from an op."""
        if isinstance(op, OpGroup) and not isinstance(op, (OpGroupAnd, OpGroupNot)):
            # It's an OpGroup (disjunction) - collect from all its branches
            for branch in _branches_only(op.branches):
                if branch:
                    collect_excluded(branch[0])
        elif isinstance(op, Key):
            # Key may wrap a pattern (Wildcard, Regex) - need special handling for lists
            inner_op = op.op
            if is_list and hasattr(inner_op, 'matches') and callable(inner_op.matches):
                # Pattern on list: match against indices
                excluded_keys.update(inner_op.matches(range(len(node))))
            else:
                excluded_keys.update(op.keys(node))
        elif hasattr(op, 'keys'):
            excluded_keys.update(op.keys(node))

    for branch in _branches_only(cur.branches):
        if branch:
            collect_excluded(branch[0])

    for k in all_keys:
        if k in excluded_keys:
            continue
        try:
            val = node[k] if hasattr(node, '__getitem__') else getattr(node, k)
        except (KeyError, IndexError, AttributeError):
            continue
        if ops:
            yield from gets(ops, val)
        else:
            yield val


def iter_until_cut(gen):
    """Consume a get generator until _CUT_SENTINEL; yield values, stop on sentinel."""
    for x in gen:
        if x is _CUT_SENTINEL:
            return
        yield x


def _gets_opgroup(cur, ops, node):
    """
    OpGroup (disjunction): yield all results from all branches.
    _BRANCH_CUT: only after the *previous* branch yielded results, yield _CUT_SENTINEL and return.
    If that branch didn't match, skip the CUT and try the next branch.
    """
    br = cur.branches
    for i in range(len(br)):
        item = br[i]
        if item is _BRANCH_CUT:
            continue
        branch_ops = list(item) + list(ops)
        if not branch_ops:
            continue
        found = False
        for value in gets(branch_ops, node):
            found = True
            yield value
        if not found:
            continue
        if i < len(br) - 1 and br[i + 1] is _BRANCH_CUT:
            yield _CUT_SENTINEL
            return


def gets(ops, node):
    cur, *ops = ops
    if isinstance(cur, NopWrap):
        cur = cur.inner
    if isinstance(cur, Invert):
        yield from gets(ops, node)
        return
    if isinstance(cur, OpGroupFirst):
        yield from _gets_opgroup_first(cur, ops, node)
        return
    if isinstance(cur, OpGroupAnd):
        yield from _gets_opgroup_and(cur, ops, node)
        return
    if isinstance(cur, OpGroupNot):
        yield from _gets_opgroup_not(cur, ops, node)
        return
    if isinstance(cur, OpGroup):
        yield from _gets_opgroup(cur, ops, node)
        return
    values = cur.values(node)
    if not ops:
        yield from values
        return
    for v in values:
        yield from gets(ops, v)


def _is_container(obj):
    """
    Check if object can be used as a container for dotted updates.
    """
    if obj is None:
        return False
    # Dict-like, sequence-like, or has attributes
    return (hasattr(obj, 'keys') or hasattr(obj, '__len__') or
            hasattr(obj, '__iter__') or hasattr(obj, '__dict__'))


def _format_path(path):
    """
    Format a path list into dotted notation for error messages.
    """
    if not path:
        return ''
    result = []
    for p in path:
        if isinstance(p, int):
            result.append(f'[{p}]')
        elif result:
            result.append(f'.{p}')
        else:
            result.append(str(p))
    return ''.join(result)


def _is_concrete_path(branch_ops):
    """
    Return True if branch_ops represents a concrete path (no wildcards/patterns).
    Concrete paths can be created when missing; wildcard paths cannot.
    """
    for op in branch_ops:
        cur = op.inner if isinstance(op, NopWrap) else op
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
    if list(gets(branch_ops, node)):
        return True
    if not _is_concrete_path(branch_ops):
        return False
    first_op = branch_ops[0]
    cur = first_op.inner if isinstance(first_op, NopWrap) else first_op
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


def _updates_opgroup_and(cur, ops, node, val, has_defaults, _path, nop=False):
    """
    OpGroupAnd: update all branches such that conjunction eval as true.
    Create missing paths when possible. If filters prevent any branch, do nothing.
    """
    for branch in cur.branches:
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if not _can_update_conjunctive_branch(branch_ops, node):
            return node
    for branch in cur.branches:
        branch_ops = list(branch) + list(ops)
        if branch_ops:
            node = updates(branch_ops, node, val, has_defaults, _path, nop)
    return node


def _updates_opgroup_not(cur, ops, node, val, has_defaults, _path, nop=False):
    """
    OpGroupNot: update keys NOT matching the inner pattern.
    """
    inner = cur.inner
    if not inner:
        return node
    all_keys = _get_all_keys(node)
    if all_keys is None:
        return node
    first_op = inner[0]
    # Collect excluded keys - handle OpGroup for (!(a,b)) case
    if isinstance(first_op, OpGroup) and not isinstance(first_op, (OpGroupAnd, OpGroupNot)):
        excluded_keys = set()
        for branch in _branches_only(first_op.branches):
            if branch and hasattr(branch[0], 'keys'):
                excluded_keys.update(branch[0].keys(node))
        # Use a Key for updates since we need to call .update()
        update_op = Key(Const(''))
    else:
        excluded_keys = set(first_op.keys(node))
        update_op = first_op
    remaining_ops = list(inner[1:]) + list(ops)
    for k in all_keys:
        if k in excluded_keys:
            continue
        try:
            v = node[k] if hasattr(node, '__getitem__') else getattr(node, k)
        except (KeyError, IndexError, AttributeError):
            continue
        if remaining_ops:
            node = update_op.update(node, k, updates(remaining_ops, v, val, has_defaults, _path + [k], nop))
        else:
            node = update_op.update(node, k, val)
    return node


def _updates_opgroup(cur, ops, node, val, has_defaults, _path, nop=False):
    """
    OpGroup (disjunction): update each branch that matches.
    _BRANCH_CUT: after updating previous branch, return.
    When nothing matches: update first concrete path (last to first).
    """
    matched_any = False
    br = cur.branches
    i = 0
    while i < len(br):
        item = br[i]
        if item is _BRANCH_CUT:
            i += 1
            continue
        branch_ops = list(item) + list(ops)
        if branch_ops and list(gets(branch_ops, node)):
            matched_any = True
            node = updates(branch_ops, node, val, has_defaults, _path, nop)
            if i + 1 < len(br) and br[i + 1] is _BRANCH_CUT:
                return node
        i += 1
    if not matched_any:
        return _disjunction_fallback(cur, ops, node, val, has_defaults, _path, nop)
    return node


def _updates_opgroup_first(cur, ops, node, val, has_defaults, _path, nop=False):
    """
    OpGroupFirst: update only the first branch that matches.
    When nothing matches: update first concrete path (last to first).
    """
    for branch in _branches_only(cur.branches):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if list(gets(branch_ops, node)):
            return updates(branch_ops, node, val, has_defaults, _path, nop)
    return _disjunction_fallback(cur, ops, node, val, has_defaults, _path, nop)


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
    nop_from_unwrap = False
    if isinstance(cur, NopWrap):
        cur = cur.inner
        nop = True
        nop_from_unwrap = True
    if isinstance(cur, Invert):
        return removes(ops, node, val)
    if isinstance(cur, OpGroupFirst):
        return _updates_opgroup_first(cur, ops, node, val, has_defaults, _path, nop)
    if isinstance(cur, OpGroupAnd):
        return _updates_opgroup_and(cur, ops, node, val, has_defaults, _path, nop)
    if isinstance(cur, OpGroupNot):
        return _updates_opgroup_not(cur, ops, node, val, has_defaults, _path, nop)
    if isinstance(cur, OpGroup):
        return _updates_opgroup(cur, ops, node, val, has_defaults, _path, nop)
    if not ops:
        return node if nop else cur.upsert(node, val)
    if cur.is_empty(node) and not has_defaults:
        if nop or isinstance(ops[0], NopWrap):
            return node  # NOP: path doesn't exist, don't create
        built = updates(ops, build_default(ops), val, True, _path, nop)
        return cur.upsert(node, built)
    # nop propagates when from parent (e.g. NopWrap(OpGroup)); not when from unwrap at this level (~a.b)
    pass_nop = nop and not nop_from_unwrap
    for k, v in cur.items(node):
        if v is None and ops:
            v = build_default(ops)
        node = cur.update(node, k, updates(ops, v, val, has_defaults, _path + [k], pass_nop))
    return node


def _removes_opgroup_and(cur, ops, node, val):
    """
    OpGroupAnd: remove only if ALL branches would match.
    """
    for branch in cur.branches:
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if not list(gets(branch_ops, node)):
            return node
    for branch in cur.branches:
        branch_ops = list(branch) + list(ops)
        if branch_ops:
            node = removes(branch_ops, node, val)
    return node


def _removes_opgroup_not(cur, ops, node, val):
    """
    OpGroupNot: remove keys NOT matching the inner pattern.
    """
    inner = cur.inner
    if not inner:
        return node
    all_keys = _get_all_keys(node)
    if all_keys is None:
        return node
    first_op = inner[0]
    # Collect excluded keys - handle OpGroup for (!(a,b)) case
    if isinstance(first_op, OpGroup) and not isinstance(first_op, (OpGroupAnd, OpGroupNot)):
        excluded_keys = set()
        for branch in _branches_only(first_op.branches):
            if branch and hasattr(branch[0], 'keys'):
                excluded_keys.update(branch[0].keys(node))
        remove_op = Key(Const(''))
    else:
        excluded_keys = set(first_op.keys(node))
        remove_op = first_op
    remaining_ops = list(inner[1:]) + list(ops)
    keys_to_remove = [k for k in all_keys if k not in excluded_keys]
    for k in reversed(keys_to_remove):
        if remaining_ops:
            try:
                v = node[k] if hasattr(node, '__getitem__') else getattr(node, k)
                node = remove_op.update(node, k, removes(remaining_ops, v, val))
            except (KeyError, IndexError, AttributeError):
                pass
        else:
            node = remove_op.pop(node, k)
    return node


def _removes_opgroup(cur, ops, node, val):
    """
    OpGroup (disjunction): remove from each branch.
    _BRANCH_CUT: after removing from previous branch, return.
    """
    br = cur.branches
    i = 0
    while i < len(br):
        item = br[i]
        if item is _BRANCH_CUT:
            i += 1
            continue
        branch_ops = list(item) + list(ops)
        if branch_ops and list(gets(branch_ops, node)):
            node = removes(branch_ops, node, val)
            if i + 1 < len(br) and br[i + 1] is _BRANCH_CUT:
                return node
        i += 1
    return node


def _removes_opgroup_first(cur, ops, node, val):
    """
    OpGroupFirst: remove only the first branch that matches.
    """
    for branch in _branches_only(cur.branches):
        branch_ops = list(branch) + list(ops)
        if not branch_ops:
            continue
        if list(gets(branch_ops, node)):
            return removes(branch_ops, node, val)
    return node


def removes(ops, node, val=ANY, nop=False):
    cur, *ops = ops
    if isinstance(cur, NopWrap):
        cur = cur.inner
        nop = True
    if isinstance(cur, Invert):
        assert val is not ANY, 'Value required'
        return updates(ops, node, val)
    if isinstance(cur, OpGroupFirst):
        return _removes_opgroup_first(cur, ops, node, val)
    if isinstance(cur, OpGroupAnd):
        return _removes_opgroup_and(cur, ops, node, val)
    if isinstance(cur, OpGroupNot):
        return _removes_opgroup_not(cur, ops, node, val)
    if isinstance(cur, OpGroup):
        return _removes_opgroup(cur, ops, node, val)
    if not ops:
        return node if nop else cur.remove(node, val)
    for k, v in cur.items(node):
        node = cur.update(node, k, removes(ops, v, val, nop=False))
    return node


def expands(ops, node):
    def _expands_opgroup_and(cur, ops, node):
        all_results = []
        for branch in cur.branches:
            branch_ops = list(branch) + list(ops)
            if not branch_ops:
                continue
            branch_results = list(_expands(branch_ops, node))
            if not branch_results:
                return
            all_results.extend(branch_results)
        yield from all_results

    def _expands_opgroup_not(cur, ops, node):
        inner = cur.inner
        if not inner:
            return
        all_keys = _get_all_keys(node)
        if all_keys is None:
            return
        first_op = inner[0]
        # Collect excluded keys - handle OpGroup for (!(a,b)) case
        if isinstance(first_op, OpGroup) and not isinstance(first_op, (OpGroupAnd, OpGroupNot)):
            excluded_keys = set()
            for branch in _branches_only(first_op.branches):
                if branch and hasattr(branch[0], 'keys'):
                    excluded_keys.update(branch[0].keys(node))
            concrete_op = Key(Const(''))
        else:
            excluded_keys = set(first_op.keys(node))
            concrete_op = first_op
        remaining_ops = list(inner[1:]) + list(ops)
        for k in all_keys:
            if k in excluded_keys:
                continue
            try:
                val = node[k] if hasattr(node, '__getitem__') else getattr(node, k)
            except (KeyError, IndexError, AttributeError):
                continue
            concrete = concrete_op.concrete(k)
            if remaining_ops:
                for m in _expands(remaining_ops, val):
                    yield (concrete,) + m
            else:
                yield (concrete,)

    def _expands_opgroup(cur, ops, node):
        br = cur.branches
        i = 0
        while i < len(br):
            item = br[i]
            if item is _BRANCH_CUT:
                return
            branch_ops = list(item) + list(ops)
            if branch_ops:
                results = list(_expands(branch_ops, node))
                if results:
                    yield from results
                    if i + 1 < len(br) and br[i + 1] is _BRANCH_CUT:
                        return
            i += 1

    def _expands(ops, node):
        cur, *ops = ops
        if isinstance(cur, NopWrap):
            cur = cur.inner
        if isinstance(cur, OpGroupAnd):
            yield from _expands_opgroup_and(cur, ops, node)
            return
        if isinstance(cur, OpGroupNot):
            yield from _expands_opgroup_not(cur, ops, node)
            return
        if isinstance(cur, OpGroup):
            yield from _expands_opgroup(cur, ops, node)
            return
        if not ops:
            yield from ((cur.concrete(k),) for k in cur.keys(node))
            return
        for k, v in cur.items(node):
            for m in _expands(ops, v):
                yield (cur.concrete(k),) + m

    return (Dotted({'ops': r, 'transforms': ops.transforms}) for r in _expands(ops, node))

# default transforms
from . import transforms
