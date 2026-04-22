"""
Flavor-neutral core of dotted.sql.

Defines the public types — `SQLFragment`, `Resolver`, `ParamStyle`,
`TranslationError` — plus the driver registry (`driver()`, `drivers()`,
`_DRIVERS`) and the `sqlize(path, driver=...)` entry point.

Translation itself is flavor-specific and lives in sibling modules
(`pg.py` for Postgres). Each flavor contributes:

  - a mixin class supplying `translate()` and `cast_fn()` methods,
  - one `Resolver` subclass per driver it supports, each setting
    `paramstyle` and the `cast` boolean as classvars, registered via
    `@driver('driver-name')`.

The legacy `_DIALECT_CAST_FNS` registry + `_register_dialect_cast`
helpers are retained only as a low-level hook for the unit test
matrix, which exercises every paramstyle emitter without needing a
corresponding driver class.
"""
import enum
import hashlib
import re
import string as _string

from ..api import parse


class TranslationError(Exception):
    """
    Raised when a dotted path cannot be translated to SQL or when a
    `Resolver.build` call is malformed.
    """
    pass


# ---- Marker syntax ---------------------------------------------------

# Bind-parameter markers in SQL fragments use Python format-string
# syntax: `{name}` is a placeholder, `{{` / `}}` are literal braces.
# `SQLFragment.substitute()` walks them via `string.Formatter.parse`
# and invokes a caller-supplied replacer per occurrence.
_FORMATTER = _string.Formatter()

_MISSING = object()   # sentinel for "value not yet supplied"


def _marker(name):
    """
    Render a bind-name marker for embedding in an SQL fragment's text.
    """
    return '{' + name + '}'


def _escape_braces(s):
    """
    Escape literal `{` and `}` in a string for inclusion in an SQL
    fragment's text. Needed wherever emitted SQL contains literal
    brace constructs (e.g. Postgres JSONB path arrays `'{a,b,c}'`)
    that would otherwise be mistaken for format placeholders.
    """
    return s.replace('{', '{{').replace('}', '}}')


def _with_cast_spec(marker):
    """
    Rewrite a `{name}` marker to `{name:cast}` so
    `SQLFragment.substitute` passes `spec='cast'` to the replacer.
    Used by dialect emitters inside polymorphic SQL contexts (e.g.
    Postgres's `jsonb_build_object(...)`) where the placeholder's
    type must be pinned explicitly.
    """
    if marker.startswith('{') and marker.endswith('}'):
        name = marker[1:-1]
        return '{' + name + ':cast}'
    return marker


# ---- Bind-parameter naming ------------------------------------------

_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

# Substitution names that aren't already valid SQL identifiers are
# mapped to a hash-based name for use as a SQL bind parameter.
# Deterministic — the same substitution name always produces the same
# hashed param.
_HASH_PREFIX = '_s_'
_GEN_PREFIX = '_p'
_RESERVED_PREFIXES = (_HASH_PREFIX, _GEN_PREFIX)


def _param_name_for_subst(name):
    """
    Return a SQL identifier-safe bind parameter name for a substitution.

    Plain identifiers pass through unchanged. Anything else — dotted
    paths, quoted keys, special characters — hashes to a deterministic
    `_s_<hex>` name. Names that collide with our reserved generated-name
    prefixes (`_p<N>`, `_s_`) are also hashed so they can never clobber
    hoisted literals.
    """
    if _IDENT_RE.fullmatch(name) and not any(
            name.startswith(p) for p in _RESERVED_PREFIXES):
        return name
    h = hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]
    return f'{_HASH_PREFIX}{h}'


# ---- Paramstyle enum ------------------------------------------------

