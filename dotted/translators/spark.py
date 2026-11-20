"""
Flavor-neutral Spark types for dotted.translators.

Defines `ColumnFragment` (a deferred Spark `Column`), `SparkResolver`
(the container `sparkize` returns), the flavor registry (`flavor()`,
`flavors()`, `_FLAVORS`) and the `sparkize(path, flavor=...)` entry
point.

Translation itself is flavor-specific and lives in sibling modules
(`spark_pyspark.py` for PySpark). Each flavor contributes a mixin (a
`.core.Translator` subclass) supplying `translate()` and one
`SparkResolver` subclass registered via `@flavor('flavor-name')`.

Nothing here imports pyspark: translation builds deferred fragments,
and a flavor imports its Spark library only when `build` makes a
Column. `import dotted` never needs pyspark.
"""
import collections.abc
import operator

from ..api import parse
from .core import TranslationError


# ---- Deferred columns -----------------------------------------------

def _merged(*unbounds):
    """
    One `unbound` map from several.
    """
    out = {}
    for u in unbounds:
        out.update(u)
    return out


class _Env(dict):
    """
    What a fragment is made with: the bindings, by original name, and
    `schema`, the DataFrame's, which says what each node a path reads
    is.
    """

    __slots__ = ('schema',)

    def __init__(self, bindings, schema):
        super().__init__(bindings)
        self.schema = schema


def _env(bindings, schema):
    """
    The `_Env` a fragment is made with. A path reads each node by its
    type, so the schema is needed.
    """
    if schema is None:
        raise TranslationError(
            "sparkize reads each node by its type: pass the DataFrame's "
            'schema, r.build(fragment, schema=df.schema)'
        )
    return _Env(bindings, schema)


class ColumnFragment:
    """
    A Spark `Column` not made yet. A Column can't carry a placeholder,
    and a path reads a node by its type, which the DataFrame's schema
    gives, so translation builds a fragment instead: `make(env)` makes
    the Column once the substitution values and the schema are known.

    `unbound` maps each substitution the fragment still needs to its
    original name (`$(min_age)` → `{'min_age': 'min_age'}`), keyed the
    same way as `SQLFragment.unbound`.

    Fragments compose with `&`, `|` and `~` before they are built;
    `unbound` merges:

        r1 = sparkize('age >= $(min_age)', flavor='pyspark')
        r2 = sparkize('status = "active"', flavor='pyspark')
        both = r1.where & r2.where
        df.where(r1.build(both, schema=df.schema, min_age=30))
    """

    __slots__ = ('_make', 'unbound')

    def __init__(self, make, unbound=None):
        self._make = make
        self.unbound = dict(unbound or {})

    def __repr__(self):
        if not self.unbound:
            return 'ColumnFragment()'
        return f'ColumnFragment(unbound={sorted(self.unbound.values())})'

    def _binary(self, other, op):
        """
        Combine with another fragment under a Column operator.
        """
        if not isinstance(other, ColumnFragment):
            return NotImplemented
        mine = self._make
        theirs = other._make
        return ColumnFragment(lambda env: op(mine(env), theirs(env)),
                              _merged(self.unbound, other.unbound))

    def __and__(self, other):
        return self._binary(other, operator.and_)

    def __or__(self, other):
        return self._binary(other, operator.or_)

    def __invert__(self):
        mine = self._make
        return ColumnFragment(lambda env: ~mine(env), self.unbound)


# ---- Typed columns --------------------------------------------------

class TypedColumn:
    """
    A Spark `Column` with its Spark type, `dtype`, as a binding. `build`
    reads a Column binding's type in the active session, against the
    schema; this carries the type of one no session resolves there, a
    higher-order function's lambda argument.
    """

    __slots__ = ('column', 'dtype')

    def __init__(self, column, dtype):
        self.column = column
        self.dtype = dtype

    def __repr__(self):
        return f'TypedColumn({self.column!r}, {self.dtype!r})'


def typed(column, dtype):
    """
    The Column `column` as a binding of the Spark type `dtype`:

        r = sparkize('$(item).id', flavor='pyspark')
        elem = df.schema['xs'].dataType.elementType
        df.select(F.transform('xs', lambda e: r.build(
            r.select, schema=df.schema, item=typed(e, elem))))
    """
    return TypedColumn(column, dtype)


