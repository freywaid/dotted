"""
Translate a dotted path into SQL clause components.

`sqlize(path)` returns a `Resolver` with format-string SQL fragments
(`.select`, `.where`, `.from_`) and the binding recipe. Fragments are
`SQLFragment` objects that concatenate naturally via `+`. Call
`Resolver.build(sql, paramstyle=..., **bindings)` to render into final
driver-ready SQL plus a params dict or args list.

    r = sqlize("data.user.age >= $(min_age)")
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric',
                               min_age=30)

ParamStyle is decided at build time, not at sqlize time. Fragments
carry `{name}` placeholder markers; `build` substitutes them with
`:name` (for `paramstyle='named'`) or `$N` positional (for
`paramstyle='dollar-numeric'`).
"""
import decimal
import enum
import hashlib
import re
import string as _string

from . import access
from . import filters as _filters
from . import groups as _groups
from . import matchers
from . import predicates
from . import recursive as _recursive
from . import wrappers
from .api import parse


class TranslationError(Exception):
    """
    Raised when a dotted path cannot be translated to SQL.
    """
    pass


# Bind-parameter markers in SQL fragments use Python format-string
# syntax: `{name}` is a placeholder, `{{` / `}}` are literal braces.
# `str.format()` does the substitution at build time.

_FORMATTER = _string.Formatter()


def _marker(name):
    """
    Render a bind-name marker for embedding in an SQL fragment's text.
    """
    return '{' + name + '}'


def _escape_braces(s):
    """
    Escape literal `{` and `}` in a string for inclusion in an SQL
    fragment's text. Needed wherever emitted SQL contains Postgres
    constructs like JSONB path arrays (`'{a,b,c}'`) that would
    otherwise be mistaken for format placeholders.
    """
    return s.replace('{', '{{').replace('}', '}}')


_MISSING = object()   # sentinel for "value not yet supplied"


_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _quote_ident(name):
    """
    Quote a SQL identifier only when needed.
    """
    if _IDENT_RE.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


# Substitution names that aren't already valid SQL identifiers are mapped
# to a hash-based name for use as a SQL bind parameter. Deterministic —
# the same subst name always produces the same hashed param.
_HASH_PREFIX = '_s_'
_GEN_PREFIX = '_p'
_RESERVED_PREFIXES = (_HASH_PREFIX, _GEN_PREFIX)


def _param_name_for_subst(name):
    """
    Return a SQL identifier-safe bind parameter name for a substitution.

    Plain identifiers pass through unchanged. Anything else — dotted
    paths, quoted keys, special characters — hashes to a deterministic
    `_s_<hex>` name. Names that collide with our reserved generated-name
    prefixes (`_p<N>`, `_s_`) are also hashed so they can never clobber
    hoisted literals.
    """
    if _IDENT_RE.fullmatch(name) and not any(
            name.startswith(p) for p in _RESERVED_PREFIXES):
        return name
    h = hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]
    return f'{_HASH_PREFIX}{h}'


def _pg_path_segment(seg):
    """
    Escape a segment for a Postgres text[] path literal like '{a,b,c}'.
    """
    if any(c in seg for c in '",{}\\'):
        return '"' + seg.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return seg


def _pg_path_array(segments):
    """
    Render literal segments as a Postgres text[] path literal. Braces
    are doubled so the result is safe to embed in a format-string
    SQL fragment (Python format literal escaping: `{{` → `{`).
    """
    return "'{{" + ",".join(_pg_path_segment(s) for s in segments) + "}}'"


def _pg_string_literal(s):
    """
    Render a Python string as a Postgres text literal.
    """
    return "'" + s.replace("'", "''") + "'"


# ---- JSONPath fragments ----