class ParamStyle(enum.StrEnum):
    """
    SQL placeholder styles supported by `Resolver.build`. Values follow
    PEP 249 naming where applicable; `dollar-numeric` covers Postgres's
    native `$N` placeholders (used by asyncpg and similar drivers).

    `StrEnum` — members compare equal to their string values, so callers
    can pass either `ParamStyle.named` or `'named'`.

    Output shape:
      - `named` / `pyformat`: `(sql, params_dict)`. Repeated markers
        share one binding entry (back-reference by name).
      - `dollar-numeric` / `numeric`: `(sql, args_list)`. Repeated
        markers share one position (back-reference by number).
      - `qmark` / `format`: `(sql, args_list)`. Each marker occurrence
        produces a separate arg — these styles have no back-reference
        so a repeated substitution's value is emitted multiple times.
    """
    named = 'named'                    # :name         (SQLAlchemy, sqlite3)
    pyformat = 'pyformat'              # %(name)s      (psycopg)
    qmark = 'qmark'                    # ?             (sqlite3 positional)
    format = 'format'                  # %s            (psycopg positional)
    numeric = 'numeric'                # :1, :2        (Oracle-style)
    dollar_numeric = 'dollar-numeric'  # $1, $2        (asyncpg, native PG)


_PARAMSTYLES = tuple(ps.value for ps in ParamStyle)


# ---- Driver registry ------------------------------------------------

# driver name (str) → Resolver subclass. Populated by `@register` in
# dialect modules (e.g. pg.py). `sqlize(..., driver='<name>')` looks
# the class up here.
_DRIVERS = {}


def driver(name):
    """
    Class decorator that registers a `Resolver` subclass under a driver
    name. Sets `cls.driver = name` so instances can introspect which
    driver they belong to.

    Usage:

        @driver('asyncpg')
        class AsyncPGResolver(PostgresMixin, Resolver):
            paramstyle = 'dollar-numeric'
            cast       = True
    """
    def _deco(cls):
        _DRIVERS[name] = cls
        cls.driver = name
        return cls
    return _deco


def drivers():
    """
    Return the sorted names of all currently registered drivers.
    Includes built-ins (`'asyncpg'`, `'psycopg2'`, `'psycopg'`) plus
    anything third-party code has registered via `@driver`.
    """
    return sorted(_DRIVERS)


# ---- Raw SQL substitution values ------------------------------------

class Raw:
    """
    Low-level wrapper signaling that a binding value should be emitted
    as literal SQL at render time rather than hoisted as a bind
    parameter. Use when the "value" for a substitution is actually a
    SQL expression — a column reference, a subquery, a function call.

        r = sqlize('customer = $(matched.customer)', driver='asyncpg')
        r.build(r.where, **{'matched.customer': Raw('matched.customer')})
        # ("customer = matched.customer", [])

    Bypasses all paramstyle / cast / args-list machinery: the
    placeholder slot is replaced verbatim with `.sql`, no arg is
    emitted, no cast is applied, no back-reference is reserved. Same
    behavior across every paramstyle.

    WARNING: escape hatch. The contents of `sql` are emitted verbatim
    — never pass untrusted input. For the common case of qualified
    column references, use `Col` instead: it validates each identifier
    segment and builds a safe `Raw`.
    """

    __slots__ = ('sql',)

    def __init__(self, sql):
        if not isinstance(sql, str):
            raise TranslationError(
                f'Raw expects a str, got {type(sql).__name__}'
            )
        self.sql = sql

    def __repr__(self):
        return f'Raw({self.sql!r})'

    def __eq__(self, other):
        if isinstance(other, Raw):
            return self.sql == other.sql
        return NotImplemented

    def __hash__(self):
        return hash(('Raw', self.sql))


class Col(Raw):
    """
    Safe qualified column reference. Validates that each identifier
    segment is a plain SQL identifier, then joins with `.`:

        Col('matched.customer')              # → Raw('matched.customer')
        Col('matched', 'customer')           # same
        Col('schema', 'table', 'col')        # → Raw('schema.table.col')

    Rejects segments that aren't plain identifiers — no spaces,
    punctuation, or SQL meta-characters — so user input flowing into
    `Col()` cannot inject SQL via this path. For exotic column names
    or expressions, use `Raw` directly.
    """

    def __init__(self, *parts):
        if not parts:
            raise TranslationError('Col() requires at least one part')
        if len(parts) == 1 and isinstance(parts[0], str) and '.' in parts[0]:
            parts = tuple(parts[0].split('.'))
        for p in parts:
            if not isinstance(p, str):
                raise TranslationError(
                    f'Col part must be a str, got {type(p).__name__}'
                )
            if not _IDENT_RE.fullmatch(p):
                raise TranslationError(
                    f'Col part is not a plain identifier: {p!r}'
                )
        super().__init__('.'.join(parts))

    def __repr__(self):
        return f'Col({self.sql!r})'


