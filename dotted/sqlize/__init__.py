"""
Translate dotted paths into SQL clause components.

Public API re-exports:
    sqlize             — translate a path into a `Resolver`
    Resolver           — holds paramstyle-neutral `SQLFragment`s plus
                         unbound binding metadata; call `.build(...)`
                         on a composed fragment to render final SQL
    SQLFragment        — format-string SQL with `{name}` markers
    ParamStyle         — enum of supported PEP 249 placeholder styles
    TranslationError   — raised on unsupported paths or malformed
                         `build` calls

The package is laid out so flavor-neutral types live in `.core` and
dialect-specific emission lives in sibling modules (`.pg` for
Postgres). Importing this package imports `.pg` for its side effect:
registering the Postgres dialect's paramstyle cast mapping with the
core so `Resolver.build('dollar-numeric', ...)` emits explicit casts
in polymorphic contexts (asyncpg type inference).
"""
from .core import (
    TranslationError,
    ParamStyle,
    SQLFragment,
    Resolver,
)
from .pg import sqlize

__all__ = [
    'sqlize',
    'Resolver',
    'SQLFragment',
    'ParamStyle',
    'TranslationError',
]