# ---- Flavor registry ------------------------------------------------

# flavor name (str) → SparkResolver subclass. Populated by `@flavor`
# in flavor modules (e.g. spark_pyspark.py).
_FLAVORS = {}


def flavor(name):
    """
    Class decorator that registers a `SparkResolver` subclass under a
    flavor name. Sets `cls.flavor = name` so instances can introspect
    which flavor they belong to.
    """
    def _deco(cls):
        _FLAVORS[name] = cls
        cls.flavor = name
        return cls
    return _deco


def flavors():
    """
    Return the sorted names of all currently registered Spark flavors.
    """
    return sorted(_FLAVORS)


# ---- Resolver --------------------------------------------------------

class SparkResolver(collections.abc.Mapping):
    """
    Base class + container for sparkize() output. Subclasses in flavor
    modules (registered via `@flavor`) mix in the flavor's translation.

    Instance attributes — populated by `translate()`:

        select  : ColumnFragment | None  — extraction expression; an
                                           array of the matches for a
                                           pattern path
        where   : ColumnFragment | None  — predicate
        unbound : dict                   — substitution name → original
                                           name, over both fragments

    A table search (`sparkize(..., root='table')`, or a path that
    starts with a row step) also sets plain values, None otherwise:

        table   : str | None  — the DataFrame's or table's name, as the
                                path spells it (`'public.hello'`)
        offset  : int | None  — rows to skip (`df.offset`, Spark 3.4+)
        limit   : int | None  — rows to keep (`df.limit`)

    and reads its rows from the table's DataFrame with `r.rows(df)`:
    `df` itself, but for a group or negation as the row step,
    `books[(*&a=1, *&b=2)]`, whose rows no filter can give.

    Mapping interface over the non-None fragments, as `Resolver`:

        r['where']                 # same as r.where
        list(r.keys())             # ['select', 'where']

    Rendering:

        r = sparkize('data.user.age >= $(min_age)', flavor='pyspark')
        df.where(r.build(r.where, schema=df.schema, min_age=30))
    """

    flavor = None     # set by @flavor

    _FRAGMENT_KEYS = ('select', 'where')

    _PLAIN_KEYS = ('table', 'offset', 'limit')

    __slots__ = ('select', 'where', 'unbound', 'table', 'offset', 'limit',
                 '_from')

    def __init__(self, select=None, where=None, unbound=None, table=None,
                 offset=None, limit=None):
        self.select = select
        self.where = where
        self.unbound = dict(unbound or {})
        self.table = table
        self.offset = offset
        self.limit = limit
        self._from = None

    def __repr__(self):
        bits = [f'flavor={type(self).flavor!r}'] if type(self).flavor else []
        bits.extend(attr for attr in self._FRAGMENT_KEYS
                    if getattr(self, attr) is not None)
        bits.extend(f'{attr}={getattr(self, attr)!r}' for attr in self._PLAIN_KEYS
                    if getattr(self, attr) is not None)
        if self.unbound:
            bits.append(f'unbound={sorted(self.unbound.values())}')
        return f'{type(self).__name__}({", ".join(bits)})'

    # ---- Mapping protocol over fragment attributes ----

    def __getitem__(self, key):
        if key not in self._FRAGMENT_KEYS:
            raise KeyError(key)
        value = getattr(self, key)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self):
        return (k for k in self._FRAGMENT_KEYS
                if getattr(self, k) is not None)

    def __len__(self):
        return sum(1 for k in self._FRAGMENT_KEYS
                   if getattr(self, k) is not None)

    # ---- overridable translation surface ----

    def translate(self, ops, root='column'):
        """
        Walk the parsed ops tree, populating self.select / self.where /
        self.unbound, and a table search's self.table / self.offset /
        self.limit. Flavor mixins supply the real implementation.
        """
        raise NotImplementedError(
            f'{type(self).__name__} has no translate() — '
            f'missing a flavor mixin?'
        )

    # ---- rendering ----

    # `r.typed(column, dtype)`: a Column binding with its type
    typed = staticmethod(typed)

    def rows(self, df, **bindings):
        """
        The rows a table search reads, from the table's DataFrame `df`,
        with `bindings` supplying the substitutions they need: `df`
        itself, but for a group or negation as the row step,
        `books[(*&a=1, *&b=2)]` — each branch's rows in turn, a row as
        often as the group yields it, which no filter of `df` can give.
        Apply `where`, `offset` / `limit` and `select` to them.
        """
        if self._from is None:
            _check_bindings({}, bindings)
            return df
        _check_bindings(self._from.unbound, bindings)
        return self._from.make(df, _env(bindings, df.schema))

    @staticmethod
    def build(fragment, schema=None, **bindings):
        """
        Make the Spark `Column` for a `ColumnFragment` (usually
        `r.where`, `r.select`, or a composition of fragments) over a
        DataFrame of `schema`, its `df.schema`, with `bindings`
        supplying its substitutions by original name.

        A path reads each node by its type, as dotted reads a Python
        value by its own, and the schema says what each is: a struct
        field or a map's key, an int or a string.

        A binding is a Python value, compared as Python compares it, or
        a Spark Column, read by its type in the active session;
        `typed(column, dtype)` gives the type of one no session
        resolves, a lambda's argument.

        A substitution at a path's head, `$(item).id`, is bound here
        too: to a Column, which the path then reads from, or to a
        column's name.

        Raises `TranslationError` on:
          - a non-fragment argument
          - no schema
          - unknown binding (kwarg not corresponding to any unbound
            substitution in the fragment)
          - missing binding for one of the fragment's substitutions
          - what Spark can't read as dotted does, for this schema
        """
        if not isinstance(fragment, ColumnFragment):
            raise TranslationError(
                f'build requires a ColumnFragment, got {type(fragment).__name__}'
            )
        _check_bindings(fragment.unbound, bindings)
        return fragment._make(_env(bindings, schema))