# ---- Legacy dialect cast registry -----------------------------------
#
# Kept for the low-level `Resolver.build(sql, paramstyle='...')`
# classmethod call used by the unit-test matrix — it exercises every
# paramstyle emitter without binding to a specific driver. Driver
# classes don't rely on this registry; they supply `cast_fn` on the
# flavor mixin and toggle `cast = True/False` as a classvar.

_DIALECT_CAST_FNS = {}   # paramstyle (str) → fn(value) → cast name or None


def _register_dialect_cast(paramstyle, fn):
    """
    Register a cast-inference function against a paramstyle for the
    legacy low-level `Resolver.build(paramstyle=...)` path. Called at
    import time by dialect modules so the test matrix behaves the same
    as before the driver-class refactor.
    """
    if fn is None:
        _DIALECT_CAST_FNS.pop(paramstyle, None)
    else:
        _DIALECT_CAST_FNS[paramstyle] = fn


# ---- SQLFragment ----------------------------------------------------

class SQLFragment:
    """
    A format-string SQL fragment with placeholder markers and their
    metadata.

    - `text` is a string with `{name}` markers where a bind value
      goes.
    - `params` maps marker name → pre-hoisted value (literals resolved
      at sqlize time, including bindings supplied then).
    - `unbound` maps marker name → original substitution name (values
      still to be provided at build time).

    Fragments compose via `+` / `__radd__`, merging metadata:

        combined = "SELECT " + r.select + " FROM t WHERE " + r.where

    `str(sql)` returns the marker-form text — inspectable but not
    directly executable. Call `r.build(sql, ...)` on a driver-bound
    Resolver to render into driver-ready SQL.
    """

    __slots__ = ('text', 'params', 'unbound')

    def __init__(self, text='', params=None, unbound=None):
        self.text = text
        self.params = dict(params or {})
        self.unbound = dict(unbound or {})

    def __str__(self):
        return self.text

    def __repr__(self):
        return f'SQLFragment({self.text!r})'

    def __eq__(self, other):
        if isinstance(other, SQLFragment):
            return (self.text == other.text
                    and self.params == other.params
                    and self.unbound == other.unbound)
        return NotImplemented

    def __hash__(self):
        return hash((self.text,
                     tuple(sorted(self.params.items())),
                     tuple(sorted(self.unbound.items()))))

    def __bool__(self):
        return bool(self.text)

    def __add__(self, other):
        if isinstance(other, SQLFragment):
            return SQLFragment(self.text + other.text,
                               {**self.params, **other.params},
                               {**self.unbound, **other.unbound})
        if isinstance(other, str):
            return SQLFragment(self.text + other, self.params, self.unbound)
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, str):
            return SQLFragment(other + self.text, self.params, self.unbound)
        return NotImplemented

    def markers(self):
        """
        Yield marker names in occurrence order (duplicates included).
        """
        return [name for _, name, _, _ in _FORMATTER.parse(self.text)
                if name is not None]

    def substitute(self, replacer):
        """
        Return self.text with every `{marker}` replaced by the return
        value of `replacer(marker_name, spec)` where `spec` is the
        format-spec portion (everything after `:` in `{marker:spec}`),
        or `None` if none. Text-level operation: the caller decides
        what placeholder form to emit (`:name`, `$N`, `?`, etc.) and
        is responsible for tracking per-marker values externally.
        Literal braces in the fragment (doubled as `{{` / `}}`) are
        unescaped to single braces in the output, per Python format-
        string rules.

        `replacer(name, spec)` is called **once per occurrence**, in
        source order. Callers that want back-referencing semantics
        (e.g. `:name` or `$N` sharing one slot across multiple
        occurrences) must cache results themselves.
        """
        parts = []
        for literal, name, spec, _ in _FORMATTER.parse(self.text):
            parts.append(literal)
            if name is not None:
                parts.append(replacer(name, spec or None))
        return ''.join(parts)


