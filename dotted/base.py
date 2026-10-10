"""
Base classes, sentinels, and infrastructure for the dotted element system.
"""
import collections
import pyparsing as pp

from .utypes import marker, ANY, CUT_SENTINEL, BRANCH_CUT, BRANCH_SOFTCUT, resolve_types  # noqa: F401


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


def match_ops(pats, path_ops, partial):
    """
    Match a list of pattern ops against a list of path ops.
    Returns a list of (value, is_pattern) capture pairs on success, None
    on failure.  Dispatch is per-op on both sides: a variadic op at the
    head of the path (group, recursive) decides how the pattern must
    cover it via do_match_path; otherwise the head pattern op decides
    how many path segments it consumes via do_match.
    """
    if path_ops and path_ops[0].is_variadic():
        return path_ops[0].do_match_path(list(pats), list(path_ops[1:]), partial)
    if pats:
        return pats[0].do_match(pats[1:], path_ops, partial)
    if not path_ops:
        return []
    if partial:
        return []
    return None


class MatchResult:
    def __init__(self, val):
        self.val = val
    def __bool__(self):
        return True


# Each MatchOp class's _match_from with its forward references resolved,
# kept here because a compiled class cannot be given a new attribute.
_MATCH_FROM = {}


def match_from(cls):
    """
    The types cls accepts as match targets; its _match_from, resolved once.
    """
    accept = _MATCH_FROM.get(cls)
    if accept is not None:
        return accept
    accept = cls._match_from
    if any(isinstance(t, str) for t in accept):
        accept = resolve_types(vars(matchers), accept)
    _MATCH_FROM[cls] = accept
    return accept


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
    def match_keys(self, node):
        """
        Keys of dict-like `node` that this op matches.
        """
        return self.matches(node.keys())
    def is_recursive(self):
        return False
    def is_slice(self):
        return False
    def is_variadic(self):
        """
        True if this op can consume a variable number of path segments when
        matching a path (recursive ops, groups).
        """
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
    @classmethod
    def match_keys(cls, node):
        return ()
    def is_slice(self):
        return False


class Settings:
    """
    What holds for the whole of one traversal: the root node, the chain of
    parents when a reference needs it, and strict. Made once by the engine
    and passed down every call; see SETTINGS for calls made without one.
    """
    __slots__ = ('root', 'parents', 'strict')

    def __init__(self, root, strict=False, parents=None):
        self.root = root
        self.parents = parents
        self.strict = strict

    def below(self, node):
        """
        The settings one level down: the same, with node added to the
        parents when they are being tracked.
        """
        if self.parents is None:
            return self
        return Settings(self.root, self.strict, (node,) + self.parents)


# For a traversal method called on its own, outside any traversal: no root,
# not strict, parents not tracked.
SETTINGS = Settings(None)


class UnresolvedReference(KeyError):
    """
    A $$(reference) names a path that is not there, or reaches above the
    ancestors the traversal has. In an access position the reference then
    matches nothing; in a transform, guard or filter the value under test
    is not matched.
    """


class Frame:
    """
    Stack frame for the traversal engine.
    """
    __slots__ = ('ops', 'node', 'prefix', 'depth', 'seen_paths', 'settings')

    def __init__(self, ops, node, prefix, depth=0, seen_paths=None, settings=SETTINGS):
        self.ops = ops
        self.node = node
        self.prefix = prefix
        self.depth = depth
        self.seen_paths = seen_paths
        self.settings = settings


