"""
PySpark flavor for dotted.translators.

Defines the `PySparkMixin` — the flavor's hooks for the neutral
`Translator` walk — and `PySparkResolver`, registered with `.spark`
via `@flavor('pyspark')`.

Columns are native Spark types (struct, array, map); a path reaches
into them with Column extraction. Pattern paths use Spark's
higher-order functions: `[*]` and a map's `*` become `exists` for a
predicate and `transform` / `filter` for a select, which is then an
array of the matches. A group joins its branches' matches with
`concat`, at each node it is at. `**` and `*` over a struct's fields
need the schema and are not supported.

Every hook returns a `ColumnFragment`; nothing calls into pyspark
until `build`. Columns are made only through `pyspark.sql.functions`
and Column methods, so Spark Connect sessions work the same as classic
ones.
"""
import functools
import operator

from .core import Hooks, TranslationError, Translator
from .spark import ColumnFragment, SparkResolver, flavor, _merged


def _functions():
    """
    `pyspark.sql.functions`, imported on first use so that dotted
    imports without pyspark installed.
    """
    from pyspark.sql import functions
    return functions


_COMPARE = {
    '=':  operator.eq,
    '!=': operator.ne,
    '<':  operator.lt,
    '<=': operator.le,
    '>':  operator.gt,
    '>=': operator.ge,
}


def _quoted(name):
    """
    A column name backticked for `col`, so Spark never splits it on
    dots.
    """
    return '`' + name.replace('`', '``') + '`'


def _extract(c, key):
    """
    `c` reached into by one segment: an int indexes an array (0-based,
    negative from the end, out of range is null); anything else is a
    struct field or map key, a Column key included.
    """
    if isinstance(key, bool) or not isinstance(key, int):
        return c[key]
    F = _functions()
    if key < 0:
        return F.get(c, F.size(c) + key)
    return F.get(c, key)


def _compare(lhs, op_str, value):
    """
    `lhs <op> value`, with `= None` / `!= None` as null tests.
    """
    if value is None:
        if op_str == '=':
            return lhs.isNull()
        if op_str == '!=':
            return lhs.isNotNull()
        raise TranslationError('ordering comparison against None')
    return _COMPARE[op_str](lhs, _functions().lit(value))


# ---- Pattern steps --------------------------------------------------
#
# A pattern path becomes a tuple of steps, applied from the column:
#   ('get', key_fn)    — reach in by key_fn(env)
#   ('each', 'array')  — every element of an array
#   ('each', 'map')    — every value of a map
#   ('cond', pred_fn)  — keep the node only where pred_fn(node, env)
#   ('group', kind, branches, cuts, softs)
#                      — each branch's steps from the node, the rest
#                        of the steps after each: 'or' yields what they
#                        all reach, up to the first branch a cut follows
#                        (`cuts`) that reaches something; 'and' that only
#                        where every branch reaches something. After a
#                        branch a soft cut follows (`softs`), what overlaps
#                        its matches is skipped.
#   ('first', branches)
#                      — the first match of the first branch to reach
#                        one from the node, the branch alone; the rest
#                        of the steps run from it
#   ('not', steps, kinds)
#                      — the complement of what `steps` match from the
#                        node: its largest subtrees that hold no match,
#                        split level by level as `kinds` read them; the
#                        rest of the steps run from each
#
# An aligned (+) `each` carries a third item, ('each', kind, 'aligned'):
# each element yields what the rest of the steps reach, or one null.
# The conditions straight after an `each` are its op's own guards and
# filters, so they decide which elements are matches.
#
# A select reads a node as its value. Under a soft cut it reads it as a
# (path, value) struct instead, the path the segments from the node the
# group is at, for a later branch to skip what overlaps: `_VALUES` and
# `_PATHS` read nodes each way.

def _elements(c, kind):
    """
    The array an `each` step runs over.
    """
    if kind == 'map':
        return _functions().map_values(c)
    return c


def _node(path, value):
    """
    A node read with its path: a (path, value) struct.
    """
    F = _functions()
    return F.struct(path.alias('path'), value.alias('value'))