# ---- Resolver -------------------------------------------------------

class Resolver:
    """
    Base class + container for sqlize() output. Subclasses in dialect
    modules (registered via `@register`) set the driver's knobs as
    classvars:

        driver     — set by the @register decorator (string name)
        paramstyle — placeholder syntax to emit at build time
        cast       — bool: does this driver need explicit SQL casts?

    Translation methods (`translate`, `cast_fn`, and the per-flavor
    internals) come from a flavor mixin (e.g. `PostgresMixin`).

    Instance attributes — populated by `translate()`:

        select : SQLFragment | None  — extraction expression
        where  : SQLFragment | None  — predicate
        from_  : SQLFragment | None  — LATERAL / join fragment (future)
        unbound: dict                — bind name → original subst name

    Rendering:

        r = sqlize('status = "active"', driver='asyncpg')
        sql, args = r.build(r.where)
        # ('status = $1::text', ['active'])

    The same classmethod `Resolver.build(sql, paramstyle=...)` form
    still works as a low-level emitter-testing escape hatch. It
    consults `_DIALECT_CAST_FNS` to decide whether to emit casts.
    """

    driver     = None     # set by @register
    paramstyle = None     # set by driver subclass
    cast       = False    # set by driver subclass

    __slots__ = ('select', 'where', 'from_', 'unbound', '_state')

    def __init__(self, select=None, where=None, from_=None, unbound=None):
        self.select = select
        self.where = where
        self.from_ = from_
        self.unbound = dict(unbound or {})
        self._state = None

    def __repr__(self):
        bits = [f'driver={type(self).driver!r}'] if type(self).driver else []
        for attr in ('select', 'where', 'from_'):
            v = getattr(self, attr)
            if v is not None:
                bits.append(f'{attr}={v.text!r}')
        if self.unbound:
            bits.append(f'unbound={list(self.unbound.values())}')
        return f'{type(self).__name__}({", ".join(bits)})'

    # ---- overridable translation surface ----

    def translate(self, ops, pool=None):
        """
        Walk the parsed ops tree, populating self.select / self.where /
        self.from_ / self.unbound. Optionally accepts a shared
        `ParamPool`; when None, the mixin creates a fresh one.

        Default implementation raises — flavor mixins (like
        PostgresMixin) supply the real implementation.
        """
        raise NotImplementedError(
            f'{type(self).__name__} has no translate() — '
            f'missing a flavor mixin?'
        )

    @staticmethod
    def cast_fn(value):
        """
        Return the SQL cast name (like 'bigint') to apply to this
        value when rendering, or None for no cast. Flavor mixins
        override to supply flavor-specific casts; the base default
        is no cast.
        """
        return None

    # ---- rendering ----

    @classmethod
    def build(cls, sql, paramstyle=None, **bindings):
        """
        Render a composed `SQLFragment` into driver-ready SQL text
        plus a params structure.

        Two calling modes:

        1. Driver-bound (instance or driver-class): `paramstyle` is
           not supplied; the class's own `paramstyle` classvar is
           used, and `cast_fn` is consulted iff `cast = True`:

               r = sqlize(path, driver='asyncpg')
               sql, args = r.build(r.where)

        2. Low-level (base class + explicit paramstyle): used by the
           unit-test matrix to exercise individual paramstyle
           emitters. Consults the legacy `_DIALECT_CAST_FNS` for
           casting:

               Resolver.build(frag, paramstyle='dollar-numeric')

        Returns `(text, params_or_args)`:
          - paramstyle='named' / 'pyformat' → (text, dict)
          - paramstyle='dollar-numeric' / 'numeric' → (text, list)
          - paramstyle='qmark' / 'format' → (text, list)

        Raises `TranslationError` on:
          - unsupported paramstyle
          - unknown binding (kwarg not corresponding to any unbound
            substitution in the fragment)
          - missing binding for an unbound marker present in the text
        """
        if not isinstance(sql, SQLFragment):
            raise TranslationError(
                f'build requires an SQLFragment, got {type(sql).__name__}'
            )
        if paramstyle is None:
            paramstyle = cls.paramstyle
            if paramstyle is None:
                # Base `Resolver` class invoked as classmethod without
                # a driver subclass — default to `named` for back-compat
                # with code that pre-dates the driver registry.
                paramstyle = 'named'
                cast_fn = None
            else:
                cast_fn = cls.cast_fn if cls.cast else None
        else:
            if paramstyle not in _PARAMSTYLES:
                raise TranslationError(
                    f'unsupported paramstyle: {paramstyle!r}'
                )
            cast_fn = _DIALECT_CAST_FNS.get(paramstyle)

        expected_origs = set(sql.unbound.values())
        extra = set(bindings) - expected_origs
        if extra:
            raise TranslationError(
                f'unknown binding(s) for this fragment: {sorted(extra)}'
            )

        return _render(sql, paramstyle, cast_fn, bindings)


