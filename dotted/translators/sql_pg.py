"""
Postgres dialect for dotted.translators.

Defines the `PostgresMixin` — the flavor's hooks for the neutral
`Translator` walk, plus cast-inference — and a `Resolver` subclass
per supported Postgres driver (`asyncpg`, `psycopg2`, `psycopg` aka
v3). Driver classes are registered with `.sql` via
`@driver('<driver-name>')`.

Also registers `_dollar_cast` with the legacy `_DIALECT_CAST_FNS`
hook so `Resolver.build(sql, paramstyle='dollar-numeric')` keeps
emitting explicit casts for the unit-test matrix, which exercises
paramstyle emitters without a specific driver.
"""
import collections
import decimal
import re

from .. import access
from .. import filters as _filters
from .. import groups as _groups
from .. import matchers
from .. import predicates
from .. import recursive as _recursive
from .. import wrappers

from .core import TranslationError, Translator
from .sql import (
    SQLFragment,
    Resolver,
    ParamPool,
    _FORMATTER,
    _quote_ident,
    _with_cast_spec,
    _register_dialect_cast,
    driver,
)


# ---- Postgres path / string literals --------------------------------

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
    SQL fragment (Python format-literal escaping: `{{` → `{`).
    """
    return "'{{" + ",".join(_pg_path_segment(s) for s in segments) + "}}'"


def _pg_string_literal(s):
    """
    Render a Python string as a Postgres text literal.
    """
    return "'" + s.replace("'", "''") + "'"


# ---- JSONPath fragments ---------------------------------------------

_JP_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _jsonpath_key(name):
    """
    Format a key name for a JSONPath accessor. JSONPath uses double-
    quoted strings for names that aren't plain identifiers.
    """
    if _JP_IDENT_RE.fullmatch(name):
        return name
    escaped = name.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _jsonpath_string_literal(s):
    """
    Format a Python string as a JSONPath string literal. JSONPath
    strings are double-quoted; escape backslashes and embedded double
    quotes.
    """
    escaped = s.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _jsonpath_value_literal(value):
    """
    Inline a literal value in a JSONPath expression. Returns None if
    the value cannot be safely inlined (caller should hoist to a
    JSONPath variable instead).
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


# ---- Pattern paths as JSONPath --------------------------------------
#
# A pattern path's ops become parts: a `_Fragment` per op — its
# JSONPath text, the JSONPath vars it uses, and whether the op is
# aligned (+) — and a `_Group` per group — its kind and each branch's
# parts. JSONPath has no union, so a path through groups runs as its
# tails: the group-free runs of fragments, one per way through each
# group.

_Fragment = collections.namedtuple('_Fragment', 'text jvars aligned')
_Group = collections.namedtuple('_Group', 'kind branches')


def _tails(parts):
    """
    The group-free runs of fragments that `parts` reach, one per way
    through each group, a way being a branch then the parts after the
    group. A `,` group's matches are its ways' together. An `&` group
    filters the node it is at, keeping it only where every way reaches
    something from it.
    """
    for i, part in enumerate(parts):
        if isinstance(part, _Fragment):
            continue
        head = list(parts[:i])
        ways = [list(branch) + list(parts[i + 1:]) for branch in part.branches]
        if part.kind == 'and':
            head.append(_held(ways))
        return [head + tail for way in ways for tail in _tails(way)]
    return [list(parts)]


def _held(ways):
    """
    A filter fragment keeping a node only where every one of `ways`
    reaches something from it.
    """
    tests = [_reaches(way) for way in ways]
    jvars = {}
    for _, test_jvars in tests:
        jvars.update(test_jvars)
    text = ' && '.join(f'({test})' for test, _ in tests)
    return _Fragment(f' ? ({text})', jvars, False)


def _reaches(parts):
    """
    A JSONPath test, and its vars, that `parts` reach something from
    `@`: some tail does, up to its first aligned (+) op, whose every
    match yields at least one result.
    """
    tests = []
    jvars = {}
    for tail in _tails(parts):
        text, tail_jvars = _segments(tail)[0]
        tests.append(f'exists(@{text})')
        jvars.update(tail_jvars)
    return ' || '.join(tests), jvars


