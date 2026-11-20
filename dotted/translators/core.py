"""
Target-neutral core of dotted.translators.

Defines `TranslationError` and `Translator`, the walk over a parsed
path's ops shared by every translation target (SQL flavors, Spark).
The walk decides what kind of path it is looking at — column guard,
top-level group, plain traversal, nested guard, nested group, pattern
path — and calls flavor hooks to emit the target's expressions.

A path reads one of two ways, picked by `root=`. As a column path
(`root='column'`, the default) its first segment is a column of the
row. As a table search (`root='table'`) its leading keys name a table,
a row step (`[*]`, `[*&…]`, `[n]`, `[a:b]`, a slice filter `[…]`)
walks its rows, and the rest is a column path under each row. A path
that starts with a row step is a search of an unnamed table under
either root. A table search hands back plain values beside the
fragments: the table's name, an offset and a limit.

Flavor hooks (each flavor mixin implements them):

    _column(name)                     → column expression
    _all_columns()                    → every column of a row (`*`)
    _row(table, columns)              → the row as one value, for a
                                        group or negation at the top
                                        of a path: its `columns`, or
                                        the whole row of `table` when
                                        `columns` is None
    _table_from(names)                → a table search's `from`: the
                                        table it reads
    _table_rows(names, preds, offset, limit)
                                      → the rows of the table `names`
                                        where `preds` hold, in the
                                        window `offset` / `limit`
    _table_rows_kept(rows, pred, gates)     → those of `rows` where `pred`
                                        holds, none unless each gate
                                        `(rows, pred, exists)` holds
    _table_rows_first(rows) / _table_rows_union(parts)
                                      → the first of the rows / each
                                        part's in turn
    _table_rows_from(rows)                  → `from` for a group of row
                                        steps: the rows it yields
    _combine_not_true(pred)           → `pred` doesn't hold: false or
                                        null
    _select_expr(col, prefix)         → extraction expression for a
                                        prefix of path segments
    _scalar_predicate(guard, col)     → predicate for a guard on the
                                        column itself
    _nested_predicate(guard, col, prefix)
                                      → predicate for a guard inside
                                        the column
    _pattern(col, prefix, ops)        → fragments dict for a pattern
                                        path (wildcards, filters, `**`,
                                        groups)
    _combine_and(preds) / _combine_or(preds) / _combine_not(pred)
                                      → boolean composition

A prefix is a tuple of `('lit', value)` segments for plain keys and
`('expr', <flavor expression>)` segments for references, whose
subpath is translated through the same walk. A literal's value is a
string unless the flavor overrides `_segment_value`.

The walk and the flavors read ops through `Hooks`: each kind of op,
matcher and filter calls its own hook (`Op.translated`), so no part of
a translation tests an op's class, and a kind a part doesn't read
raises in its `other` hook.

Expressions are opaque to the walk: whatever the hooks return is
carried through and handed back to other hooks. The walk returns a
dict of fragment name (`select`, `where`, `lateral`, ...) → flavor
expression; turning that into the target's public result is the
flavor's `translate()`.
"""
import functools

from .. import access
from .. import base
from .. import engine
from .. import groups as _groups
from .. import matchers
from .. import predicates
from .. import wrappers
from ..api import parse


# what a path's first segment is: a column of the row, or a table
ROOTS = ('column', 'table')

# the plain values of a column path, which searches no table
_NO_TABLE = (None, None, None)


# ---- Python regexes, as their parse shows them ----------------------

def regex_parser():
    """
    Python's own regex parser: `re._parser`, `sre_parse` before 3.11.
    """
    try:
        from re import _parser
        return _parser
    except ImportError:
        import sre_parse
        return sre_parse


def may_start_with(pattern, c):
    """
    False where no match of the Python regex `pattern` can start with
    the character `c`, as its parse shows; True where one may.
    """
    sre = regex_parser()
    return _items_may_start(list(sre.parse(pattern)), c, sre)


def _items_may_start(items, c, sre):
    """
    `may_start_with` for a parsed run of regex items.
    """
    for op, av in items:
        may, empty = _item_may_start(op, av, c, sre)
        if may:
            return True
        if not empty:
            return False
    return False


def _items_may_be_empty(items, c, sre):
    """
    True if a parsed run of regex items may match the empty string.
    """
    return all(_item_may_start(op, av, c, sre)[1] for op, av in items)


def _item_may_start(op, av, c, sre):
    """
    For one parsed regex item: whether a match of it may start with the
    character `c`, and whether it may match the empty string. What it
    can't tell, it says may.
    """
    if op is sre.LITERAL:
        return chr(av) == c, False
    if op is sre.NOT_LITERAL:
        return chr(av) != c, False
    if op is sre.ANY:
        return c != '\n', False
    if op is sre.AT:
        return False, True
    if op is sre.IN:
        return _in_class(av, c, sre), False
    if op is sre.SUBPATTERN:
        body = list(av[-1])
        return _items_may_start(body, c, sre), _items_may_be_empty(body, c, sre)
    if op is sre.BRANCH:
        alts = [list(alt) for alt in av[1]]
        return (any(_items_may_start(alt, c, sre) for alt in alts),
                any(_items_may_be_empty(alt, c, sre) for alt in alts))
    if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, getattr(sre, 'POSSESSIVE_REPEAT', None)):
        low, _, body = av
        body = list(body)
        return _items_may_start(body, c, sre), low == 0 or _items_may_be_empty(body, c, sre)
    return True, False


def _in_class(items, c, sre):
    """
    Whether a parsed character class may match the character `c`.
    """
    negate = bool(items) and items[0][0] is sre.NEGATE
    found = False
    for op, av in items:
        if op is sre.NEGATE:
            continue
        if op is sre.LITERAL and chr(av) == c:
            found = True
        elif op is sre.RANGE and av[0] <= ord(c) <= av[1]:
            found = True
        elif op is sre.CATEGORY and _in_category(av, c, sre):
            found = True
        elif op not in (sre.LITERAL, sre.RANGE, sre.CATEGORY):
            return True
    return found != negate


def _in_category(category, c, sre):
    """
    Whether the character `c` is in a regex category, `\\d`, `\\w`, `\\s`
    or their negations.
    """
    tests = {
        sre.CATEGORY_DIGIT: c.isdigit(),
        sre.CATEGORY_NOT_DIGIT: not c.isdigit(),
        sre.CATEGORY_WORD: c.isalnum() or c == '_',
        sre.CATEGORY_NOT_WORD: not (c.isalnum() or c == '_'),
        sre.CATEGORY_SPACE: c.isspace(),
        sre.CATEGORY_NOT_SPACE: not c.isspace(),
    }
    return tests.get(category, True)


class TranslationError(Exception):
    """
    Raised when a dotted path cannot be translated or when a
    `Resolver.build` call is malformed.
    """
    pass


def _op_is_pattern_like(op):
    """
    True if the op introduces a set-returning or recursive element
    that can't be expressed as a literal path of segments.
    """
    if isinstance(op, (access.SliceFilter, access.Slice, access.SlotSpecial,
                       wrappers.FilterWrap, wrappers.TransformWrap)):
        # a filter or an append slot may reach nothing, a slice reads a
        # list as one value, and a transform changes what the path after
        # it reads: a pattern path reads them
        return True
    if hasattr(op, 'is_pattern') and op.is_pattern():
        return True
    return False


