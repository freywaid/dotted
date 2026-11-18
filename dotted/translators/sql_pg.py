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
import json
import math
import re

from .. import predicates

from . import core
from .core import Hooks, TranslationError, Translator
from .sql import (
    SQLFragment,
    Raw,
    Resolver,
    ParamPool,
    _FORMATTER,
    _quote_ident,
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


# ---- Reading, as dotted reads ---------------------------------------
#
# A pattern path reads a step at a time, as dotted's walk does (see
# `_path_rows`). A step reads a node by its own type (`jsonb_typeof`):
# a key reads an object's member; an int an array's element or a
# string's character, never an object's member, whose names are
# strings; a wildcard of keys an object's members; a wildcard of slots
# an array's elements, an object's values or a string's characters.
#
# Filters and guards compare as dotted does, which is as Python does:
# a number and a boolean as numbers (True is 1), a string with a
# string in code-point order, any other pairing unequal and unordered;
# `!=` is `=`'s negation, so it holds for a filter's node that is no
# object or lacks the key; a regex fullmatches str() of the value.

# the path reading a jsonb value whole, as text (`#>>`), braces doubled
# for the fragment's format string
_WHOLE = "'{{}}'"

_SQL_OPS = {
    predicates.EQ: '=',
    predicates.NE: '<>',
    predicates.LT: '<',
    predicates.GT: '>',
    predicates.LE: '<=',
    predicates.GE: '>=',
}

# what Python's int() and float() take from a string, as ARE regexes
_INT_TEXT = r'^\s*[-+]?[0-9](_?[0-9])*\s*$'
_DIGITS = r'[0-9](_?[0-9])*'
_FLOAT_TEXT = (r'^\s*[-+]?(' + _DIGITS + r'(\.(' + _DIGITS + r')?)?|\.' + _DIGITS + r')'
               r'([eE][-+]?' + _DIGITS + r')?\s*$')
_NONFINITE_TEXT = r'^\s*[-+]?(inf|infinity|nan)\s*$'


def _numeric_sql(value):
    """
    A Python number as a SQL numeric literal, a bool as 1 or 0.
    """
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        raise TranslationError(f'a non-finite number in a comparison not supported: {value!r}')
    return repr(value) if isinstance(value, float) else str(value)


def _number_of(j):
    """
    SQL numeric of the jsonb `j`, a number or a boolean: Python's True
    is 1, its False 0.
    """
    return (f"(CASE WHEN {j} = 'true'::jsonb THEN 1::numeric "
            f"WHEN {j} = 'false'::jsonb THEN 0::numeric ELSE ({j})::numeric END)")


def _py_equal(j, b):
    """
    SQL boolean: the jsonb `j` equals the jsonb `b` as Python's `==`
    does on their values: a number and a boolean as numbers (True is
    1), other values of one type by jsonb equality, else unequal.
    """
    numbers = (f"jsonb_typeof({j}) IN ('number', 'boolean') "
               f"AND jsonb_typeof({b}) IN ('number', 'boolean')")
    return (f'(CASE WHEN {numbers} THEN {_number_of(j)} = {_number_of(b)} '
            f'WHEN jsonb_typeof({j}) = jsonb_typeof({b}) THEN {j} = {b} ELSE FALSE END)')


def _failing(what, j):
    """
    SQL int that fails at run time, where dotted raises or the target
    can't say what dotted would give: a cast of the message to an int,
    which Postgres reports with the message whole. It reads the jsonb
    `j`, so the planner can't fold it ahead of the CASE that guards it;
    cast it to the type the CASE needs.
    """
    message = _pg_string_literal(f'dotted: {what}, at a ')
    return f'(({message} || jsonb_typeof({j}))::int)'


def _py_text_of_number(j):
    """
    SQL text of the jsonb number `j` as Python's str() writes it: an int
    as written, a float as its shortest repr, a whole one with `.0`.
    From 1e15 to 1e16 Postgres writes an exponent and Python doesn't:
    that fails at run time.
    """
    text = f'({j} #>> {_WHOLE})'
    shortest = f'(({j})::numeric::float8::text)'
    return (f"(CASE WHEN {text} ~ '^-?[0-9]+$' THEN {text} "
            f'WHEN abs(({j})::numeric) >= 1e15 AND abs(({j})::numeric) < 1e16 '
            f"THEN {_failing('a float Python writes in full', j)}::text "
            f"WHEN {shortest} ~ '[.e]' THEN {shortest} ELSE {shortest} || '.0' END)")


def _are_regex(pattern):
    """
    A Python regex as a Postgres ARE that matches where Python's
    fullmatch does: anchored at both ends, `\\b` and `\\B` as ARE's `\\y`
    and `\\Y`, a leading `(?i)` / `(?x)` kept in front. Any other inline
    flag raises.
    """
    try:
        re.compile(pattern)
    except re.error as e:
        raise TranslationError(f'invalid regex {pattern!r}: {e}')
    prefix = ''
    lead = re.match(r'\(\?([a-zA-Z]+)\)', pattern)
    if lead:
        if set(lead.group(1)) - set('ix'):
            raise TranslationError(f'regex flags other than i and x not supported: {pattern!r}')
        prefix = lead.group(0)
        pattern = pattern[lead.end():]
    out = []
    i = 0
    in_class = False
    while i < len(pattern):
        c = pattern[i]
        if c == '\\' and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if not in_class and nxt in 'bB':
                out.append('\\y' if nxt == 'b' else '\\Y')
            else:
                out.append(c + nxt)
            i += 2
            continue
        if c == '[' and not in_class:
            in_class = True
            out.append(c)
            i += 1
            # a ] first in a class, after an optional ^, is a literal
            if pattern[i:i + 1] == '^':
                out.append('^')
                i += 1
            if pattern[i:i + 1] == ']':
                out.append(']')
                i += 1
            continue
        if c == ']' and in_class:
            in_class = False
        out.append(c)
        i += 1
    return f'{prefix}^(?:{"".join(out)})$'


def _int_of(j):
    """
    SQL jsonb of dotted's `int` transform on the jsonb `j`: a number
    truncated, a boolean as 1 or 0, a string Python's int() takes as
    that int; anything else as it was, as `int` leaves what int()
    raises on.
    """
    text = f'({j} #>> {_WHOLE})'
    return (f"(CASE jsonb_typeof({j}) WHEN 'number' THEN to_jsonb(trunc(({j})::numeric)) "
            f"WHEN 'boolean' THEN to_jsonb(CASE WHEN {j} = 'true'::jsonb THEN 1 ELSE 0 END) "
            f"WHEN 'string' THEN (CASE WHEN {text} ~ {_pg_string_literal(_INT_TEXT)} "
            f"THEN to_jsonb(regexp_replace({text}, '\\s|_', '', 'g')::numeric) ELSE {j} END) "
            f'ELSE {j} END)')


def _as_float(numeric):
    """
    SQL jsonb of the SQL numeric `numeric` as a Python float writes:
    its double's shortest digits, a whole one with `.0`.
    """
    return f'to_jsonb(({numeric})::float8::numeric + 0.0)'


def _float_of(j):
    """
    SQL jsonb of dotted's `float` transform on the jsonb `j`: a number
    as is, a boolean as 1.0 or 0.0, a string Python's float() takes as
    that number; another string as it was. float() raises a TypeError
    dotted doesn't catch on a null, an array or an object, and its inf
    and nan have no JSON: those fail at run time.
    """
    text = f'({j} #>> {_WHOLE})'
    number = _as_float(f'({j})::numeric')
    parsed = _as_float(f"regexp_replace({text}, '\\s|_', '', 'g')::numeric")
    return (f"(CASE jsonb_typeof({j}) WHEN 'number' THEN {number} "
            f"WHEN 'boolean' THEN to_jsonb(CASE WHEN {j} = 'true'::jsonb THEN 1.0 ELSE 0.0 END) "
            f"WHEN 'string' THEN (CASE WHEN {text} ~ {_pg_string_literal(_FLOAT_TEXT)} "
            f"THEN {parsed} "
            f"WHEN lower({text}) ~ {_pg_string_literal(_NONFINITE_TEXT)} "
            f"THEN to_jsonb({_failing('float() of inf or nan', j)}) ELSE {j} END) "
            f"ELSE to_jsonb({_failing('float() of a value that is not a number or string', j)}) END)")


def _str_of(j):
    """
    SQL jsonb of dotted's `str` transform on the jsonb `j`: str() of it,
    a number as Python writes it, a boolean as `True` / `False`, a null
    as `None`. Python writes an array or object as its repr, which
    fails at run time.
    """
    return (f"(CASE jsonb_typeof({j}) WHEN 'string' THEN {j} "
            f"WHEN 'number' THEN to_jsonb({_py_text_of_number(j)}) "
            f"WHEN 'boolean' THEN (CASE WHEN {j} = 'true'::jsonb THEN "
            f"'\"True\"'::jsonb ELSE '\"False\"'::jsonb END) "
            f"WHEN 'null' THEN '\"None\"'::jsonb "
            f"ELSE to_jsonb({_failing('str() of an array or object', j)}) END)")


# dotted's transforms a comparison can take, by name
_TRANSFORMS = {'int': _int_of, 'float': _float_of, 'str': _str_of}


def _json_spec(marker):
    """
    A `{name}` marker as `{name:json}`: the binding goes to the driver
    as JSON text, cast to jsonb (`sql._render`), so a comparison reads
    its type.
    """
    return '{' + marker[1:-1] + ':json}'


# an expression simple enough to repeat: a column, or an alias's column
_SIMPLE_SQL = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?')


class _Writer:
    """
    What a write does at one node, as SQL: `write(node)` gives the
    node's new jsonb from its SQL. `value` marks the writer that puts
    the written value there whatever the node.
    """

    def __init__(self, write, value=False):
        self.write = write
        self.value = value

    def __call__(self, node):
        return self.write(node)


def _holds_written(value):
    """
    True if `value`, a node dotted built, holds the written value as
    the target's own expression, `core.WRITTEN`.
    """
    if value is core.WRITTEN:
        return True
    if isinstance(value, dict):
        return any(_holds_written(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_written(v) for v in value)
    return False


class _TableRows(collections.namedtuple('_TableRows', 'text alias')):
    """
    The rows of a group of row steps: a query `text` of the table's
    rows, read under the table's own name, `alias`, so the path after
    the group reads each the way it reads a row of the table.
    """


class _Fragment(collections.namedtuple(
        '_Fragment', 'steps test aligned', defaults=(None, False))):
    """
    One op's reading: the `steps` it takes from a node (`_path_step`),
    the `test` what it reaches must pass (a function of the reached
    value's jsonb SQL; None for none), and whether it is aligned (+):
    the path after it yields a null where it reaches nothing.
    """
    kind = None
    branches = ()
    softs = ()

    def filtered(self, test):
        """
        This fragment, as parts, its test and `test` both holding.
        """
        if self.test is None:
            return [self._replace(test=test)]
        first = self.test
        return [self._replace(test=lambda e: f'({first(e)}) AND ({test(e)})')]


class _Group(collections.namedtuple(
        '_Group', 'kind branches cuts softs reads', defaults=(None,))):
    """
    A group's kind, each branch's parts, and per branch whether a cut
    follows it and whether a soft cut does. A negation ('not') `reads`
    its node's children as its branch does: 'key' or 'slot'.
    """

    def filtered(self, test):
        """
        This group, as parts, `test` a filter on each of its matches.
        """
        return [self, _Fragment((), test)]


def _segment_sql(text_expr):
    """
    SQL of a path segment from the SQL text expression `text_expr`: a
    JSON string and a comma, so a path starts another exactly when its
    text does.
    """
    return f"to_jsonb({text_expr}::text)::text || ','"


def _members(expr):
    """
    SQL of jsonb_each over the jsonb `expr`, with ordinality, where it
    is an object; no rows where it isn't.
    """
    return (f"jsonb_each(CASE WHEN jsonb_typeof({expr}) = 'object' THEN {expr} END) "
            f'WITH ORDINALITY')


def _existence(parts):
    """
    `parts` as whether they reach anything reads them: a group's cuts
    and soft cuts change which matches it yields, not whether it yields
    any, so its ways read without them. A negation's branch keeps its
    own: its complement is of what that branch yields.
    """
    out = []
    for part in parts:
        if not part.branches or part.kind == 'not':
            out.append(part)
            continue
        plain = (False,) * len(part.branches)
        out.append(part._replace(branches=[_existence(b) for b in part.branches],
                                 cuts=plain, softs=plain))
    return out


def _items(expr):
    """
    SQL of the (key, value, n) rows of a wildcard of slots over the
    jsonb `expr`, as dotted's `[*]` reads it: an array's elements (the
    key the index), an object's members, a string's characters; none
    for anything else.
    """
    s = f'({expr} #>> {_WHOLE})'
    return (f'SELECT (i.n - 1)::text AS key, i.value, i.n FROM jsonb_array_elements('
            f"CASE WHEN jsonb_typeof({expr}) = 'array' THEN {expr} END) WITH ORDINALITY AS i(value, n) "
            f'UNION ALL SELECT m.key, m.value, m.n FROM {_members(expr)} AS m(key, value, n) '
            f'UNION ALL SELECT (c.n - 1)::text, to_jsonb(substr({s}, c.n, 1)), c.n::bigint '
            f"FROM generate_series(1, CASE WHEN jsonb_typeof({expr}) = 'string' "
            f'THEN char_length({s}) END) AS c(n)')


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
    a `Resolver` subclass to get Postgres JSONB SQL
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

        self._ops = ops
        self._root = root
        self._pool = state
        self.select = self._pool_fragment(raw.get('select'))
        self.where = self._pool_fragment(raw.get('where'))
        self.from_ = self._pool_fragment(raw.get('from'))
        self.lateral = self._pool_fragment(raw.get('lateral'))
        self.unbound = dict(state.unbound)

    def as_update(self, value):
        """
        The write of `value` at the path's matches, as dotted's `update`
        does it, as the SQL fragment of its assignments: `col = expr`
        per column the write touches, for an `UPDATE ... SET`. `value`
        is a literal, hoisted as a bind parameter, or a `Raw` / `Col`
        SQL expression.
        """
        self._state = self._pool
        self._written_raw = value
        try:
            pairs = self._translate_write(
                self._ops, self._root, value, not isinstance(value, Raw))
        finally:
            self._state = None
        text = ', '.join(f'{_quote_ident(name)} = {expr}' for name, expr in pairs)
        return self._pool_fragment(text)

    def _pool_fragment(self, text):
        """
        The fragment of `text`, None for none: its params and unbound
        substitutions narrowed from the translation's pool to the
        markers it uses, so composing fragments doesn't pull in
        bindings they don't need.
        """
        if text is None:
            return None
        params = dict(self._pool.params)
        unbound = dict(self._pool.unbound)
        used = {name for _, name, _, _ in _FORMATTER.parse(text) if name is not None}
        return SQLFragment(text,
                           {k: v for k, v in params.items() if k in used},
                           {k: v for k, v in unbound.items() if k in used})

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

    def _row(self, table, columns):
        """
        The row as one jsonb value: an object of its `columns`, or the
        whole row of `table` when `columns` is None.
        """
        if columns is None:
            return f'to_jsonb({_quote_ident(table)})'
        pairs = ', '.join(f'{_pg_string_literal(name)}, {self._column(name)}'
                          for name in columns)
        return f'jsonb_build_object({pairs})'

    def _select_expr(self, col_ident, prefix):
        """
        Text extraction for a non-empty prefix; the bare column for an
        empty one.
        """
        return self._path_expr(col_ident, prefix, text=bool(prefix))

    def _nested_predicate(self, guard_op, col_ident, prefix):
        """
        A guard inside a JSONB column: on the value at `prefix`, where
        there is one, as dotted compares (`_guard_test`).
        """
        j = self._path_expr(col_ident, prefix, text=False)
        test = self._guard_test(guard_op)
        return self._bound(j, lambda x: f'({x} IS NOT NULL AND {test(x)})')

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

    def _combine_not_true(self, pred):
        """
        The parenthesized predicate IS NOT TRUE: false or null.
        """
        return f'({pred}) IS NOT TRUE'

    # ---- writes -----------------------------------------------------

    def _w_keep(self):
        """
        The writer that leaves the node as it is: a `~` holds the write
        back. dotted's update still raises on a scalar but a string.
        """
        def body(n):
            return (f"CASE WHEN jsonb_typeof({n}) IN ('object', 'array', 'string') THEN {n} "
                    f"ELSE {self._w_error('a scalar', n)} END")
        return _Writer(lambda node: self._bound(node, body))

    def _w_value(self):
        """
        The writer that puts the written value at the node.
        """
        written = self._written
        return _Writer(lambda node: self._jsonb_of(written, node), value=True)

    def _w_test(self, op):
        """
        A filter's or guard's test on a child, as a read takes it: a
        function of the child's jsonb SQL.
        """
        return op.translated(_WRITE_TEST, self)

    def _w_column_test(self, op, col):
        """
        A filter's or guard's test on a column: a guard's as on the
        column itself, a filter's on the column's jsonb.
        """
        return op.translated(_COLUMN_TEST, self, col)

    def _w_column_value(self, col, test):
        """
        A column's new value, the written value, where `test` holds.
        """
        value = self._written_sql(self._written, col)
        if test is None:
            return value
        return f'CASE WHEN {test} THEN {value} ELSE {col} END'

    def _w_column(self, col, child, null, test):
        """
        A jsonb column's new value: `child` written in it, or the node
        dotted builds for a null one, where `test` holds.
        """
        out = (f"CASE WHEN {col} IS NULL OR jsonb_typeof({col}) = 'null' "
               f'THEN {self._jsonb_of(null, col)} ELSE {child(col)} END')
        if test is None:
            return out
        return f'CASE WHEN {test} THEN {out} ELSE {col} END'

    def _w_key(self, kind, value, child, absent, null):
        """
        The writer of a literal key ('key') or slot ('index'): the
        child it names written, or, where it's missing, `absent`, the
        node dotted builds (`core.UNBUILT`: the node stays). An object reads it as
        a member's name; an array an int as an index, from the end where
        negative, a slot past the end appending, a key there failing as
        dotted's update fails.
        """
        def body(n):
            obj = self._w_member(n, str(value), child, absent, null)
            # dotted builds a list of pairs there: a key reads no element
            arr = self._w_error('a key on an array', n)
            if _is_int(value):
                arr = self._w_index(n, value, child, absent, null, kind == 'index')
            return (f"CASE jsonb_typeof({n}) WHEN 'object' THEN {obj} "
                    f"WHEN 'array' THEN {arr} ELSE {self._w_error('a scalar', n)} END")
        return _Writer(lambda node: self._bound(node, body))

    def _w_member(self, n, key, child, absent, null):
        """
        SQL: the object `n` with its member `key` written: `child` in
        it, the node dotted builds for a null one, `absent`
        (`core.UNBUILT`: none) where it's missing.
        """
        path = _pg_path_array([key])
        get = f'({n} -> {_pg_string_literal(key)})'
        if child.value and absent is null:
            return f'jsonb_set({n}, {path}, {child(get)})'
        missing = n if absent is core.UNBUILT else f'jsonb_set({n}, {path}, {self._jsonb_of(absent, n)})'
        return (f'CASE WHEN {get} IS NULL THEN {missing} '
                f'ELSE jsonb_set({n}, {path}, {self._w_put(get, child, null)}) END')

    def _w_index(self, n, i, child, absent, null, appends):
        """
        SQL: the array `n` with its element `i` written, from the end
        where negative. Past the end a slot (`appends`) appends
        `absent`, dotted's built node; before the start, or a key past
        either end, fails as dotted's update does. Nothing changes there
        where `absent` is `core.UNBUILT`: a `~` holds the write back.
        """
        length = f'jsonb_array_length({n})'
        path = _pg_path_array([str(i)])
        get = f'({n} -> ({i}))'
        inside = f'jsonb_set({n}, {path}, {self._w_put(get, child, null)})'
        if absent is core.UNBUILT:
            outside = n
        elif appends and i >= 0:
            outside = f'{n} || jsonb_build_array({self._jsonb_of(absent, n)})'
        else:
            outside = self._w_error('an index out of range', n)
        return f'CASE WHEN {i} >= -{length} AND {i} < {length} THEN {inside} ELSE {outside} END'

    def _w_put(self, e, child, null):
        """
        SQL: the child `e` written: `child` in it, or for a null child the
        node dotted builds, `null`.
        """
        if child.value:
            return child(e)
        return (f"CASE WHEN jsonb_typeof({e}) = 'null' THEN {self._jsonb_of(null, e)} "
                f'ELSE {child(e)} END')

    def _w_each(self, kind, value, first, test, child, null):
        """
        The writer of the children an op picks: those of a wildcard of
        keys ('keys': an object's members) or slots ('items': an array's
        elements, an object's members), or a literal's one ('key',
        'index'), that pass `test`; for `first`, the first child alone,
        where it passes. None is built where they're missing.
        """
        def body(n):
            if kind in ('key', 'index'):
                return self._w_one(n, value, test, child, null)
            obj = self._w_rebuilt(n, 'object', first, test, child, null)
            arr = self._w_rebuilt(n, 'array', first, test, child, null)
            string = self._w_error('a slot wildcard', n)
            if kind == 'keys':
                # a key reaches no element or character: nothing is written
                arr = string = n
            return (f"CASE jsonb_typeof({n}) WHEN 'object' THEN {obj} "
                    f"WHEN 'array' THEN {arr} WHEN 'string' THEN {string} "
                    f"ELSE {self._w_error('a scalar', n)} END")
        return _Writer(lambda node: self._bound(node, body))

    def _w_one(self, n, value, test, child, null):
        """
        SQL: the node `n` with the one child a literal names written
        where it's there and passes `test`, as an object's member or an
        array's element.
        """
        key = str(value)
        get = f'({n} -> {_pg_string_literal(key)})'
        obj = (f'CASE WHEN {get} IS NOT NULL AND ({test(get)}) '
               f'THEN jsonb_set({n}, {_pg_path_array([key])}, {self._w_put(get, child, null)}) '
               f'ELSE {n} END')
        arr = n
        if _is_int(value):
            get = f'({n} -> ({value}))'
            arr = (f'CASE WHEN {get} IS NOT NULL AND ({test(get)}) '
                   f'THEN jsonb_set({n}, {_pg_path_array([key])}, {self._w_put(get, child, null)}) '
                   f'ELSE {n} END')
        return (f"CASE jsonb_typeof({n}) WHEN 'object' THEN {obj} "
                f"WHEN 'array' THEN {arr} ELSE {self._w_error('a scalar', n)} END")

    def _w_rebuilt(self, n, typ, first, test, child, null):
        """
        SQL: the object or array (`typ`) `n` rebuilt, each child the
        test picks written; for `first`, the first child alone, where
        it passes.
        """
        a = self._state.alloc_pattern_alias()
        b = self._state.alloc_pattern_alias()
        picked = test(f'{a}.e') if test else 'TRUE'
        if first:
            # the wildcard's first child alone, then the test on it, as
            # dotted's `[*?&…]` reads
            picked = f'{a}.i = 1 AND ({picked})'
        put = self._w_put(f'{b}.e', child, null)
        each = f'CASE WHEN {b}.s THEN {put} ELSE {b}.e END'
        if typ == 'array':
            return (f'(SELECT COALESCE(jsonb_agg({each} ORDER BY {b}.i), \'[]\'::jsonb) '
                    f'FROM (SELECT {a}.e, {a}.i, {picked} AS s FROM jsonb_array_elements({n}) '
                    f'WITH ORDINALITY AS {a}(e, i)) AS {b})')
        return (f'(SELECT COALESCE(jsonb_object_agg({b}.k, {each}), \'{{{{}}}}\'::jsonb) '
                f'FROM (SELECT {a}.k, {a}.e, {a}.i, {picked} AS s FROM jsonb_each({n}) '
                f'WITH ORDINALITY AS {a}(k, e, i)) AS {b})')

    def _w_append(self, built, unique):
        """
        The writer of an append slot: `built` appended to the array, or
        for `unique` only where the array doesn't hold it.
        """
        def body(n):
            item = self._jsonb_of(built, n)
            arr = f'{n} || jsonb_build_array({item})'
            if unique:
                # held already as Python's `in` tells: True is 1
                a = self._state.alloc_pattern_alias()
                arr = (f'CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({n}) AS {a}(e) '
                       f'WHERE {_py_equal(f"{a}.e", item)}) THEN {n} ELSE {arr} END')
            return (f"CASE jsonb_typeof({n}) WHEN 'array' THEN {arr} "
                    f"ELSE {self._w_error('an append to a non-array', n)} END")
        return _Writer(lambda node: self._bound(node, body))

    def _w_matches(self, ops):
        """
        A write's test that `ops` reach something from a node, as dotted's
        walk reads them: a function of the node's jsonb SQL.
        """
        parts = _existence(self._read_parts(ops))

        def test(n):
            rows = self._path_rows(n, "''", 'ARRAY[]::bigint[]', parts)
            return f'EXISTS ({rows})'
        return test

    def _w_or(self, parts, fallback):
        """
        The writer of a disjunction: each of `parts`, (test, cut,
        writer), writes where its test holds on the node as the parts
        before it left it, a test of True always; after a part a cut
        follows that wrote, no other does. Where none wrote, `fallback`
        writes the node as it was (None: it stays).
        """
        def body(n):
            a = self._state.alloc_pattern_alias()
            cur = f'(SELECT {n} AS o, {n} AS n, FALSE AS s, FALSE AS m) AS {a}'
            prev = a
            for test, cut, writer in parts:
                q = self._state.alloc_pattern_alias()
                r = self._state.alloc_pattern_alias()
                held = 'TRUE' if test is True else test(f'{prev}.n')
                inner = (f'(SELECT {prev}.o, {prev}.n, {prev}.s, {prev}.m, '
                         f'(NOT {prev}.s AND {held}) AS t FROM {cur}) AS {q}')
                cut_sql = 'TRUE' if cut else 'FALSE'
                cur = (f'(SELECT {q}.o, CASE WHEN {q}.t THEN {writer(f"{q}.n")} ELSE {q}.n END AS n, '
                       f'{q}.s OR ({cut_sql} AND {q}.t) AS s, {q}.m OR {q}.t AS m FROM {inner}) AS {r}')
                prev = r
            stays = f'{prev}.o' if fallback is None else fallback(f'{prev}.o')
            return f'(SELECT CASE WHEN {prev}.m THEN {prev}.n ELSE {stays} END FROM {cur})'
        return _Writer(lambda node: self._bound(node, lambda n: self._w_container(n, body(n))))

    def _w_and(self, parts):
        """
        The writer of a conjunction: each of `parts`, (test, creatable,
        cut, writer), writes in turn, up to the first a cut follows whose
        test held, where every part tried either holds its test on the
        node as it was or is `creatable`; else the node stays.
        """
        def body(n):
            stopped = 'FALSE'
            held = []
            cur = n
            for test, creatable, cut, writer in parts:
                t = 'TRUE' if test is True else test(n)
                held.append(f'(({stopped}) OR ({t}) OR {"TRUE" if creatable else "FALSE"})')
                step_stopped = stopped
                cur = self._bound(cur, lambda x, w=writer, st=step_stopped: (
                    f'CASE WHEN NOT ({st}) THEN {w(x)} ELSE {x} END'))
                if cut:
                    stopped = f'({stopped}) OR ({t})'
            ok = ' AND '.join(held) or 'TRUE'
            return self._w_container(n, f'CASE WHEN {ok} THEN {cur} ELSE {n} END')
        return _Writer(lambda node: self._bound(node, body))

    def _w_first(self, cands, fallback):
        """
        The writer of a first-match group: the first of `cands`, (test,
        writer), whose test holds on the node writes; where none does,
        `fallback` (None: the node stays).
        """
        def body(n):
            out = n if fallback is None else fallback(n)
            for test, writer in reversed(cands):
                held = 'TRUE' if test is True else test(n)
                out = f'CASE WHEN {held} THEN {writer(n)} ELSE {out} END'
            return self._w_container(n, out)
        return _Writer(lambda node: self._bound(node, body))

    def _w_container(self, n, written):
        """
        SQL: `written` where the node `n` is a container dotted's update
        writes in, an object, an array or a string; else failing, as
        dotted's update fails at a scalar.
        """
        return (f"CASE WHEN jsonb_typeof({n}) IN ('object', 'array', 'string') THEN {written} "
                f"ELSE {self._w_error('a scalar', n)} END")

    def _bound(self, node, body):
        """
        SQL `body(n)` with `n` the node, bound once where its SQL is
        more than a name, so a writer that reads it often doesn't repeat
        it.
        """
        if _SIMPLE_SQL.fullmatch(node):
            return body(node)
        a = self._state.alloc_pattern_alias()
        return f'(SELECT {body(f"{a}.n")} FROM (SELECT {node} AS n) AS {a})'

    def _w_error(self, what, n):
        """
        SQL jsonb that fails at run time, where dotted's update raises:
        a cast of the message to an int, which Postgres reports with the
        message whole. It reads the node, so the planner can't fold it
        ahead of the CASE that guards it.
        """
        message = _pg_string_literal(f'dotted update: cannot write {what} at a ')
        return f'to_jsonb(({message} || jsonb_typeof({n}))::int)'

    def _jsonb_of(self, value, n):
        """
        SQL jsonb of a node dotted built, or of the written value: a
        literal as a hoisted JSON parameter; the written value as the
        target's expression where `core.WRITTEN` stands for it; a
        `BuildError` failing at run time, `n` the node it is read at.
        """
        if isinstance(value, core.BuildError):
            return self._w_error(f'what dotted can\'t build ({value.message})', n)
        if value is core.WRITTEN:
            return f'to_jsonb({self._raw_written()})'
        if not _holds_written(value):
            return self._jsonb_literal(value)
        if isinstance(value, dict):
            pairs = ', '.join(f'{_pg_string_literal(str(k))}, {self._jsonb_of(v, n)}'
                              for k, v in value.items())
            return f'jsonb_build_object({pairs})'
        return f'jsonb_build_array({", ".join(self._jsonb_of(v, n) for v in value)})'

    def _jsonb_literal(self, value):
        """
        A literal as a hoisted JSON text parameter cast to jsonb.
        """
        try:
            text = json.dumps(value)
        except (TypeError, ValueError) as e:
            raise TranslationError(f'a value a write can\'t give as JSON: {value!r} ({e})')
        return f'{self._hoist_value(text)}::jsonb'

    def _written_sql(self, value, col):
        """
        SQL of the written value for a column itself: a literal as a
        hoisted parameter (a dict or list as jsonb), or the target's own
        expression.
        """
        if value is core.WRITTEN:
            return self._raw_written()
        if isinstance(value, core.BuildError):
            raise TranslationError(f'dotted can\'t build it: {value.message}')
        if value is None:
            return 'NULL'
        if isinstance(value, (dict, list, tuple)):
            return self._jsonb_literal(value)
        return self._hoist_value(value)

    def _raw_written(self):
        """
        The written `Raw` / `Col`'s SQL.
        """
        return self._written_raw.sql

    # ---- rows of a table search -------------------------------------

    def _table_from(self, names):
        """
        The table's name, each part quoted where it needs it; None for
        an unnamed table, whose FROM is the caller's.
        """
        if not names:
            return None
        return '.'.join(_quote_ident(name) for name in names)

    def _table_rows(self, names, preds, offset, limit):
        """
        `SELECT * FROM` the table, `WHERE` the predicates, `OFFSET` /
        `LIMIT` the window. An unnamed table has none to select from.
        """
        if not names:
            raise TranslationError(
                'a group of row steps selects its rows from the table — '
                'name it (`books[(...)]`)'
            )
        text = f'SELECT * FROM {self._table_from(names)}'
        if preds:
            text += f' WHERE {self._all_of(preds)}'
        if offset is not None:
            text += f' OFFSET {offset}'
        if limit is not None:
            text += f' LIMIT {limit}'
        return _TableRows(text, _quote_ident(names[-1]))

    def _table_rows_kept(self, rows, pred, gates):
        """
        The rows `WHERE` the predicate, and an `EXISTS` / `NOT EXISTS`
        per gate.
        """
        conds = [] if pred is None else [pred]
        for hits, hit, exists in gates:
            test = f'SELECT 1 FROM ({hits.text}) AS {hits.alias}'
            if hit is not None:
                test += f' WHERE {hit}'
            conds.append(f'EXISTS ({test})' if exists else f'NOT EXISTS ({test})')
        if not conds:
            return rows
        return _TableRows(f'SELECT * FROM ({rows.text}) AS {rows.alias} '
                     f'WHERE {self._all_of(conds)}', rows.alias)

    def _table_rows_first(self, rows):
        """
        The rows' first: `LIMIT 1`.
        """
        return _TableRows(f'SELECT * FROM ({rows.text}) AS {rows.alias} LIMIT 1',
                     rows.alias)

    def _table_rows_union(self, parts):
        """
        The parts' rows `UNION ALL`, each kept as often as it comes.
        """
        if len(parts) == 1:
            return parts[0]
        return _TableRows(' UNION ALL '.join(f'({part.text})' for part in parts),
                     parts[0].alias)

    def _table_rows_from(self, rows):
        """
        The rows as a derived table under the table's own name.
        """
        return f'({rows.text}) AS {rows.alias}'

    def _pattern(self, col_ident, prefix, ops):
        """
        A pattern path's fragments, read a step at a time as dotted's
        walk reads it (`_path_rows`): `where` whether it reaches
        anything (`_existence`), `lateral` its matches, a `value` each,
        and `select` the lateral's value. The engine picks which to compose —
        filter-style from `where` or extract-style from `lateral` +
        `select`.
        """
        parts = [self._head(prefix)] + self._read_parts(ops)
        start = (col_ident, "''", 'ARRAY[]::bigint[]')
        a = self._state.alloc_pattern_alias()
        b = self._state.alloc_pattern_alias()
        where = f'EXISTS ({self._path_rows(*start, _existence(parts))})'
        rows = self._path_rows(*start, parts)
        return {'select': f'{a}.value', 'where': where,
                'lateral': f'LATERAL (SELECT {b}.value FROM ({rows}) AS {b}) AS {a}(value)'}

    def _rows_where(self, rows, pred, columns=('value',)):
        """
        SQL query of the rows of `rows`, whose columns are `columns`,
        where `pred` holds.
        """
        alias = self._state.alloc_pattern_alias()
        picked = ', '.join(f'{alias}.{c}' for c in columns)
        return (f'SELECT {picked} FROM ({rows}) AS {alias}({", ".join(columns)}) '
                f'WHERE {pred}')

    # ---- Path-tracking rows -----------------------------------------
    #
    # A pattern path walks a step at a time, each row a (path, ord,
    # value): `path` the segments from the column as text, each a JSON
    # string and a comma, so that one path starts another exactly when
    # its text does; `ord` the bigint[] that orders the rows as dotted
    # reaches them.

    _PATH_COLUMNS = ('path', 'ord', 'value')

    def _path_rows(self, v, p, o, parts):
        """
        SQL query of the (path, ord, value) rows `parts` reach from the
        jsonb `v`, whose own path and ord are `p` and `o`.
        """
        query = f'SELECT {p} AS path, {o} AS ord, {v} AS value'
        for i, part in enumerate(parts):
            if part.branches:
                return self._path_lateral(
                    query, lambda a, part=part, rest=parts[i + 1:]: self._path_group_rows(
                        f'{a}.value', f'{a}.path', f'{a}.ord', part, rest))
            for step in part.steps:
                query = self._path_step(query, step)
            if part.test is not None:
                query = self._path_tested(query, part.test)
            if part.aligned:
                return self._path_aligned(query, parts[i + 1:])
        return query

    def _path_row(self, a):
        """
        The row `a` as a (path, ord, value) query of its own.
        """
        return f'SELECT {a}.path AS path, {a}.ord AS ord, {a}.value AS value'

    def _path_first(self, query):
        """
        The first of the rows of `query`, in dotted's order.
        """
        a = self._state.alloc_pattern_alias()
        return f'SELECT {a}.path, {a}.ord, {a}.value FROM ({query}) AS {a} ORDER BY {a}.ord LIMIT 1'

    def _path_lateral(self, query, under):
        """
        The (path, ord, value) rows `under(alias)` gives at each row of
        `query`, `alias` naming that row.
        """
        a = self._state.alloc_pattern_alias()
        b = self._state.alloc_pattern_alias()
        rows = under(a)
        return (f'SELECT {b}.path, {b}.ord, {b}.value FROM ({query}) AS {a} '
                f'CROSS JOIN LATERAL ({rows}) AS {b}')

    def _path_tested(self, query, test):
        """
        The rows of `query` whose value passes `test`, a function of the
        value's jsonb SQL.
        """
        a = self._state.alloc_pattern_alias()
        return (f'SELECT {a}.path, {a}.ord, {a}.value FROM ({query}) AS {a} '
                f'WHERE {test(f"{a}.value")}')

    def _path_aligned(self, query, rest):
        """
        The rows `rest` reaches from each row of `query`, an aligned (+)
        op's matches: where it reaches nothing, the match yields one
        null, as dotted's aligned op does.
        """
        a = self._state.alloc_pattern_alias()
        b = self._state.alloc_pattern_alias()
        rows = self._path_rows(f'{a}.value', f'{a}.path', f'{a}.ord', rest)
        return (f'SELECT COALESCE({b}.path, {a}.path) AS path, COALESCE({b}.ord, {a}.ord) AS ord, '
                f"COALESCE({b}.value, 'null'::jsonb) AS value FROM ({query}) AS {a} "
                f'LEFT JOIN LATERAL ({rows}) AS {b} ON TRUE')

    def _path_step(self, query, step):
        """
        The rows one step reaches from each row of `query`, each node
        read by its own type: ('key', name) an object's member; ('at',
        n) an array's element or a string's character, from the end
        where negative; ('keys',) an object's members; ('items',) an
        array's elements, an object's values or a string's characters;
        ('descend',) every value under it at any depth by keys, as
        dotted's ** reads; ('list', test) an array filtered by `test`,
        as one value, as dotted's slice filter reads; ('map', fn) the
        value through `fn`; ('none',) nothing.
        """
        a = self._state.alloc_pattern_alias()
        kind = step[0]
        if kind == 'none':
            return f'SELECT {a}.path, {a}.ord, {a}.value FROM ({query}) AS {a} WHERE FALSE'
        if kind == 'map':
            # the value is tested, so a transform that fails, fails even
            # where only an EXISTS reads the rows
            x = self._state.alloc_pattern_alias()
            return (f'SELECT {a}.path, {a}.ord, {x}.value FROM ({query}) AS {a} '
                    f'CROSS JOIN LATERAL (SELECT {step[1](f"{a}.value")} AS value) AS {x} '
                    f'WHERE {x}.value IS NOT NULL')
        if kind == 'key':
            key = _pg_string_literal(step[1])
            return (f'SELECT {a}.path || {_segment_sql(key)} AS path, {a}.ord, '
                    f'{a}.value -> {key} AS value FROM ({query}) AS {a} '
                    f'WHERE ({a}.value -> {key}) IS NOT NULL')
        if kind == 'at':
            return self._path_at(query, a, int(step[1]))
        if kind == 'list':
            return self._path_list(query, a, step)
        e = self._state.alloc_pattern_alias()
        if kind == 'keys':
            return (f'SELECT {a}.path || {_segment_sql(f"{e}.key")} AS path, '
                    f'{a}.ord || {e}.n AS ord, {e}.value AS value FROM ({query}) AS {a} '
                    f'CROSS JOIN LATERAL {_members(f"{a}.value")} AS {e}(key, value, n)')
        if kind == 'items':
            return (f'SELECT {a}.path || {_segment_sql(f"{e}.key")} AS path, '
                    f'{a}.ord || {e}.n AS ord, {e}.value AS value FROM ({query}) AS {a} '
                    f'CROSS JOIN LATERAL ({_items(f"{a}.value")}) AS {e}(key, value, n)')
        # descend: by keys only, the node itself not among them; the ord
        # orders the values in pre-order, as dotted's ** yields them
        d = self._state.alloc_pattern_alias()
        f = self._state.alloc_pattern_alias()
        g = self._state.alloc_pattern_alias()
        return (f'SELECT {g}.path, {g}.ord, {g}.value FROM ({query}) AS {a} '
                f'CROSS JOIN LATERAL (WITH RECURSIVE {d}(path, ord, value) AS ('
                f'SELECT {a}.path || {_segment_sql(f"{e}.key")}, {a}.ord || {e}.n, {e}.value '
                f'FROM {_members(f"{a}.value")} AS {e}(key, value, n) '
                f'UNION ALL SELECT {d}.path || {_segment_sql(f"{f}.key")}, {d}.ord || {f}.n, '
                f'{f}.value FROM {d} CROSS JOIN LATERAL {_members(f"{d}.value")} '
                f'AS {f}(key, value, n)) SELECT {d}.path, {d}.ord, {d}.value FROM {d}) AS {g}')

    def _path_at(self, query, a, n):
        """
        `_path_step`'s ('at', n): the element of an array, or character
        of a string, at `n`, the path's segment its index from the
        start.
        """
        x = self._state.alloc_pattern_alias()
        value = f'{a}.value'
        s = f'({value} #>> {_WHOLE})'
        size = (f"(CASE jsonb_typeof({value}) WHEN 'array' THEN jsonb_array_length({value}) "
                f"WHEN 'string' THEN char_length({s}) END)")
        index = str(n) if n >= 0 else f'({size} + ({n}))'
        inside = f'{index} < {size}' if n >= 0 else f'{index} >= 0 AND {index} < {size}'
        found = (f"(CASE WHEN {inside} THEN "
                 f"(CASE jsonb_typeof({value}) WHEN 'array' THEN {value} -> ({index}) "
                 f'ELSE to_jsonb(substr({s}, {index} + 1, 1)) END) END)')
        return (f'SELECT {a}.path || {_segment_sql(f"({index})::text")} AS path, {a}.ord, '
                f'{x}.value FROM ({query}) AS {a} CROSS JOIN LATERAL (SELECT {found} AS value) AS {x} '
                f'WHERE {x}.value IS NOT NULL')

    def _path_list(self, query, a, step):
        """
        `_path_step`'s ('list', test, keeps_scalar): an array, its
        elements filtered by `test`, as one value, as dotted's slice
        filter reads; the path its node's. dotted filters an object's
        keys as its items: where the filter keeps no scalar
        (`keeps_scalar`), that's `{}`; otherwise dotted builds garbage
        from the keys, and on anything else it raises: those fail at run
        time, in the row's own test, so that an EXISTS over it fails too.
        """
        _, test, keeps_scalar = step
        e = self._state.alloc_pattern_alias()
        value = f'{a}.value'
        kept = (f'(SELECT COALESCE(jsonb_agg({e}.value ORDER BY {e}.n) '
                f"FILTER (WHERE {test(f'{e}.value')}), '[]'::jsonb) "
                f'FROM jsonb_array_elements({value}) WITH ORDINALITY AS {e}(value, n))')
        readable = f"jsonb_typeof({value}) = 'array'"
        listed = f"(CASE jsonb_typeof({value}) WHEN 'array' THEN {kept} ELSE '{{{{}}}}'::jsonb END)"
        if not keeps_scalar:
            readable = f"jsonb_typeof({value}) IN ('array', 'object')"
        fails = _failing('a slice filter on what it can\'t filter', value)
        return (f'SELECT {a}.path, {a}.ord, {listed} AS value FROM ({query}) AS {a} '
                f'WHERE (CASE WHEN {readable} THEN TRUE ELSE {fails}::boolean END)')

    def _path_group_rows(self, v, p, o, group, rest):
        """
        The (path, ord, value) rows a group at the jsonb `v` reaches,
        `rest` the parts after it: what `_rows_at` reads, with paths.
        Each branch's ord takes its index after `o`, so the rows order
        as dotted reaches them.
        """
        if group.kind == 'not':
            return self._path_lateral(self._complement(v, p, o, group), lambda b: self._path_rows(
                f'{b}.value', f'{b}.path', f'{b}.ord', rest))
        if group.kind == 'first':
            firsts = ' UNION ALL '.join(
                self._path_rows(v, p, f'{o} || {j}::bigint', branch)
                for j, branch in enumerate(group.branches))
            a = self._state.alloc_pattern_alias()
            first = (f'SELECT {a}.path, {a}.ord, {a}.value FROM ({firsts}) AS {a} '
                     f'ORDER BY {a}.ord LIMIT 1')
            return self._path_lateral(first, lambda b: self._path_rows(
                f'{b}.value', f'{b}.path', f'{b}.ord', rest))
        ways = [list(branch) + list(rest) for branch in group.branches]
        rows = [self._path_rows(v, p, f'{o} || {j}::bigint', way) for j, way in enumerate(ways)]
        if any(group.softs):
            folded = self._uncovered(rows, group)
            if group.kind == 'or':
                return folded
            held = self._combine_and([f'EXISTS ({r})' for r in rows])
            return self._rows_where(folded, held, self._PATH_COLUMNS)
        if group.kind == 'and':
            held = self._combine_and([f'EXISTS ({r})' for r in rows])
            return self._rows_where(' UNION ALL '.join(rows), held, self._PATH_COLUMNS)
        queries = []
        for j, r in enumerate(rows):
            cut = [rows[k] for k in range(j) if group.cuts[k]]
            if cut:
                r = self._rows_where(r, self._combine_and(
                    [f'NOT EXISTS ({c})' for c in cut]), self._PATH_COLUMNS)
            queries.append(r)
        return ' UNION ALL '.join(queries)

    def _complement(self, v, p, o, group):
        """
        The (path, ord, value) rows of a negation's complement at the
        jsonb `v`: the largest subtrees of `v` that hold no match of its
        branch. A recursive query walks down from `v` through the
        children some match runs through, none ending at; the
        complement is the children of what it reaches that no match is
        at or under. At `v` the children are read as the branch reads
        them, an object's members for a key and an array's elements too
        for a slot; below, a match runs only through what its own
        segments read, so they are read by what each node is.
        """
        alloc = self._state.alloc_pattern_alias
        m, t, ch, x, k = (alloc() for _ in range(5))
        matched = self._path_rows(v, p, o, group.branches[0])
        tops = '' if group.reads == 'slot' else f' AND {t}.path <> {p}'

        def children():
            e = alloc()
            i = alloc()
            return (f'SELECT {t}.path || {_segment_sql(f"{e}.key")} AS path, '
                    f'{t}.ord || {e}.n AS ord, {e}.value AS value '
                    f'FROM {_members(f"{t}.value")} AS {e}(key, value, n) '
                    f'UNION ALL SELECT {t}.path || {_segment_sql(f"({i}.n - 1)::text")}, '
                    f'{t}.ord || {i}.n, {i}.value FROM jsonb_array_elements(CASE WHEN '
                    f"jsonb_typeof({t}.value) = 'array'{tops} THEN {t}.value END) "
                    f'WITH ORDINALITY AS {i}(value, n)')

        under = f'EXISTS (SELECT 1 FROM {m} WHERE starts_with({m}.path, {ch}.path))'
        return (
            f'SELECT {k}.path, {k}.ord, {k}.value FROM (WITH RECURSIVE {m} AS ({matched}), '
            f'{t}(path, ord, value) AS (SELECT {p}, {o}, {v} '
            f'UNION ALL SELECT {ch}.path, {ch}.ord, {ch}.value FROM {t} '
            f'CROSS JOIN LATERAL ({children()}) AS {ch} WHERE {under} '
            f'AND NOT EXISTS (SELECT 1 FROM {m} WHERE {m}.path = {ch}.path)) '
            f'SELECT {ch}.path, {ch}.ord, {ch}.value FROM {t} '
            f'CROSS JOIN LATERAL ({children()}) AS {ch} WHERE NOT {under}) AS {k}')

    def _uncovered(self, rows, group):
        """
        The (path, ord, value) rows of the group's ways, `rows`, that a
        soft cut didn't cover, in dotted's order: a fold over them all
        in ord order (a recursive query), dropping one whose path
        overlaps the path of one a soft-cut branch kept, keeping a
        soft-cut branch's own as they pass, and after a branch a cut
        follows keeps one, dropping the branches after it.
        """
        alloc = self._state.alloc_pattern_alias
        tags = []
        for j, rows_j in enumerate(rows):
            a = alloc()
            tags.append(f'SELECT {a}.path, {a}.ord, {a}.value, {j} AS branch, '
                        f'{str(group.cuts[j]).upper()} AS cut, '
                        f'{str(group.softs[j]).upper()} AS soft FROM ({rows_j}) AS {a}')
        tagged = ' UNION ALL '.join(tags)
        u, r, f, c, s, k, q = (alloc() for _ in range(7))
        covered = (f'EXISTS (SELECT 1 FROM unnest({f}.kept) AS {q}(p) '
                   f'WHERE starts_with({r}.path, {q}.p) OR starts_with({q}.p, {r}.path))')
        return (
            f'SELECT {k}.path, {k}.ord, {k}.value FROM (WITH RECURSIVE '
            f'{r} AS (SELECT row_number() OVER (ORDER BY {u}.ord) AS n, {u}.* '
            f'FROM ({tagged}) AS {u}), '
            f'{f}(n, kept, branch, cut, found, stop, take, path, ord, value) AS ('
            f'SELECT 0::bigint, ARRAY[]::text[], -1, FALSE, FALSE, FALSE, FALSE, '
            f'NULL::text, NULL::bigint[], NULL::jsonb '
            f'UNION ALL SELECT {r}.n, '
            f'CASE WHEN {c}.take AND {r}.soft THEN {f}.kept || {r}.path ELSE {f}.kept END, '
            f'{r}.branch, {r}.cut, '
            f'CASE WHEN {r}.branch <> {f}.branch THEN {c}.take ELSE {f}.found OR {c}.take END, '
            f'{c}.stop, {c}.take, {r}.path, {r}.ord, {r}.value '
            f'FROM {f} JOIN {r} ON {r}.n = {f}.n + 1 '
            f'CROSS JOIN LATERAL (SELECT {s}.stop, NOT {s}.stop AND NOT {covered} AS take '
            f'FROM (SELECT {f}.stop OR ({r}.branch <> {f}.branch AND {f}.cut AND {f}.found) '
            f'AS stop) AS {s}) AS {c}) '
            f'SELECT {f}.path, {f}.ord, {f}.value FROM {f} WHERE {f}.take) AS {k}')

    # ---- Reading a pattern path ---------------------------------------

    def _head(self, prefix):
        """
        The literal prefix as a fragment of steps: a key's name, an
        int's index.
        """
        steps = []
        for kind, value in prefix:
            if kind != 'lit':
                raise TranslationError(
                    'dynamic path segments (references) combined with '
                    'patterns are not supported yet'
                )
            steps.append(('at', value) if _is_int(value) else ('key', value))
        return _Fragment(tuple(steps))

    def _read_parts(self, ops):
        """
        A pattern path's ops as parts: a `_Fragment` per op, a `_Group`
        per group. Each op is translated once, so a value it hoists is
        hoisted once, however often its test is read.
        """
        return [part for op in ops for part in op.translated(_PARTS, self)]

    # ---- Comparing, as dotted compares ------------------------------

    def _compared(self, j, pred, val):
        """
        SQL boolean: the jsonb `j`, a value that is there, compares to a
        guard's or filter's value `val` by `pred` as dotted compares
        (see `_WHOLE`'s section).
        """
        return val.translated(_COMPARED, self, j, pred)

    def _compared_value(self, j, pred, value):
        """
        `_compared` for a literal `value`: its Python type picks the
        values of `j` it can equal or order against.
        """
        if pred is predicates.NE:
            return f'NOT {self._compared_value(j, predicates.EQ, value)}'
        if value is None:
            return f"(jsonb_typeof({j}) = 'null')" if pred is predicates.EQ else 'FALSE'
        if isinstance(value, (bool, int, float, decimal.Decimal)):
            return self._compared_number(j, pred, value)
        if isinstance(value, bytes):
            # JSON holds no bytes: nothing there equals or orders with them
            return 'FALSE'
        if not isinstance(value, str):
            raise TranslationError(f'unsupported value in a comparison: {value!r}')
        text = f'({j} #>> {_WHOLE})'
        if pred is not predicates.EQ:
            text += ' COLLATE "C"'
        return (f"(CASE WHEN jsonb_typeof({j}) = 'string' "
                f'THEN {text} {_SQL_OPS[pred]} {self._hoist_value(value)} ELSE FALSE END)')

    def _compared_number(self, j, pred, value):
        """
        `_compared` for a number (a bool as 1 or 0): a number compares as
        a number, and a boolean as 1 or 0, as Python's does.
        """
        number = int(value) if isinstance(value, bool) else value
        on_true = pred.pred(1, number)
        on_false = pred.pred(0, number)
        boolean = ''
        if on_true and on_false:
            boolean = "WHEN 'boolean' THEN TRUE "
        elif on_true:
            boolean = f"WHEN 'boolean' THEN {j} = 'true'::jsonb "
        elif on_false:
            boolean = f"WHEN 'boolean' THEN {j} = 'false'::jsonb "
        return (f"(CASE jsonb_typeof({j}) WHEN 'number' THEN ({j})::numeric "
                f"{_SQL_OPS[pred]} ({_numeric_sql(number)}) {boolean}ELSE FALSE END)")

    def _compared_jsonb(self, j, pred, b):
        """
        `_compared` for a value known only at build, the jsonb `b` (a
        substitution's binding, as JSON): both read by their own types.
        A number and a boolean compare as numbers, strings in code-point
        order, other values of one type by jsonb equality; arrays don't
        order as Python's lists do, so ordering them fails at run time.
        """
        if pred is predicates.NE:
            return f'NOT {self._compared_jsonb(j, predicates.EQ, b)}'
        numbers = (f"jsonb_typeof({j}) IN ('number', 'boolean') "
                   f"AND jsonb_typeof({b}) IN ('number', 'boolean')")
        # dotted raises on an array or object bound to a guard's value
        container = (f"WHEN jsonb_typeof({b}) IN ('array', 'object') "
                     f"THEN {_failing('an array or object bound to a value', b)}::boolean")
        op = _SQL_OPS[pred]
        if pred is predicates.EQ:
            return f'(CASE {container} ELSE {_py_equal(j, b)} END)'
        return (f'(CASE {container} WHEN {numbers} THEN {_number_of(j)} {op} {_number_of(b)} '
                f"WHEN jsonb_typeof({j}) = 'string' AND jsonb_typeof({b}) = 'string' "
                f'THEN ({j} #>> {_WHOLE}) COLLATE "C" {op} ({b} #>> {_WHOLE}) COLLATE "C" '
                f'ELSE FALSE END)')

    def _fullmatches(self, j, pattern):
        """
        SQL boolean: `pattern`, a Python regex, fullmatches str() of the
        jsonb `j`, as dotted's regex matches: a string itself, a number
        as Python writes it, `True` / `False`, `None`. str() of an array
        or object is Python's repr, which starts with `[` or `{`: a regex
        that can't start so matches none; one that may fails at run
        time, as jsonb keeps no object's key order to write its repr by.
        """
        compiled = re.compile(pattern)
        are = self._hoist_value(_are_regex(pattern))

        def static(text):
            return 'TRUE' if compiled.fullmatch(text) else 'FALSE'
        boolean = static('True')
        if static('True') != static('False'):
            boolean = f"{j} = 'true'::jsonb" if static('True') == 'TRUE' else f"{j} = 'false'::jsonb"
        containers = 'FALSE'
        if core.may_start_with(pattern, '[') or core.may_start_with(pattern, '{'):
            containers = f"{_failing('a regex on the repr of an array or object', j)}::boolean"
        return (f"(CASE jsonb_typeof({j}) WHEN 'string' THEN ({j} #>> {_WHOLE}) ~ {are} "
                f"WHEN 'number' THEN {_py_text_of_number(j)} ~ {are} "
                f"WHEN 'boolean' THEN {boolean} WHEN 'null' THEN {static('None')} "
                f'ELSE {containers} END)')

    def _via_transforms(self, j, transforms):
        """
        SQL jsonb of `j` through dotted's transforms: `int`, `float` and
        `str`, each without arguments. Any other raises.
        """
        for transform in transforms:
            fn = _TRANSFORMS.get(transform.name)
            if fn is None or transform.params:
                raise TranslationError(f'transform not supported in sqlize: {transform.operator()}')
            j = fn(j)
        return j

    def _guard_test(self, guard_op):
        """
        A guard's test, a function of the jsonb SQL of a value that is
        there: its transforms, then its comparison.
        """
        def test(e):
            value = self._via_transforms(e, guard_op.guard_transforms)
            return self._bound(value, lambda x: self._compared(x, guard_op.pred_op, guard_op.guard))
        return test

    def _filters_test(self, filters):
        """
        A run of filters' test, all of which must pass: a function of
        the jsonb SQL of the node they filter.
        """
        def test(e):
            return self._all_of([f.translated(_FILTER_TEST, self, e) for f in filters])
        return test

    def _filter_holds(self, f, e, pred):
        """
        SQL boolean: the key-value filter `f`'s comparison, by `pred`,
        holds for a value its key reaches in the node `e`, an object.
        """
        if f.key.is_packed():
            raise TranslationError('a packed filter key not supported')
        values = self._filter_values(e, self._key_parts(f.key))
        a = self._state.alloc_pattern_alias()
        value = self._via_transforms(f'{a}.value', f.guard_transforms)
        test = self._bound(value, lambda x: self._compared(x, pred, f.val))
        return (f"(jsonb_typeof({e}) = 'object' "
                f'AND EXISTS (SELECT 1 FROM ({values}) AS {a} WHERE {test}))')

    def _filter_values(self, e, parts):
        """
        The rows of the values a filter key's `parts` reach from the
        node `e`, as dotted's filter key reads them: a key on the way
        reaches its first match alone, the last key every match, a slot
        what it reaches.
        """
        query = f"SELECT ''::text AS path, ARRAY[]::bigint[] AS ord, {e} AS value"
        for i, part in enumerate(parts):
            step, keyed = part.translated(_FILTER_STEP, self)
            if keyed and i < len(parts) - 1:
                query = self._path_lateral(
                    query, lambda a, step=step: self._path_first(
                        self._path_step(self._path_row(a), step)))
                continue
            query = self._path_step(query, step)
        return query

    # ---- scalar (non-pattern) path helpers --------------------------

    def _segment_value(self, value, index):
        """
        A literal segment keeps an int: it reads an array's element or a
        string's character, never an object's member.
        """
        return value if _is_int(value) else str(value)

    def _path_expr(self, col_ident, prefix, text):
        """
        The Postgres expression for `prefix` inside the column, as
        dotted reads it: keys alone as a `#>` / `#>>` path; an int as an
        array's element or a string's character (`_at`); a reference's
        value as a key. As text (`text`) it is `#>>`: a string itself,
        anything else its JSON.
        """
        if not prefix:
            return col_ident
        if all(kind == 'lit' and isinstance(value, str) for kind, value in prefix):
            op = '#>>' if text else '#>'
            return f'{col_ident} {op} {_pg_path_array([value for _, value in prefix])}'
        expr = col_ident
        last = len(prefix) - 1
        for i, (kind, value) in enumerate(prefix):
            if kind == 'lit' and not isinstance(value, str):
                expr = self._at(expr, value)
                continue
            # a key by name, or by a reference's value; text the last as `->>`
            arrow = '->>' if text and i == last else '->'
            rhs = _pg_string_literal(value) if kind == 'lit' else f'({value})'
            expr = f'{expr} {arrow} {rhs}'
        if text and prefix[-1][0] == 'lit' and not isinstance(prefix[-1][1], str):
            return f'({expr} #>> {_WHOLE})'
        return expr

    def _at(self, expr, n):
        """
        SQL jsonb of the jsonb `expr` at the int `n`, as dotted reads it:
        an array's element, a string's character (from the end where
        negative), else NULL.
        """
        def body(x):
            s = f'({x} #>> {_WHOLE})'
            index = str(n) if n >= 0 else f'(char_length({s}) + ({n}))'
            inside = f'{index} < char_length({s})'
            if n < 0:
                inside = f'{index} >= 0 AND {inside}'
            char = (f'(CASE WHEN {inside} '
                    f'THEN to_jsonb(substr({s}, {index} + 1, 1)) END)')
            return (f"(CASE jsonb_typeof({x}) WHEN 'array' THEN {x} -> ({n}) "
                    f"WHEN 'string' THEN {char} END)")
        return self._bound(expr, body)

    def _scalar_predicate(self, guard_op, col_ident):
        """
        Build a WHERE predicate for a guard on a scalar (non-JSONB)
        column: SQL's own comparison of its type, but `!=` holding for a
        NULL, as Python's `None != x` does, and a regex fullmatching.
        """
        if guard_op.guard_transforms:
            raise TranslationError(
                'guard transforms on scalar columns not supported in v1'
            )
        return guard_op.guard.translated(
            _SCALAR_PREDICATE, self, col_ident, guard_op.pred_op)

def _is_int(value):
    """
    True for an int, not a bool.
    """
    return isinstance(value, int) and not isinstance(value, bool)


class _ColumnTest(Hooks):
    """
    A filter's or guard's test on a column a write's first op names: a
    guard's as on the column itself, a filter's on its jsonb.
    """

    def other(self, op, t, col):
        raise TranslationError(f'not supported in a write: {op}')

    def value_guard(self, op, t, col):
        return t._scalar_predicate(op, col)

    def filter_wrap(self, op, t, col):
        return t._w_test(op)(col)


_COLUMN_TEST = _ColumnTest()


class _WriteTest(Hooks):
    """
    A filter's or guard's own test on a child a write reaches, a
    function of the child's jsonb SQL.
    """

    def other(self, op, t):
        raise TranslationError(f'not supported in a write: {op}')

    def filter_wrap(self, op, t):
        return t._filters_test(op.filters)

    def value_guard(self, op, t):
        return t._guard_test(op)


_WRITE_TEST = _WriteTest()


# ---- Reading ops ----------------------------------------------------

class _Parts(Hooks):
    """
    An op of a pattern path as parts: a `_Fragment`, or a `_Group`.
    """

    def other(self, op, t):
        raise TranslationError(
            f'op not supported in pattern paths: {type(op).__name__}'
        )

    def transform_wrap(self, op, t):
        # what the op reaches goes through the transforms, and the path
        # after it reads that, as dotted's get does
        parts = op.inner.translated(self, t)
        return parts + [_Fragment((('map', lambda v: t._via_transforms(v, op.transforms)),))]

    def key(self, op, t):
        # an Attr reads as a key
        return _accessed(op, t, True)

    def slot(self, op, t):
        return _accessed(op, t, False)

    def slot_special(self, op, t):
        # an append slot is past the end: there is never a value there
        return [_Fragment((('none',),))]

    def recursive(self, op, t):
        plain = (op.accessors is None and op.inner.translated(core.IS_WILDCARD)
                 and (op.depth_start, op.depth_stop, op.depth_step) == (None, None, None))
        if not plain:
            raise TranslationError('a ** with depths or accessors, or of a key, not supported yet')
        test = t._filters_test(op.filters) if op.filters else None
        return [_Fragment((('descend',),), test, op.is_aligned())]

    def slice_filter(self, op, t):
        # the filtered list as one value, as dotted's slice filter reads;
        # whether its filters keep a scalar, as they do an object's keys
        keeps_scalar = all(f.is_filtered(0) for f in op.filters)
        step = ('list', t._filters_test(op.filters), keeps_scalar)
        return [_Fragment((step,), None, op.is_aligned())]

    def filter_wrap(self, op, t):
        parts = op.inner.translated(self, t)
        return parts[:-1] + parts[-1].filtered(t._filters_test(op.filters))

    def value_guard(self, op, t):
        # a guard on a group, (a, b)?=1, tests each of its matches
        parts = op.inner.translated(self, t)
        return parts[:-1] + parts[-1].filtered(t._guard_test(op))

    def group(self, op, kind, branches, cuts, softs, t):
        return [_Group(kind, [t._read_parts(b) for b in branches], cuts, softs)]

    def not_group(self, op, t):
        reads = t._access_kinds(op.inner)[0]
        return [_Group('not', [t._read_parts(op.inner)], (False,), (False,), reads)]


def _accessed(op, t, is_key):
    """
    A Key's or Slot's parts: its step; for a first wildcard, `*?`, the
    first-match group of the wildcard, its first match alone.
    """
    part = _Fragment((op.op.translated(_PATH_STEP, t, is_key),), None, op.is_aligned())
    if not op.op.translated(core.IS_FIRST):
        return [part]
    return [_Group('first', [[part]], (False,), (False,))]


class _PathStep(Hooks):
    """
    A Key's or Slot's matcher as the step it takes (`_path_step`): a
    string's ('key', name), an int's ('at', n), a wildcard's every
    member ('keys',) for a key or ('items',) for a slot. A key of any
    other type reaches nothing: no JSON member is named by one.
    """

    def other(self, m, t, is_key):
        raise TranslationError(f'unsupported matcher in pattern path: {type(m).__name__}')

    def const(self, m, t, is_key):
        if _is_int(m.value):
            return ('at', m.value)
        if isinstance(m.value, str):
            return ('key', m.value)
        return ('none',)

    def wildcard(self, m, t, is_key):
        return ('keys',) if is_key else ('items',)

    def wildcard_first(self, m, t, is_key):
        # the first of the wildcard's: `_accessed` groups it
        return self.wildcard(m, t, is_key)

    def subst(self, m, t, is_key):
        raise TranslationError(f'unresolved substitution in pattern path: {m}')

    def reference(self, m, t, is_key):
        raise TranslationError('references inside pattern paths not supported yet')


class _FilterStep(Hooks):
    """
    A filter key's part as the step it takes, and whether it reads by
    key: a key's matcher an object's member (none for an int: no member
    is named by one), a wildcard every member; a slot as `_PathStep`
    reads it.
    """

    def other(self, part, t):
        raise TranslationError(f'unsupported filter key part: {type(part).__name__}')

    def const(self, m, t):
        if isinstance(m.value, str):
            return ('key', m.value), True
        return ('none',), True

    def wildcard(self, m, t):
        return ('keys',), True

    def slot(self, op, t):
        return op.op.translated(_PATH_STEP, t, False), False

    def slice(self, op, t):
        raise TranslationError('a slice in a filter key not supported')


class _FilterTest(Hooks):
    """
    A filter's test on a node, `e` its jsonb SQL, as dotted's filter
    passes it: a key-value filter holds where the node is an object and
    a value its key reaches compares; `!=` is `=`'s negation, so it
    passes a node that is no object, or lacks the key.
    """

    def other(self, f, t, e):
        raise TranslationError(f'filter type not supported: {type(f).__name__}')

    def key_value_filter(self, f, t, e):
        pred = predicates.PRED_OPS[f._eq_str]
        if pred is predicates.NE:
            return f'NOT {t._filter_holds(f, e, predicates.EQ)}'
        return t._filter_holds(f, e, pred)

    def and_filter(self, f, t, e):
        return t._combine_and([sub.translated(self, t, e) for sub in f.filters])

    def or_filter(self, f, t, e):
        return t._combine_or([sub.translated(self, t, e) for sub in f.filters])

    def not_filter(self, f, t, e):
        return t._combine_not_true(f.inner.translated(self, t, e))

    def group_filter(self, f, t, e):
        return f.inner.translated(self, t, e)


class _Compared(Hooks):
    """
    A guard's or filter's value as `_compared` reads it: a literal by
    its Python type, a substitution as its binding's JSON, a regex
    fullmatching; an ordering against a regex compares with its text,
    as dotted's does.
    """

    def other(self, val, t, j, pred):
        raise TranslationError(f'unsupported value in a comparison: {type(val).__name__}')

    def const(self, val, t, j, pred):
        return t._compared_value(j, pred, val.value)

    def subst(self, val, t, j, pred):
        # bound once, so a positional paramstyle sends it once
        b = _json_spec(t._hoist_subst(val.value))
        return t._bound(b, lambda x: t._compared_jsonb(j, pred, x))

    def regex(self, val, t, j, pred):
        if pred is predicates.EQ:
            return t._fullmatches(j, val.args[0])
        if pred is predicates.NE:
            return f'NOT {t._fullmatches(j, val.args[0])}'
        return t._compared_value(j, pred, val.value)

    def pattern(self, val, t, j, pred):
        raise TranslationError(f'pattern guard values not supported: {type(val).__name__}')


class _ScalarPredicate(Hooks):
    """
    A guard's value as a WHERE predicate on a column of a SQL type,
    `lhs`: SQL's own comparison of that type, but `!=` holding for a
    NULL, as Python's `None != x` does, `<` against None matching
    nothing, and a regex fullmatching, str(None) included.
    """

    def other(self, val, t, lhs, pred):
        raise TranslationError(f'unsupported guard value: {type(val).__name__}')

    def const(self, val, t, lhs, pred):
        return self._compared(lhs, pred, t._hoist_value(val.value))

    def subst(self, val, t, lhs, pred):
        return self._compared(lhs, pred, t._hoist_subst(val.value))

    def none_value(self, val, t, lhs, pred):
        if pred is predicates.EQ:
            return f'{lhs} IS NULL'
        if pred is predicates.NE:
            return f'{lhs} IS NOT NULL'
        # an ordering against None raises in Python: no match
        return 'FALSE'

    def regex(self, val, t, lhs, pred):
        if pred not in (predicates.EQ, predicates.NE):
            return self._compared(lhs, pred, t._hoist_value(val.value))
        test = f'{lhs} ~ {t._hoist_value(_are_regex(val.args[0]))}'
        if re.fullmatch(val.args[0], 'None'):
            # a NULL reads as str(None), which the regex matches
            test = f'({test} OR {lhs} IS NULL)'
        if pred is predicates.NE:
            return f'({test}) IS NOT TRUE'
        return test

    def pattern(self, val, t, lhs, pred):
        raise TranslationError(
            f'pattern guard values not supported in v1: {type(val).__name__}'
        )

    def _compared(self, lhs, pred, rhs):
        if pred is predicates.NE:
            return f'{lhs} IS DISTINCT FROM {rhs}'
        return f'{lhs} {pred.op} {rhs}'


_PARTS = _Parts()
_PATH_STEP = _PathStep()
_FILTER_STEP = _FilterStep()
_FILTER_TEST = _FilterTest()
_COMPARED = _Compared()
_SCALAR_PREDICATE = _ScalarPredicate()


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
