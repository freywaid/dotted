"""
Flavor-neutral core of dotted.sqlize.

Defines the public types (`SQLFragment`, `Resolver`, `ParamStyle`,
`TranslationError`) and the marker / paramstyle rendering machinery
that doesn't depend on any SQL dialect. Dialect-specific translation
lives in sibling modules (`pg.py` for Postgres).

Dialects register cast mappings with `_register_dialect_cast` at
import time. `Resolver.build` consults that registry when a marker
carries the `:cast` format spec — so translators in dialect modules
can mark individual placeholders as needing an explicit SQL cast
without teaching core about any particular SQL type system.
"""
import enum
import hashlib
import re
import string as _string


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


# ---- Dialect cast registry ------------------------------------------
#
# Dialect modules register a cast-inference function per paramstyle.
# When Resolver.build hits a marker carrying the `:cast` format spec,
# it consults the registry for the active paramstyle, calls the fn
# with the bound value, and appends `::<returned_cast>` to the
# rendered placeholder.

_DIALECT_CAST_FNS = {}   # paramstyle (str) → fn(value) → cast name or None


def _register_dialect_cast(paramstyle, fn):
    """
    Register a cast-inference function for a paramstyle. Called by
    dialect modules at import time.

    `fn(value)` returns an SQL cast name (like `'bigint'`) or `None`
    if the driver can handle that value natively. Registering `None`
    as the fn disables casting for the paramstyle.
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
    directly executable. Use `Resolver.build(sql, ...)` to render into
    driver-ready SQL.
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
    Result of `sqlize(...)`. Carries paramstyle-neutral SQL fragments
    as `SQLFragment` objects plus the union of their unbound bindings.

    Attributes
    ----------
    select : SQLFragment | None
        Extraction expression (e.g. `data #>> '{user,name}'`).
    where : SQLFragment | None
        Predicate.
    from_ : SQLFragment | None
        LATERAL / join fragment (future — None in v1).
    unbound : dict
        Union of all fragments' unbound mappings: bind name →
        original substitution name. Convenient for listing required
        bindings before calling `build`.

    Call `Resolver.build(sql, paramstyle=..., **bindings)` with any
    `SQLFragment` (usually composed from this Resolver's fragments) to
    render final SQL.
    """

    __slots__ = ('select', 'where', 'from_', 'unbound')

    def __init__(self, select=None, where=None, from_=None, unbound=None):
        self.select = select
        self.where = where
        self.from_ = from_
        self.unbound = dict(unbound or {})

    def __repr__(self):
        bits = []
        for attr in ('select', 'where', 'from_'):
            v = getattr(self, attr)
            if v is not None:
                bits.append(f'{attr}={v.text!r}')
        if self.unbound:
            bits.append(f'unbound={list(self.unbound.values())}')
        return f'Resolver({", ".join(bits)})'

    @classmethod
    def build(cls, sql, paramstyle='named', **bindings):
        """
        Render a composed `SQLFragment` into driver-ready SQL text
        plus a params structure.

        Returns `(text, params_or_args)`:
          - `paramstyle='named'` → `(sql_text_with_colon_names, dict)`
          - `paramstyle='pyformat'` → `(sql_text_with_percent_name, dict)`
          - `paramstyle='dollar-numeric'` → `(sql_text_with_$N, list)`
          - `paramstyle='numeric'` → `(sql_text_with_:N, list)`
          - `paramstyle='qmark'` → `(sql_text_with_?, list)`
          - `paramstyle='format'` → `(sql_text_with_%s, list)`

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
        if paramstyle not in _PARAMSTYLES:
            raise TranslationError(
                f'unsupported paramstyle: {paramstyle!r}'
            )
        # Validate bindings: every kwarg must correspond to an unbound
        # orig name in this fragment.
        expected_origs = set(sql.unbound.values())
        extra = set(bindings) - expected_origs
        if extra:
            raise TranslationError(
                f'unknown binding(s) for this fragment: {sorted(extra)}'
            )

        cast_fn = _DIALECT_CAST_FNS.get(paramstyle)

        # Build a per-paramstyle replacer. Replacers are called once
        # per marker occurrence — name-keyed styles cache results so
        # repeated markers share a single param; positional back-ref
        # styles (numeric, dollar-numeric) cache by position; qmark /
        # format emit a fresh arg for every occurrence.
        #
        # The `spec` arg is the format-spec portion of a marker
        # (e.g. `{marker:cast}`). `spec='cast'` means "emit an
        # explicit SQL cast based on the Python value's type" —
        # delegated to the dialect's registered cast function.
        missing = []
        if paramstyle in ('named', 'pyformat'):
            out_params = {}
            fmt = ':{name}' if paramstyle == 'named' else '%({name})s'

            def replacer(marker, spec):
                value = cls._lookup_value(sql, marker, bindings, missing)
                if value is _MISSING:
                    return ''
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
                value = cls._lookup_value(sql, marker, bindings, missing)
                if value is _MISSING:
                    return ''
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
                value = cls._lookup_value(sql, marker, bindings, missing)
                if value is _MISSING:
                    return ''
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

    @staticmethod
    def _lookup_value(sql, marker, bindings, missing):
        """
        Resolve a marker to its value using the fragment's own
        metadata, supplemented by caller-provided bindings. Records
        marker-side misses in `missing` and returns `_MISSING` so the
        caller can continue scanning (build raises after the full pass).
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


# ---- Param state for translators ------------------------------------

class _ParamState:
    """
    Shared state for hoisted params across a translate call and its
    sub-translators. ParamStyle-neutral — fragments emit `{name}`
    markers; final paramstyle is chosen at build time.

    Fields:
      params  — marker name → pre-hoisted value (literals + resolved
                substitutions)
      unbound — marker name → original substitution name (to be bound
                later)
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
        Repeated uses of the same original name share one marker.
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