def _segment(c, key):
    """
    The path segment a reach into `c` by `key` adds, a string: the key,
    or for an int the index it reads.
    """
    F = _functions()
    if isinstance(key, bool) or not isinstance(key, int):
        if isinstance(key, (str, bool, float)):
            return F.lit(str(key))
        return key.cast('string')
    if key < 0:
        return (F.size(c) + key).cast('string')
    return F.lit(str(key))


class _Values:
    """
    Nodes read as their values: what a select yields.
    """

    def start(self, c):
        return _node(_functions().array().cast('array<string>'), c)

    def read(self, nodes):
        return _functions().transform(nodes, lambda n: n['value'])

    def value(self, node):
        return node

    def get(self, node, key):
        return _extract(node, key)

    def elements(self, node, kind):
        return _elements(node, kind)

    def filled(self, found):
        return _filled(found)


class _Paths:
    """
    Nodes read as (path, value) structs, the path the segments from the
    node a soft cut's group is at: what a soft cut compares.
    """

    def start(self, node):
        return node

    def read(self, nodes):
        return nodes

    def value(self, node):
        return node['value']

    def get(self, node, key):
        F = _functions()
        value = node['value']
        return _node(F.concat(node['path'], F.array(_segment(value, key))),
                     _extract(value, key))

    def elements(self, node, kind):
        F = _functions()
        path = node['path']
        value = node['value']
        if kind == 'map':
            return F.transform(F.map_entries(value), lambda e: _node(
                F.concat(path, F.array(e['key'].cast('string'))), e['value']))
        return F.transform(value, lambda x, i: _node(
            F.concat(path, F.array(i.cast('string'))), x))

    def filled(self, found):
        raise TranslationError('aligned (+) under a soft cut not supported yet')


_VALUES = _Values()
_PATHS = _Paths()


def _gets(c, steps, env, rd=_VALUES):
    """
    `c` reached into by a run of `get` steps.
    """
    for _, key_fn in steps:
        c = rd.get(c, key_fn(env))
    return c


def _exists(c, steps, env):
    """
    Boolean Column: some node that `steps` reach from `c` exists (is
    not null) and passes every condition on the way.
    """
    for i, step in enumerate(steps):
        kind = step[0]
        if kind == 'get':
            c = _extract(c, step[1](env))
            continue
        rest = steps[i + 1:]
        # a soft cut changes which matches a group yields, not whether
        # it yields any
        if kind == 'group':
            return _group_exists(c, step, rest, env)
        if kind == 'first':
            return _by_first(
                c, step[1], env,
                lambda first: _functions().exists(first, lambda x: _exists(x, rest, env)),
                _functions().lit(False))
        if kind == 'not':
            return _functions().exists(
                _complement(c, step, env, _VALUES), lambda x: _exists(x, rest, env))
        if kind == 'cond':
            pred = step[1](c, env)
            if not rest:
                return pred
            return pred & _exists(c, rest, env)
        if len(step) > 2:
            return _functions().exists(
                _elements(c, step[1]), lambda x: _matches(x, rest, env))
        return _functions().exists(
            _elements(c, step[1]), lambda x: _exists(x, rest, env))
    return c.isNotNull()


def _matches(c, steps, env):
    """
    Boolean Column: the element `c` of an aligned `each` is a match,
    passing the conditions straight after it.
    """
    pred = _functions().lit(True)
    for step in steps:
        if step[0] != 'cond':
            break
        pred = pred & step[1](c, env)
    return pred


def _filled(found):
    """
    The array `found`, or one null where it is empty or null: what an
    element of an aligned `each` yields.
    """
    F = _functions()
    return F.when(F.size(found) > 0, found).otherwise(F.array(F.lit(None)))


def _collect(c, steps, env, rd=_VALUES):
    """
    Array Column of the nodes that `steps` reach from `c`, each read by
    `rd`.
    """
    F = _functions()
    for i, step in enumerate(steps):
        kind = step[0]
        if kind == 'get':
            c = rd.get(c, step[1](env))
            continue
        if kind == 'group':
            return _group_collect(c, step, steps[i + 1:], env, rd)
        rest = steps[i + 1:]
        if kind == 'first':
            return _by_first(c, step[1], env,
                             lambda first: _onward(first, rest, env, rd), F.array(), rd)
        if kind == 'not':
            return _onward(_complement(c, step, env, rd), rest, env, rd)
        if kind == 'cond':
            return _onward(F.array(c), steps[i:], env, rd)
        return _onward(rd.elements(c, step[1]), rest, env, rd, aligned=len(step) > 2)
    return F.array(c)


