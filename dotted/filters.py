"""
Filter ops: key-value filters, boolean combinators, and negation.
"""
from . import base
from .access import Slot, Slice


class FilterOp(base.MatchOp):
    def is_pattern(self):
        return False

    def filtered(self, items):
        raise NotImplementedError

    def matchable(self, op):
        raise NotImplementedError

    def match(self, op):
        raise NotImplementedError


class FilterKey(base.MatchOp):
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
        """
        Get all values from node matching this key, traversing dotted path if needed.
        Supports slot parts (e.g. tags[*]) to yield each element of a list for matching.
        Yields (value, True) for each match, or (None, False) if no matches.
        """
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
    Single key=value filter comparison.
    Optionally applies transforms before comparison: key|int=7.
    """
    _eq_str = '='

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if len(self.args) == 1 and hasattr(self.args[0], '__iter__') and not isinstance(self.args[0], (str, bytes)):
            items = list(self.args[0])
            self.key = items[0]
            self.val = items[-1]
            # Middle items are transforms (list after as_list(), or ParseResults)
            self.transforms = tuple(
                tuple(item) for item in items[1:-1]
                if isinstance(item, (list, tuple))
            )
        else:
            self.key = self.args[0]
            self.val = self.args[1]
            self.transforms = ()

    def __hash__(self):
        return hash((self._eq_str, self.key, self.val, self.transforms))

    def __repr__(self):
        t_str = ''.join(f'|{t[0]}' for t in self.transforms) if self.transforms else ''
        return f'{self.key}{t_str}{self._eq_str}{self.val}'

    def _eq_match(self, node):
        """
        True if any value from self.key in node matches self.val (after transforms).
        """
        if not hasattr(node, 'keys'):
            return False
        for val, found in self.key.get_values(node):
            if found:
                if self.transforms:
                    from .results import apply_transforms
                    val = apply_transforms(val, self.transforms)
                for vm in self.val.matches((val,)):
                    return True
        return False

    def is_filtered(self, node):
        return self._eq_match(node)

    def filtered(self, items):
        return (item for item in items if self.is_filtered(item))

    def matchable(self, op):
        if isinstance(op, type(self)):
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
        if not isinstance(op, type(self)):
            return None
        if not self.key.matchable(op.key) or not self.val.matchable(op.val):
            return None
        mk = next(self.key.matches((op.key.value,)), base.marker)
        mv = next(self.val.matches((op.val.value,)), base.marker)
        if base.marker in (mk, mv):
            return None
        return type(op)(op.key, op.val)


class FilterKeyValueNot(FilterKeyValue):
    """
    Single key!=value filter (negated key=value).
    Optionally applies transforms before comparison: key|int!=7.
    """
    _eq_str = '!='

    def is_filtered(self, node):
        return not self._eq_match(node)


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
