"""
Translate dotted paths into other query languages.

Public API:
    sqlize             — translate a path into a driver-specific
                         `Resolver` instance. Signature:
                         `sqlize(path, *, driver, bindings=None,
                          pool=None)`.
    Resolver           — base class + container; driver subclasses
                         (registered via `@driver`) carry paramstyle
                         / cast knobs and live in the dialect module.
    SQLFragment        — format-string SQL with `{name}` markers.
    ParamStyle         — enum of supported PEP 249 placeholder styles
                         (low-level; normal callers pass `driver=`).
    ParamPool          — shared bind-parameter pool passed across
                         multiple `sqlize()` calls so composed
                         fragments don't have marker collisions.
    sparkize           — translate a path into a flavor-specific
                         `SparkResolver`. Signature:
                         `sparkize(path, *, flavor, bindings=None)`.
    SparkResolver      — its base class + container.
    ColumnFragment     — a deferred Spark Column.
    TranslationError   — raised on unsupported paths or malformed
                         `build` calls.

Layout (flat): `.core` holds the target-neutral walker (`Translator`)
and `TranslationError`; `.sql` holds the flavor-neutral SQL types and
`.spark` the flavor-neutral Spark ones. Each flavor lives in a sibling
`sql_<flavor>` / `spark_<flavor>` module (`.sql_pg` for Postgres,
`.spark_pyspark` for PySpark) exposing a `<Flavor>Mixin` and its
registered resolver classes. Importing this package imports both for
the side effect of registering them; neither imports a database driver
or pyspark until it is used.
"""
from .core import TranslationError
from .sql import (
    ParamStyle,
    ParamPool,
    SQLFragment,
    Resolver,
    Raw,
    Col,
    sqlize,
    driver,
    drivers,
)
from .spark import (
    ColumnFragment,
    SparkResolver,
    sparkize,
    flavor,
    flavors,
)
from . import sql_pg  # side effect: registers Postgres drivers
from . import spark_pyspark  # side effect: registers the PySpark flavor

__all__ = [
    'sqlize',
    'Resolver',
    'SQLFragment',
    'ParamStyle',
    'ParamPool',
    'Raw',
    'Col',
    'TranslationError',
    'driver',
    'drivers',
    'sparkize',
    'SparkResolver',
    'ColumnFragment',
    'flavor',
    'flavors',
]