def _onward(candidates, rest, env, rd=_VALUES, aligned=False):
    """
    Array Column of what the steps `rest` reach from each node of the
    array `candidates`. For an aligned `each`, each node yields what
    they reach, or one null.
    """
    F = _functions()
    # conditions straight after filter these candidates; `filter`
    # calls the lambda at once, so closing over `pred_fn` is safe
    # (a default argument would be taken for the element index)
    while rest and rest[0][0] == 'cond':
        pred_fn = rest[0][1]
        candidates = F.filter(candidates, lambda x: pred_fn(rd.value(x), env))
        rest = rest[1:]
    if not rest:
        return candidates
    if aligned:
        return F.flatten(
            F.transform(candidates, lambda x: rd.filled(_collect(x, rest, env, rd))))
    if all(s[0] == 'get' for s in rest):
        return F.transform(candidates, lambda x: _gets(x, rest, env, rd))
    return F.flatten(
        F.transform(candidates, lambda x: _collect(x, rest, env, rd)))


def _first_of(c, branch, env, rd=_VALUES):
    """
    Array Column of the first non-null node `branch` reaches from `c`,
    or empty. A null is no match, as in `_exists`.
    """
    F = _functions()
    found = F.filter(_collect(c, branch, env, rd), lambda x: rd.value(x).isNotNull())
    return F.slice(found, 1, 1)


def _by_first(c, branches, env, then, otherwise, rd=_VALUES):
    """
    The Column `then(first)` gives for the first of `branches` to reach
    a match from `c`, `first` the array of that match; `otherwise`
    where none does. Each branch's case runs on from its own match, so
    branches that reach different types still share what follows.
    """
    F = _functions()
    firsts = [_first_of(c, branch, env, rd) for branch in branches]
    case = F.when(F.size(firsts[0]) > 0, then(firsts[0]))
    for first in firsts[1:]:
        case = case.when(F.size(first) > 0, then(first))
    return case.otherwise(otherwise)


def _complement(c, step, env, rd):
    """
    Array Column of the negation step's complement at `c`, read by `rd`:
    the largest subtrees of `c` that hold no match of its branch, in
    order. The branch's matches are read with their paths; then, level
    by level as the branch reads them, a child no match path reaches is
    kept whole, one a match ends at is dropped, and one a match runs
    through is split in turn.
    """
    F = _functions()
    _, steps, kinds = step
    node = rd.start(c)
    found = F.coalesce(_collect(node, steps, env, _PATHS), F.array())
    matched = F.transform(found, lambda n: n['path'])
    return rd.read(_split(node, matched, kinds))


def _starts(path, prefix):
    """
    Boolean Column: the path `prefix` starts `path`.
    """
    F = _functions()
    return F.slice(path, 1, F.size(prefix)) == prefix


def _split(node, matched, kinds):
    """
    Array Column of the subtrees under `node`, a (path, value) struct,
    that hold none of the `matched` paths: its children, read as
    `kinds[0]` says (a map's entries for 'key', an array's elements for
    'slot'), each kept, dropped or split by `kinds[1:]` in turn.
    """
    F = _functions()
    children = _PATHS.elements(node, 'map' if kinds[0] == 'key' else 'array')
    if len(kinds) == 1:
        return F.filter(children, lambda ch: ~F.exists(
            matched, lambda m: _starts(m, ch['path'])))

    def piece(ch):
        reached = F.exists(matched, lambda m: _starts(m, ch['path']))
        ends = F.exists(matched, lambda m: m == ch['path'])
        return (F.when(~reached, F.array(ch))
                 .when(ends, F.slice(F.array(ch), 1, 0))
                 .otherwise(_split(ch, matched, kinds[1:])))
    return F.flatten(F.transform(children, piece))


_JOIN = {'or': operator.or_, 'and': operator.and_}


def _group_exists(c, step, rest, env):
    """
    Boolean Column: the group step reaches something from `c`, the rest
    of the steps after each branch: some branch for 'or', every branch
    for 'and'. A cut changes which branches yield, not whether any does.
    """
    _, kind, branches, _, _ = step
    found = (_exists(c, branch + rest, env) for branch in branches)
    return functools.reduce(_JOIN[kind], found)


