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
                                        path (wildcards, filters, `**`)
    _combine_and(preds) / _combine_or(preds) / _combine_not(pred)
                                      → boolean composition

A prefix is a tuple of `('lit', value)` segments for plain keys and
`('expr', <flavor expression>)` segments for references, whose
subpath is translated through the same walk. A literal's value is a
string unless the flavor overrides `_segment_value`.

Expressions are opaque to the walk: whatever the hooks return is
carried through and handed back to other hooks. The walk returns a
dict of fragment name (`select`, `where`, `lateral`, ...) → flavor
expression; turning that into the target's public result is the
flavor's `translate()`.
"""
from .. import access
from .. import filters as _filters
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


def _row_index(op):
    """
    The row number `[n]` names.
    """
    matcher = op.op
    if isinstance(matcher, matchers.Subst):
        raise TranslationError(
            f'unresolved substitution in row index: {matcher} — pass bindings='
        )
    value = getattr(matcher, 'value', None)
    if isinstance(matcher, matchers.Pattern) or not _non_negative_int(value):
        raise TranslationError(
            f'row index must be a non-negative int in a table search: {op}'
        )
    return value


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
        col_op = ops[0]
        rest = ops[1:]
        if isinstance(col_op, _groups.Pack):
            raise TranslationError('packed group not supported')
        # Column-level guard: `status = "active"` has one op, a
        # ValueGuard wrapping the column key.
        if isinstance(col_op, wrappers.ValueGuard):
            if rest:
                raise TranslationError('path continues after column-level guard')
            col = self._column(self._column_name(col_op.inner))
            where = self._scalar_predicate(col_op, col)
            return self._result(select=col, where=where)
        # Top-level group: each branch is a complete path with its own column.
        if isinstance(col_op, _groups.OpGroup):
            if rest:
                raise TranslationError('path continues after top-level group')
            where = self._translate_top_group(col_op)
            return self._result(where=where)
        # Otherwise first op must be a plain Key naming the column.
        col = self._column(self._column_name(col_op))
        result = self._walk(col, (), rest)
        return self._result(**result)

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
        if not _is_table_key(op):
            raise TranslationError(
                f'first path segment must be a column key, got {type(op).__name__}'
            )
        return self._name(op.op, 'column')

    def _name(self, matcher, position):
        """
        The name `matcher` holds in `position` (a column, a table, a
        filter key). Reject matchers that can't name one.
        """
        if isinstance(matcher, matchers.Subst):
            raise TranslationError(f'unresolved substitution in {position} position')
        if isinstance(matcher, matchers.Reference):
            raise TranslationError(f'reference in {position} position')
        if isinstance(matcher, matchers.Pattern):
            raise TranslationError(f'pattern/wildcard in {position} position')
        if not hasattr(matcher, 'value'):
            raise TranslationError(
                f'unsupported {position} matcher: {type(matcher).__name__}'
            )
        value = matcher.value
        if not isinstance(value, str):
            value = str(value)
        return value

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
            offset, limit = _row_index(step), 1
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
        preds = [self._row_filter(f) for f in filters]
        if len(preds) == 1:
            return preds[0]
        return self._combine_and(preds)

    def _row_filter(self, f):
        """
        One row filter as a predicate over the table's columns.
        """
        if isinstance(f, _filters.FilterKeyValue):
            # Includes KeyValue, KeyValueNot, KeyValueLt, etc. via subclass
            return self._row_comparison(f)
        if isinstance(f, _filters.FilterAnd):
            return self._combine_and([self._row_filter(sub) for sub in f.filters])
        if isinstance(f, _filters.FilterOr):
            return self._combine_or([self._row_filter(sub) for sub in f.filters])
        if isinstance(f, _filters.FilterNot):
            return self._combine_not(self._row_filter(f.inner))
        if isinstance(f, _filters.FilterGroup):
            return self._row_filter(f.inner)
        raise TranslationError(f'filter type not supported: {type(f).__name__}')

    def _row_comparison(self, f):
        """
        A `key<op>value` row filter: its key reads as a column path
        (`&book_id="b1"` is the column `book_id`, `&data.user.age>30`
        reaches into the column `data`), its comparison as a guard at
        the end of it.
        """
        parts = f.key.parts if isinstance(f.key, _filters.FilterKey) else (f.key,)
        head, rest = parts[0], parts[1:]
        if isinstance(head, (access.Slot, access.Slice, _groups.Pack)):
            raise TranslationError(
                f'filter key must start with a column name: {f.key}'
            )
        col = self._column(self._name(head, 'column'))
        guard = wrappers.ValueGuard(
            None, f.val, pred_op=predicates.PRED_OPS[f._eq_str],
            transforms=f.guard_transforms)
        if not rest:
            return self._scalar_predicate(guard, col)
        prefix = tuple(self._filter_segment(p) for p in rest)
        return self._nested_predicate(guard, col, prefix)

    def _filter_segment(self, part):
        """
        A filter key part after its column as a prefix segment: a key
        (a raw matcher) or an index (a `Slot`).
        """
        if isinstance(part, (access.Slice, _groups.Pack)):
            raise TranslationError(
                f'{type(part).__name__} in a row filter key not supported in v1'
            )
        if isinstance(part, access.Slot):
            return self._segment_from_key(part)
        return ('lit', self._name(part, 'filter key'))

    def _walk(self, col, prefix, ops):
        """
        Walk the remaining path ops after the column, accumulating
        literal segments in `prefix`. Dispatches to group /
        pattern-mode / guard paths as needed.
        """
        if not ops:
            return {'select': self._select_expr(col, prefix)}
        op = ops[0]
        rest = ops[1:]
        # value transforms, a.b|int, have no target side: translate the op under them
        if isinstance(op, wrappers.TransformWrap):
            op = op.inner
        if isinstance(op, wrappers.TypeRestriction):
            raise TranslationError('type restriction not supported')
        if isinstance(op.most_inner, _groups.Pack):
            raise TranslationError('packed group not supported')
        # Groups: each branch is walked independently, so dispatch first
        # (OpGroup has is_pattern=True but shouldn't take the pattern-mode
        # path wholesale).
        if isinstance(op, _groups.OpGroup):
            if rest:
                raise TranslationError('path continues after group')
            return self._translate_group(op, col, prefix)
        # If any remaining op introduces a pattern-like segment (wildcard,
        # recursive, filter bracket), the flavor's pattern mode takes the
        # whole remainder.
        if any(_op_is_pattern_like(o) for o in ops):
            return self._pattern(col, prefix, ops)
        if isinstance(op, wrappers.ValueGuard):
            if rest:
                raise TranslationError('path continues after value guard')
            segment = self._segment_from_key(op.inner)
            full = prefix + (segment,)
            select = self._select_expr(col, full)
            where = self._nested_predicate(op, col, full)
            return {'select': select, 'where': where}
        if isinstance(op, access.Key):
            segment = self._segment_from_key(op)
            return self._walk(col, prefix + (segment,), rest)
        raise TranslationError(f'unsupported op: {type(op).__name__}')

    def _segment_from_key(self, key_op):
        """
        Turn a `Key` op inside a path into a `(kind, value)` tuple:
        `('lit', 'user')` for a plain key, or `('expr', expr)` for a
        reference whose subpath is recursively translated.
        """
        if not isinstance(key_op, access.Key):
            raise TranslationError(
                f'expected Key inside path, got {type(key_op).__name__}'
            )
        matcher = key_op.op
        if isinstance(matcher, matchers.Reference):
            if matcher.depth != 0:
                raise TranslationError(
                    'relative/parent references not supported in v1'
                )
            subpath = matcher.inner_path
            if not subpath:
                raise TranslationError('empty reference path')
            sub_parsed = parse(subpath, partial=True)
            # Recurse on self — any flavor state stays live across
            # the sub-translation.
            sub_result = self._translate(sub_parsed.ops)
            sub_expr = sub_result.get('select')
            if sub_expr is None:
                raise TranslationError('reference subpath has no select expression')
            return ('expr', sub_expr)
        if isinstance(matcher, matchers.Subst):
            raise TranslationError(
                f'unresolved substitution in path position: {matcher}'
            )
        if isinstance(matcher, matchers.Pattern):
            raise TranslationError(
                f'pattern segments not supported in v1: {matcher}'
            )
        if not hasattr(matcher, 'value'):
            raise TranslationError(f'unsupported matcher: {type(matcher).__name__}')
        return ('lit', self._segment_value(key_op, matcher.value))

    def _segment_value(self, key_op, value):
        """
        The literal segment for a plain key whose matcher holds `value`.
        Its string form by default; a flavor that tells list indexes
        (`[0]`) from keys overrides this.
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

    def _combine_group(self, group_op, preds):
        """
        Combine branch predicates per the group's boolean type.
        """
        if isinstance(group_op, _groups.OpGroupAnd):
            return self._combine_and(preds)
        if isinstance(group_op, _groups.OpGroupOr):
            return self._combine_or(preds)
        if isinstance(group_op, _groups.OpGroupNot):
            if len(preds) != 1:
                raise TranslationError(
                    'negation group must have exactly one branch'
                )
            return self._combine_not(preds[0])
        raise TranslationError(
            f'unsupported group type: {type(group_op).__name__}'
        )

    def _translate_top_group(self, group_op):
        """
        Translate a group op at the top of a path — each branch is a
        complete path starting from its own column.
        """
        wheres = []
        for branch in group_op.branches:
            if not isinstance(branch, tuple):
                raise TranslationError(
                    f'unsupported branch form: {type(branch).__name__}'
                )
            br_result = self._translate(branch)
            where = br_result.get('where')
            if where is None:
                raise TranslationError(
                    'top-level group branch has no predicate'
                )
            wheres.append(where)
        return self._combine_group(group_op, wheres)

    def _translate_group(self, group_op, col, prefix):
        """
        Translate a group op nested inside a path — each branch is a
        tail continuation sharing the outer column and prefix.
        """
        wheres = []
        for branch in group_op.branches:
            if not isinstance(branch, tuple):
                raise TranslationError(
                    f'unsupported branch form: {type(branch).__name__}'
                )
            br = self._walk(col, prefix, branch)
            if 'where' not in br:
                raise TranslationError(
                    'group branch without a guard (bare traversal) not supported in v1'
                )
            wheres.append(br['where'])
        joined = self._combine_group(group_op, wheres)
        select = self._select_expr(col, prefix)
        return {'select': select, 'where': joined}

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
        op.
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
