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
#   ('group', kind, branches)
#                      — each branch's steps from the node, the rest
#                        of the steps after each: 'or' yields what they
#                        all reach, 'and' that only where every branch
#                        reaches something
#   ('first', branches)
#                      — the first match of the first branch to reach
#                        one from the node, the branch alone; the rest
#                        of the steps run from it
#
# An aligned (+) `each` carries a third item, ('each', kind, 'aligned'):
# each element yields what the rest of the steps reach, or one null.
# The conditions straight after an `each` are its op's own guards and
# filters, so they decide which elements are matches.

def _elements(c, kind):
    """
    The array an `each` step runs over.
    """
    if kind == 'map':
        return _functions().map_values(c)
    return c


def _gets(c, steps, env):
    """
    `c` reached into by a run of `get` steps.
    """
    for _, key_fn in steps:
        c = _extract(c, key_fn(env))
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
        if kind == 'group':
            return _group_exists(c, step, rest, env)
        if kind == 'first':
            return _by_first(
                c, step[1], env,
                lambda first: _functions().exists(first, lambda x: _exists(x, rest, env)),
                _functions().lit(False))
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


def _collect(c, steps, env):
    """
    Array Column of the nodes that `steps` reach from `c`.
    """
    F = _functions()
    for i, step in enumerate(steps):
        kind = step[0]
        if kind == 'get':
            c = _extract(c, step[1](env))
            continue
        if kind == 'group':
            return _group_collect(c, step, steps[i + 1:], env)
        rest = steps[i + 1:]
        if kind == 'first':
            return _by_first(c, step[1], env,
                             lambda first: _onward(first, rest, env), F.array())
        if kind == 'cond':
            return _onward(F.array(c), steps[i:], env)
        return _onward(_elements(c, step[1]), rest, env, aligned=len(step) > 2)
    return F.array(c)


def _onward(candidates, rest, env, aligned=False):
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
        candidates = F.filter(candidates, lambda x: pred_fn(x, env))
        rest = rest[1:]
    if not rest:
        return candidates
    if aligned:
        return F.flatten(
            F.transform(candidates, lambda x: _filled(_collect(x, rest, env))))
    if all(s[0] == 'get' for s in rest):
        return F.transform(candidates, lambda x: _gets(x, rest, env))
    return F.flatten(
        F.transform(candidates, lambda x: _collect(x, rest, env)))


def _first_of(c, branch, env):
    """
    Array Column of the first non-null node `branch` reaches from `c`,
    or empty. A null is no match, as in `_exists`.
    """
    F = _functions()
    found = F.filter(_collect(c, branch, env), lambda x: x.isNotNull())
    return F.slice(found, 1, 1)


def _by_first(c, branches, env, then, otherwise):
    """
    The Column `then(first)` gives for the first of `branches` to reach
    a match from `c`, `first` the array of that match; `otherwise`
    where none does. Each branch's case runs on from its own match, so
    branches that reach different types still share what follows.
    """
    F = _functions()
    firsts = [_first_of(c, branch, env) for branch in branches]
    case = F.when(F.size(firsts[0]) > 0, then(firsts[0]))
    for first in firsts[1:]:
        case = case.when(F.size(first) > 0, then(first))
    return case.otherwise(otherwise)


_JOIN = {'or': operator.or_, 'and': operator.and_}


def _group_exists(c, step, rest, env):
    """
    Boolean Column: the group step reaches something from `c`, the rest
    of the steps after each branch: some branch for 'or', every branch
    for 'and'.
    """
    _, kind, branches = step
    found = (_exists(c, branch + rest, env) for branch in branches)
    return functools.reduce(_JOIN[kind], found)


def _group_collect(c, step, rest, env):
    """
    Array Column of what the group step reaches from `c`: each branch's
    matches in turn, the rest of the steps after it; for 'and', none
    unless every branch reaches something. A branch that reaches a null
    array adds nothing, as `concat` would make the whole null.
    """
    F = _functions()
    _, kind, branches = step
    found = F.concat(*(F.coalesce(_collect(c, branch + rest, env), F.array())
                       for branch in branches))
    if kind == 'or':
        return found
    held = _group_exists(c, step, rest, env)
    return F.filter(found, lambda x: held)


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

    def _group_steps(self, kind, branches):
        """
        The one step for a group of `kind`: each branch's steps, for a
        first match or for a group of its kind. Returns (steps, unbound).
        """
        parts = [self._ops_steps(branch) for branch in branches]
        branch_steps = tuple(steps for steps, _ in parts)
        unbound = _merged(*(unbound for _, unbound in parts))
        if kind == 'first':
            return [('first', branch_steps)], unbound
        return [('group', kind, branch_steps)], unbound

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

    def group(self, op, kind, branches, t):
        return t._group_steps(kind, branches)


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
