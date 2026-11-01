"""
Target-neutral core of dotted.translators.

Defines `TranslationError` and `Translator`, the walk over a parsed
path's ops shared by every translation target (SQL flavors, Spark).
The walk decides what kind of path it is looking at — column guard,
top-level group, plain traversal, nested guard, nested group, pattern
path — and calls flavor hooks to emit the target's expressions.

Flavor hooks (each flavor mixin implements them):

    _column(name)                     → column expression
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
subpath is translated through the same walk.

Expressions are opaque to the walk: whatever the hooks return is
carried through and handed back to other hooks. The walk returns a
dict of fragment name (`select`, `where`, `lateral`, ...) → flavor
expression; turning that into the target's public result is the
flavor's `translate()`.
"""
from .. import access
from .. import groups as _groups
from .. import matchers
from .. import wrappers
from ..api import parse


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


class Translator:
    """
    Target-neutral walk over parsed path ops. Mixed into a flavor
    class that implements the hooks listed in the module docstring.
    """

    # transform name (`|int`) → target cast name; set by each flavor
    _CAST_MAP = {}

    # ---- core walk --------------------------------------------------

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
        if not isinstance(op, access.Key):
            raise TranslationError(
                f'first path segment must be a column key, got {type(op).__name__}'
            )
        matcher = op.op
        if isinstance(matcher, matchers.Subst):
            raise TranslationError('unresolved substitution in column position')
        if isinstance(matcher, matchers.Reference):
            raise TranslationError('reference in column position')
        if isinstance(matcher, matchers.Pattern):
            raise TranslationError(
                'pattern/wildcard in column position — no "all columns" in SQL'
            )
        if not hasattr(matcher, 'value'):
            raise TranslationError(
                f'unsupported column matcher: {type(matcher).__name__}'
            )
        value = matcher.value
        if not isinstance(value, str):
            value = str(value)
        return value

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
        return ('lit', str(matcher.value))

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
