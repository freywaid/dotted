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
    TranslationError   — raised on unsupported paths or malformed
                         `build` calls.

Layout (flat): `.core` holds the target-neutral walker (`Translator`)
and `TranslationError`; `.sql` holds the flavor-neutral SQL types;
each SQL flavor lives in a sibling `sql_<flavor>` module (`.sql_pg`
for Postgres) exposing a `<Flavor>Mixin` and one `Resolver` subclass
per supported driver. Importing this package imports `.sql_pg` for
the side effect of registering its driver classes.
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
from . import sql_pg  # side effect: registers Postgres drivers

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
]
