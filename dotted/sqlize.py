"""
Translate a dotted path into SQL clause components.

Returns a dict with keys select, where, from, params, missing. Only keys that
apply to the given path are included. Callers stitch the fragments into a
full statement, e.g.:

    parts = sqlize("data.user.age >= 30")
    sql = f"SELECT {parts['select']} FROM t WHERE {parts['where']}"

First segment of the path is the SQL column. Further segments are JSON
navigation inside it.

Placeholders are emitted in SQLAlchemy named style (`:name`). Literals on the
RHS of guards are hoisted into `params` under generated names (`_p1`, `_p2`,
…). Unresolved substitutions keep their declared names and appear in
`missing`; callers fill them in by name before executing.
"""
import decimal
import re

from . import access
from . import groups as _groups
from . import matchers
from . import predicates
from . import wrappers
from .api import parse


class TranslationError(Exception):
    """
    Raised when a dotted path cannot be translated to SQL.
    """
    pass


_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _quote_ident(name):
    """
    Quote a SQL identifier only when needed.
    """
    if _IDENT_RE.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


# Subst names may themselves be dotted paths. To survive as SQL bind
# parameter names (which must be plain identifiers), the access operators
# are encoded with mnemonic tokens. This preserves the distinction
# between `a.b`, `a@b`, and `a[0]` in the placeholder name.
_OP_ENCODE = {
    '.': '_dot_',
    '@': '_at_',
    '[': '_br_',
    ']': '',
}


def _encode_subst_name(name):
    """
    Encode a subst name (a dotted path) into a plain SQL identifier.
    Recognised access ops become mnemonic tokens; unsupported characters
    raise TranslationError.
    """
    if _IDENT_RE.fullmatch(name):
        return name
    out = []
    for c in name:
        if c in _OP_ENCODE:
            out.append(_OP_ENCODE[c])
        elif c.isalnum() or c == '_':
            out.append(c)
        else:
            raise TranslationError(
                f'cannot encode character {c!r} in substitution name {name!r}; '
                'resolve via bindings= at sqlize time'
            )
    encoded = ''.join(out)
    if not _IDENT_RE.fullmatch(encoded):
        raise TranslationError(
            f'substitution name {name!r} does not encode to a valid '
            'SQL identifier'
        )
    return encoded


def _pg_path_segment(seg):
    """
    Escape a segment for a Postgres text[] path literal like '{a,b,c}'.
    """
    if any(c in seg for c in '",{}\\'):
        return '"' + seg.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return seg


def _pg_path_array(segments):
    """
    Render literal segments as a Postgres text[] path literal.
    """
    return "'{" + ",".join(_pg_path_segment(s) for s in segments) + "}'"


def _pg_string_literal(s):
    """
    Render a Python string as a Postgres text literal.
    """
    return "'" + s.replace("'", "''") + "'"


class _ParamState:
    """
    Shared state for hoisted params across a translate call and its
    sub-translators. Generated param names use the `_p{n}` convention;
    substitution param names come from the substitution itself.
    """

    def __init__(self):
        self.params = {}          # name → resolved value
        self.missing = []         # list of substitution names awaiting a value
        self._gen_counter = 0

    def hoist_literal(self, value):
        """
        Hoist a concrete literal into a generated-name slot.
        """
        self._gen_counter += 1
        name = f'_p{self._gen_counter}'
        self.params[name] = value
        return name

    def hoist_named(self, name):
        """
        Hoist a named substitution. If a value is supplied later it lands
        under the encoded name; until then it's recorded in `missing`.

        Names that already are plain identifiers are used as-is. Dotted
        paths (`$(user.age)`, `$(users[0].name)`, `$(obj@attr)`) are
        encoded into plain identifiers using mnemonic tokens for access
        ops so they can serve as SQL bind-parameter names.
        """
        encoded = _encode_subst_name(str(name))
        if encoded not in self.params and encoded not in self.missing:
            self.missing.append(encoded)
        return encoded


