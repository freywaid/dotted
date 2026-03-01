"""
Base classes, sentinels, and infrastructure for the dotted element system.
"""
import collections
import pyparsing as pp

from .utypes import marker, ANY, CUT_SENTINEL, BRANCH_CUT, BRANCH_SOFTCUT  # noqa: F401


# Generator safety (data): keep lazy things lazy as long as possible. Avoid needlessly consuming
# the user's data when it is a generator/iterator (e.g. a sequence at some path, or values from
# .items()/.values()). Only materialize (list/tuple) when we must iterate multiple times or
# mutate-during-iterate. Use has_any(gen) for "any match?", any(True for _ in gen) for "is empty?",
# next(gen, sentinel) when only the first item is needed. Keeps get_multi(obj, path_iterator)
# lazy-in/lazy-out and avoids pulling large or infinite streams into memory.


def branches_only(branches):
    """
    Yield branch tuples from OpGroup.branches, skipping BRANCH_CUT and BRANCH_SOFTCUT.
    """
    for b in branches:
        if b not in (BRANCH_CUT, BRANCH_SOFTCUT):
            yield b


def path_overlaps(softcut_paths, path):
    """
    Return True if path overlaps with any softcut path — i.e. one is a prefix of the other.
    """
    for sp in softcut_paths:
        n = min(len(sp), len(path))
        if all(sp[j].match(path[j], specials=True) for j in range(n)):
            return True
    return False


def has_any(gen):
    """
    Return True if gen yields at least one item, without consuming the rest.
    """
    return any(True for _ in gen)


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
    def resolve(self, bindings, partial=False):
        """
        Return a new op with all substitutions resolved.
        Default: return self (no substitutions).
        """
        return self
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
    __slots__ = ('ops', 'node', 'prefix', 'depth', 'seen_paths', 'kwargs')

    def __init__(self, ops, node, prefix, depth=0, seen_paths=None, kwargs=None):
        self.ops = ops
        self.node = node
        self.prefix = prefix
        self.depth = depth
        self.seen_paths = seen_paths
        self.kwargs = kwargs


class DepthStack:
    """
    Stack of substacks, indexed by depth. Each depth level is a deque.
    OpGroups push a new level for branch isolation; simple ops push
    onto the current level.
    """
    __slots__ = ('_stacks', 'level', 'current')

    def __init__(self):
        self._stacks = collections.defaultdict(collections.deque)
        self.level = 0
        self.current = self._stacks[0]

    def push(self, frame):
        self.current.append(frame)

    def pop(self):
        return self.current.pop()

    def push_level(self):
        self.level += 1
        self.current = self._stacks[self.level]

    def pop_level(self):
        del self._stacks[self.level]
        self.level -= 1
        self.current = self._stacks[self.level]

    def __bool__(self):
        return bool(self._stacks)


class TraversalOp(Op):
    """
    Base for all ops that participate in traversal (walk/update/remove).
    Base class for ops that participate in stack-based traversal.
    Subclasses must implement push_children(stack, frame, paths).
    """
    @property
    def most_inner(self):
        """
        Return self — no wrapping to unwrap.
        """
        return self

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

    def is_template(self):
        """
        True if this op contains unresolved substitution references.
        """
        return False


class MatchOp(Op):
    """
    Base for ops that match values/keys (Const, Pattern, Special, Filter).
    These are used by TraversalOps for pattern matching but never appear
    directly in the ops list processed by the engine.
    """
    def is_pattern(self):
        """
        True if this op is a pattern (wildcard, regex, etc.).
        """
        return False

    def is_template(self):
        """
        True if this op is a substitution reference.
        """
        return False

    def is_reference(self):
        """
        True if this op is an internal reference ($(path)).
        """
        return False

    def quote(self):
        """
        Return the dotted notation form of this op.
        """
        return repr(self)

    def to_branches(self):
        from .access import Key
        return [tuple([Key(self)])]


class Transform(Op):
    """
    A named transform with optional parameters: |name or |name:param1:param2.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.args[0]
        self.params = self.args[1:]

    def __repr__(self):
        return self.operator()

    def __hash__(self):
        try:
            return hash(('transform', self.name, self.params))
        except TypeError:
            return hash(('transform', self.name,
                         tuple(tuple(p) if isinstance(p, list) else p for p in self.params)))

    def __eq__(self, other):
        return (isinstance(other, Transform)
                and self.name == other.name
                and self.params == other.params)

    def operator(self):
        """
        Render as name:param1:param2 (without leading |).
        """
        parts = [self.name]
        for p in self.params:
            if p is None:
                parts.append(':')
            elif isinstance(p, str):
                parts.append(':' + repr(p))
            else:
                parts.append(':' + repr(p))
        return ''.join(parts)

    def resolve(self, bindings, partial=False):
        """
        Resolve $N in transform params.
        """
        new_params = tuple(
            p.resolve(bindings, partial) if hasattr(p, 'resolve') else p
            for p in self.params)
        if all(np is op for np, op in zip(new_params, self.params)):
            return self
        return Transform(self.name, *new_params)