def _group_collect(c, step, rest, env, rd=_VALUES):
    """
    Array Column of what the group step reaches from `c`: each branch's
    matches in turn, the rest of the steps after it; for 'and', none
    unless every branch reaches something. A branch that reaches a null
    array adds nothing, as `concat` would make the whole null.
    """
    F = _functions()
    _, kind, branches, _, softs = step
    if any(softs):
        return _softcut_collect(c, step, rest, env, rd)
    found = [F.coalesce(_collect(c, branch + rest, env, rd), F.array()) for branch in branches]
    if kind == 'or':
        return _cut_concat(c, step, rest, env, found, rd)
    held = _group_exists(rd.value(c), step, rest, env)
    return F.filter(F.concat(*found), lambda x: held)


def _cut_concat(c, step, rest, env, found, rd):
    """
    Array Column of the 'or' group step's branch matches, `found`, in
    turn, ending after the first branch a cut follows that reaches
    something from `c`: a null is no match, as in `_exists`.
    """
    F = _functions()
    _, _, branches, cuts, _ = step
    out = found[-1]
    for branch, cut, matches in reversed(list(zip(branches, cuts, found))[:-1]):
        if cut:
            out = F.when(_exists(rd.value(c), branch + rest, env), F.array()).otherwise(out)
        out = F.concat(matches, out)
    return out


def _softcut_collect(c, step, rest, env, rd):
    """
    Array Column of what a group step with a soft cut reaches from `c`:
    each branch's matches in turn, read with their paths from the
    group's node, less those that overlap a match a soft-cut branch
    kept (`_uncovered`); for 'and', none unless every branch reaches
    something.
    """
    F = _functions()
    _, kind, branches, cuts, softs = step
    node = rd.start(c)
    tagged = []
    for j, branch in enumerate(branches):
        found = F.coalesce(_collect(node, branch + rest, env, _PATHS), F.array())
        # transform calls the lambda at once, so j is this branch's
        tagged.append(F.transform(found, lambda n: F.struct(
            n.alias('node'), F.lit(j).alias('branch'),
            F.lit(cuts[j]).alias('cut'), F.lit(softs[j]).alias('soft'))))
    kept = _uncovered(F.concat(*tagged))
    if kind == 'and':
        held = _group_exists(rd.value(c), step, rest, env)
        kept = F.filter(kept, lambda x: held)
    return rd.read(kept)


def _overlap(p, q):
    """
    Boolean Column: one of the paths `p` and `q` starts the other.
    """
    F = _functions()
    return (F.slice(q, 1, F.size(p)) == p) | (F.slice(p, 1, F.size(q)) == q)


def _uncovered(tagged):
    """
    Array Column of the nodes of `tagged`, (node, branch, cut, soft)
    structs in order, that a soft cut didn't cover, in dotted's order:
    one whose path overlaps the path of one a soft-cut branch kept is
    dropped; a soft-cut branch's own nodes are kept as they pass; and
    after a branch a cut follows keeps one, the branches after it are
    dropped.
    """
    F = _functions()
    init = F.struct(
        F.array().cast('array<array<string>>').alias('kept'),
        F.slice(F.transform(tagged, lambda t: t['node']), 1, 0).alias('out'),
        F.lit(-1).alias('branch'),
        F.lit(False).alias('cut'),
        F.lit(False).alias('found'),
        F.lit(False).alias('stop'))

    def merge(acc, t):
        moved = t['branch'] != acc['branch']
        stop = acc['stop'] | (moved & acc['cut'] & acc['found'])
        path = t['node']['path']
        covered = F.exists(acc['kept'], lambda s: _overlap(s, path))
        take = ~stop & ~covered
        return F.struct(
            F.when(take & t['soft'], F.concat(acc['kept'], F.array(path)))
             .otherwise(acc['kept']).alias('kept'),
            F.when(take, F.concat(acc['out'], F.array(t['node'])))
             .otherwise(acc['out']).alias('out'),
            t['branch'].alias('branch'),
            t['cut'].alias('cut'),
            F.when(moved, take).otherwise(acc['found'] | take).alias('found'),
            stop.alias('stop'))

    return F.aggregate(tagged, init, merge, lambda acc: acc['out'])


