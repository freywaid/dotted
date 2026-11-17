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
from .. import access
from .. import base
from .. import groups as _groups
from .. import matchers
from .. import predicates
from .. import wrappers
from ..api import parse


# what a path's first segment is: a column of the row, or a table
ROOTS = ('column', 'table')

# the plain values of a column path, which searches no table
_NO_TABLE = (None, None, None)


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
    if isinstance(op, access.SliceFilter):
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
    `key`, `numeric` to `const`, `wildcard` to `pattern`, the `,`, `&`
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

    def pack(self, op, *args):
        raise TranslationError('packed group not supported')

    def or_group(self, op, *args):
        return self.group(op, 'or', _branches(op), *args)

    def and_group(self, op, *args):
        return self.group(op, 'and', _branches(op), *args)

    def first_group(self, op, *args):
        return self.group(op, 'first', _branches(op), *args)

    def group(self, op, kind, branches, *args):
        """
        A group of `kind`: `'or'` for `(a, b)`, each branch's matches
        in turn; `'and'` for `(a & b)`, those only where every branch
        matches, a branch running on into the path after the group;
        `'first'` for `(a, b)?`, the first match of the first branch to
        match, the branch alone, the path after the group running on
        from it.
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


def _branches(group_op):
    """
    A group's branches. A cut, `#` or `##`, raises: the translators
    don't take one yet.
    """
    if any(b in (base.BRANCH_CUT, base.BRANCH_SOFTCUT) for b in group_op.branches):
        raise TranslationError('cut (# or ##) in a group not supported yet')
    return group_op.branches


class _Column(Hooks):
    """
    The first op of a path read as columns: the column a key names, a
    guard on the column itself, or a group of paths each from its own
    column. `rest` is the path after it.
    """

    def key(self, op, t, rest):
        col = t._column(t._column_name(op))
        return t._result(**t._walk(col, (), rest))

    # any other first op is a column key or raises in `_column_name`
    other = key

    def value_guard(self, op, t, rest):
        # `status = "active"`: one op, a guard wrapping the column key
        if rest:
            raise TranslationError('path continues after column-level guard')
        col = t._column(t._column_name(op.inner))
        return t._result(select=col, where=t._scalar_predicate(op, col))

    def group(self, op, kind, branches, t, rest):
        if not op.is_pattern():
            return t._translate(_only_branch(op) + rest)
        return t._result(where=t._translate_top_group(kind, branches, rest))


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

    def group(self, op, kind, branches, t, col, prefix, rest):
        if not op.is_pattern():
            return t._walk(col, prefix, _only_branch(op) + rest)
        return t._group(col, prefix, op, kind, branches, rest)


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
        return str(m.value)

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
        return t._combine_not(f.inner.translated(self, t))

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
_COLUMN_NAME = _ColumnName()
_WALK = _Walk()
_KEY_SEGMENT = _KeySegment()
_SEGMENT = _Segment()
_NAME = _Name()
_ROW_INDEX = _RowIndex()
_ROW_FILTER = _RowFilter()
_FILTER_SEGMENT = _FilterSegment()
_KEY_PARTS = _KeyParts()


class Translator:
    """
    Target-neutral walk over parsed path ops. Mixed into a flavor
    class that implements the hooks listed in the module docstring.
    """

    # transform name (`|int`) → target cast name; set by each flavor
    _CAST_MAP = {}

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

    def _translate(self, ops):
        """
        Inner translation entry point — returns a dict of flavor
        expressions (`select`, `where`, ...). Nested invocations
        (group branches, reference subpaths) re-enter here.
        """
        if not ops:
            raise TranslationError('empty path')
        return ops[0].translated(_COLUMN, self, tuple(ops[1:]))

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
        names = [self._name(op.op, 'table') for op in ops[:n]]
        table = '.'.join(names) if names else None
        steps = ops[n:]
        wheres = []
        while steps and isinstance(steps[0], access.SliceFilter):
            wheres.append(self._row_filters(steps[0].filters))
            steps = steps[1:]
        offset, limit = None, None
        if steps and isinstance(steps[0], access.Slice):
            offset, limit = _slice_window(steps[0])
            steps = steps[1:]
        listed = len(steps) < len(ops) - n
        if not steps:
            return self._table_result(wheres, None), (table, offset, limit)
        step, rest = steps[0], steps[1:]
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
        if isinstance(step, wrappers.FilterWrap):
            if not _is_every_row(step.inner):
                raise TranslationError(f'unsupported filtered row step: {step}')
            wheres.append(self._row_filters(step.filters))
        if isinstance(step, access.Slot) and not _is_every_row(step):
            offset, limit = step.op.translated(_ROW_INDEX, step), 1
        if rest and _is_row_step(rest[0]):
            raise TranslationError(
                'row step after a row step not supported — in dotted it '
                'indexes into each row'
            )
        under = self._translate(rest) if rest else None
        return self._table_result(wheres, under), (table, offset, limit)

    def _table_result(self, wheres, under):
        """
        A table search's fragments: the path under each row's own
        (`select='*'` when none follows the row step), its `where`
        ANDed after the row filters.
        """
        out = dict(under) if under else {'select': self._all_columns()}
        if 'where' in out:
            wheres = wheres + [out['where']]
        if len(wheres) == 1:
            out['where'] = wheres[0]
        if len(wheres) > 1:
            out['where'] = self._combine_and(wheres)
        return self._result(**out)

    def _row_filters(self, filters):
        """
        A row step's filters, all of which must hold, as one predicate
        over the table's columns.
        """
        preds = [f.translated(_ROW_FILTER, self) for f in filters]
        if len(preds) == 1:
            return preds[0]
        return self._combine_and(preds)

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

    def _transforms_to_cast(self, transforms):
        """
        Map a path-level transform (like `|int`) to the flavor's cast
        name via `_CAST_MAP`. Only a narrow set is supported today.
        """
        if len(transforms) != 1:
            raise TranslationError(
                'only single-transform casts supported in v1'
            )
        name = transforms[0].name
        cast = self._CAST_MAP.get(name)
        if cast is None:
            raise TranslationError(
                f'transform not supported as SQL cast: {name}'
            )
        return cast

    # ---- groups -----------------------------------------------------

    def _combine_group(self, kind, preds):
        """
        Branch predicates joined per the group's kind: a first match
        is there where some branch matches, as for `'or'`.
        """
        if kind == 'and':
            return self._combine_and(preds)
        return self._combine_or(preds)

    def _translate_top_group(self, kind, branches, rest):
        """
        The `where` of a group of `kind` at the top of a path: each
        branch is a complete path from its own column, the rest of the
        path after it.
        """
        if kind == 'first' and rest:
            raise TranslationError(
                'path after a top-level first-match group (?) not supported yet'
            )
        wheres = []
        for branch in branches:
            where = self._translate(tuple(branch) + tuple(rest)).get('where')
            if where is None:
                raise TranslationError(
                    'top-level group branch has no predicate'
                )
            wheres.append(where)
        return self._combine_group(kind, wheres)

    def _group(self, col, prefix, group_op, kind, branches, rest):
        """
        A group of `kind` at one node, `rest` the path after it. Its
        matches are the pattern path's. When no branch of a `,` or `&`
        group reaches a pattern, its `where` is the branches' own,
        joined: a group of guards stays plain predicates. A first
        match's rest runs from the match, not from each branch, so it
        keeps the pattern's.
        """
        ways = [tuple(branch) + rest for branch in branches]
        wheres = None
        plain = not any(_op_is_pattern_like(op) for way in ways for op in way)
        if kind != 'first' and plain:
            wheres = [self._walk(col, prefix, way).get('where') for way in ways]
        out = self._patterned(col, prefix, (group_op,) + rest)
        if wheres is None or any(where is None for where in wheres):
            return out
        return dict(out, where=self._combine_group(kind, wheres))

    def _patterned(self, col, prefix, ops):
        """
        The flavor's pattern path for `ops`, once no value guard in
        them has more path after it.
        """
        _check_guards(ops)
        return self._pattern(col, prefix, ops)

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

    def _combine_not(self, pred):
        """
        Return the negation of `pred`.
        """
        raise NotImplementedError