def _render(sql, paramstyle, cast_fn, bindings):
    """
    Core rendering loop shared by all `Resolver.build` entry points.

    `paramstyle` is a validated paramstyle string. `cast_fn(value)`
    returns a SQL cast name or None — pass None (not a no-op fn) to
    skip casting entirely so the `:cast` marker spec has no effect.
    `bindings` is a dict of orig-name → value provided by the caller.

    `Raw` values bypass all paramstyle / cast / args machinery: their
    `.sql` is substituted in place of the placeholder, no arg is
    emitted, no cast is applied. Same behavior across every paramstyle.
    """
    missing = []
    if paramstyle in ('named', 'pyformat'):
        out_params = {}
        fmt = ':{name}' if paramstyle == 'named' else '%({name})s'

        def replacer(marker, spec):
            value = _lookup_value(sql, marker, bindings, missing)
            if value is _MISSING:
                return ''
            if isinstance(value, Raw):
                return value.sql
            out_params[marker] = value
            return fmt.format(name=marker)

        out_values = out_params
    elif paramstyle in ('numeric', 'dollar-numeric'):
        prefix = '$' if paramstyle == 'dollar-numeric' else ':'
        out_args = []
        pos_by_marker = {}

        def replacer(marker, spec):
            if marker in pos_by_marker:
                return pos_by_marker[marker]
            value = _lookup_value(sql, marker, bindings, missing)
            if value is _MISSING:
                return ''
            if isinstance(value, Raw):
                # No arg appended, no back-ref slot reserved — each
                # occurrence emits the raw SQL afresh.
                return value.sql
            out_args.append(value)
            ph = f'{prefix}{len(out_args)}'
            if spec == 'cast' and cast_fn is not None:
                cast = cast_fn(value)
                if cast is not None:
                    ph = f'{ph}::{cast}'
            pos_by_marker[marker] = ph
            return ph

        out_values = out_args
    else:  # qmark, format — no back-reference
        placeholder = '?' if paramstyle == 'qmark' else '%s'
        out_args = []

        def replacer(marker, spec):
            value = _lookup_value(sql, marker, bindings, missing)
            if value is _MISSING:
                return ''
            if isinstance(value, Raw):
                return value.sql
            out_args.append(value)
            return placeholder

        out_values = out_args

    final = sql.substitute(replacer)

    if missing:
        raise TranslationError(
            f'missing binding(s) for substitution(s): '
            f'{sorted(set(missing))}'
        )
    return final, out_values


def _lookup_value(sql, marker, bindings, missing):
    """
    Resolve a marker to its value using the fragment's own metadata,
    supplemented by caller-provided bindings. Records marker-side
    misses in `missing` and returns `_MISSING` so the caller can
    continue scanning (build raises after the full pass).
    """
    if marker in sql.params:
        return sql.params[marker]
    if marker in sql.unbound:
        orig = sql.unbound[marker]
        if orig not in bindings:
            missing.append(orig)
            return _MISSING
        return bindings[orig]
    raise TranslationError(
        f'unknown marker {{{marker}}} in SQL fragment'
    )


# ---- Shared bind-parameter pool -------------------------------------

