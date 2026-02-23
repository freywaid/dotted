"""
Base classes, sentinels, and infrastructure for the dotted element system.
"""
import collections
import pyparsing as pp

from .utils import is_dict_like, is_list_like, is_set_like, is_terminal


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
        from .elements import Key
        return [tuple([Key(self)])]