class DepthStack:
    """
    Stack of substacks, indexed by depth. Each depth level is a deque.
    OpGroups push a new level for branch isolation; simple ops push
    onto the current level. The transforms, when given, are applied to
    each value as its frame reaches a leaf (see engine.process).
    """
    __slots__ = ('_stacks', 'level', 'current', 'transforms')

    def __init__(self, transforms=()):
        self._stacks = collections.defaultdict(collections.deque)
        self.level = 0
        self.current = self._stacks[0]
        self.transforms = transforms

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
    def push_children(self, stack, frame, paths):
        """
        Push a frame for each match of this op in frame.node onto stack,
        and return this op's own results, if any.
        """
        raise NotImplementedError

    def update_items(self, node, settings=SETTINGS):
        """
        The items do_update should update in node, or None if this op finds
        node empty.
        """
        if self.is_empty(node):
            return None
        return self.items(node, settings=settings)

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

    def is_reference(self):
        """
        True if this op contains an internal reference.
        """
        return False

    def covered_by(self, matcher):
        """
        True if *matcher* (a match op) matches every segment this op
        denotes when it appears on the path side.  Simple ops denote a
        single concrete segment; variadic ops override.
        """
        val = getattr(getattr(self, 'op', self), 'value', self)
        return has_any(matcher.matches((val,)))

    def do_match(self, rest_pats, path_ops, partial):
        """
        Match this op against exactly one path segment, then continue with
        the remaining pattern ops.  Variadic ops (recursive, groups)
        override to consume a variable number of segments.
        """
        if not path_ops:
            return None
        m = self.match(path_ops[0], specials=True)
        if not m:
            return None
        rest = match_ops(rest_pats, path_ops[1:], partial)
        if rest is None:
            return None
        is_pat = self.is_pattern()
        if isinstance(m, (tuple, list)):
            return [(_m.val, is_pat) for _m in m] + rest
        return [(m.val, is_pat)] + rest


class MatchOp(Op):
    """
    Base for ops that match values/keys (Const, Pattern, Special, Filter).
    These are used by TraversalOps for pattern matching but never appear
    directly in the ops list processed by the engine.

    Subclasses declare their matchability via _match_from:

    _match_from: tuple of types this op accepts as match targets, or None
        to use (type(self),) as default (meaning "accept my own kind").
        Use (ANY,) to accept everything.  Entries may be strings
        (forward references) which are resolved lazily against the
        declaring module's namespace on first access.
        Non-Const targets are only accepted when specials=True.
    """
    _match_from = None

    def matchable(self, op, specials=False):
        """
        Can this op match against *op*?

        Checks isinstance(op, t) for each type t in _match_from.
        When _match_from is None, accepts own type unconditionally
        ("same-kind" matching, no specials gate).
        When _match_from is explicit, Const targets are always accepted
        and non-Const targets require specials=True.
        """
        if self._match_from is None:
            return isinstance(op, type(self))
        accept = match_from(type(self))
        if any(isinstance(op, t) for t in accept):
            if isinstance(op, matchers.Const):
                return True
            return specials
        return False

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

    def quote_top(self):
        """
        quote() for the first segment of a path. A path that starts with '-'
        is inverted, so a key that would be written with a leading '-' has
        to be written another way there.
        """
        return self.quote()

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
            from .results import Dotted
            return hash(('transform', self.name, Dotted._hashable(self.params)))

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

    def is_template(self):
        """
        True if a param is an unresolved substitution.
        """
        return any(hasattr(p, 'is_template') and p.is_template() for p in self.params)

    def is_reference(self):
        """
        True if a param is an internal reference.
        """
        return any(hasattr(p, 'is_reference') and p.is_reference() for p in self.params)

    def arguments(self, root=None, node=None, parents=()):
        """
        The params as the values the transform function receives: a
        reference resolved against root, node and parents, where node is
        the value being transformed (so $$(^x) looks x up in it) and
        parents its ancestors, nearest first; a parsed constant, such as a
        resolved substitution or null, unwrapped; anything else as parsed.
        Raises UnresolvedReference when a reference does not resolve.
        """
        if not self.params:
            return ()
        args = []
        for p in self.params:
            if hasattr(p, 'is_reference') and p.is_reference():
                args.append(p.resolve_ref(root, node=node, parents=parents))
                continue
            if isinstance(p, matchers.Const):
                args.append(p.value)
                continue
            args.append(p)
        return args


# matchers imports this module, so it can only be bound once the classes
# above exist.
from . import matchers  # noqa: E402
