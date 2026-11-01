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


class ColumnFragment:
    """
    A Spark `Column` not made yet. A Column can't carry a placeholder,
    so translation builds a fragment instead: `make(bindings)` makes
    the Column once the substitution values are known.

    `unbound` maps each substitution the fragment still needs to its
    original name (`$(min_age)` → `{'min_age': 'min_age'}`), keyed the
    same way as `SQLFragment.unbound`.

    Fragments compose with `&`, `|` and `~` before they are built;
    `unbound` merges:

        r1 = sparkize('age >= $(min_age)', flavor='pyspark')
        r2 = sparkize('status = "active"', flavor='pyspark')
        both = r1.where & r2.where
        df.where(r1.build(both, min_age=30))
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

    Mapping interface over the non-None fragments, as `Resolver`:

        r['where']                 # same as r.where
        list(r.keys())             # ['select', 'where']

    Rendering:

        r = sparkize('data.user.age >= $(min_age)', flavor='pyspark')
        df.where(r.build(r.where, min_age=30))
    """

    flavor = None     # set by @flavor

    _FRAGMENT_KEYS = ('select', 'where')

    __slots__ = ('select', 'where', 'unbound')

    def __init__(self, select=None, where=None, unbound=None):
        self.select = select
        self.where = where
        self.unbound = dict(unbound or {})

    def __repr__(self):
        bits = [f'flavor={type(self).flavor!r}'] if type(self).flavor else []
        bits.extend(attr for attr in self._FRAGMENT_KEYS
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

    def translate(self, ops):
        """
        Walk the parsed ops tree, populating self.select / self.where /
        self.unbound. Flavor mixins supply the real implementation.
        """
        raise NotImplementedError(
            f'{type(self).__name__} has no translate() — '
            f'missing a flavor mixin?'
        )

    # ---- rendering ----

    @staticmethod
    def build(fragment, **bindings):
        """
        Make the Spark `Column` for a `ColumnFragment` (usually
        `r.where`, `r.select`, or a composition of fragments), with
        `bindings` supplying its substitutions by original name.

        A binding may be any value `lit` accepts — a Spark Column too,
        which is used as is.

        Raises `TranslationError` on:
          - a non-fragment argument
          - unknown binding (kwarg not corresponding to any unbound
            substitution in the fragment)
          - missing binding for one of the fragment's substitutions
        """
        if not isinstance(fragment, ColumnFragment):
            raise TranslationError(
                f'build requires a ColumnFragment, got {type(fragment).__name__}'
            )
        expected = set(fragment.unbound.values())
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
        return fragment._make(bindings)


def sparkize(path, *, flavor, bindings=None):
    """
    Translate a dotted path into a flavor-specific `SparkResolver`
    carrying deferred Spark Column fragments.

    Arguments:
        path     — dotted path string or pre-parsed Dotted result.
        flavor   — required. Flavor name registered via `@flavor`:
                   'pyspark'.
        bindings — optional mapping/list used to resolve substitutions
                   at sparkize time. Path-position substitutions must
                   be resolved here or `TranslationError` is raised.
                   Unresolved value-position substitutions appear in
                   `r.unbound`.

    Columns are native Spark types: the first path segment is the
    column, further segments reach into structs, maps and arrays.

        r = sparkize('data.user.age >= 30', flavor='pyspark')
        df.where(r.build(r.where))
    """
    cls = _FLAVORS.get(flavor)
    if cls is None:
        raise TranslationError(
            f'unknown flavor: {flavor!r}. '
            f'registered: {sorted(_FLAVORS)}'
        )
    parsed = parse(path, bindings=bindings, partial=True)
    r = cls()
    r.translate(parsed.ops)
    return r