# ---- Rows of a table search -----------------------------------------

# the column a gate's count of rows is read from, dropped after
_GATE = '_dotted_gate'


class _TableRows:
    """
    The rows of a group of row steps, not made yet: `make(df, env)`
    gives them from the table's DataFrame `df`, `env` the bindings.
    `unbound` are the substitutions they need.
    """

    def __init__(self, make, unbound=None):
        self.make = make
        self.unbound = dict(unbound or {})


def _unbound_of(*frags):
    """
    The substitutions `frags` need, a None among them needing none.
    """
    return _merged(*(frag.unbound for frag in frags if frag is not None))


def _gated(out, hits, exists):
    """
    The DataFrame `out`, or no rows of it unless `hits` has a row
    (none, when not `exists`): a cross join with the one-row count of
    `hits`, kept or not by its value.
    """
    F = _functions()
    found = hits.limit(1).agg(F.count(F.lit(1)).alias(_GATE))
    found = found.where(F.col(_GATE) > 0 if exists else F.col(_GATE) == 0)
    return out.crossJoin(found).drop(_GATE)


# ---- PySpark mixin --------------------------------------------------

class PySparkMixin(Translator):
    """
    PySpark-flavored translation hooks. Mix into a `SparkResolver`
    subclass. The walk itself is `Translator`'s; the hooks below return
    `ColumnFragment`s.
    """

    _CAST_MAP = {
        'int': 'int',
        'float': 'double',
        'str': 'string',
        'bool': 'boolean',
    }

    def translate(self, ops, root='column'):
        """
        Walk the parsed ops tree; populate `self.select`, `self.where`
        and `self.unbound`, and a table search's `self.table`,
        `self.offset` and `self.limit`.
        """
        raw, (self.table, self.offset, self.limit) = (
            self._translate_root(ops, root))
        self.select = raw.get('select')
        self.where = raw.get('where')
        self._from = raw.get('from')
        self.unbound = _merged(*(frag.unbound for frag in raw.values()))

    # ---- Translator hooks -------------------------------------------

    def _segment_value(self, value, index):
        """
        `[0]` keeps its int, an array index; any other key is a string.
        """
        return value if index else str(value)

    def _column(self, name):
        """
        The named column.
        """
        quoted = _quoted(name)
        return ColumnFragment(lambda env: _functions().col(quoted))

    def _all_columns(self):
        """
        `*`.
        """
        return ColumnFragment(lambda env: _functions().col('*'))

    def _row(self, table, columns):
        """
        The row as one struct of its `columns`. The whole row, for a
        negation over its columns, raises: a negation splits a node by
        key as a map, and the row is a struct, whose fields need the
        schema.
        """
        if columns is None:
            raise TranslationError(
                "a negation over the row's columns needs the schema — not "
                'supported in sparkize'
            )
        quoted = [(_quoted(name), name) for name in columns]
        return ColumnFragment(lambda env: _functions().struct(
            *(_functions().col(q).alias(name) for q, name in quoted)))

    def _select_expr(self, col, prefix):
        """
        The column reached into by each prefix segment.
        """
        if not prefix:
            return col
        key_fns, unbound = self._key_fns(prefix)
        make_col = col._make

        def make(env):
            c = make_col(env)
            for key_fn in key_fns:
                c = _extract(c, key_fn(env))
            return c
        return ColumnFragment(make, _merged(col.unbound, unbound))

    def _scalar_predicate(self, guard_op, col):
        """
        A guard on the column itself.
        """
        return self._guard_fragment(guard_op, col)

    def _nested_predicate(self, guard_op, col, prefix):
        """
        A guard at `prefix` inside the column.
        """
        return self._guard_fragment(guard_op, self._select_expr(col, prefix))

    def _combine_and(self, preds):
        """
        Fragments joined with `&`.
        """
        return functools.reduce(operator.and_, preds)

    def _combine_or(self, preds):
        """
        Fragments joined with `|`.
        """
        return functools.reduce(operator.or_, preds)

    def _combine_not(self, pred):
        """
        The fragment negated with `~`.
        """
        return ~pred

    def _combine_not_true(self, pred):
        """
        The fragment false or null: `~coalesce(pred, false)`.
        """
        make = pred._make
        return ColumnFragment(
            lambda env: ~_functions().coalesce(make(env), _functions().lit(False)),
            pred.unbound)

    # ---- rows of a table search -------------------------------------

    def _table_from(self, names):
        """
        None: the caller hands `rows` the table's DataFrame.
        """
        return None

    def _table_rows(self, names, preds, offset, limit):
        """
        The DataFrame's rows `where` the predicates, `offset` / `limit`
        the window.
        """
        pred = self._all_of(preds) if preds else None

        def make(df, env):
            if pred is not None:
                df = df.where(pred._make(env))
            if offset is not None:
                df = df.offset(offset)
            if limit is not None:
                df = df.limit(limit)
            return df
        return _TableRows(make, _unbound_of(pred))

    def _table_rows_kept(self, rows, pred, gates):
        """
        The rows `where` the predicate, each gate a cross join with the
        one-row count of its rows (`_gated`).
        """
        def make(df, env):
            out = rows.make(df, env)
            if pred is not None:
                out = out.where(pred._make(env))
            for hits, hit, exists in gates:
                found = hits.make(df, env)
                if hit is not None:
                    found = found.where(hit._make(env))
                out = _gated(out, found, exists)
            return out
        unbound = _merged(
            rows.unbound, _unbound_of(pred),
            *(_merged(hits.unbound, _unbound_of(hit)) for hits, hit, _ in gates))
        return _TableRows(make, unbound)

    def _table_rows_first(self, rows):
        """
        The rows' first: `limit(1)`.
        """
        return _TableRows(lambda df, env: rows.make(df, env).limit(1), rows.unbound)

    def _table_rows_union(self, parts):
        """
        The parts' rows `unionByName`, each kept as often as it comes.
        """
        def make(df, env):
            return functools.reduce(
                lambda a, b: a.unionByName(b), (part.make(df, env) for part in parts))
        return _TableRows(make, _merged(*(part.unbound for part in parts)))

    def _table_rows_from(self, rows):
        """
        The rows themselves, for `r.rows(df)` to make.
        """
        return rows

    def _pattern(self, col, prefix, ops):
        """
        A pattern path: `where` is whether any match exists, `select`
        the array of matches.
        """
        key_fns, unbound = self._key_fns(prefix)
        op_steps, op_unbound = self._ops_steps(ops)
        steps = tuple(('get', key_fn) for key_fn in key_fns) + op_steps
        unbound = _merged(col.unbound, unbound, op_unbound)
        make_col = col._make
        where = ColumnFragment(
            lambda env: _exists(make_col(env), steps, env), unbound)
        select = ColumnFragment(
            lambda env: _collect(make_col(env), steps, env), unbound)
        return {'select': select, 'where': where}

    # ---- segments ---------------------------------------------------

    def _key_fns(self, prefix):
        """
        One `env → key` function per prefix segment, plus the
        substitutions the reference segments need.
        """
        key_fns = []
        unbound = {}
        for kind, value in prefix:
            if kind == 'lit':
                key_fns.append(lambda env, value=value: value)
                continue
            key_fns.append(value._make)
            unbound = _merged(unbound, value.unbound)
        return key_fns, unbound

    def _ops_steps(self, ops):
        """
        Translate a run of ops of a pattern path into pattern steps.
        Returns (steps, unbound).
        """
        steps = []
        unbound = {}
        for op in ops:
            op_steps, op_unbound = op.translated(_STEPS, self)
            steps.extend(op_steps)
            unbound = _merged(unbound, op_unbound)
        return tuple(steps), unbound

    def _group_steps(self, kind, branches, cuts, softs):
        """
        The one step for a group of `kind`: each branch's steps, for a
        first match or for a group of its kind, with the `cuts` and
        `softs` after them. Returns (steps, unbound).
        """
        parts = [self._ops_steps(branch) for branch in branches]
        branch_steps = tuple(steps for steps, _ in parts)
        unbound = _merged(*(unbound for _, unbound in parts))
        if kind == 'first':
            return [('first', branch_steps)], unbound
        return [('group', kind, branch_steps, cuts, softs)], unbound

    # ---- predicates -------------------------------------------------

    def _guard_fragment(self, guard_op, lhs):
        """
        A guard's predicate over the fragment `lhs`.
        """
        pred_fn, unbound = self._guard_pred(guard_op)
        make_lhs = lhs._make
        return ColumnFragment(lambda env: pred_fn(make_lhs(env), env),
                              _merged(lhs.unbound, unbound))

    def _guard_pred(self, guard_op):
        """
        A ValueGuard as `(pred_fn(node, env), unbound)`.
        """
        return self._comparison(guard_op.pred_op.op, guard_op.guard,
                                guard_op.guard_transforms)

    def _comparison(self, op_str, val, transforms):
        """
        `node <op_str> val` as `(pred_fn(node, env), unbound)`, the node
        cast first when the guard carries a transform (`|int`).
        """
        if op_str not in _COMPARE:
            raise TranslationError(f'unsupported predicate: {op_str}')
        cast = self._transforms_to_cast(transforms) if transforms else None
        return val.translated(_COMPARISON, self, op_str, cast)

    def _regex_pred(self, op_str, pattern, cast):
        """
        `node = /re/` / `node != /re/` as `pred_fn(node, env)`.
        """
        if op_str not in ('=', '!='):
            raise TranslationError('ordering comparison against regex')
        negate = op_str == '!='

        def pred_fn(node, env):
            if cast:
                node = node.cast(cast)
            matched = node.rlike(pattern)
            return ~matched if negate else matched
        return pred_fn

    def _filters_pred(self, filters):
        """
        A sequence of bracket filters (all must hold) as
        `(pred_fn(node, env), unbound)`.
        """
        return _all_of(f.translated(_FILTER_PRED, self) for f in filters)

    def _filter_key_fns(self, key):
        """
        The `get` steps from a filtered node to the filter key's value.
        """
        return [part.translated(_FILTER_KEY_STEP, self, False)
                for part in self._key_parts(key)]