def _segments(tail):
    """
    A tail's fragments joined into `(text, jvars)`, split after each
    aligned (+) op. The first segment's matches are the rows; each later
    one runs under a match of the one before, and a match it finds
    nothing under keeps a NULL row.
    """
    segments = []
    text, jvars = '', {}
    for fragment in tail:
        text += fragment.text
        jvars = {**jvars, **fragment.jvars}
        if not fragment.aligned:
            continue
        segments.append((text, jvars))
        text, jvars = '', {}
    if text or not segments:
        segments.append((text, jvars))
    return segments


def _joined(fragments):
    """
    Fragments joined into one `(text, jvars)`.
    """
    text, jvars = '', {}
    for fragment in fragments:
        text += fragment.text
        jvars = {**jvars, **fragment.jvars}
    return text, jvars


def _anywhere(parts, test):
    """
    True if `test` holds for a part of `parts`, one in a group's branch
    included.
    """
    for part in parts:
        if test(part):
            return True
        if isinstance(part, _Group) and any(_anywhere(b, test) for b in part.branches):
            return True
    return False


def _is_first(part):
    """
    True if `part` is a first-match group (?).
    """
    return isinstance(part, _Group) and part.kind == 'first'


def _is_split(part):
    """
    True if `part` is a group or an aligned (+) op: where rows read in
    dotted's order take a lateral of their own.
    """
    return isinstance(part, _Group) or part.aligned


# ---- Cast inference for Postgres ------------------------------------
#
# Postgres drivers that use server-side prepared statements (asyncpg
# always; psycopg3 in binary/server-bound mode) need explicit SQL
# casts on placeholders inside polymorphic-argument functions like
# `jsonb_build_object("any", "any", ...)`. psycopg2 substitutes
# client-side so it never needs this.
#
# `PostgresMixin.cast_fn` is the method driver classes inherit; it's
# consulted at build time when `cast = True` on the class. The same
# function is also registered into the legacy `_DIALECT_CAST_FNS`
# under `'dollar-numeric'` so the low-level `Resolver.build(
# paramstyle='dollar-numeric')` call path (used by the unit-test
# matrix) still emits casts the same way.

_DOLLAR_CAST_BY_PY_TYPE = {
    bool: 'boolean',
    int: 'bigint',
    float: 'float8',
    str: 'text',
    bytes: 'bytea',
}


def _dollar_cast(value):
    """
    Return the SQL cast (e.g. `'text'`) appropriate for the given
    Python value when a Postgres placeholder needs explicit typing,
    or None if no cast should be emitted (driver handles the type
    natively).
    """
    # bool is a subclass of int, so check bool first.
    if isinstance(value, bool):
        return 'boolean'
    for py_type, cast in _DOLLAR_CAST_BY_PY_TYPE.items():
        if type(value) is py_type:
            return cast
    return None


_register_dialect_cast('dollar-numeric', _dollar_cast)


# ---- Postgres mixin -------------------------------------------------