class ParamPool:
    """
    Shared pool of bind-parameter metadata that can span multiple
    `sqlize()` calls. Pass the same `ParamPool` as `pool=` to each
    `sqlize()` so the Resolvers it produces share one bind-name space
    — literal hoists count through a single counter, substitutions
    dedup across Resolvers, and the combined `params` / `unbound`
    maps never have collisions.

    Without a shared pool, each `sqlize()` creates its own and two
    Resolvers can independently pick the same marker name (e.g.
    `_p1`). Composing such fragments via `+` would silently overwrite
    one with the other.

    Fields:
      params  — marker name → pre-hoisted value (literals + resolved
                substitutions)
      unbound — marker name → original substitution name (to be bound
                at build time)

    Normal use is handoff only — construct once, pass to each
    `sqlize()` that should share the bind space, then read each
    Resolver's fragments normally. Mutating the pool externally is
    not supported.
    """

    def __init__(self):
        self.params = {}    # marker name → resolved value
        self.unbound = {}   # marker name → original subst name
        self._gen_counter = 0
        self._by_orig = {}  # original subst name → marker name (dedup)

    def hoist_literal(self, value):
        """
        Hoist a concrete literal. Returns the marker form `{name}`
        ready to embed in SQL text.
        """
        self._gen_counter += 1
        name = f'{_GEN_PREFIX}{self._gen_counter}'
        self.params[name] = value
        return _marker(name)

    def hoist_named(self, name):
        """
        Hoist a named substitution. Returns the marker form `{name}`.
        Repeated uses of the same original name share one marker —
        within a single pool, which means across all Resolvers using
        that pool.
        """
        name = str(name)
        if name in self._by_orig:
            return _marker(self._by_orig[name])
        marker = _param_name_for_subst(name)
        self._by_orig[name] = marker
        self.unbound[marker] = name
        return _marker(marker)


# ---- Shared identifier helper ---------------------------------------

def _quote_ident(name):
    """
    Quote a SQL identifier only when needed. Uses the SQL standard
    double-quoted form (Postgres, SQLite, and most others).
    """
    if _IDENT_RE.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


# ---- sqlize entry point ---------------------------------------------

def sqlize(path, *, driver, bindings=None, pool=None):
    """
    Translate a dotted path into a driver-specific `Resolver` instance
    carrying paramstyle-neutral SQL fragments.

    Arguments:
        path     — dotted path string or pre-parsed Dotted result.
        driver   — required. Driver name registered via `@driver`:
                   'asyncpg', 'psycopg2', 'psycopg', ...
        bindings — optional mapping/list used to resolve substitutions
                   at sqlize time. Path-position substitutions must be
                   resolved here or `TranslationError` is raised.
                   Unresolved value-position substitutions appear in
                   `r.unbound`.
        pool     — optional `ParamPool` shared across multiple
                   `sqlize()` calls. When Resolvers share a pool their
                   bind-parameter names are allocated from a single
                   counter so fragments from different Resolvers can
                   be composed (via `+`, a CTE envelope, etc.) without
                   marker-name collisions. Omit to give this Resolver
                   its own private pool.

    The driver picks both the SQL flavor (via the class's flavor
    mixin, e.g. `PostgresMixin`) and the build-time rendering behavior
    (paramstyle + cast).

    Literals on the RHS of guards are hoisted for injection safety.
    Substitutions that are valid SQL identifiers bind by that name;
    any other form (dotted, quoted, punctuation) gets a deterministic
    `_s_<hash>` marker.

    >>> r = sqlize("status = 'active'", driver='asyncpg')
    >>> r.build(r.where)
    ('status = $1', ['active'])
    >>> r2 = sqlize("status = 'active'", driver='psycopg2')
    >>> r2.build(r2.where)
    ('status = %(_p1)s', {'_p1': 'active'})
    """
    cls = _DRIVERS.get(driver)
    if cls is None:
        raise TranslationError(
            f'unknown driver: {driver!r}. '
            f'registered: {sorted(_DRIVERS)}'
        )
    parsed = parse(path, bindings=bindings, partial=True)
    r = cls()
    r.translate(parsed.ops, pool=pool)
    return r