_JP_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _jsonpath_key(name):
    """
    Format a key name for a JSONPath accessor. JSONPath uses double-quoted
    strings for names that aren't plain identifiers.
    """
    if _JP_IDENT_RE.fullmatch(name):
        return name
    escaped = name.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _jsonpath_string_literal(s):
    """
    Format a Python string as a JSONPath string literal. JSONPath strings
    are double-quoted; escape backslashes and embedded double quotes.
    """
    escaped = s.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _jsonpath_value_literal(value):
    """
    Inline a literal value in a JSONPath expression. Returns None if the
    value cannot be safely inlined (caller should hoist to a JSONPath
    variable instead).
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    return None


_PRED_JSONPATH = {
    predicates.EQ: '==',
    predicates.NE: '!=',
    predicates.LT: '<',
    predicates.GT: '>',
    predicates.LE: '<=',
    predicates.GE: '>=',
}


def _op_is_pattern_like(op):
    """
    True if the op introduces a set-returning or recursive element that
    can't be expressed as a literal JSON path — requires JSONPath.
    """
    if isinstance(op, access.SliceFilter):
        return True
    if hasattr(op, 'is_pattern') and op.is_pattern():
        return True
    return False


class ParamStyle(enum.StrEnum):
    """
    SQL placeholder styles supported by `Resolver.build`. Values follow
    PEP 249 naming where applicable; `dollar-numeric` covers Postgres's
    native `$N` placeholders (used by asyncpg and similar drivers).

    `StrEnum` — members compare equal to their string values, so callers
    can pass either `ParamStyle.named` or `'named'`.

    Output shape:
      - `named` / `pyformat`: `(sql, params_dict)`. Repeated markers
        share one binding entry (back-reference by name).
      - `dollar-numeric` / `numeric`: `(sql, args_list)`. Repeated
        markers share one position (back-reference by number).
      - `qmark` / `format`: `(sql, args_list)`. Each marker occurrence
        produces a separate arg — these styles have no back-reference
        so a repeated substitution's value is emitted multiple times.
    """
    named = 'named'                    # :name         (SQLAlchemy, sqlite3)
    pyformat = 'pyformat'              # %(name)s      (psycopg)
    qmark = 'qmark'                    # ?             (sqlite3 positional)
    format = 'format'                  # %s            (psycopg positional)
    numeric = 'numeric'                # :1, :2        (Oracle-style)
    dollar_numeric = 'dollar-numeric'  # $1, $2        (asyncpg, native PG)


_PARAMSTYLES = tuple(ps.value for ps in ParamStyle)


class SQLFragment:
    """
    A format-string SQL fragment with placeholder markers and their
    metadata.

    - `text` is a string with `{name}` markers where a bind value
      goes.
    - `params` maps marker name → pre-hoisted value (literals resolved
      at sqlize time, including bindings supplied then).
    - `unbound` maps marker name → original substitution name (values
      still to be provided at build time).

    Fragments compose via `+` / `__radd__`, merging metadata:

        combined = "SELECT " + r.select + " FROM t WHERE " + r.where

    `str(sql)` returns the marker-form text — inspectable but not
    directly executable. Use `Resolver.build(sql, ...)` to render into
    driver-ready SQL.
    """

    __slots__ = ('text', 'params', 'unbound')

    def __init__(self, text='', params=None, unbound=None):
        self.text = text
        self.params = dict(params or {})
        self.unbound = dict(unbound or {})

    def __str__(self):
        return self.text

    def __repr__(self):
        return f'SQLFragment({self.text!r})'

    def __eq__(self, other):
        if isinstance(other, SQLFragment):
            return (self.text == other.text
                    and self.params == other.params
                    and self.unbound == other.unbound)
        return NotImplemented

    def __hash__(self):
        return hash((self.text,
                     tuple(sorted(self.params.items())),
                     tuple(sorted(self.unbound.items()))))

    def __bool__(self):
        return bool(self.text)

    def __add__(self, other):
        if isinstance(other, SQLFragment):
            return SQLFragment(self.text + other.text,
                       {**self.params, **other.params},
                       {**self.unbound, **other.unbound})
        if isinstance(other, str):
            return SQLFragment(self.text + other, self.params, self.unbound)
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, str):
            return SQLFragment(other + self.text, self.params, self.unbound)
        return NotImplemented

    def markers(self):
        """
        Yield marker names in occurrence order (duplicates included).
        """
        return [name for _, name, _, _ in _FORMATTER.parse(self.text)
                if name is not None]

    def substitute(self, replacer):
        """
        Return self.text with every `{marker}` replaced by the return
        value of `replacer(marker_name)`. Text-level operation: the
        caller decides what placeholder form to emit (`:name`, `$N`,
        `?`, etc.) and is responsible for tracking per-marker values
        externally. Literal braces in the fragment (doubled as `{{`/
        `}}`) are unescaped to single braces in the output, per Python
        format-string rules.

        `replacer(name)` is called **once per occurrence**, in source
        order. Callers that want back-referencing semantics (e.g.
        `:name` or `$N` sharing one slot across multiple occurrences)
        must cache results themselves.
        """
        parts = []
        for literal, name, _, _ in _FORMATTER.parse(self.text):
            parts.append(literal)
            if name is not None:
                parts.append(replacer(name))
        return ''.join(parts)


class Resolver:
    """
    Result of `sqlize(...)`. Carries paramstyle-neutral SQL fragments as
    `SQLFragment` objects plus the union of their unbound bindings.

    Attributes
    ----------
    select : SQL | None
        Extraction expression (e.g. `data #>> '{user,name}'`).
    where : SQL | None
        Predicate.
    from_ : SQL | None
        LATERAL / join fragment (future — None in v1).
    unbound : dict
        Union of all fragments' unbound mappings: bind name → original
        substitution name. Convenient for listing required bindings
        before calling `build`.

    Call `Resolver.build(sql, paramstyle=..., **bindings)` with any
    `SQLFragment` (usually composed from this Resolver's fragments) to
    render final SQL.
    """

    __slots__ = ('select', 'where', 'from_', 'unbound')

    def __init__(self, select=None, where=None, from_=None, unbound=None):
        self.select = select
        self.where = where
        self.from_ = from_
        self.unbound = dict(unbound or {})

    def __repr__(self):
        bits = []
        for attr in ('select', 'where', 'from_'):
            v = getattr(self, attr)
            if v is not None:
                bits.append(f'{attr}={v.text!r}')
        if self.unbound:
            bits.append(f'unbound={list(self.unbound.values())}')
        return f'Resolver({", ".join(bits)})'

    @classmethod
    def build(cls, sql, paramstyle='named', **bindings):
        """
        Render a composed `SQLFragment` into driver-ready SQL text plus
        a params structure.

        Returns `(text, params_or_args)`:
          - `paramstyle='named'` → `(sql_text_with_colon_names, dict)`
          - `paramstyle='dollar-numeric'` → `(sql_text_with_$N, list)`

        Raises `TranslationError` on:
          - unsupported paramstyle
          - unknown binding (kwarg not corresponding to any unbound
            substitution in the fragment)
          - missing binding for an unbound marker present in the text
        """
        if not isinstance(sql, SQLFragment):
            raise TranslationError(
                f'build requires an SQLFragment, got {type(sql).__name__}'
            )
        if paramstyle not in _PARAMSTYLES:
            raise TranslationError(
                f'unsupported paramstyle: {paramstyle!r}'
            )
        # Validate bindings: every kwarg must correspond to an unbound
        # orig name in this fragment.
        expected_origs = set(sql.unbound.values())
        extra = set(bindings) - expected_origs
        if extra:
            raise TranslationError(
                f'unknown binding(s) for this fragment: {sorted(extra)}'
            )

        # Build a per-paramstyle replacer. Replacers are called once
        # per marker occurrence — name-keyed styles cache results so
        # repeated markers share a single param; positional back-ref
        # styles (numeric, dollar-numeric) cache by position; qmark /
        # format emit a fresh arg for every occurrence.
        missing = []
        if paramstyle in ('named', 'pyformat'):
            out_params = {}
            fmt = ':{name}' if paramstyle == 'named' else '%({name})s'

            def replacer(marker):
                value = cls._lookup_value(sql, marker, bindings, missing)
                if value is _MISSING:
                    return ''
                out_params[marker] = value
                return fmt.format(name=marker)

            out_values = out_params
        elif paramstyle in ('numeric', 'dollar-numeric'):
            prefix = '$' if paramstyle == 'dollar-numeric' else ':'
            out_args = []
            pos_by_marker = {}

            def replacer(marker):
                if marker in pos_by_marker:
                    return pos_by_marker[marker]
                value = cls._lookup_value(sql, marker, bindings, missing)
                if value is _MISSING:
                    return ''
                out_args.append(value)
                ph = f'{prefix}{len(out_args)}'
                pos_by_marker[marker] = ph
                return ph

            out_values = out_args
        else:  # qmark, format — no back-reference
            placeholder = '?' if paramstyle == 'qmark' else '%s'
            out_args = []

            def replacer(marker):
                value = cls._lookup_value(sql, marker, bindings, missing)
                if value is _MISSING:
                    return ''
                out_args.append(value)
                return placeholder

            out_values = out_args

        final = sql.substitute(replacer)

        if missing:
            raise TranslationError(
                f'missing binding(s) for substitution(s): '
                f'{sorted(set(missing))}'
            )
        return final, out_values

    @staticmethod
    def _lookup_value(sql, marker, bindings, missing):
        """
        Resolve a marker to its value using the fragment's own
        metadata, supplemented by caller-provided bindings. Records
        marker-side misses in `missing` and returns `_MISSING` so the
        caller can continue scanning (build raises after the full pass).
        """
        if marker in sql.params:
            return sql.params[marker]
        if marker in sql.unbound:
            orig = sql.unbound[marker]
            if orig not in bindings:
                missing.append(orig)
                return _MISSING
            return bindings[orig]
        raise TranslationError(
            f'unknown marker {{{marker}}} in SQL fragment'
        )


class _ParamState:
    """
    Shared state for hoisted params across a translate call and its
    sub-translators. ParamStyle-neutral — fragments emit `{name}`
    markers; final paramstyle is chosen at build time.

    Fields:
      params  — marker name → pre-hoisted value (literals + resolved
                substitutions)
      unbound — marker name → original substitution name (to be bound
                later)
    """

    def __init__(self):
        self.params = {}    # marker name → resolved value
        self.unbound = {}   # marker name → original subst name
        self._gen_counter = 0
        self._by_orig = {}  # original subst name → marker name (dedup)

    def hoist_literal(self, value):
        """
        Hoist a concrete literal. Returns the marker form `{name}`
        ready to embed in SQL text.
        """
        self._gen_counter += 1
        name = f'{_GEN_PREFIX}{self._gen_counter}'
        self.params[name] = value
        return _marker(name)

    def hoist_named(self, name):
        """
        Hoist a named substitution. Returns the marker form `{name}`.
        Repeated uses of the same original name share one marker.
        """
        name = str(name)
        if name in self._by_orig:
            return _marker(self._by_orig[name])
        marker = _param_name_for_subst(name)
        self._by_orig[name] = marker
        self.unbound[marker] = name
        return _marker(marker)


class _Translator:
    """
    Walks the ops tree producing SQL fragments, sharing a _ParamState
    across branches and sub-translators.
    """

    def __init__(self, state=None):
        self.state = state if state is not None else _ParamState()

    def _hoist_value(self, value):
        return self.state.hoist_literal(value)

    def _hoist_subst(self, name):
        return self.state.hoist_named(name)

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
        """
        Carry intermediate translation output. Keys are fragment names
        (`select`, `where`, `from_`), values are marker-form SQL text
        strings. State (params, unbound) is read from self.state at
        the top level.
        """
        return {k: v for k, v in kw.items() if v is not None}

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
        # Groups: each branch is walked independently, so dispatch first
        # (OpGroup has is_pattern=True but shouldn't take the pattern-mode
        # path wholesale).
        if isinstance(op, _groups.OpGroup):
            if rest:
                raise TranslationError('path continues after group')
            return self._translate_group(op, col_ident, prefix)
        # If any remaining op introduces a pattern-like segment (wildcard,
        # recursive, filter bracket), switch to JSONPath mode.
        if any(_op_is_pattern_like(o) for o in ops):
            where = self._pattern_predicate(col_ident, prefix, ops)
            return {'select': col_ident, 'where': where}
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
        raise TranslationError(f'unsupported op: {type(op).__name__}')

    # ---- Pattern paths via jsonb_path_exists ----

    def _pattern_predicate(self, col_ident, prefix, ops):
        """
        Build a `jsonb_path_exists(col, 'path', vars_jsonb?)` predicate for
        a path containing patterns. `prefix` is the tuple of literal
        segments already accumulated (before the pattern section); `ops`
        is the remaining path from the first pattern-like op onward.
        """
        jvars = {}   # jsonpath var name → SQL placeholder expression
        parts = ['$']
        # Literal prefix becomes plain `.key` accessors.
        for kind, value in prefix:
            if kind != 'lit':
                raise TranslationError(
                    'dynamic path segments (references) combined with '
                    'patterns are not supported yet'
                )
            parts.append('.' + _jsonpath_key(value))
        # Walk remaining ops.
        for i, op in enumerate(ops):
            frag, trailing_filter = self._op_to_jsonpath(op, jvars)
            parts.append(frag)
            if trailing_filter:
                parts.append(f' ? ({trailing_filter})')
            # If this op was a terminal ValueGuard, it consumed the guard;
            # anything after is a bug.
            if isinstance(op, wrappers.ValueGuard) and i != len(ops) - 1:
                raise TranslationError('path continues after value guard')
        jsonpath_str = ''.join(parts)
        jsonpath_sql = _pg_string_literal(jsonpath_str)
        if not jvars:
            return f'jsonb_path_exists({col_ident}, {jsonpath_sql})'
        kv_pairs = ', '.join(
            f"'{name}', {placeholder}"
            for name, placeholder in jvars.items())
        return (f'jsonb_path_exists({col_ident}, {jsonpath_sql}, '
                f'jsonb_build_object({kv_pairs}))')

    def _op_to_jsonpath(self, op, jvars):
        """
        Translate one op into a JSONPath fragment. Returns
        (fragment, trailing_filter_or_None).  trailing_filter is an
        expression placed inside `? (...)` after the fragment — used for
        guards and bracket filters.
        """
        # NB: Slot is a subclass of Key, so check Slot first.
        if isinstance(op, access.Slot):
            return self._key_slot_to_jsonpath(op, is_key=False), None
        if isinstance(op, access.Attr):
            # Attrs have no JSONPath equivalent; treat as a key access.
            return self._key_slot_to_jsonpath(op, is_key=True), None
        if isinstance(op, access.Key):
            return self._key_slot_to_jsonpath(op, is_key=True), None
        if isinstance(op, _recursive.Recursive):
            return self._recursive_to_jsonpath(op), None
        if isinstance(op, access.SliceFilter):
            # [pred] → [*] ? (pred)
            filter_expr = self._filters_to_jsonpath(op.filters, jvars)
            return '[*]', filter_expr
        if isinstance(op, wrappers.FilterWrap):
            inner_frag, inner_filter = self._op_to_jsonpath(op.inner, jvars)
            filter_expr = self._filters_to_jsonpath(op.filters, jvars)
            if inner_filter:
                filter_expr = f'{inner_filter} && {filter_expr}'
            return inner_frag, filter_expr
        if isinstance(op, wrappers.ValueGuard):
            inner_frag, inner_filter = self._op_to_jsonpath(op.inner, jvars)
            pred_expr = self._guard_to_jsonpath(op, jvars)
            combined = (f'{inner_filter} && {pred_expr}'
                        if inner_filter else pred_expr)
            return inner_frag, combined
        raise TranslationError(
            f'op not supported in pattern paths: {type(op).__name__}'
        )

    def _key_slot_to_jsonpath(self, op, is_key):
        """
        Translate a Key/Slot op with any concrete or wildcard matcher.
        is_key=True uses `.name`/`.*`; is_key=False uses `[N]`/`[*]`.
        """
        matcher = op.op
        if isinstance(matcher, matchers.Wildcard):
            return '.*' if is_key else '[*]'
        if isinstance(matcher, matchers.Subst):
            raise TranslationError(
                f'unresolved substitution in pattern path: {matcher}'
            )
        if isinstance(matcher, matchers.Reference):
            raise TranslationError(
                'references inside pattern paths not supported yet'
            )
        if isinstance(matcher, matchers.Pattern):
            raise TranslationError(
                f'unsupported matcher in pattern path: {type(matcher).__name__}'
            )
        if not hasattr(matcher, 'value'):
            raise TranslationError(
                f'unsupported matcher: {type(matcher).__name__}'
            )
        value = matcher.value
        if is_key:
            return '.' + _jsonpath_key(str(value))
        # Slot: integer index
        return f'[{value}]'

    def _recursive_to_jsonpath(self, op):
        """
        Translate a Recursive op (** or *key etc).
        """
        inner = op.inner
        if isinstance(inner, matchers.Wildcard):
            return '.**'
        raise TranslationError(
            f'recursive op with {type(inner).__name__} inner not supported yet'
        )

    def _guard_to_jsonpath(self, guard_op, jvars):
        """
        Translate a ValueGuard's predicate to a JSONPath filter expression
        (without the surrounding `? (...)`).
        """
        if guard_op.transforms:
            raise TranslationError(
                'guard transforms inside pattern paths not supported yet'
            )
        pred = guard_op.pred_op
        value = guard_op.guard
        op_str = _PRED_JSONPATH.get(pred)
        if op_str is None:
            raise TranslationError(f'unsupported predicate: {pred!r}')
        rhs = self._value_to_jsonpath(value, jvars)
        return f'@ {op_str} {rhs}'

    def _value_to_jsonpath(self, val, jvars):
        """
        Render a guard / filter value as a JSONPath expression — either an
        inline literal or a $var referencing a SQL placeholder.
        """
        if isinstance(val, matchers.NoneValue):
            return 'null'
        if isinstance(val, matchers.Boolean):
            return 'true' if val.value else 'false'
        if isinstance(val, (matchers.Numeric, matchers.NumericQuoted,
                            matchers.NumericExtended)):
            py_val = val.value
            inlined = _jsonpath_value_literal(py_val)
            if inlined is not None:
                return inlined
            # Fallback — hoist
            placeholder = self._hoist_value(py_val)
            jvar = self._jvar_for_placeholder(placeholder, jvars)
            return f'${jvar}'
        if isinstance(val, matchers.Regex):
            # Only valid inside a filter; caller must place it correctly
            return f'like_regex {_jsonpath_string_literal(val.args[0])}'
        if isinstance(val, matchers.Subst):
            placeholder = self._hoist_subst(val.value)
            jvar = self._jvar_for_placeholder(placeholder, jvars)
            return f'${jvar}'
        if isinstance(val, matchers.ResolvedValue):
            py_val = val.value
            inlined = _jsonpath_value_literal(py_val)
            if inlined is not None:
                return inlined
            # Strings and other types: hoist
            placeholder = self._hoist_value(py_val)
            jvar = self._jvar_for_placeholder(placeholder, jvars)
            return f'${jvar}'
        if isinstance(val, (matchers.String, matchers.Word, matchers.Bytes)):
            # Hoist strings via a JSONPath variable.
            placeholder = self._hoist_value(val.value)
            jvar = self._jvar_for_placeholder(placeholder, jvars)
            return f'${jvar}'
        if hasattr(val, 'value'):
            placeholder = self._hoist_value(val.value)
            jvar = self._jvar_for_placeholder(placeholder, jvars)
            return f'${jvar}'
        raise TranslationError(
            f'unsupported value in pattern predicate: {type(val).__name__}'
        )

    def _jvar_for_placeholder(self, placeholder, jvars):
        """
        Map a SQL placeholder marker (`{name}`) into a JSONPath variable
        name, registering it in the vars dict. The JSONPath var name
        mirrors the bind marker name — safe under any paramstyle since
        marker names are always plain identifiers.
        """
        if not (placeholder.startswith('{') and placeholder.endswith('}')):
            raise TranslationError(
                f'unexpected placeholder form: {placeholder!r}'
            )
        name = placeholder[1:-1]
        jvars[name] = placeholder
        return name

    def _filters_to_jsonpath(self, filters, jvars):
        """
        Translate a sequence of FilterOp predicates to a JSONPath filter
        expression (no surrounding `? (...)`).
        """
        parts = []
        for f in filters:
            parts.append(self._filter_to_jsonpath(f, jvars))
        return ' && '.join(parts)

    def _filter_to_jsonpath(self, f, jvars):
        """
        Translate one FilterOp to a JSONPath filter expression.
        """
        if isinstance(f, _filters.FilterKeyValue):
            # Includes KeyValue, KeyValueNot, KeyValueLt, etc. via subclass
            if f.transforms:
                raise TranslationError(
                    'filter transforms inside pattern paths not supported yet'
                )
            op_str = {
                '=':  '==',
                '!=': '!=',
                '<':  '<',
                '>':  '>',
                '<=': '<=',
                '>=': '>=',
            }.get(f._eq_str)
            if op_str is None:
                raise TranslationError(
                    f'unsupported filter operator: {f._eq_str}'
                )
            key_expr = self._filter_key_to_jsonpath(f.key)
            rhs = self._value_to_jsonpath(f.val, jvars)
            return f'{key_expr} {op_str} {rhs}'
        if isinstance(f, _filters.FilterAnd):
            parts = [self._filter_to_jsonpath(sub, jvars)
                     for sub in f.filters]
            return ' && '.join(f'({p})' for p in parts)
        if isinstance(f, _filters.FilterOr):
            parts = [self._filter_to_jsonpath(sub, jvars)
                     for sub in f.filters]
            return ' || '.join(f'({p})' for p in parts)
        if isinstance(f, _filters.FilterNot):
            inner = self._filter_to_jsonpath(f.inner, jvars)
            return f'!({inner})'
        if isinstance(f, _filters.FilterGroup):
            inner = self._filter_to_jsonpath(f.inner, jvars)
            return f'({inner})'
        raise TranslationError(
            f'filter type not supported: {type(f).__name__}'
        )

    def _filter_key_to_jsonpath(self, key):
        """
        Translate a FilterKey to the LHS of a JSONPath filter predicate,
        relative to `@` (current node). FilterKey.parts contains raw
        matchers (Word, Wildcard, Numeric, etc.) plus access ops for
        slots/slices — handle both forms.
        """
        if isinstance(key, _filters.FilterKey):
            parts = ['@']
            for p in key.parts:
                if isinstance(p, access.Slot):
                    m = p.op
                    if isinstance(m, matchers.Wildcard):
                        parts.append('[*]')
                    elif hasattr(m, 'value'):
                        parts.append(f'[{m.value}]')
                    else:
                        raise TranslationError(
                            f'unsupported slot matcher in filter key: '
                            f'{type(m).__name__}'
                        )
                    continue
                if isinstance(p, access.Slice):
                    raise TranslationError(
                        'slice in filter key not supported yet'
                    )
                # Otherwise p is a raw matcher (Word, Wildcard, Numeric, …).
                if isinstance(p, matchers.Wildcard):
                    parts.append('.*')
                elif hasattr(p, 'value'):
                    parts.append('.' + _jsonpath_key(str(p.value)))
                else:
                    raise TranslationError(
                        f'unsupported filter key part: {type(p).__name__}'
                    )
            return ''.join(parts)
        # Raw matcher fallback.
        if hasattr(key, 'value'):
            return '@.' + _jsonpath_key(str(key.value))
        raise TranslationError(
            f'unsupported filter key: {type(key).__name__}'
        )

    # ---- scalar (non-pattern) path helpers ----

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


def sqlize(path, *, bindings=None, flavor='postgres'):
    """
    Translate a dotted path into a `Resolver` carrying paramstyle-neutral
    SQL fragments.

    path — dotted path string or pre-parsed Dotted result.
    bindings — optional mapping/list used to resolve substitutions at
        sqlize time. Path-position substitutions must be resolved here
        or a TranslationError is raised. Unresolved value-position
        substitutions appear in `unbound`.
    flavor — SQL flavor for JSONB operators; only `'postgres'` is
        implemented.

    Literals on the RHS of guards are hoisted for injection safety.
    Substitutions that are valid SQL identifiers bind by that name; any
    other form (dotted, quoted, punctuation) gets a deterministic
    `_s_<hash>` marker.

    Use `Resolver.build(sql, paramstyle=..., **bindings)` on any SQL
    fragment (or composition like `r.select + r.where`) to render final
    driver-ready SQL.

    >>> r = sqlize("status = 'active'")
    >>> Resolver.build(r.where)
    ('status = :_p1', {'_p1': 'active'})
    >>> Resolver.build(r.where, paramstyle='dollar-numeric')
    ('status = $1', ['active'])
    """
    if flavor != 'postgres':
        raise TranslationError(f'unsupported flavor: {flavor!r}')
    parsed = parse(path, bindings=bindings, partial=True)
    tr = _Translator()
    raw = tr.translate(parsed.ops)
    # Build SQLFragment objects per fragment sharing the translator state.
    state = tr.state
    params = dict(state.params)
    unbound = dict(state.unbound)

    def _mkfrag(text):
        if text is None:
            return None
        # Limit params/unbound to markers that actually appear in this
        # fragment's text.
        used = {name for _, name, _, _ in _FORMATTER.parse(text)
                if name is not None}
        f_params = {k: v for k, v in params.items() if k in used}
        f_unbound = {k: v for k, v in unbound.items() if k in used}
        return SQLFragment(text, f_params, f_unbound)

    return Resolver(
        select=_mkfrag(raw.get('select')),
        where=_mkfrag(raw.get('where')),
        from_=_mkfrag(raw.get('from')),
        unbound=unbound,
    )