def _check_guards(ops, followed=False):
    """
    Raise if a value guard has more path after it, a group's branch
    included: there the path goes on with what follows the group.
    `followed` says more path follows `ops`.
    """
    for i, op in enumerate(ops):
        more = followed or i < len(ops) - 1
        if isinstance(op, wrappers.ValueGuard) and more:
            raise TranslationError('path continues after value guard')
        if not isinstance(op, _groups.OpGroup):
            continue
        for branch in base.branches_only(op.branches):
            _check_guards(branch, more)


def _only_branch(op):
    """
    The one branch of a group that is not a pattern, (a) or (a.b): it
    reads as its branch spliced into the path.
    """
    return tuple(next(base.branches_only(op.branches)))


def _is_table_key(op):
    """
    True if `op` is a plain key, one segment of a table's name.
    """
    return isinstance(op, access.Key) and not isinstance(op, access.Slot)


def _is_every_row(op):
    """
    True if `op` is `[*]`.
    """
    if isinstance(op, access.SlotSpecial):
        return False
    return isinstance(op, access.Slot) and isinstance(op.op, matchers.Wildcard)


def _is_row_step(op):
    """
    True if `op` steps into a table's rows: `[*]`, `[*&…]`, `[n]`,
    `[a:b]` or a slice filter `[…]`.
    """
    if isinstance(op, access.SlotSpecial):
        return False
    if isinstance(op, (access.Slot, access.Slice, access.SliceFilter)):
        return True
    return isinstance(op, wrappers.FilterWrap) and isinstance(op.inner, access.Slot)