# ---- Reading ops ----------------------------------------------------

def _get_step(value, index):
    """
    The step that reaches in by a key holding `value`: an array index
    when `index`, else a struct field or map key by its string.
    """
    key = value if index else str(value)
    return ('get', lambda env: key)


def _compared(op_str, cast, value_fn):
    """
    `pred_fn(node, env)`: `node <op_str> value_fn(env)`, the node cast
    first when `cast`.
    """
    def pred_fn(node, env):
        if cast:
            node = node.cast(cast)
        return _compare(node, op_str, value_fn(env))
    return pred_fn


def _joined(join, preds):
    """
    `(pred_fn(node, env), unbound)` that joins the `(pred_fn, unbound)`
    pairs `preds` with `join`.
    """
    preds = list(preds)
    unbound = _merged(*(u for _, u in preds))
    fns = [fn for fn, _ in preds]
    return (lambda node, env:
            functools.reduce(join, (fn(node, env) for fn in fns))), unbound


def _all_of(preds):
    """
    `(pred_fn, unbound)` pairs `preds` that must all hold.
    """
    return _joined(operator.and_, preds)


class _Steps(Hooks):
    """
    An op of a pattern path as pattern steps: `(steps, unbound)`.
    """

    def other(self, op, t):
        raise TranslationError(
            f'op not supported in pattern paths: {type(op).__name__}'
        )

    def transform_wrap(self, op, t):
        return op.inner.translated(self, t)

    def key(self, op, t):
        return op.op.translated(_KEY_STEP, t, 'map', False), {}

    def slot(self, op, t):
        return op.op.translated(_KEY_STEP, t, 'array', True), {}

    def recursive(self, op, t):
        raise TranslationError(
            'recursive paths (**) need a schema — not supported in sparkize'
        )

    def slice_filter(self, op, t):
        pred_fn, unbound = t._filters_pred(op.filters)
        return [('each', 'array'), ('cond', pred_fn)], unbound

    def filter_wrap(self, op, t):
        steps, inner_unbound = op.inner.translated(self, t)
        pred_fn, unbound = t._filters_pred(op.filters)
        return steps + [('cond', pred_fn)], _merged(inner_unbound, unbound)

    def value_guard(self, op, t):
        steps, inner_unbound = op.inner.translated(self, t)
        pred_fn, unbound = t._guard_pred(op)
        return steps + [('cond', pred_fn)], _merged(inner_unbound, unbound)

    def group(self, op, kind, branches, cuts, softs, t):
        return t._group_steps(kind, branches, cuts, softs)

    def not_group(self, op, t):
        kinds = t._access_kinds(op.inner)
        if not kinds or kinds[-1] is ...:
            raise TranslationError(
                'a negation reaching any depth needs a schema — not supported in sparkize'
            )
        steps, unbound = t._ops_steps(op.inner)
        return [('not', steps, kinds)], unbound


