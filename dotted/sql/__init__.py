"""
Translate dotted paths into SQL clause components.

Public API:
    sqlize             — translate a path into a driver-specific
                         `Resolver` instance. Signature:
                         `sqlize(path, *, driver, bindings=None)`.
    Resolver           — base class + container; driver subclasses
                         (registered via `@register`) carry paramstyle
                         / cast knobs and live in the dialect module.
    SQLFragment        — format-string SQL with `{name}` markers.
    ParamStyle         — enum of supported PEP 249 placeholder styles
                         (low-level; normal callers pass `driver=`).
    TranslationError   — raised on unsupported paths or malformed
                         `build` calls.

Layout: flavor-neutral types live in `.core`; each flavor lives in a
sibling module (`.pg` for Postgres) exposing a `<Flavor>Mixin` and
one `Resolver` subclass per supported driver. Importing this package
imports `.pg` for the side effect of registering its driver classes.
"""
from .core import (
    TranslationError,
    ParamStyle,
    SQLFragment,
    Resolver,
    sqlize,
    driver,
    drivers,
)
from . import pg  # side effect: registers Postgres drivers

__all__ = [
    'sqlize',
    'Resolver',
    'SQLFragment',
    'ParamStyle',
    'TranslationError',
    'driver',
    'drivers',
]
