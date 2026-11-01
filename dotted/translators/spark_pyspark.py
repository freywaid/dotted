"""
PySpark flavor for dotted.translators.

Defines the `PySparkMixin` — the flavor's hooks for the neutral
`Translator` walk — and `PySparkResolver`, registered with `.spark`
via `@flavor('pyspark')`.

Columns are native Spark types (struct, array, map); a path reaches
into them with Column extraction. Pattern paths use Spark's
higher-order functions: `[*]` and a map's `*` become `exists` for a
predicate and `transform` / `filter` for a select, which is then an
array of the matches. `**` and `*` over a struct's fields need the
schema and are not supported.

Every hook returns a `ColumnFragment`; nothing calls into pyspark
until `build`. Columns are made only through `pyspark.sql.functions`
and Column methods, so Spark Connect sessions work the same as classic
ones.
"""
import functools
import operator

from .. import access
from .. import filters as _filters
from .. import groups as _groups
from .. import matchers
from .. import recursive as _recursive
from .. import wrappers

from .core import TranslationError, Translator
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
        if kind == 'cond':
            pred = step[1](c, env)
            if not rest:
                return pred
            return pred & _exists(c, rest, env)
        return _functions().exists(
            _elements(c, step[1]), lambda x: _exists(x, rest, env))
    return c.isNotNull()


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
        if kind == 'cond':
            candidates = F.array(c)
            rest = steps[i:]
        else:
            candidates = _elements(c, step[1])
            rest = steps[i + 1:]
        # conditions straight after filter these candidates; `filter`
        # calls the lambda at once, so closing over `pred_fn` is safe
        # (a default argument would be taken for the element index)
        while rest and rest[0][0] == 'cond':
            pred_fn = rest[0][1]
            candidates = F.filter(candidates, lambda x: pred_fn(x, env))
            rest = rest[1:]
        if not rest:
            return candidates
        if all(s[0] == 'get' for s in rest):
            return F.transform(candidates, lambda x: _gets(x, rest, env))
        return F.flatten(
            F.transform(candidates, lambda x: _collect(x, rest, env)))
    return F.array(c)


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

    def translate(self, ops):
        """
        Walk the parsed ops tree; populate `self.select`, `self.where`
        and `self.unbound`.
        """
        raw = self._translate(ops)
        self.select = raw.get('select')
        self.where = raw.get('where')
        self.unbound = _merged(*(frag.unbound for frag in raw.values()))

    # ---- Translator hooks -------------------------------------------

    def _segment_value(self, key_op, value):
        """
        `[0]` keeps its int, an array index; any other key is a string.
        """
        if isinstance(key_op, access.Slot):
            return value
        return str(value)

    def _column(self, name):
        """
        The named column.
        """
        quoted = _quoted(name)
        return ColumnFragment(lambda env: _functions().col(quoted))

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
        steps = [('get', key_fn) for key_fn in key_fns]
        for i, op in enumerate(ops):
            if isinstance(op, wrappers.ValueGuard) and i != len(ops) - 1:
                raise TranslationError('path continues after value guard')
            op_steps, op_unbound = self._op_steps(op)
            steps.extend(op_steps)
            unbound = _merged(unbound, op_unbound)
        steps = tuple(steps)
        unbound = _merged(col.unbound, unbound)
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

    def _op_steps(self, op):
        """
        Translate one op of a pattern path into pattern steps. Returns
        (steps, unbound).
        """
        if isinstance(op, wrappers.TransformWrap):
            return self._op_steps(op.inner)
        if isinstance(op, wrappers.TypeRestriction):
            raise TranslationError('type restriction not supported')
        if isinstance(op.most_inner, _groups.Pack):
            raise TranslationError('packed group not supported')
        # NB: Slot and Attr are subclasses of Key, so check them first.
        if isinstance(op, access.Slot):
            return self._key_step(op, 'array', index=True), {}
        if isinstance(op, access.Attr):
            return self._key_step(op, 'map', index=False), {}
        if isinstance(op, access.Key):
            return self._key_step(op, 'map', index=False), {}
        if isinstance(op, _recursive.Recursive):
            raise TranslationError(
                'recursive paths (**) need a schema — not supported in sparkize'
            )
        if isinstance(op, access.SliceFilter):
            pred_fn, unbound = self._filters_pred(op.filters)
            return [('each', 'array'), ('cond', pred_fn)], unbound
        if isinstance(op, wrappers.FilterWrap):
            steps, inner_unbound = self._op_steps(op.inner)
            pred_fn, unbound = self._filters_pred(op.filters)
            return steps + [('cond', pred_fn)], _merged(inner_unbound, unbound)
        if isinstance(op, wrappers.ValueGuard):
            steps, inner_unbound = self._op_steps(op.inner)
            pred_fn, unbound = self._guard_pred(op)
            return steps + [('cond', pred_fn)], _merged(inner_unbound, unbound)
        raise TranslationError(
            f'op not supported in pattern paths: {type(op).__name__}'
        )

    def _key_step(self, op, each_kind, index):
        """
        Steps for a Key / Slot / Attr with a concrete or wildcard
        matcher. A wildcard Key is every value of a map — a struct's
        fields can't be iterated without the schema.
        """
        matcher = op.op
        if isinstance(matcher, matchers.Wildcard):
            return [('each', each_kind)]
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
        key = matcher.value if index else str(matcher.value)
        return [('get', lambda env: key)]

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
        if isinstance(val, matchers.Regex):
            return self._regex_pred(op_str, val.args[0], cast), {}
        value_fn, unbound = self._operand(op_str, val)

        def pred_fn(node, env):
            if cast:
                node = node.cast(cast)
            return _compare(node, op_str, value_fn(env))
        return pred_fn, unbound

    def _operand(self, op_str, val):
        """
        A guard or filter value as `(value_fn(env), unbound)`: a
        literal, or a substitution looked up in the bindings.
        """
        if isinstance(val, matchers.NoneValue):
            if op_str not in ('=', '!='):
                raise TranslationError('ordering comparison against None')
            return (lambda env: None), {}
        if isinstance(val, matchers.Subst):
            name = val.value
            return (lambda env: env[name]), {name: name}
        if isinstance(val, matchers.Pattern):
            raise TranslationError(
                f'pattern guard values not supported in v1: {type(val).__name__}'
            )
        if not hasattr(val, 'value'):
            raise TranslationError(f'unsupported guard value: {type(val).__name__}')
        value = val.value
        return (lambda env: value), {}

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
        parts = [self._filter_pred(f) for f in filters]
        unbound = _merged(*(u for _, u in parts))
        fns = [fn for fn, _ in parts]
        return (lambda node, env:
                functools.reduce(operator.and_, (fn(node, env) for fn in fns))), unbound

    def _filter_pred(self, f):
        """
        One bracket filter as `(pred_fn(node, env), unbound)`.
        """
        if isinstance(f, _filters.FilterKeyValue):
            # Includes KeyValue, KeyValueNot, KeyValueLt, etc. via subclass
            key_fns = self._filter_key_fns(f.key)
            cmp_fn, unbound = self._comparison(f._eq_str, f.val,
                                               f.guard_transforms)
            return (lambda node, env:
                    cmp_fn(_gets(node, key_fns, env), env)), unbound
        if isinstance(f, (_filters.FilterAnd, _filters.FilterOr)):
            parts = [self._filter_pred(sub) for sub in f.filters]
            unbound = _merged(*(u for _, u in parts))
            fns = [fn for fn, _ in parts]
            op = operator.and_ if isinstance(f, _filters.FilterAnd) else operator.or_
            return (lambda node, env:
                    functools.reduce(op, (fn(node, env) for fn in fns))), unbound
        if isinstance(f, _filters.FilterNot):
            inner_fn, unbound = self._filter_pred(f.inner)
            return (lambda node, env: ~inner_fn(node, env)), unbound
        if isinstance(f, _filters.FilterGroup):
            return self._filter_pred(f.inner)
        raise TranslationError(
            f'filter type not supported: {type(f).__name__}'
        )

    def _filter_key_fns(self, key):
        """
        The `get` steps from a filtered node to the filter key's value.
        FilterKey.parts holds raw matchers (Word, Numeric, …) plus Slot
        ops for indexes.
        """
        parts = key.parts if isinstance(key, _filters.FilterKey) else (key,)
        steps = []
        for p in parts:
            if isinstance(p, _groups.Pack):
                raise TranslationError('packed group in filter key not supported')
            if isinstance(p, access.Slice):
                raise TranslationError('slice in filter key not supported yet')
            if isinstance(p, access.Slot):
                m = p.op
                if not hasattr(m, 'value') or isinstance(m, matchers.Wildcard):
                    raise TranslationError(
                        f'unsupported slot matcher in filter key: {type(m).__name__}'
                    )
                steps.append(('get', lambda env, k=m.value: k))
                continue
            if isinstance(p, matchers.Wildcard) or not hasattr(p, 'value'):
                raise TranslationError(
                    f'unsupported filter key part: {type(p).__name__}'
                )
            steps.append(('get', lambda env, k=str(p.value): k))
        return steps


# ---- Flavor classes -------------------------------------------------

@flavor('pyspark')
class PySparkResolver(PySparkMixin, SparkResolver):
    """
    PySpark, classic or Spark Connect.
    """