class _KeyStep(Hooks):
    """
    A Key's or Slot's matcher as pattern steps: for a wildcard, every
    value of a map or element of an array (`each_kind`) — a struct's
    fields can't be iterated without the schema; else a reach in, by an
    index when `index`.
    """

    def other(self, m, t, each_kind, index):
        raise TranslationError(f'unsupported matcher: {type(m).__name__}')

    def const(self, m, t, each_kind, index):
        return [_get_step(m.value, index)]

    def wildcard(self, m, t, each_kind, index):
        if m.is_aligned():
            return [('each', each_kind, 'aligned')]
        return [('each', each_kind)]

    def pattern(self, m, t, each_kind, index):
        raise TranslationError(
            f'unsupported matcher in pattern path: {type(m).__name__}'
        )

    def subst(self, m, t, each_kind, index):
        raise TranslationError(f'unresolved substitution in pattern path: {m}')

    def reference(self, m, t, each_kind, index):
        raise TranslationError('references inside pattern paths not supported yet')


class _Comparison(Hooks):
    """
    A guard's or filter's value as `(pred_fn(node, env), unbound)`:
    `node <op_str> value`, the node cast first when `cast`. A
    substitution's value is looked up in the bindings.
    """

    def other(self, val, t, op_str, cast):
        raise TranslationError(f'unsupported guard value: {type(val).__name__}')

    def const(self, val, t, op_str, cast):
        value = val.value
        return _compared(op_str, cast, lambda env: value), {}

    def none_value(self, val, t, op_str, cast):
        if op_str not in ('=', '!='):
            raise TranslationError('ordering comparison against None')
        return _compared(op_str, cast, lambda env: None), {}

    def subst(self, val, t, op_str, cast):
        name = val.value
        return _compared(op_str, cast, lambda env: env[name]), {name: name}

    def regex(self, val, t, op_str, cast):
        return t._regex_pred(op_str, val.args[0], cast), {}

    def pattern(self, val, t, op_str, cast):
        raise TranslationError(
            f'pattern guard values not supported in v1: {type(val).__name__}'
        )