def _check_bindings(unbound, bindings):
    """
    Raise unless `bindings` binds each of the substitutions `unbound`
    needs, by original name, and no other.
    """
    expected = set(unbound.values())
    extra = set(bindings) - expected
    if extra:
        raise TranslationError(
            f'unknown binding(s) for this fragment: {sorted(extra)}'
        )
    missing = expected - set(bindings)
    if missing:
        raise TranslationError(
            f'missing binding(s) for substitution(s): {sorted(missing)}'
        )


def sparkize(path, *, flavor, bindings=None, root='column'):
    """
    Translate a dotted path into a flavor-specific `SparkResolver`
    carrying deferred Spark Column fragments.

    Arguments:
        path     — dotted path string or pre-parsed Dotted result.
        flavor   — required. Flavor name registered via `@flavor`:
                   'pyspark'.
        bindings — optional mapping/list used to resolve substitutions
                   at sparkize time. Path-position substitutions must
                   be resolved here or `TranslationError` is raised,
                   but for one at the path's head, the column a read
                   starts at. That one and unresolved value-position
                   substitutions appear in `r.unbound`.
        root     — `'column'` (default): the path's first segment is a
                   column of the row. `'table'`: its leading keys name
                   a DataFrame or table, a row step (`[*]`, `[*&…]`,
                   `[n]`, `[a:b]`, a slice filter `[…]`) walks its
                   rows, and the rest is a column path under each row;
                   `r.table`, `r.offset` and `r.limit` carry the plain
                   values. A path that starts with a row step searches
                   an unnamed one under either root.

    Columns are native Spark types: the first path segment is the
    column, further segments reach into structs, maps and arrays.

        r = sparkize('data.user.age >= 30', flavor='pyspark')
        df.where(r.build(r.where, schema=df.schema))

        r = sparkize('hello[book_id="b1"][0:10][*].title',
                     flavor='pyspark', root='table')
        df = spark.table(r.table)
        (df.where(r.build(r.where, schema=df.schema))
           .offset(r.offset).limit(r.limit)
           .select(r.build(r.select, schema=df.schema)))
    """
    cls = _FLAVORS.get(flavor)
    if cls is None:
        raise TranslationError(
            f'unknown flavor: {flavor!r}. '
            f'registered: {sorted(_FLAVORS)}'
        )
    parsed = parse(path, bindings=bindings, partial=True)
    r = cls()
    r.translate(parsed.ops, root=root)
    return r