class _Translator:
    """
    Walks the ops tree producing SQL fragments, sharing a _ParamState
    across branches and sub-translators.
    """

    def __init__(self, state=None):
        self.state = state if state is not None else _ParamState()

    def _ph(self, name):
        """
        Render a named placeholder in the current format (SQLAlchemy `:name`).
        """
        return f':{name}'

    def _hoist_value(self, value):
        return self._ph(self.state.hoist_literal(value))

    def _hoist_subst(self, name):
        return self._ph(self.state.hoist_named(name))

    def translate(self, ops):
        if not ops:
            raise TranslationError('empty path')
        col_op = ops[0]
        rest = ops[1:]
        # Column-level guard: `status = "active"` has one op, a ValueGuard
        # wrapping the column key.
        if isinstance(col_op, wrappers.ValueGuard):
            if rest:
                raise TranslationError('path continues after column-level guard')
            col_name = self._column_name(col_op.inner)
            col_ident = _quote_ident(col_name)
            where = self._scalar_predicate(col_op, col_ident)
            return self._result(select=col_ident, where=where)
        # Top-level group: each branch is a complete path with its own column.
        if isinstance(col_op, _groups.OpGroup):
            if rest:
                raise TranslationError('path continues after top-level group')
            where = self._translate_top_group(col_op)
            return self._result(where=where)
        # Otherwise first op must be a plain Key naming the column.
        col_name = self._column_name(col_op)
        col_ident = _quote_ident(col_name)
        result = self._walk(col_ident, (), rest)
        return self._result(**result)

    def _result(self, **kw):
        d = {k: v for k, v in kw.items() if v is not None}
        d['params'] = dict(self.state.params)
        if self.state.missing:
            d['missing'] = list(self.state.missing)
        return d

    def _column_name(self, op):
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

    def _walk(self, col_ident, prefix, ops):
        if not ops:
            return {'select': self._path_expr(col_ident, prefix, text=bool(prefix))}
        op = ops[0]
        rest = ops[1:]
        if isinstance(op, wrappers.ValueGuard):
            if rest:
                raise TranslationError('path continues after value guard')
            segment = self._segment_from_key(op.inner)
            full = prefix + (segment,)
            select = self._path_expr(col_ident, full, text=True)
            where = self._jsonb_predicate(op, col_ident, full)
            return {'select': select, 'where': where}
        if isinstance(op, access.Key):
            segment = self._segment_from_key(op)
            return self._walk(col_ident, prefix + (segment,), rest)
        if isinstance(op, _groups.OpGroup):
            if rest:
                raise TranslationError('path continues after group')
            return self._translate_group(op, col_ident, prefix)
        raise TranslationError(f'unsupported op: {type(op).__name__}')

    def _segment_from_key(self, key_op):
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
            sub = _Translator(state=self.state)
            sub_result = sub.translate(sub_parsed.ops)
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

    def _path_expr(self, col_ident, prefix, text):
        if not prefix:
            return col_ident
        if all(kind == 'lit' for kind, _ in prefix):
            seg_strs = [s for _, s in prefix]
            op = '#>>' if text else '#>'
            return f"{col_ident} {op} {_pg_path_array(seg_strs)}"
        expr = col_ident
        last = len(prefix) - 1
        for i, (kind, val) in enumerate(prefix):
            arrow = '->>' if (text and i == last) else '->'
            rhs = _pg_string_literal(val) if kind == 'lit' else f'({val})'
            expr = f'{expr} {arrow} {rhs}'
        return expr

    def _scalar_predicate(self, guard_op, col_ident):
        if guard_op.transforms:
            raise TranslationError(
                'guard transforms on scalar columns not supported in v1'
            )
        val = guard_op.guard
        pred = guard_op.pred_op
        if isinstance(val, matchers.NoneValue):
            if pred is predicates.EQ:
                return f'{col_ident} IS NULL'
            if pred is predicates.NE:
                return f'{col_ident} IS NOT NULL'
            raise TranslationError('ordering comparison against None')
        if isinstance(val, matchers.Regex):
            if pred is predicates.EQ:
                return f'{col_ident} ~ {self._hoist_value(val.args[0])}'
            if pred is predicates.NE:
                return f'{col_ident} !~ {self._hoist_value(val.args[0])}'
            raise TranslationError('ordering comparison against regex')
        if isinstance(val, matchers.Subst):
            return f'{col_ident} {pred.op} {self._hoist_subst(val.value)}'
        if isinstance(val, matchers.Pattern):
            raise TranslationError(
                f'pattern guard values not supported in v1: {type(val).__name__}'
            )
        if not hasattr(val, 'value'):
            raise TranslationError(f'unsupported guard value: {type(val).__name__}')
        return f'{col_ident} {pred.op} {self._hoist_value(val.value)}'

    def _jsonb_predicate(self, guard_op, col_ident, full_prefix):
        text_expr = self._path_expr(col_ident, full_prefix, text=True)
        jsonb_expr = self._path_expr(col_ident, full_prefix, text=False)
        pred = guard_op.pred_op
        val = guard_op.guard
        if guard_op.transforms:
            cast = self._transforms_to_cast(guard_op.transforms)
            lhs = f'({text_expr})::{cast}'
            return self._compare_with_lhs(lhs, pred, val)
        if isinstance(val, matchers.NoneValue):
            if pred is predicates.EQ:
                return f"{jsonb_expr} IS NULL OR jsonb_typeof({jsonb_expr}) = 'null'"
            if pred is predicates.NE:
                return f"{jsonb_expr} IS NOT NULL AND jsonb_typeof({jsonb_expr}) != 'null'"
            raise TranslationError('ordering comparison against None')
        if isinstance(val, matchers.Boolean):
            lit = 'true' if val.value else 'false'
            if pred is predicates.EQ:
                return f"{jsonb_expr} = '{lit}'::jsonb"
            if pred is predicates.NE:
                return f"{jsonb_expr} != '{lit}'::jsonb"
            raise TranslationError('ordering comparison against boolean')
        if isinstance(val, matchers.Regex):
            if pred is predicates.EQ:
                return f'({text_expr}) ~ {self._hoist_value(val.args[0])}'
            if pred is predicates.NE:
                return f'({text_expr}) !~ {self._hoist_value(val.args[0])}'
            raise TranslationError('ordering comparison against regex')
        if isinstance(val, matchers.Subst):
            return f'({text_expr}) {pred.op} {self._hoist_subst(val.value)}'
        if isinstance(val, (matchers.Numeric, matchers.NumericQuoted,
                            matchers.NumericExtended)):
            return f'({text_expr})::numeric {pred.op} {self._hoist_value(val.value)}'
        if isinstance(val, matchers.ResolvedValue):
            return self._predicate_for_resolved(text_expr, jsonb_expr, pred, val.value)
        if isinstance(val, (matchers.String, matchers.Word, matchers.Bytes)):
            return f'({text_expr}) {pred.op} {self._hoist_value(val.value)}'
        if isinstance(val, matchers.Pattern):
            raise TranslationError(
                f'pattern guard values not supported in v1: {type(val).__name__}'
            )
        if hasattr(val, 'value'):
            return f'({text_expr}) {pred.op} {self._hoist_value(val.value)}'
        raise TranslationError(f'unsupported guard value: {type(val).__name__}')

    def _predicate_for_resolved(self, text_expr, jsonb_expr, pred, py_val):
        if py_val is None:
            if pred is predicates.EQ:
                return f"{jsonb_expr} IS NULL OR jsonb_typeof({jsonb_expr}) = 'null'"
            if pred is predicates.NE:
                return f"{jsonb_expr} IS NOT NULL AND jsonb_typeof({jsonb_expr}) != 'null'"
            raise TranslationError('ordering comparison against None')
        if isinstance(py_val, bool):
            lit = 'true' if py_val else 'false'
            if pred is predicates.EQ:
                return f"{jsonb_expr} = '{lit}'::jsonb"
            if pred is predicates.NE:
                return f"{jsonb_expr} != '{lit}'::jsonb"
            raise TranslationError('ordering comparison against boolean')
        if isinstance(py_val, (int, float, decimal.Decimal)):
            return f'({text_expr})::numeric {pred.op} {self._hoist_value(py_val)}'
        return f'({text_expr}) {pred.op} {self._hoist_value(py_val)}'

    def _compare_with_lhs(self, lhs, pred, val):
        if isinstance(val, matchers.NoneValue):
            if pred is predicates.EQ:
                return f'{lhs} IS NULL'
            if pred is predicates.NE:
                return f'{lhs} IS NOT NULL'
            raise TranslationError('ordering comparison against None')
        if isinstance(val, matchers.Subst):
            return f'{lhs} {pred.op} {self._hoist_subst(val.value)}'
        if isinstance(val, matchers.Pattern):
            raise TranslationError(
                f'pattern guard values not supported in v1: {type(val).__name__}'
            )
        if not hasattr(val, 'value'):
            raise TranslationError(f'unsupported guard value: {type(val).__name__}')
        return f'{lhs} {pred.op} {self._hoist_value(val.value)}'

    _CAST_MAP = {
        'int': 'int',
        'float': 'float',
        'str': 'text',
        'bool': 'bool',
    }

    def _transforms_to_cast(self, transforms):
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

    def _translate_top_group(self, group_op):
        wheres = []
        for branch in group_op.branches:
            if not isinstance(branch, tuple):
                raise TranslationError(
                    f'unsupported branch form: {type(branch).__name__}'
                )
            sub = _Translator(state=self.state)
            br_result = sub.translate(branch)
            where = br_result.get('where')
            if where is None:
                raise TranslationError(
                    'top-level group branch has no predicate'
                )
            wheres.append(where)
        if isinstance(group_op, _groups.OpGroupAnd):
            return ' AND '.join(f'({w})' for w in wheres)
        if isinstance(group_op, _groups.OpGroupOr):
            return ' OR '.join(f'({w})' for w in wheres)
        if isinstance(group_op, _groups.OpGroupNot):
            if len(wheres) != 1:
                raise TranslationError(
                    'negation group must have exactly one branch'
                )
            return f'NOT ({wheres[0]})'
        raise TranslationError(
            f'unsupported group type: {type(group_op).__name__}'
        )

    def _translate_group(self, group_op, col_ident, prefix):
        branch_results = []
        for branch in group_op.branches:
            if not isinstance(branch, tuple):
                raise TranslationError(
                    f'unsupported branch form: {type(branch).__name__}'
                )
            br = self._walk(col_ident, prefix, branch)
            if 'where' not in br:
                raise TranslationError(
                    'group branch without a guard (bare traversal) not supported in v1'
                )
            branch_results.append(br)
        if isinstance(group_op, _groups.OpGroupAnd):
            joined = ' AND '.join(f'({r["where"]})' for r in branch_results)
        elif isinstance(group_op, _groups.OpGroupOr):
            joined = ' OR '.join(f'({r["where"]})' for r in branch_results)
        elif isinstance(group_op, _groups.OpGroupNot):
            if len(branch_results) != 1:
                raise TranslationError(
                    'negation group must have exactly one branch'
                )
            joined = f'NOT ({branch_results[0]["where"]})'
        else:
            raise TranslationError(
                f'unsupported group type: {type(group_op).__name__}'
            )
        select = self._path_expr(col_ident, prefix, text=bool(prefix))
        return {'select': select, 'where': joined}