def _non_negative_int(value):
    """
    True if `value` is an int (not a bool) of at least zero.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _slice_window(op):
    """
    A slice's `(offset, limit)`: `[a:b]` → `(a, b-a)`, `[a:]` →
    `(a, None)`, `[:b]` → `(None, b)`.
    """
    start, stop, step = op.args
    if step is not None:
        raise TranslationError(f'slice step not supported in a table search: {op}')
    for bound in (start, stop):
        if isinstance(bound, matchers.Subst):
            raise TranslationError(
                f'unresolved substitution in slice bound: {bound} — pass bindings='
            )
        if bound is not None and not _non_negative_int(bound):
            raise TranslationError(
                f'slice bounds must be non-negative ints in a table search: {op}'
            )
    if stop is None:
        return start, None
    return start, max(stop - (start or 0), 0)


class Hooks:
    """
    How one part of a translation reads each kind of op, matcher and
    filter. An op calls the hook for its kind (`Op.translated`), so a
    translation names the kinds it reads and never tests an op's class.

    A hook falls back to its kind's broader one: `attr` and `slot` to
    `key`, `numeric` to `const`, `wildcard` and `wildcard_first` to
    `pattern`, the `,`, `&`
    and `?` groups to `group` (with their kind and branches), and the
    broadest to `other`, which raises. A part of a translation
    overrides the hooks for what it reads, and `other` for its error.
    """

    def other(self, op, *args):
        """
        The hook for a kind this part of the translation doesn't read.
        """
        raise TranslationError(f'not supported here: {type(op).__name__} {op}')

    # ---- path ops

    def key(self, op, *args):
        return self.other(op, *args)

    def attr(self, op, *args):
        return self.key(op, *args)

    def slot(self, op, *args):
        return self.key(op, *args)

    def slot_special(self, op, *args):
        return self.slot(op, *args)

    def slice(self, op, *args):
        return self.other(op, *args)

    def slice_filter(self, op, *args):
        return self.other(op, *args)

    def recursive(self, op, *args):
        return self.other(op, *args)

    def transform_wrap(self, op, *args):
        return self.other(op, *args)

    def value_guard(self, op, *args):
        return self.other(op, *args)

    def type_restriction(self, op, *args):
        raise TranslationError('type restriction not supported')

    def filter_wrap(self, op, *args):
        return self.other(op, *args)

    def nop_wrap(self, op, *args):
        # a `~` holds back a write, not a read: a read reads what it wraps
        return op.inner.translated(self, *args)

    def pack(self, op, *args):
        raise TranslationError('packed group not supported')

    def or_group(self, op, *args):
        return self.group(op, 'or', *_branches(op, 'or'), *args)

    def and_group(self, op, *args):
        return self.group(op, 'and', *_branches(op, 'and'), *args)

    def first_group(self, op, *args):
        return self.group(op, 'first', *_branches(op, 'first'), *args)

    def group(self, op, kind, branches, cuts, softs, *args):
        """
        A group of `kind`: `'or'` for `(a, b)`, each branch's matches
        in turn; `'and'` for `(a & b)`, those only where every branch
        matches, a branch running on into the path after the group;
        `'first'` for `(a, b)?`, the first match of the first branch to
        match, the branch alone, the path after the group running on
        from it. `cuts` says, per branch, whether a cut follows it: at
        a node where that branch yields, no later branch is tried.
        `softs` says whether a soft cut does: a later branch skips what
        overlaps that branch's matches.
        """
        return self.other(op, *args)

    def not_group(self, op, *args):
        raise TranslationError('negation group (!) not supported yet')

    # ---- filters

    def filter_key(self, key, *args):
        return self.other(key, *args)

    def key_value_filter(self, f, *args):
        return self.other(f, *args)

    def and_filter(self, f, *args):
        return self.other(f, *args)

    def or_filter(self, f, *args):
        return self.other(f, *args)

    def not_filter(self, f, *args):
        return self.other(f, *args)

    def group_filter(self, f, *args):
        return self.other(f, *args)

    def first_filter(self, f, *args):
        return self.other(f, *args)

    # ---- matchers

    def const(self, m, *args):
        return self.other(m, *args)

    def numeric(self, m, *args):
        return self.const(m, *args)

    def word(self, m, *args):
        return self.const(m, *args)

    def string(self, m, *args):
        return self.const(m, *args)

    def bytes(self, m, *args):
        return self.const(m, *args)

    def boolean(self, m, *args):
        return self.const(m, *args)

    def none_value(self, m, *args):
        return self.const(m, *args)

    def resolved_value(self, m, *args):
        return self.const(m, *args)

    def pattern(self, m, *args):
        return self.other(m, *args)

    def wildcard(self, m, *args):
        return self.pattern(m, *args)

    def wildcard_first(self, m, *args):
        return self.pattern(m, *args)

    def regex(self, m, *args):
        return self.pattern(m, *args)

    def subst(self, m, *args):
        return self.other(m, *args)

    def reference(self, m, *args):
        return self.other(m, *args)

    def special(self, m, *args):
        return self.other(m, *args)

    def concat(self, m, *args):
        return self.other(m, *args)


def _branches(group_op, kind, whole=False):
    """
    A group of `kind`'s branches, and per branch whether a cut follows
    it and whether a soft cut does. Neither changes anything in a
    first-match group, whose first match ends it anyway. In a
    conjunction a read ends the branches at a cut: the conjunction holds
    only where the cut branch matched, and there the cut stops it. A
    write, `whole`, keeps them and their cuts: it builds a cut branch
    that doesn't match and goes on.
    """
    branches = []
    cuts = []
    softs = []
    for item in group_op.branches:
        if item is base.BRANCH_CUT:
            cuts[-1] = True
            continue
        if item is base.BRANCH_SOFTCUT:
            softs[-1] = True
            continue
        branches.append(item)
        cuts.append(False)
        softs.append(False)
    if kind == 'first':
        return tuple(branches), (False,) * len(branches), (False,) * len(branches)
    if kind == 'and' and not whole and True in cuts:
        n = cuts.index(True) + 1
        branches, softs = branches[:n], softs[:n]
    if kind == 'and' and not whole:
        cuts = [False] * len(branches)
    return tuple(branches), tuple(cuts), tuple(softs)


class _Column(Hooks):
    """
    The first op of a path read as columns: the column a key names, a
    guard on the column itself, or a group or negation over the row's
    columns. `rest` is the path after it, `row` the table whose row it
    reads, None for a column path or an unnamed table.
    """

    def key(self, op, t, rest, row):
        col = t._head_column(op)
        return t._result(**t._walk(col, (), rest))

    # any other first op is a column key or raises in `_column_name`
    other = key

    def value_guard(self, op, t, rest, row):
        # `status = "active"`: one op, a guard wrapping the column key
        if rest:
            raise TranslationError('path continues after column-level guard')
        col = t._head_column(op.inner)
        return t._result(select=col, where=t._scalar_predicate(op, col))

    def group(self, op, kind, branches, cuts, softs, t, rest, row):
        if not op.is_pattern():
            return t._translate(_only_branch(op) + rest, row)
        source = t._row_source(row, op.translated(_ROW_COLUMNS, t))
        wheres = functools.partial(t._row_wheres, row)
        return t._result(**t._group(source, (), op, kind, branches, rest, wheres))

    def not_group(self, op, t, rest, row):
        source = t._row_source(row, None)
        return t._result(**t._patterned(source, (), (op,) + rest))


class _RowColumns(Hooks):
    """
    The columns of the row a group at the top of a path reads, in
    order: each branch's first column. None where a branch reads the
    whole row: a negation, whose complement is every other column.
    """

    def other(self, op, t):
        return (t._column_name(op),)

    def value_guard(self, op, t):
        return op.inner.translated(self, t)

    def group(self, op, kind, branches, cuts, softs, t):
        names = ()
        for branch in branches:
            got = branch[0].translated(self, t)
            if got is None:
                return None
            names += tuple(name for name in got if name not in names)
        return names

    def not_group(self, op, t):
        return None


class _ColumnName(Hooks):
    """
    The column the first op of a path names: a plain key's.
    """

    def other(self, op, t):
        raise TranslationError(
            f'first path segment must be a column key, got {type(op).__name__}'
        )

    def key(self, op, t):
        return t._name(op.op, 'column')

    def slot(self, op, t):
        return self.other(op, t)


class _Walk(Hooks):
    """
    An op after the column, ahead of any pattern: a key goes on the
    prefix, a guard ends the path, a group is read at its node.
    `col`, `prefix` and `rest` are the walk's so far and the path after
    the op.
    """

    def other(self, op, t, col, prefix, rest):
        raise TranslationError(f'unsupported op: {type(op).__name__}')

    def key(self, op, t, col, prefix, rest):
        return t._walk(col, prefix + (t._segment(op),), rest)

    def transform_wrap(self, op, t, col, prefix, rest):
        # value transforms, a.b|int, have no target side: translate the op under them
        return op.inner.translated(self, t, col, prefix, rest)

    def value_guard(self, op, t, col, prefix, rest):
        if rest:
            raise TranslationError('path continues after value guard')
        full = prefix + (t._segment(op.inner),)
        return {'select': t._select_expr(col, full),
                'where': t._nested_predicate(op, col, full)}

    def group(self, op, kind, branches, cuts, softs, t, col, prefix, rest):
        if not op.is_pattern():
            return t._walk(col, prefix, _only_branch(op) + rest)
        wheres = functools.partial(t._node_wheres, col, prefix)
        return t._group(col, prefix, op, kind, branches, rest, wheres)

    def not_group(self, op, t, col, prefix, rest):
        return t._patterned(col, prefix, (op,) + rest)


class _Kinds(Hooks):
    """
    How a run of ops reads the segments it reaches, level by level:
    'key' or 'slot', the tuple ending in `...` where the ops reach any
    depth (a **, a negation). A negation splits the levels it reads the
    way its branch reads them.
    """

    def other(self, op):
        raise TranslationError(f'op not supported in a negation: {type(op).__name__}')

    def key(self, op):
        return ('key',)

    def slot(self, op):
        return ('slot',)

    def slice(self, op):
        return ('slot',)

    def slice_filter(self, op):
        return ('slot',)

    def transform_wrap(self, op):
        return op.inner.translated(self)

    def value_guard(self, op):
        return op.inner.translated(self)

    def filter_wrap(self, op):
        return op.inner.translated(self)

    def recursive(self, op):
        return ('key', ...)

    def not_group(self, op):
        return _kinds(op.inner)[:1] + (...,)

    def group(self, op, kind, branches, cuts, softs):
        out = []
        reads = [_kinds(branch) for branch in branches]
        for level in range(max(len(r) for r in reads)):
            here = {r[level] for r in reads if len(r) > level}
            if ... in here:
                return tuple(out) + (...,)
            if len(here) > 1:
                raise TranslationError(
                    'a negation whose branches read a level both by key and by '
                    'slot not supported yet'
                )
            out.append(here.pop())
        return tuple(out)


def _kinds(ops):
    """
    How `ops` read the segments they reach, level by level (see _Kinds).
    """
    out = ()
    for op in ops:
        out += op.translated(_KINDS)
        if out and out[-1] is ...:
            return out
    return out


class _KeySegment(Hooks):
    """
    A key inside a path as a prefix segment: its matcher's, a Slot's an
    index.
    """

    def other(self, op, t):
        raise TranslationError(f'expected Key inside path, got {type(op).__name__}')

    def key(self, op, t):
        return op.op.translated(_SEGMENT, t, False)

    def slot(self, op, t):
        return op.op.translated(_SEGMENT, t, True)


class _Segment(Hooks):
    """
    A key's matcher as a prefix segment: `('lit', value)` for a plain
    key, an index when `index`, or `('expr', expr)` for a reference,
    whose subpath translates through the same walk.
    """

    def other(self, m, t, index):
        raise TranslationError(f'unsupported matcher: {type(m).__name__}')

    def const(self, m, t, index):
        return ('lit', t._segment_value(m.value, index))

    def subst(self, m, t, index):
        raise TranslationError(f'unresolved substitution in path position: {m}')

    def pattern(self, m, t, index):
        raise TranslationError(f'pattern segments not supported in v1: {m}')

    def reference(self, m, t, index):
        if m.depth != 0:
            raise TranslationError('relative/parent references not supported in v1')
        if not m.inner_path:
            raise TranslationError('empty reference path')
        # on `t` itself, so any flavor state stays live across the
        # sub-translation
        expr = t._translate(parse(m.inner_path, partial=True).ops).get('select')
        if expr is None:
            raise TranslationError('reference subpath has no select expression')
        return ('expr', expr)


class _Name(Hooks):
    """
    A matcher naming something in `position` (a column, a table, a
    filter key): the name it holds.
    """

    def other(self, m, t, position):
        raise TranslationError(f'unsupported {position} matcher: {type(m).__name__}')

    def const(self, m, t, position):
        return t._named(m.value, position)

    def subst(self, m, t, position):
        raise TranslationError(f'unresolved substitution in {position} position')

    def reference(self, m, t, position):
        raise TranslationError(f'reference in {position} position')

    def pattern(self, m, t, position):
        raise TranslationError(f'pattern/wildcard in {position} position')


class _RowIndex(Hooks):
    """
    The matcher of a `[n]` row step, `op`, as the row number it names.
    """

    def other(self, m, op):
        raise TranslationError(
            f'row index must be a non-negative int in a table search: {op}'
        )

    def const(self, m, op):
        if not _non_negative_int(m.value):
            return self.other(m, op)
        return m.value

    def subst(self, m, op):
        raise TranslationError(
            f'unresolved substitution in row index: {m} — pass bindings='
        )


class _RowFilter(Hooks):
    """
    A row filter as a predicate over the table's columns.
    """

    def other(self, f, t):
        raise TranslationError(f'filter type not supported: {type(f).__name__}')

    def key_value_filter(self, f, t):
        return t._row_comparison(f)

    def and_filter(self, f, t):
        return t._combine_and([sub.translated(self, t) for sub in f.filters])

    def or_filter(self, f, t):
        return t._combine_or([sub.translated(self, t) for sub in f.filters])

    def not_filter(self, f, t):
        # not true: a NULL compares to nothing, as None == x is false
        return t._combine_not_true(f.inner.translated(self, t))

    def group_filter(self, f, t):
        return f.inner.translated(self, t)


class _FilterSegment(Hooks):
    """
    A row filter key's part after its column as a prefix segment: an
    index (a Slot), or a key (a matcher, by its name).
    """

    def other(self, part, t):
        return ('lit', t._name(part, 'filter key'))

    def slot(self, part, t):
        return t._segment(part)

    def slice(self, part, t):
        raise TranslationError('Slice in a row filter key not supported in v1')


class _KeyParts(Hooks):
    """
    A filter's key as its parts: a dotted filter key's, or the one
    matcher it is.
    """

    def other(self, key):
        return (key,)

    def filter_key(self, key):
        return key.parts


_COLUMN = _Column()
_ROW_COLUMNS = _RowColumns()
_COLUMN_NAME = _ColumnName()
_WALK = _Walk()
_KEY_SEGMENT = _KeySegment()
_SEGMENT = _Segment()
_NAME = _Name()
_ROW_INDEX = _RowIndex()
_ROW_FILTER = _RowFilter()
_FILTER_SEGMENT = _FilterSegment()
class _RowGroup(Hooks):
    """
    A table search's row step read as a group or negation of row
    steps, `books[(*&a=1, *&b=2)]` or `books(![*&a=1])`: its rows,
    from which the path after it runs on. None for any other step.
    """

    def other(self, op, *args):
        return None

    def group(self, op, kind, branches, cuts, softs, t, names, wheres, window, rest, listed):
        if not op.is_pattern():
            return t._table_steps(names, wheres, window, _only_branch(op) + rest, listed)
        rows = functools.partial(
            t._table_rows_in_turn, names, wheres, kind, branches, cuts, softs, rest)
        return t._derived(names, window, rest, rows)

    def not_group(self, op, t, names, wheres, window, rest, listed):
        rows = functools.partial(t._table_rows_unreached, names, wheres, op.inner)
        return t._derived(names, window, rest, rows)


_KEY_PARTS = _KeyParts()
_KINDS = _Kinds()
_ROW_GROUP = _RowGroup()


# ---- writes ---------------------------------------------------------
#
# A write (`as_update(value)`) does what dotted's `update` does: the
# value at each match of the path, and along a path of literal keys
# and slots, the nodes it lacks built. What dotted builds for a missing
# or null node depends on the path alone, so dotted's own engine builds
# it at translation time, and the flavor writes that value: with the
# written value where it was placed, `WRITTEN` standing for it when it
# is the target's own expression. A flavor's writer is what a write
# does at one node, an existing container; it reads the node's own
# type, an object's members or an array's elements.


class _Written:
    """
    The written value inside a value dotted builds for a missing or
    null node, where that value is the target's own expression (a
    Column, a `Raw`), not a literal dotted can place itself.
    """

    def __repr__(self):
        return 'WRITTEN'


WRITTEN = _Written()


class _Unbuilt:
    """
    Where a write builds no node for a missing one: a `~`, a filter or
    a guard holds it back, or the op is a wildcard.
    """

    def __repr__(self):
        return 'UNBUILT'


UNBUILT = _Unbuilt()


class BuildError:
    """
    A node dotted's update raises building: the flavor raises there at
    run time, where the write reaches it.
    """

    def __init__(self, message):
        self.message = message

    def __repr__(self):
        return f'BuildError({self.message!r})'


class _IsFirst(Hooks):
    """
    True for a first wildcard's matcher, `*?`: it reaches its first
    match alone, as the first-match group of the wildcard does.
    """

    def other(self, m, *args):
        return False

    def wildcard_first(self, m, *args):
        return True


IS_FIRST = _IsFirst()


class _IsWildcard(Hooks):
    """
    True for a wildcard matcher, `*`.
    """

    def other(self, m, *args):
        return False

    def wildcard(self, m, *args):
        return True


IS_WILDCARD = _IsWildcard()


class _IsNop(Hooks):
    """
    True for a `~` op: a write doesn't build the node it holds back.
    """

    def other(self, op, *args):
        return False

    def nop_wrap(self, op, *args):
        return True


class _Access(Hooks):
    """
    The children an access op reaches, for a write: `(kind, value,
    first)`, kind 'key' or 'index' for a literal key or slot, 'keys' or
    'items' for a wildcard of keys or of slots, `first` for `*?`.
    """

    def other(self, op):
        raise TranslationError(f'not supported in a write: {type(op).__name__} {op}')

    def key(self, op):
        return op.op.translated(_ACCESS_MATCH, 'key')

    def slot(self, op):
        return op.op.translated(_ACCESS_MATCH, 'index')

    def slot_special(self, op):
        return self.other(op)


class _AccessMatch(Hooks):
    """
    A key's or slot's matcher, for `_Access`: a slot holding anything
    but an int reaches a key.
    """

    def other(self, m, kind):
        raise TranslationError(f'unsupported matcher in a write: {type(m).__name__}')

    def const(self, m, kind):
        if kind == 'index' and not _is_index(m.value):
            return 'key', m.value, False
        return kind, m.value, False

    def wildcard(self, m, kind):
        return ('keys' if kind == 'key' else 'items'), None, False

    def wildcard_first(self, m, kind):
        return ('keys' if kind == 'key' else 'items'), None, True

    def subst(self, m, kind):
        raise TranslationError(f'unresolved substitution in path position: {m}')

    def reference(self, m, kind):
        raise TranslationError('references in a write not supported yet')

    def pattern(self, m, kind):
        raise TranslationError(f'unsupported matcher in a write: {type(m).__name__}')


def _is_index(value):
    """
    True for an int, not a bool: a slot holding it indexes an array.
    """
    return isinstance(value, int) and not isinstance(value, bool)


class _Write(Hooks):
    """
    An op of a write's path at a node, as the flavor's writer: what
    dotted's update does there, `rest` the ops after it. `nop` says a
    `~` holds this op back; `unwrapped`, that the `~` was this op's own,
    so the ops after it write.
    """

    def other(self, op, t, rest, nop, unwrapped):
        raise TranslationError(f'not supported in a write: {type(op).__name__} {op}')

    def key(self, op, t, rest, nop, unwrapped):
        return t._write_access(op, op, rest, nop, unwrapped)

    def slot_special(self, op, t, rest, nop, unwrapped):
        return t._write_append(op, rest, nop, unwrapped)

    def filter_wrap(self, op, t, rest, nop, unwrapped):
        return t._write_access(op, op.inner, rest, nop, unwrapped)

    def value_guard(self, op, t, rest, nop, unwrapped):
        if op.inner.translated(_IS_NOP):
            return t._w_keep()
        return t._write_access(op, op.inner, rest, nop, unwrapped)

    def transform_wrap(self, op, t, rest, nop, unwrapped):
        if rest:
            return op.inner.translated(self, t, rest, nop, unwrapped)
        return t._transformed(op, lambda: op.inner.translated(self, t, rest, nop, unwrapped))

    def nop_wrap(self, op, t, rest, nop, unwrapped):
        if op._group() is not None:
            raise TranslationError('a ~ over a group in a write not supported yet')
        return op.inner.translated(self, t, rest, True, True)

    def slice_filter(self, op, t, rest, nop, unwrapped):
        # dotted's update raises here too
        raise TranslationError('a write not supported through a slice filter')

    def slice(self, op, t, rest, nop, unwrapped):
        raise TranslationError('a slice in a write not supported yet')

    def recursive(self, op, t, rest, nop, unwrapped):
        raise TranslationError('** in a write not supported yet')

    def and_group(self, op, *args):
        return self.group(op, 'and', *_branches(op, 'and', whole=True), *args)

    def group(self, op, kind, branches, cuts, softs, t, rest, nop, unwrapped):
        return t._write_group(kind, branches, cuts, softs, rest, nop, unwrapped)

    def not_group(self, op, t, rest, nop, unwrapped):
        raise TranslationError('a negation in a write not supported yet')


class _WriteRow(Hooks):
    """
    The first op of a write's path, at the row: the column a key names,
    and what the rest writes in it. The row has every column, so none
    is built.
    """

    def other(self, op, t, rest, nop, unwrapped):
        return self.key(op, t, rest, nop, unwrapped)

    def key(self, op, t, rest, nop, unwrapped):
        return t._write_column(t._column_name(op), None, rest, nop, unwrapped)

    def filter_wrap(self, op, t, rest, nop, unwrapped):
        return t._write_column(t._column_name(op.inner), op, rest, nop, unwrapped)

    def value_guard(self, op, t, rest, nop, unwrapped):
        if op.inner.translated(_IS_NOP):
            name = t._column_name(op.inner.inner)
            return [(name, t._column(name))]
        return t._write_column(t._column_name(op.inner), op, rest, nop, unwrapped)

    def transform_wrap(self, op, t, rest, nop, unwrapped):
        if rest:
            return op.inner.translated(self, t, rest, nop, unwrapped)
        return t._transformed(op, lambda: op.inner.translated(self, t, rest, nop, unwrapped))

    def nop_wrap(self, op, t, rest, nop, unwrapped):
        if op._group() is not None:
            raise TranslationError('a ~ over a group in a write not supported yet')
        return op.inner.translated(self, t, rest, True, True)

    def group(self, op, kind, branches, cuts, softs, t, rest, nop, unwrapped):
        raise TranslationError('a group in a write not supported yet')

    def not_group(self, op, t, rest, nop, unwrapped):
        raise TranslationError('a negation in a write not supported yet')


_IS_NOP = _IsNop()
_ACCESS = _Access()
_ACCESS_MATCH = _AccessMatch()
_WRITE = _Write()
_WRITE_ROW = _WriteRow()


class Translator:
    """
    Target-neutral walk over parsed path ops. Mixed into a flavor
    class that implements the hooks listed in the module docstring.
    """

    # ---- core walk --------------------------------------------------

    def _translate_root(self, ops, root):
        """
        Top-level translation entry point. Returns the fragments dict
        and the plain values `(table, offset, limit)`, all None for a
        column path. `root='table'` reads the leading keys as a table's
        name; a path that starts with a row step searches an unnamed
        table under either root.
        """
        if root not in ROOTS:
            raise TranslationError(
                f'unknown root: {root!r}. expected one of {list(ROOTS)}'
            )
        if not ops:
            raise TranslationError('empty path')
        if root == 'column' and not _is_row_step(ops[0]):
            return self._translate(ops), _NO_TABLE
        return self._translate_table(ops)

    def _translate(self, ops, row=None):
        """
        Inner translation entry point — returns a dict of flavor
        expressions (`select`, `where`, ...). Nested invocations
        (group branches, reference subpaths) re-enter here. `row` is
        the table whose row the path reads, for a group or negation
        over the row's columns; None for a column path or an unnamed
        table.
        """
        if not ops:
            raise TranslationError('empty path')
        return ops[0].translated(_COLUMN, self, tuple(ops[1:]), row)

    def _result(self, **kw):
        """
        Carry intermediate translation output. Keys are fragment names
        (`select`, `where`, `from_`), values are flavor expressions.
        """
        return {k: v for k, v in kw.items() if v is not None}

    def _column_name(self, op):
        """
        Extract the column name from the first op of a path. Reject
        matchers that can't name a column.
        """
        return op.translated(_COLUMN_NAME, self)

    def _head_column(self, op):
        """
        The column the first op of a read names, as `_column` gives it.
        A flavor that can start a read elsewhere overrides this.
        """
        return self._column(self._column_name(op))

    def _named(self, value, position):
        """
        The name a matcher's `value` gives in `position`. A flavor
        overrides this to refuse a value that names nothing.
        """
        return str(value)

    def _name(self, matcher, position):
        """
        The name `matcher` holds in `position` (a column, a table, a
        filter key). Reject matchers that can't name one.
        """
        return matcher.translated(_NAME, self, position)

    # ---- table search -----------------------------------------------

    def _translate_table(self, ops):
        """
        A table search: the table's name (its leading keys, joined
        whole), its row steps, then the path under each row, read as a
        column path. Slice filters and a slice keep the rows one list,
        as in dotted, so a row step may follow them but a key may not:
        `hello[a=1][0:10][*].title`.
        """
        n = 0
        while n < len(ops) and _is_table_key(ops[n]):
            n += 1
        names = tuple(self._name(op.op, 'table') for op in ops[:n])
        table = '.'.join(names) if names else None
        steps = ops[n:]
        wheres = []
        while steps and isinstance(steps[0], access.SliceFilter):
            wheres.append(self._row_filters(steps[0].filters))
            steps = steps[1:]
        window = (None, None)
        if steps and isinstance(steps[0], access.Slice):
            window = _slice_window(steps[0])
            steps = steps[1:]
        listed = len(steps) < len(ops) - n
        out, window = self._table_steps(names, wheres, window, tuple(steps), listed)
        return out, (table,) + window

    def _table_steps(self, names, wheres, window, steps, listed):
        """
        A table search from its row step on: its fragments and its
        `(offset, limit)` window. `wheres` and `window` are the slice
        filters' and the slice's before it, `listed` whether there were
        any, so `steps` reads the list of rows.
        """
        offset, limit = window
        if not steps:
            return self._table_result(names, wheres, None), window
        step, rest = steps[0], steps[1:]
        grouped = step.translated(_ROW_GROUP, self, names, wheres, window, rest, listed)
        if grouped is not None:
            return grouped
        if not _is_row_step(step) and listed:
            raise TranslationError(
                f'{type(step).__name__} after a slice or slice filter reads '
                f'the list of rows, not each row — add [*]'
            )
        if not _is_row_step(step):
            raise TranslationError(
                f'expected a row step after the table name, got {type(step).__name__}'
            )
        if isinstance(step, (access.SliceFilter, access.Slice)):
            raise TranslationError(
                'a slice filter or slice after a slice not supported in v1'
            )
        if (offset, limit) != (None, None) and not _is_every_row(step):
            raise TranslationError('only [*] may follow a slice in v1')
        preds, at, count = self._row_step(step)
        wheres = wheres + preds
        if at is not None:
            offset, limit = at, count
        under = self._under_rows(names, rest)
        return self._table_result(names, wheres, under), (offset, limit)

    def _under_rows(self, names, rest):
        """
        The fragments of the path after a table search's row step,
        `rest`, run on from each row; None where nothing follows. A
        group or negation over the row's columns reads the row of the
        table, by its own name, without its schema.
        """
        if not rest:
            return None
        if _is_row_step(rest[0]):
            raise TranslationError(
                'row step after a row step not supported — in dotted it '
                'indexes into each row'
            )
        return self._translate(rest, names[-1] if names else None)

    def _row_step(self, step):
        """
        A row step's predicates and the row an index picks, `(offset,
        limit)`: `[*]` every row, `[*&…]` those its filters keep, `[n]`
        the one at n.
        """
        if isinstance(step, wrappers.FilterWrap):
            if not _is_every_row(step.inner):
                raise TranslationError(f'unsupported filtered row step: {step}')
            return [self._row_filters(step.filters)], None, None
        if isinstance(step, access.Slot) and not _is_every_row(step):
            return [], step.op.translated(_ROW_INDEX, step), 1
        return [], None, None

    def _table_result(self, names, wheres, under):
        """
        A table search's fragments: the table it reads (`from`), and
        the path under each row's own (`select='*'` when none follows
        the row step), its `where` ANDed after the row filters.
        """
        out = dict(under) if under else {'select': self._all_columns()}
        if 'where' in out:
            wheres = wheres + [out['where']]
        if wheres:
            out['where'] = self._all_of(wheres)
        out['from'] = self._table_from(names)
        return self._result(**out)

    # ---- groups of row steps ----------------------------------------

    def _derived(self, names, window, rest, rows):
        """
        A table search whose row step is a group or negation of row
        steps: its rows, `rows(after)`, as `from`, `after` the `where`
        of the path after it, which runs on from each row. A row the
        group reaches twice comes twice, as dotted yields it, which no
        filter of the table's rows can give.
        """
        if window != (None, None):
            raise TranslationError('a group of row steps after a slice not supported yet')
        under = self._under_rows(names, rest)
        after = under.get('where') if under else None
        out = dict(under) if under else {'select': self._all_columns()}
        out['from'] = self._table_rows_from(rows(after))
        return self._result(**out), (None, None)

    def _row_branch(self, branch):
        """
        A branch of a group of row steps as the row step it is: its
        predicates, offset and limit (`_row_step`).
        """
        if len(branch) != 1:
            raise TranslationError(
                'a branch of a group of row steps that goes on past its '
                'row step not supported yet'
            )
        step = branch[0]
        if isinstance(step, (access.SliceFilter, access.Slice)):
            raise TranslationError(
                f'{type(step).__name__} in a group of row steps reads the list '
                f'of rows, not each row — use [*&…]'
            )
        if not _is_row_step(step):
            raise TranslationError(
                f'expected a row step in a group of row steps, got {type(step).__name__}'
            )
        return self._row_step(step)

    def _table_rows_in_turn(self, names, wheres, kind, branches, cuts, softs, rest, after):
        """
        The rows of a group of row steps of `kind` over the table
        `names`, after the slice filters' `wheres`, as dotted reads the
        group at the table: each branch's rows in turn, a branch
        yielding a row where the path after the group matches it
        (`after`). A cut holds over the whole table: after `(a#, b)`,
        b's rows come only where a yielded none. A soft cut skips, in
        the branches after it, the rows its branch yielded, which only
        its predicates tell, and a path after it that matches once per
        row. An `&` yields only where every branch yields.
        """
        reached = []
        for branch in branches:
            preds, offset, limit = self._row_branch(branch)
            rows = self._table_rows(names, list(wheres) + preds, offset, limit)
            reached.append((rows, preds, offset is not None))
        if kind == 'first':
            return self._first_table_rows(reached)
        if any(softs) and any(_op_is_pattern_like(op) for op in rest):
            raise TranslationError(
                'a soft cut in a group of row steps, with a path after it '
                'that may match more than once per row, not supported yet'
            )
        gates = []
        if kind == 'and':
            gates = [(rows, after, True) for rows, _, _ in reached]
        parts = []
        covers = []
        for (rows, preds, indexed), cut, soft in zip(reached, cuts, softs):
            unless = None
            if covers:
                unless = self._combine_not_true(self._any_of(covers))
            parts.append(self._table_rows_kept(rows, unless, gates))
            if cut:
                took = self._all_of([p for p in (after, unless) if p is not None])
                gates = gates + [(rows, took, False)]
            if not soft:
                continue
            if indexed:
                raise TranslationError(
                    'a soft cut after rows an index picks needs to tell rows '
                    'apart, which a table search can\'t — not supported'
                )
            cover = [p for p in preds + [after] if p is not None]
            if not cover:
                # the soft branch yields every row: the later ones add none
                break
            covers.append(self._all_of(cover))
        return self._table_rows_union(parts)

    def _first_table_rows(self, reached):
        """
        The first row of the first branch of `reached` to reach one, a
        first-match group of row steps, `books[(*&a=1, *&b=2)?]`.
        """
        parts = []
        gates = []
        for rows, _, _ in reached:
            parts.append(self._table_rows_first(self._table_rows_kept(rows, None, gates)))
            gates = gates + [(rows, None, False)]
        return self._table_rows_union(parts)

    def _table_rows_unreached(self, names, wheres, inner, after):
        """
        The rows a negation of a row step, `books(![*&a=1])`, yields:
        those its row step doesn't reach, each whole. Rows an index
        picks no predicate tells, so a negation of them raises.
        """
        preds, offset, _ = self._row_branch(inner)
        if offset is not None:
            raise TranslationError(
                'a negation of rows an index picks needs to tell rows '
                'apart, which a table search can\'t — not supported'
            )
        if not preds:
            # (![*]) reaches every row: none is left
            return self._table_rows(names, list(wheres), None, 0)
        unless = self._combine_not_true(self._all_of(preds))
        return self._table_rows(names, list(wheres) + [unless], None, None)

    def _all_of(self, preds):
        """
        `preds` all holding: the one, or their conjunction; None for
        none, which holds for every row.
        """
        if not preds:
            return None
        if len(preds) == 1:
            return preds[0]
        return self._combine_and(preds)

    def _any_of(self, preds):
        """
        Some of `preds` holding: the one, or their disjunction.
        """
        if len(preds) == 1:
            return preds[0]
        return self._combine_or(preds)

    def _row_filters(self, filters):
        """
        A row step's filters, all of which must hold, as one predicate
        over the table's columns.
        """
        return self._all_of([f.translated(_ROW_FILTER, self) for f in filters])

    def _row_comparison(self, f):
        """
        A `key<op>value` row filter: its key reads as a column path
        (`&book_id="b1"` is the column `book_id`, `&data.user.age>30`
        reaches into the column `data`), its comparison as a guard at
        the end of it.
        """
        head, *rest = self._key_parts(f.key)
        col = self._column(self._name(head, 'column'))
        guard = wrappers.ValueGuard(
            None, f.val, pred_op=predicates.PRED_OPS[f._eq_str],
            transforms=f.guard_transforms)
        if not rest:
            return self._scalar_predicate(guard, col)
        prefix = tuple(part.translated(_FILTER_SEGMENT, self) for part in rest)
        return self._nested_predicate(guard, col, prefix)

    def _key_parts(self, key):
        """
        A filter's key as its parts: raw matchers (Word, Numeric, …)
        and Slot ops for indexes.
        """
        return key.translated(_KEY_PARTS)

    def _access_kinds(self, ops):
        """
        How `ops` read the segments they reach, level by level: 'key'
        or 'slot', ending in `...` where they reach any depth. A
        negation of them splits its node's children level by level
        that way.
        """
        return _kinds(ops)

    def _walk(self, col, prefix, ops):
        """
        Walk the remaining path ops after the column, accumulating
        literal segments in `prefix`. If an op ahead introduces a
        pattern-like segment (wildcard, recursive, filter bracket)
        before any group, the flavor's pattern mode takes the whole
        remainder; up to a group, the walk goes on, so the group is
        read at its node.
        """
        if not ops:
            return {'select': self._select_expr(col, prefix)}
        lead = next((o for o in ops if _op_is_pattern_like(o)), None)
        if lead is not None and not isinstance(lead, _groups.OpGroup):
            return self._patterned(col, prefix, ops)
        return ops[0].translated(_WALK, self, col, prefix, tuple(ops[1:]))

    def _segment(self, key_op):
        """
        A `Key` op inside a path as a `(kind, value)` tuple:
        `('lit', 'user')` for a plain key, or `('expr', expr)` for a
        reference whose subpath is recursively translated.
        """
        return key_op.translated(_KEY_SEGMENT, self)

    def _segment_value(self, value, index):
        """
        The literal segment for a plain key whose matcher holds `value`,
        a list index when `index`. Its string form by default; a flavor
        that tells list indexes (`[0]`) from keys overrides this.
        """
        return str(value)

    # ---- groups -----------------------------------------------------

    def _combine_group(self, kind, preds):
        """
        Branch predicates joined per the group's kind: a first match
        is there where some branch matches, as for `'or'`.
        """
        if kind == 'and':
            return self._combine_and(preds)
        return self._combine_or(preds)

    def _group(self, col, prefix, group_op, kind, branches, rest, wheres):
        """
        A group of `kind` at one node, `rest` the path after it: a node
        inside a column, or the row (`_row_source`). Its matches are
        the pattern path's. `wheres(ways)` reads each way, a branch and
        the rest after it, as a plain path: its `where`, None where it
        has none, or None for them all where they can't be read so.
        When every way has one, the group's `where` is theirs, joined:
        a group of guards stays plain predicates; a cut changes which
        branches yield, not whether the group matches. A first match's
        rest runs from the match, not from each branch, so with a rest
        it keeps the pattern's.
        """
        ways = [tuple(branch) + rest for branch in branches]
        read = None
        if kind != 'first' or not rest:
            read = wheres(ways)
        out = self._patterned(col, prefix, (group_op,) + rest)
        if read is None or any(where is None for where in read):
            return out
        return dict(out, where=self._combine_group(kind, read))

    def _node_wheres(self, col, prefix, ways):
        """
        The `where` of each way of a group at a node inside a column,
        read as a plain path from it; None when a way reaches a
        pattern, whose `where` the group's pattern path has.
        """
        if any(_op_is_pattern_like(op) for way in ways for op in way):
            return None
        return [self._walk(col, prefix, way).get('where') for way in ways]

    def _row_wheres(self, row, ways):
        """
        The `where` of each way of a group at the top of a path, read
        as a complete path from its own column.
        """
        return [self._translate(way, row).get('where') for way in ways]

    def _row_source(self, table, columns):
        """
        The row as one value, for a group or negation at the top of a
        path to read as a node: its `columns`, or, where `columns` is
        None, the whole row of `table`, which only a search of a named
        table has.
        """
        if columns is None and table is None:
            raise TranslationError(
                "a negation at the top of a path is the complement of the "
                "row's columns, which only a search of a named table can read"
            )
        return self._row(table, columns)

    def _patterned(self, col, prefix, ops):
        """
        The flavor's pattern path for `ops`, once no value guard in
        them has more path after it.
        """
        _check_guards(ops)
        return self._pattern(col, prefix, ops)

    # ---- writes -----------------------------------------------------

    def _translate_write(self, ops, root, value, literal):
        """
        A write of `value` at the path's matches, as dotted's `update`
        does it: `(column, flavor expression)` per column it touches,
        each the column's new value. `literal` says `value` is a plain
        value dotted can place itself, not the target's expression.
        """
        if root not in ROOTS:
            raise TranslationError(
                f'unknown root: {root!r}. expected one of {list(ROOTS)}'
            )
        if not ops:
            raise TranslationError('empty path')
        if root == 'table' or _is_row_step(ops[0]):
            raise TranslationError('a write of a table search not supported yet')
        _check_guards(ops)
        self._written = value if literal else WRITTEN
        self._written_literal = literal
        return ops[0].translated(_WRITE_ROW, self, tuple(ops[1:]), False, False)

    def _write(self, ops, nop=False):
        """
        The flavor's writer for `ops` at a node: dotted's update of
        `ops` there, the node an existing container.
        """
        return ops[0].translated(_WRITE, self, tuple(ops[1:]), nop, False)

    def _write_column(self, name, test_op, rest, nop, unwrapped):
        """
        The one column a write's first op names, with its new value: the
        written value, or what `rest` writes in it, a null column
        written as dotted writes a None. A filter or guard on the column,
        `test_op`, holds the write back where it fails.
        """
        col = self._column(name)
        test = None if test_op is None else self._w_column_test(test_op, col)
        if not rest:
            if nop:
                return [(name, col)]
            return [(name, self._w_column_value(col, test))]
        pass_nop = nop and not unwrapped
        child = self._write(rest, pass_nop)
        null = self._built(rest, False, pass_nop)
        return [(name, self._w_column(col, child, null, test))]

    def _write_access(self, op, inner, rest, nop, unwrapped):
        """
        The writer of an access op, `op` (`inner` under its filter or
        guard): each child it reaches written, the value at the end of
        the path, else what `rest` writes there. A child that is null
        is written as dotted writes a None: the node it builds. A
        literal key or slot builds the child it lacks, unless a `~`, a
        filter or a guard holds it back.
        """
        kind, value, first = inner.translated(_ACCESS)
        test = None if op is inner else self._w_test(op)
        if not rest:
            if nop:
                return self._w_keep()
            child, absent, null = self._w_value(), self._written, self._written
        else:
            pass_nop = nop and not unwrapped
            child = self._write(rest, pass_nop)
            null = self._built(rest, False, pass_nop)
            absent = UNBUILT
            if not (nop or rest[0].translated(_IS_NOP)):
                absent = self._built(rest, True, False)
        if kind in ('keys', 'items') or first or test is not None:
            return self._w_each(kind, value, first, test, child, null)
        return self._w_key(kind, value, child, absent, null)

    def _write_group(self, kind, branches, cuts, softs, rest, nop, unwrapped):
        """
        The writer of a group at a node, `rest` the ops after it, as
        dotted's update writes one. A disjunction writes each branch
        where it matches, the rest after it, on the node as the branches
        before it left it, a branch with an append slot always, and ends
        after a branch a cut follows that matched; where none matched,
        the last concrete branch is written, built as it lacks. A
        conjunction writes every branch, in turn, where each matches on
        the node as it was or is a plain path it can build, up to the
        first a cut follows that matched; else nothing. A first match
        writes the first branch that appends or, alone, matches; else
        the last concrete branch.
        """
        if any(softs):
            raise TranslationError('a soft cut in a write not supported yet')
        pass_nop = nop and not unwrapped
        parts = []
        for branch, cut in zip(branches, cuts):
            way = tuple(branch) + tuple(rest)
            if not way:
                continue
            parts.append((tuple(branch), way, cut))
        if kind == 'first':
            cands = [(True if _groups.appends(branch) else self._w_matches(branch),
                      self._write(way, pass_nop)) for branch, way, _ in parts]
            return self._w_first(cands, self._w_fallback(parts, pass_nop))
        if kind == 'and':
            return self._w_and([(self._w_matches(way), _groups._creatable(list(way)), cut,
                                 self._write(way, nop)) for _, way, cut in parts])
        ors = [(True if _groups.appends(branch) else self._w_matches(way), cut,
                self._write(way, nop)) for branch, way, cut in parts]
        return self._w_or(ors, self._w_fallback(parts, nop))

    def _w_fallback(self, parts, nop):
        """
        The writer of the last concrete branch, as a disjunction writes
        where none of its branches matched; None where none is concrete.
        """
        for _, way, _ in reversed(parts):
            if _groups._is_concrete_path(list(way)):
                return self._write(way, nop)
        return None

    def _write_append(self, op, rest, nop, unwrapped):
        """
        The writer of an append slot, `[+]` (`[+?]` where the node
        doesn't hold it already): the written value, or the node `rest`
        builds, appended.
        """
        if nop:
            return self._w_keep()
        unique = op.op.value == '+?'
        if not rest:
            return self._w_append(self._written, unique)
        if rest[0].translated(_IS_NOP):
            return self._w_keep()
        return self._w_append(self._built(rest, True, False), unique)

    def _transformed(self, op, write):
        """
        `write()` with the written value put through the transforms of
        `op`, as dotted's update does at the end of the path: a literal
        value only, which dotted can transform itself.
        """
        if not self._written_literal:
            raise TranslationError(
                'a transform on a value that is the target\'s own expression '
                'not supported: dotted transforms the value it writes'
            )
        written = self._written
        self._written = op.apply(written)
        try:
            return write()
        finally:
            self._written = written

    def _built(self, ops, has_defaults, nop):
        """
        The node dotted's update builds for `ops`, where the node is
        missing (`has_defaults`) or null: dotted's own result, from the
        path alone, the written value placed in it; a `BuildError` where
        dotted raises building it.
        """
        ops = list(ops)
        try:
            return engine.updates(ops, engine.build_default(ops), self._written,
                                  has_defaults, [], nop)
        except Exception as e:
            return BuildError(f'{type(e).__name__}: {e}')

    # ---- flavor hooks -----------------------------------------------

    def _column(self, name):
        """
        Return the flavor's expression for column `name`.
        """
        raise NotImplementedError

    def _all_columns(self):
        """
        Return the flavor's expression for every column of a row.
        """
        raise NotImplementedError

    def _row(self, table, columns):
        """
        Return the flavor's expression for a row as one value, a node
        the pattern path reads: an object of its `columns`, or the
        whole row of `table` when `columns` is None.
        """
        raise NotImplementedError

    def _select_expr(self, col, prefix):
        """
        Return the flavor's extraction expression for `prefix` inside
        `col` (an empty prefix is the column itself).
        """
        raise NotImplementedError

    def _scalar_predicate(self, guard_op, col):
        """
        Return the flavor's predicate for a guard on the column itself.
        """
        raise NotImplementedError

    def _nested_predicate(self, guard_op, col, prefix):
        """
        Return the flavor's predicate for a guard at `prefix` inside
        the column.
        """
        raise NotImplementedError

    def _pattern(self, col, prefix, ops):
        """
        Return the fragments dict for a pattern path: `ops` is the
        remainder after `prefix`, containing at least one pattern-like
        op or group. A `Hooks` subclass reads each op.
        """
        raise NotImplementedError

    def _combine_and(self, preds):
        """
        Return the conjunction of `preds`.
        """
        raise NotImplementedError

    def _combine_or(self, preds):
        """
        Return the disjunction of `preds`.
        """
        raise NotImplementedError

    def _combine_not_true(self, pred):
        """
        Return the predicate that `pred` doesn't hold: false or null.
        """
        raise NotImplementedError

    def _table_from(self, names):
        """
        Return a table search's `from`: the table `names` it reads, or
        None where the flavor reads it otherwise.
        """
        raise NotImplementedError

    def _table_rows(self, names, preds, offset, limit):
        """
        Return the flavor's rows of the table `names` where every one
        of `preds` holds, skipping `offset` and keeping `limit` of
        them where either is set.
        """
        raise NotImplementedError

    def _table_rows_kept(self, rows, pred, gates):
        """
        Return those of `rows` where `pred` holds (every one where it
        is None), and none unless each gate `(rows, pred, exists)`
        holds: some of its rows where its pred holds, or none of them
        when not `exists`.
        """
        raise NotImplementedError

    def _table_rows_first(self, rows):
        """
        Return the first of `rows`.
        """
        raise NotImplementedError

    def _table_rows_union(self, parts):
        """
        Return the rows of each of `parts` in turn, a row as often as
        the parts yield it.
        """
        raise NotImplementedError

    def _table_rows_from(self, rows):
        """
        Return `from` for a group of row steps: the `rows` it yields.
        """
        raise NotImplementedError

    def _combine_not(self, pred):
        """
        Return the negation of `pred`.
        """
        raise NotImplementedError