class PostgresMixin(Translator):
    """
    Postgres-flavored translation hooks and cast inference. Mix into
    a `Resolver` subclass to get Postgres JSONB / JSONPath SQL
    generation. The walk itself is `Translator`'s; the hooks below
    return marker-form SQL text.

    Shared across all Postgres drivers — each driver subclass only
    needs to set `paramstyle` and toggle `cast` on/off according to
    its binding model.

    Invariants while `translate()` runs:
      - `self._state` is a live `ParamPool` shared across every
        nested translation (group branches, reference subpaths).
        When a pool is passed by the caller, `self._state` is that
        pool — used to coordinate names across sibling Resolvers.
      - `self._state` is cleared back to None before translate()
        returns, so callers can reuse an instance safely.
    """

    def translate(self, ops, pool=None, root='column'):
        """
        Walk the parsed ops tree; populate `self.select`, `self.where`,
        `self.from_`, `self.unbound` with the rendered SQLFragments,
        and a table search's plain `self.table`, `self.offset`,
        `self.limit`.

        `pool` — optional `ParamPool` shared with sibling Resolvers so
        bind-parameter names don't collide across fragments. When None,
        a fresh pool is allocated for this call alone.

        `root` — `'column'` or `'table'`: what the path's first segment
        names (see `sqlize`).
        """
        self._state = pool if pool is not None else ParamPool()
        try:
            raw, (self.table, self.offset, self.limit) = (
                self._translate_root(ops, root))
        finally:
            state = self._state
            self._state = None

        params = dict(state.params)
        unbound = dict(state.unbound)

        def _mkfrag(text):
            """
            Narrow the shared params/unbound maps down to the markers
            this specific fragment actually references, so downstream
            composition of fragments doesn't pull in unrelated bindings.
            """
            if text is None:
                return None
            used = {name for _, name, _, _ in _FORMATTER.parse(text)
                    if name is not None}
            f_params = {k: v for k, v in params.items() if k in used}
            f_unbound = {k: v for k, v in unbound.items() if k in used}
            return SQLFragment(text, f_params, f_unbound)

        self.select = _mkfrag(raw.get('select'))
        self.where = _mkfrag(raw.get('where'))
        self.from_ = _mkfrag(raw.get('from'))
        self.lateral = _mkfrag(raw.get('lateral'))
        self.unbound = unbound

    @staticmethod
    def cast_fn(value):
        """
        Postgres cast inference for placeholders that need explicit
        typing (polymorphic contexts under server-side PREPARE).
        Returns a SQL cast name ('bigint', 'text', …) or None.
        """
        return _dollar_cast(value)

    def _hoist_value(self, value):
        """
        Hoist a concrete literal into `self._state.params`; return the
        `{marker}` string to embed in SQL text.
        """
        return self._state.hoist_literal(value)

    def _hoist_subst(self, name):
        """
        Hoist a named substitution into `self._state.unbound`; return
        the `{marker}` string.
        """
        return self._state.hoist_named(name)

    # ---- Translator hooks -------------------------------------------

    _CAST_MAP = {
        'int': 'int',
        'float': 'float',
        'str': 'text',
        'bool': 'bool',
    }

    def _column(self, name):
        """
        Quote the column name as a SQL identifier.
        """
        return _quote_ident(name)

    def _all_columns(self):
        """
        `*`.
        """
        return '*'

    def _select_expr(self, col_ident, prefix):
        """
        Text extraction for a non-empty prefix; the bare column for an
        empty one.
        """
        return self._path_expr(col_ident, prefix, text=bool(prefix))

    def _nested_predicate(self, guard_op, col_ident, prefix):
        """
        Guards inside a column are JSONB guards.
        """
        return self._jsonb_predicate(guard_op, col_ident, prefix)

    def _combine_and(self, preds):
        """
        Parenthesized predicates joined with AND.
        """
        return ' AND '.join(f'({p})' for p in preds)

    def _combine_or(self, preds):
        """
        Parenthesized predicates joined with OR.
        """
        return ' OR '.join(f'({p})' for p in preds)

    def _combine_not(self, pred):
        """
        NOT over the parenthesized predicate.
        """
        return f'NOT ({pred})'

    def _pattern(self, col_ident, prefix, ops):
        """
        Pattern paths produce three fragments: a WHERE predicate (via
        jsonb_path_exists), a LATERAL clause (via jsonb_path_query),
        and a select expression that references the lateral's alias.
        The engine picks which to compose — filter-style from `where`
        or extract-style from `lateral` + `select`.

        An aligned (+) op splits the path: the rest runs under each of
        its matches in a `LEFT JOIN LATERAL ... ON TRUE`, so a match the
        rest finds nothing under yields one NULL.

        A path through `,` and `&` groups runs as its tails (`_tails`):
        `where` holds when any tail matches, and the lateral yields
        every tail's matches, `UNION ALL`. JSONPath has no first, so a
        path through a first-match group (?) runs a lateral at each node
        a group or aligned op is at (`_rows`), and `where` holds when it
        yields a row.
        """
        parts = [self._jsonpath_head(prefix)] + self._jsonpath_parts(ops)
        where = self._some(col_ident, parts)
        if not _anywhere(parts, _is_first):
            tails = _tails(parts)
            if len(tails) == 1:
                lateral, alias = self._chain(col_ident, _segments(tails[0]))
                return {'select': f'{alias}.value', 'where': where,
                        'lateral': lateral}
        alias = self._state.alloc_pattern_alias()
        rows = self._rows(col_ident, parts)
        return {'select': f'{alias}.value', 'where': where,
                'lateral': f'LATERAL ({rows}) AS {alias}(value)'}

    def _rows(self, src, parts, ordered=False):
        """
        SQL query of the rows `parts` reach from the jsonb `src`, a
        `value` each. Without a first-match group, and unless `ordered`,
        it runs as its tails, `UNION ALL`. Otherwise it runs a lateral
        at each node a group or an aligned (+) op is at, so its rows
        come in the order dotted reaches them. `ordered` is for a first
        match: there an aligned op's default is a JSON null, a match.
        """
        if not ordered and not _anywhere(parts, _is_first):
            return self._tail_rows(src, parts)
        i = next((i for i, part in enumerate(parts) if _is_split(part)), None)
        if i is None:
            return self._tail_rows(src, parts)
        part, rest = parts[i], parts[i + 1:]
        if isinstance(part, _Group):
            text, jvars = _joined(parts[:i])
            if not text:
                return self._rows_at(src, part, rest, ordered)
            node = self._state.alloc_pattern_alias()
            under = self._rows_at(f'{node}.value', part, rest, ordered)
            found = self._state.alloc_pattern_alias()
            nodes = self._path_call('jsonb_path_query', src, text, jvars)
            return (f'SELECT {found}.value FROM {nodes} AS {node}(value) '
                    f'CROSS JOIN LATERAL ({under}) AS {found}(value)')
        # an aligned op: what follows runs under each of its matches,
        # which keeps a row where it finds nothing
        node = self._state.alloc_pattern_alias()
        under = self._rows(f'{node}.value', rest, ordered)
        found = self._state.alloc_pattern_alias()
        nodes = self._path_call('jsonb_path_query', src, *_joined(parts[:i + 1]))
        value = f'{found}.value'
        if ordered:
            value = f"COALESCE({value}, 'null'::jsonb)"
        return (f'SELECT {value} FROM {nodes} AS {node}(value) '
                f'LEFT JOIN LATERAL ({under}) AS {found}(value) ON TRUE')

    def _rows_at(self, node, group, rest, ordered):
        """
        SQL query of the rows a group at the jsonb `node` reaches,
        `rest` the parts after it: its ways' rows, `UNION ALL`; for an
        `&`, only where every way reaches something; for a first match,
        the rest's from it.
        """
        if group.kind == 'first':
            return self._rows(self._first(node, group.branches), rest, ordered)
        ways = [list(branch) + list(rest) for branch in group.branches]
        union = ' UNION ALL '.join(self._rows(node, way, ordered) for way in ways)
        if group.kind == 'or':
            return union
        held = self._combine_and([self._some(node, way) for way in ways])
        alias = self._state.alloc_pattern_alias()
        return (f'SELECT {alias}.value FROM ({union}) AS {alias}(value) '
                f'WHERE {held}')

    def _tail_rows(self, src, parts):
        """
        SQL query of the rows a path without a first-match group
        reaches from the jsonb `src`: each tail's, `UNION ALL`.
        """
        queries = []
        for tail in _tails(parts):
            chain, alias = self._chain(src, _segments(tail))
            queries.append(f'SELECT {alias}.value FROM {chain}')
        return ' UNION ALL '.join(queries)

    def _some(self, src, parts):
        """
        SQL boolean: `parts` reach something from the jsonb `src`.
        Without a first-match group some tail does, up to its first
        aligned (+) op; with one, its rows have a row.
        """
        if _anywhere(parts, _is_first):
            return f'EXISTS ({self._rows(src, parts)})'
        tests = [self._path_call('jsonb_path_exists', src, *_segments(tail)[0])
                 for tail in _tails(parts)]
        if len(tests) == 1:
            return tests[0]
        return self._combine_or(tests)

    def _first(self, node, branches):
        """
        SQL jsonb of a first-match group's match at the jsonb `node`:
        the first match of the first branch to have one, the branch
        alone; NULL where none does.
        """
        firsts = [self._first_of(node, branch) for branch in branches]
        if len(firsts) == 1:
            return firsts[0]
        return f'COALESCE({", ".join(firsts)})'

    def _first_of(self, src, parts):
        """
        SQL jsonb of the first row `parts` reach from the jsonb `src`,
        in the order dotted reaches them; NULL where there is none.
        """
        if not any(_is_split(part) for part in parts):
            return self._path_call('jsonb_path_query_first', src, *_joined(parts))
        alias = self._state.alloc_pattern_alias()
        rows = self._rows(src, parts, ordered=True)
        return f'(SELECT {alias}.value FROM ({rows}) AS {alias}(value) LIMIT 1)'

    def _chain(self, src, segments):
        """
        The laterals a tail's `(text, jvars)` segments run through, and
        the alias of the last: the first segment's matches in the jsonb
        `src`, then each later segment under a match of the one before.
        """
        (text, jvars), *rest = segments
        alias = self._state.alloc_pattern_alias()
        query = self._path_call('jsonb_path_query', src, text, jvars)
        lateral = f'LATERAL {query} AS {alias}(value)'
        for text, jvars in rest:
            source = f'{alias}.value'
            alias = self._state.alloc_pattern_alias()
            query = self._path_call('jsonb_path_query', source, text, jvars)
            lateral += f' LEFT JOIN LATERAL {query} AS {alias}(value) ON TRUE'
        return lateral, alias

    def _path_call(self, fn, src, text, jvars):
        """
        `fn(src, '$text'[, vars])`: a JSONPath function on the jsonb
        `src`, its JSONPath vars passed as a `jsonb_build_object`.
        """
        jsonpath_sql = _pg_string_literal('$' + text)
        if not jvars:
            return f'{fn}({src}, {jsonpath_sql})'
        return f'{fn}({src}, {jsonpath_sql}, {self._build_jsonpath_vars(jvars)})'

    # ---- Pattern paths via jsonb_path_exists ------------------------

    def _jsonpath_head(self, prefix):
        """
        The literal prefix as a fragment of plain `.key` accessors.
        """
        text = ''
        for kind, value in prefix:
            if kind != 'lit':
                raise TranslationError(
                    'dynamic path segments (references) combined with '
                    'patterns are not supported yet'
                )
            text += '.' + _jsonpath_key(value)
        return _Fragment(text, {}, False)

    def _jsonpath_parts(self, ops):
        """
        A pattern path's ops as parts: a `_Fragment` per op, a `_Group`
        per group. Each op is translated once, so a value it hoists is
        hoisted once, however many tails it is in.
        """
        parts = []
        for op in ops:
            if isinstance(op, _groups.OpGroup):
                parts.append(self._group_part(op))
                continue
            # a guard on a group, (a, b)?=1, tests each of its matches
            if (isinstance(op, wrappers.ValueGuard)
                    and isinstance(op.inner, _groups.OpGroup)):
                parts.append(self._group_part(op.inner))
                jvars = {}
                guard = self._guard_to_jsonpath(op, jvars)
                parts.append(_Fragment(f' ? ({guard})', jvars, False))
                continue
            jvars = {}
            frag, trailing_filter = self._op_to_jsonpath(op, jvars)
            if trailing_filter:
                frag += f' ? ({trailing_filter})'
            parts.append(_Fragment(frag, jvars, op.is_aligned()))
        return parts

    def _group_part(self, op):
        """
        A group as a `_Group`: its kind and each branch's parts.
        """
        kind, branches = self._grouped(op)
        return _Group(kind, [self._jsonpath_parts(b) for b in branches])

    def _build_jsonpath_vars(self, jvars):
        """
        Render the `jsonb_build_object('n1', $1::cast, ...)` argument
        list used to pass SQL values into a jsonpath expression as
        `$var` references. Each placeholder carries the `:cast` format
        spec so it picks up an explicit SQL cast at build time (asyncpg
        and similar server-bound drivers need it).
        """
        kv_pairs = ', '.join(
            f"'{name}', {_with_cast_spec(placeholder)}"
            for name, placeholder in jvars.items())
        return f'jsonb_build_object({kv_pairs})'

    def _op_to_jsonpath(self, op, jvars):
        """
        Translate one op into a JSONPath fragment. Returns
        (fragment, trailing_filter_or_None). trailing_filter is an
        expression placed inside `? (...)` after the fragment — used
        for guards and bracket filters.
        """
        if isinstance(op, wrappers.TransformWrap):
            return self._op_to_jsonpath(op.inner, jvars)
        if isinstance(op, wrappers.TypeRestriction):
            raise TranslationError('type restriction not supported')
        if isinstance(op.most_inner, _groups.Pack):
            raise TranslationError('packed group not supported')
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
        Translate a ValueGuard's predicate to a JSONPath filter
        expression (without the surrounding `? (...)`).
        """
        if guard_op.guard_transforms:
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
        Render a guard / filter value as a JSONPath expression —
        either an inline literal or a $var referencing a SQL
        placeholder.
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
        Map a SQL placeholder marker (`{name}`) into a JSONPath
        variable name, registering it in the vars dict. The JSONPath
        var name mirrors the bind marker name — safe under any
        paramstyle since marker names are always plain identifiers.
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
        Translate a sequence of FilterOp predicates to a JSONPath
        filter expression (no surrounding `? (...)`).
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
            if f.guard_transforms:
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
        Translate a FilterKey to the LHS of a JSONPath filter
        predicate, relative to `@` (current node). FilterKey.parts
        contains raw matchers (Word, Wildcard, Numeric, etc.) plus
        access ops for slots/slices — handle both forms.
        """
        if isinstance(key, _filters.FilterKey):
            parts = ['@']
            for p in key.parts:
                if isinstance(p, _groups.Pack):
                    raise TranslationError('packed group in filter key not supported')
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

    # ---- scalar (non-pattern) path helpers --------------------------

    def _path_expr(self, col_ident, prefix, text):
        """
        Build the Postgres extraction expression for a `prefix` tuple.
        Pure literal prefixes use `#>>` / `#>` with a text[] array
        literal; mixed literal + expression prefixes use a chain of
        `->` / `->>` arrows so embedded reference expressions can be
        inlined.
        """
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
        """
        Build a WHERE predicate for a guard on a scalar (non-JSONB)
        column.
        """
        if guard_op.guard_transforms:
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
        """
        Build a WHERE predicate for a guard nested inside a JSONB
        column. Computes both the text-extraction and the jsonb-form
        of the path so each value type can be compared with its
        matching operator (numeric cast, boolean equality, etc.).
        """
        text_expr = self._path_expr(col_ident, full_prefix, text=True)
        jsonb_expr = self._path_expr(col_ident, full_prefix, text=False)
        pred = guard_op.pred_op
        val = guard_op.guard
        if guard_op.guard_transforms:
            cast = self._transforms_to_cast(guard_op.guard_transforms)
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
        """
        Build a JSONB predicate when the RHS has already been resolved
        from a binding (ResolvedValue) — dispatch on the Python value's
        type.
        """
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
        """
        Compare a pre-built LHS expression (e.g. `(expr)::bigint`)
        against a guard value. Used for JSONB predicates whose LHS
        already carries a transform-driven cast.
        """
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