_SUPPORTED_FORMATS = ('sqlalchemy',)


def sqlize(path, *, bindings=None, flavor='postgres', format='sqlalchemy'):
    """
    Translate a dotted path into SQL clause components.

    Returns a dict with some subset of keys select, where, from, params,
    missing.

    path — dotted path string or pre-parsed Dotted result.
    bindings — optional mapping/list used to resolve substitutions before
        translation. Path-position substitutions must be resolved or a
        TranslationError is raised. Unresolved value-position substitutions
        keep their declared names and appear in 'missing'.
    flavor — SQL flavor for JSONB operators; only 'postgres' is implemented.
    format — placeholder style. Only 'sqlalchemy' (`:name`) is supported in v1.

    Literals on the RHS of guards are hoisted into params under generated
    names (`_p1`, `_p2`, …). Named substitutions keep their original names.
    Callers fill any names still in 'missing' before handing params to a
    driver.

    >>> sqlize("status = 'active'")
    {'select': 'status', 'where': 'status = :_p1', 'params': {'_p1': 'active'}}
    """
    if flavor != 'postgres':
        raise TranslationError(f'unsupported flavor: {flavor!r}')
    if format not in _SUPPORTED_FORMATS:
        raise TranslationError(f'unsupported format: {format!r}')
    parsed = parse(path, bindings=bindings, partial=True)
    tr = _Translator()
    return tr.translate(parsed.ops)