class _FilterPred(Hooks):
    """
    A bracket filter as `(pred_fn(node, env), unbound)`.
    """

    def other(self, f, t):
        raise TranslationError(f'filter type not supported: {type(f).__name__}')

    def key_value_filter(self, f, t):
        key_fns = t._filter_key_fns(f.key)
        cmp_fn, unbound = t._comparison(f._eq_str, f.val, f.guard_transforms)
        return (lambda node, env: cmp_fn(_gets(node, key_fns, env), env)), unbound

    def and_filter(self, f, t):
        return _all_of(sub.translated(self, t) for sub in f.filters)

    def or_filter(self, f, t):
        return _joined(operator.or_, (sub.translated(self, t) for sub in f.filters))

    def not_filter(self, f, t):
        inner_fn, unbound = f.inner.translated(self, t)
        return (lambda node, env: ~inner_fn(node, env)), unbound

    def group_filter(self, f, t):
        return f.inner.translated(self, t)


class _FilterKeyStep(Hooks):
    """
    A filter key's part as the `get` step that reaches in by it: a key
    (a matcher), or an index (a Slot, its matcher read with `index`).
    """

    def other(self, part, t, index):
        raise TranslationError(f'unsupported filter key part: {type(part).__name__}')

    def const(self, part, t, index):
        return _get_step(part.value, index)

    def slot(self, part, t, index):
        return part.op.translated(self, t, True)

    def slice(self, part, t, index):
        raise TranslationError('slice in filter key not supported yet')


_STEPS = _Steps()
_KEY_STEP = _KeyStep()
_COMPARISON = _Comparison()
_FILTER_PRED = _FilterPred()
_FILTER_KEY_STEP = _FilterKeyStep()


# ---- Flavor classes -------------------------------------------------

@flavor('pyspark')
class PySparkResolver(PySparkMixin, SparkResolver):
    """
    PySpark, classic or Spark Connect.
    """