# ---- Driver classes -------------------------------------------------

@driver('asyncpg')
class AsyncPGResolver(PostgresMixin, Resolver):
    """
    asyncpg driver. Uses native `$N` placeholders and server-side
    PREPARE, so placeholders in polymorphic contexts need explicit
    casts.
    """
    paramstyle = 'dollar-numeric'
    cast       = True


@driver('psycopg2')
class Psycopg2Resolver(PostgresMixin, Resolver):
    """
    psycopg2 driver. Substitutes `%s` / `%(name)s` placeholders
    client-side before sending SQL to the server, so no type
    inference is needed.
    """
    paramstyle = 'pyformat'
    # cast = False inherited.


@driver('psycopg')
@driver('psycopg3')
class PsycopgResolver(PostgresMixin, Resolver):
    """
    psycopg (v3) driver. Uses `%s` / `%(name)s` like psycopg2 but can
    run in binary / server-bound mode where placeholder types are
    resolved server-side — keep casts on to stay correct in that mode.

    Registered under both `'psycopg'` (matches the Python module name)
    and `'psycopg3'` (matches what most humans type). Either works in
    `sqlize(..., driver=...)`; `type(r).driver` holds the canonical
    `'psycopg'`.
    """
    paramstyle = 'pyformat'
    cast       = True
