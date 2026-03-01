"""
"""
import decimal
import pyparsing as pp
from pyparsing import pyparsing_common as ppc

pp.ParserElement.enable_packrat()
from . import base
from . import groups
from . import matchers
from . import filters as flt
from . import access
from . import predicates
from . import recursive
from . import utypes
from . import wrappers
from . import containers as ct

S = pp.Suppress
L = pp.Literal
Opt = pp.Optional
ZM = pp.ZeroOrMore
OM = pp.OneOrMore
at = pp.Suppress('@')
equal = pp.Suppress('=')
not_equal = pp.Suppress('!=')
dot = pp.Suppress('.')
amp = pp.Suppress('&')
comma = pp.Suppress(',')
# Optional whitespace after comma so "(a, b)" and "(.a, .b)" parse (path/op group)
_comma_ws = comma + pp.Optional(pp.White()).suppress()
lb = pp.Suppress('[')
rb = pp.Suppress(']')
colon = pp.Suppress(':')
pipe = pp.Suppress('|')
slash = pp.Suppress('/')
backslash = pp.Suppress('\\')
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
transform_name = pp.Word(pp.alphas + '_', pp.alphanums + '_.')
quoted = pp.QuotedString('"', esc_char='\\') | pp.QuotedString("'", esc_char='\\')
plus = pp.Literal('+')
integer = ppc.signed_integer
none = pp.Literal('None').set_parse_action(matchers.NoneValue)
true = pp.Literal('True').set_parse_action(matchers.Boolean)
false = pp.Literal('False').set_parse_action(matchers.Boolean)

reserved = '.[]*:|+?/=,@&()!~#{}$<>' # {} for container syntax, $ for substitution, <> for comparisons
breserved = ''.join('\\' + i for i in reserved)
tilde = L('~')

# atomic ops
appender = pp.Literal('+').set_parse_action(matchers.Appender)
appender_unique = pp.Literal('+?').set_parse_action(matchers.AppenderUnique)

_numeric_quoted = S('#') + ((S("'") + ppc.number + S("'")) | (S('"') + ppc.number + S('"')))
numeric_quoted = _numeric_quoted.set_parse_action(matchers.NumericQuoted)

numeric_key = integer.copy().set_parse_action(matchers.Numeric)
numeric_slot = ppc.number.copy().set_parse_action(matchers.Numeric)

# Extended numeric literals: scientific (1e10), underscore (1_000), hex (0x1F), octal (0o17), binary (0b1010)
_numeric_extended_re = pp.Regex(
    r'[-]?0[xX][0-9a-fA-F]+'        # hex
    r'|[-]?0[oO][0-7]+'             # octal
    r'|[-]?0[bB][01]+'              # binary
    r'|[-]?[0-9][0-9_]*[eE][+-]?[0-9]+'  # scientific notation
    r'|[-]?[0-9]+(?:_[0-9]+)+'      # underscore separators
)
numeric_extended = _numeric_extended_re.copy().set_parse_action(matchers.NumericExtended)

# Exclude whitespace so "first )" parses as key "first", not "first "
word = (pp.Optional(backslash) + pp.CharsNotIn(reserved + ' \t\n\r')).set_parse_action(matchers.Word)
non_integer = pp.Regex(f'[-]?[0-9]+[^0-9{breserved}]+').set_parse_action(matchers.Word)
nameop = name.copy().set_parse_action(matchers.Word)

string = quoted.copy().set_parse_action(matchers.String)
bytes_literal = (S(L('b')) + quoted).set_parse_action(matchers.Bytes)
wildcard = pp.Literal('*').set_parse_action(matchers.Wildcard)
wildcard_first = pp.Literal('*?').set_parse_action(matchers.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().set_parse_action(matchers.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).set_parse_action(matchers.RegexFirst)
_escaped_dollar = pp.Regex(r'\\\$\${0,2}(\([^)]*\)|[0-9]*)').set_parse_action(
    lambda t: matchers.Word(t[0][1:]))
_reference = pp.Regex(r'\$\$\([^)]+\)').set_parse_action(
    lambda t: matchers.Reference(t[0][3:-1]))
_named_subst = pp.Regex(r'\$\([a-zA-Z_]\w*\)').set_parse_action(
    lambda t: matchers.NamedSubst(t[0][2:-1]))
_raw_subst = pp.Regex(r'\$[0-9]+').set_parse_action(
    lambda t: matchers.PositionalSubst(int(t[0][1:])))
subst = _escaped_dollar | _reference | _named_subst | _raw_subst
slice = pp.Optional(integer | plus) + ':' + pp.Optional(integer | plus) \
         + pp.Optional(':') + pp.Optional(integer | plus)

_common_pats = wildcard_first | wildcard | subst | regex_first | regex
_commons = bytes_literal | string | _common_pats | numeric_quoted


# ---------------------------------------------------------------------------
# Container grammar — pattern containers (for value/filter context)
# ---------------------------------------------------------------------------

# Comma with optional surrounding whitespace for container interiors
_ccomma = pp.Optional(pp.White()).suppress() + comma + pp.Optional(pp.White()).suppress()

# Forward for recursive container values
container_value = pp.Forward()

# Scalar atoms usable inside containers (same as value atoms minus containers)
_val_atoms = bytes_literal | string | wildcard | subst | regex | numeric_quoted | none | true | false | numeric_extended | numeric_key

# Glob inside containers: ... with optional /regex/ then optional count
# Regex pattern for glob (unsuppressed slashes handled inline)
_glob_regex = (slash + pp.Regex(r'(\\/|[^/])+') + slash).set_parse_action(matchers.Regex)

# Count forms: min:max, min:, max (single number)
_glob_count_full = integer + S(':') + integer       # min:max
_glob_count_min = integer + S(':')                   # min:
_glob_count_max = integer.copy()                     # max

# Glob: ...  with optional /regex/ then optional count
_ellipsis = S(L('.') + L('.') + L('.'))

def _make_glob_action(has_regex, has_count):
    """
    Build a parse action for a glob form.
    """
    def action(t):
        t = list(t)
        i = 0
        pattern = None
        min_count = 0
        max_count = None
        if has_regex:
            pattern = t[i]
            i += 1
        if has_count == 'full':
            min_count = t[i]
            max_count = t[i + 1]
        elif has_count == 'min':
            min_count = t[i]
        elif has_count == 'max':
            max_count = t[i]
        return ct.Glob(pattern=pattern, min_count=min_count, max_count=max_count)
    return action

# Glob variants — most specific first
_glob_re_full = (_ellipsis + _glob_regex + _glob_count_full).set_parse_action(_make_glob_action(True, 'full'))
_glob_re_min = (_ellipsis + _glob_regex + _glob_count_min).set_parse_action(_make_glob_action(True, 'min'))
_glob_re_max = (_ellipsis + _glob_regex + _glob_count_max).set_parse_action(_make_glob_action(True, 'max'))
_glob_re_bare = (_ellipsis + _glob_regex).set_parse_action(_make_glob_action(True, None))
_glob_bare_full = (_ellipsis + _glob_count_full).set_parse_action(_make_glob_action(False, 'full'))
_glob_bare_min = (_ellipsis + _glob_count_min).set_parse_action(_make_glob_action(False, 'min'))
_glob_bare_max = (_ellipsis + _glob_count_max).set_parse_action(_make_glob_action(False, 'max'))
_glob_bare = _ellipsis.copy().set_parse_action(lambda: [ct.Glob()])

container_glob = (
    _glob_re_full | _glob_re_min | _glob_re_max | _glob_re_bare |
    _glob_bare_full | _glob_bare_min | _glob_bare_max | _glob_bare
)

# String/bytes glob: "prefix"..."suffix", b"hello"..."world", etc.
# Must have at least one string/bytes AND one glob (otherwise it's a plain string or bare glob).
# If any part is b"...", produces BytesGlob (naked strings encoded to bytes);
# otherwise produces StringGlob.
_strblob_atom = bytes_literal.copy() | quoted.copy()
_strblob_part = _strblob_atom | container_glob
_strblob_a = _strblob_atom + container_glob + ZM(_strblob_part)  # starts with str/bytes
_strblob_b = container_glob + _strblob_atom + ZM(_strblob_part)  # starts with glob
def _make_strblob(t):
    """
    Produce StringGlob or BytesGlob depending on whether any b"..." part is present.
    """
    has_bytes = any(isinstance(p, matchers.Bytes) for p in t)
    if has_bytes:
        def to_bytes(p):
            if isinstance(p, matchers.Bytes):
                return p.value
            if isinstance(p, str):
                return p.encode()
            return p
        return ct.BytesGlob(*tuple(to_bytes(p) for p in t))
    return ct.StringGlob(*t)
string_glob = (_strblob_a | _strblob_b).set_parse_action(lambda t: [_make_strblob(t)])

# Container element: can be a nested container, glob, string/bytes glob, or scalar/pattern atom
_container_elem = container_value | string_glob | container_glob | _val_atoms

# List: [elem, elem, ...]
_container_list_inner = _container_elem + ZM(_ccomma + _container_elem)
_container_list_body = S('[') + Opt(_container_list_inner) + S(']')

def _make_container_list(prefix):
    """
    Parse action for container list with given type prefix.
    """
    def action(t):
        return ct.ContainerList(*t, type_prefix=prefix)
    return action

# Unprefixed: [...]
container_list = _container_list_body.copy().set_parse_action(_make_container_list(None))

# Prefixed: l[...], t[...]
container_list_l = (S(L('l')) + _container_list_body.copy()).set_parse_action(_make_container_list('l'))
container_list_t = (S(L('t')) + _container_list_body.copy()).set_parse_action(_make_container_list('t'))

# Dict entry: key_pattern : val_pattern
_dict_key = _container_elem
_dict_val = _container_elem
_dict_entry = (_dict_key + S(':') + pp.Optional(pp.White()).suppress() + _dict_val).set_parse_action(lambda t: [(t[0], t[1])])

# Dict glob entry: glob : val_pattern  (glob on key side)
_dict_glob_entry = (container_glob + S(':') + pp.Optional(pp.White()).suppress() + _container_elem).set_parse_action(
    lambda t: [ct.DictGlobEntry(t[0], t[1])])
# Dict glob entry bare: glob alone (no value constraint)
_dict_glob_entry_bare = container_glob.copy().set_parse_action(
    lambda t: [ct.DictGlobEntry(t[0], None)])

_dict_any_entry = _dict_glob_entry | _dict_glob_entry_bare | _dict_entry
_container_dict_inner = _dict_any_entry + ZM(_ccomma + _dict_any_entry)
_container_dict_body = S('{') + _container_dict_inner + S('}')

def _make_container_dict(prefix):
    """
    Parse action for container dict with given type prefix.
    """
    def action(t):
        return ct.ContainerDict(*t, type_prefix=prefix)
    return action

# Unprefixed: {...: ...}
container_dict = _container_dict_body.copy().set_parse_action(_make_container_dict(None))

# Prefixed: d{...}
container_dict_d = (S(L('d')) + _container_dict_body.copy()).set_parse_action(_make_container_dict('d'))

# Set: {elem, elem, ...} — no colons (disambiguated from dict by trying dict first)
_container_set_inner = _container_elem + ZM(_ccomma + _container_elem)
_container_set_body = S('{') + _container_set_inner + S('}')

def _make_container_set(prefix):
    """
    Parse action for container set with given type prefix.
    """
    def action(t):
        return ct.ContainerSet(*t, type_prefix=prefix)
    return action

# Unprefixed: {elem, ...}
container_set = _container_set_body.copy().set_parse_action(_make_container_set(None))

# Prefixed: s{...}, fs{...}
container_set_s = (S(L('s')) + _container_set_body.copy()).set_parse_action(_make_container_set('s'))
container_set_fs = (S(L('fs')) + _container_set_body.copy()).set_parse_action(_make_container_set('fs'))

# Empty containers
_empty_list_body = S('[') + S(']')
container_empty_list = _empty_list_body.copy().set_parse_action(lambda: [ct.ContainerList(type_prefix=None)])
container_empty_list_l = (S(L('l')) + _empty_list_body.copy()).set_parse_action(lambda: [ct.ContainerList(type_prefix='l')])
container_empty_list_t = (S(L('t')) + _empty_list_body.copy()).set_parse_action(lambda: [ct.ContainerList(type_prefix='t')])

_empty_brace_body = S('{') + S('}')
container_empty_dict = _empty_brace_body.copy().set_parse_action(lambda: [ct.ContainerDict(type_prefix=None)])
container_empty_dict_d = (S(L('d')) + _empty_brace_body.copy()).set_parse_action(lambda: [ct.ContainerDict(type_prefix='d')])
container_empty_set_s = (S(L('s')) + _empty_brace_body.copy()).set_parse_action(lambda: [ct.ContainerSet(type_prefix='s')])
container_empty_set_fs = (S(L('fs')) + _empty_brace_body.copy()).set_parse_action(lambda: [ct.ContainerSet(type_prefix='fs')])

# Resolve forward: try prefixed before unprefixed, empties before non-empty, dict before set
container_value <<= (
    container_list_l | container_list_t |
    container_empty_list_l | container_empty_list_t | container_empty_list |
    container_list |
    container_dict_d |
    container_empty_dict_d | container_empty_dict |
    container_empty_set_fs | container_empty_set_s |
    container_set_fs | container_set_s |
    container_dict | container_set
)


# ---------------------------------------------------------------------------
# Concrete containers (for transform args — no patterns/globs allowed)
# ---------------------------------------------------------------------------

_concrete_atoms = quoted.copy().set_parse_action(lambda t: [t[0]]) | ppc.number | none | true | false

# Forward for recursive concrete values
concrete_value = pp.Forward()
_concrete_elem = concrete_value | _concrete_atoms

# Concrete list: [elem, ...]
_concrete_list_inner = _concrete_elem + ZM(_ccomma + _concrete_elem)
_concrete_list_body = S('[') + Opt(_concrete_list_inner) + S(']')

concrete_list = _concrete_list_body.copy().set_parse_action(lambda t: [list(t)])
concrete_list_t = (S(L('t')) + _concrete_list_body.copy()).set_parse_action(lambda t: [tuple(t)])
concrete_list_l = (S(L('l')) + _concrete_list_body.copy()).set_parse_action(lambda t: [list(t)])

# Concrete dict entry: scalar : scalar
_concrete_dict_entry = (_concrete_elem + S(':') + pp.Optional(pp.White()).suppress() + _concrete_elem).set_parse_action(lambda t: [(t[0], t[1])])
_concrete_dict_inner = _concrete_dict_entry + ZM(_ccomma + _concrete_dict_entry)
_concrete_dict_body = S('{') + _concrete_dict_inner + S('}')

concrete_dict = _concrete_dict_body.copy().set_parse_action(lambda t: [dict(t.as_list())])
concrete_dict_d = (S(L('d')) + _concrete_dict_body.copy()).set_parse_action(lambda t: [dict(t.as_list())])

# Concrete set: {elem, ...}
_concrete_set_inner = _concrete_elem + ZM(_ccomma + _concrete_elem)
_concrete_set_body = S('{') + _concrete_set_inner + S('}')

concrete_set = _concrete_set_body.copy().set_parse_action(lambda t: [set(t)])
concrete_set_s = (S(L('s')) + _concrete_set_body.copy()).set_parse_action(lambda t: [set(t)])
concrete_set_fs = (S(L('fs')) + _concrete_set_body.copy()).set_parse_action(lambda t: [frozenset(t)])

# Concrete empties
_concrete_empty_list = (S('[') + S(']')).set_parse_action(lambda: [[]])
_concrete_empty_list_l = (S(L('l')) + S('[') + S(']')).set_parse_action(lambda: [[]])
_concrete_empty_list_t = (S(L('t')) + S('[') + S(']')).set_parse_action(lambda: [()])
_concrete_empty_dict = (S('{') + S('}') ).set_parse_action(lambda: [{}])
_concrete_empty_dict_d = (S(L('d')) + S('{') + S('}')).set_parse_action(lambda: [{}])
_concrete_empty_set_s = (S(L('s')) + S('{') + S('}')).set_parse_action(lambda: [set()])
_concrete_empty_set_fs = (S(L('fs')) + S('{') + S('}')).set_parse_action(lambda: [frozenset()])

# Resolve forward: try prefixed before unprefixed, empties before non-empty, dict before set
concrete_value <<= (
    concrete_list_l | concrete_list_t |
    _concrete_empty_list_l | _concrete_empty_list_t | _concrete_empty_list |
    concrete_list |
    concrete_dict_d |
    _concrete_empty_dict_d | _concrete_empty_dict |
    _concrete_empty_set_fs | _concrete_empty_set_s |
    concrete_set_fs | concrete_set_s |
    concrete_dict | concrete_set
)


# ---------------------------------------------------------------------------
# value and key (updated to include containers)
# ---------------------------------------------------------------------------

# value is a Forward to allow value_group to reference it recursively
value = pp.Forward()
_value_group_inner = value + OM(_ccomma + value)
value_group = (S('(') + _value_group_inner + S(')')).set_parse_action(lambda t: [ct.ValueGroup(*t)])
value <<= value_group | container_value | string_glob | bytes_literal | string | wildcard | subst | regex | numeric_quoted | none | true | false | numeric_extended | numeric_key
key = _commons | numeric_extended | non_integer | numeric_key | word

# Transform: |name or |name:param — defined early so filters and guards can reference it
targ = concrete_value | subst | quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().set_parse_action(lambda: [None])
transform = (transform_name.copy() + ZM(param)).set_parse_action(lambda s, loc, t: base.Transform(*t))
transforms = ZM(pipe + transform)

# filter_key: dotted paths (user.id), slot paths (tags[*], tags[0]), slice (name[:5], name[-5:]).
# Dot introduces a key part only; slot/slice directly after key. Try slice before slot so [:] parses as slice.
_filter_key_part = string | _common_pats | numeric_extended | non_integer | numeric_key | word
filter_key_slice = (lb + slice + rb).set_parse_action(lambda t: access.Slice(*t))
filter_key_slot = (lb + (_commons | numeric_extended | numeric_slot) + rb).set_parse_action(lambda t: access.Slot(t[0]))
_filter_key_segment = _filter_key_part | filter_key_slice | filter_key_slot
filter_key = pp.Group(
    _filter_key_segment + ZM(filter_key_slice | filter_key_slot | (dot + _filter_key_part))
).set_parse_action(flt.FilterKey)

# Single operator regex: longest-first via regex alternation.
_pred_op_re = pp.Regex('|'.join(predicates.PRED_OPS))

def _filter_single_action(t):
    """
    Dispatch filter to the correct class based on the operator string.
    """
    items = list(t)
    val = items[-1]
    op_str = items[-2]
    rest = items[:-2]
    cls = flt.FILTER_PRED_CLS[op_str]
    return cls(rest + [val])

_filter_single_all = (filter_key + ZM(pipe + transform) + _pred_op_re + value).set_parse_action(_filter_single_action)

# Recursive filter expression with grouping
filter_expr = pp.Forward()

# Atom: single comparison or grouped expression
lparen = pp.Suppress('(')
rparen = pp.Suppress(')')
bang = pp.Suppress('!')
filter_group = (lparen + filter_expr + rparen).set_parse_action(flt.FilterGroup)
filter_atom = filter_group | _filter_single_all

# NOT: ! prefix binds tightest (higher precedence than &)
filter_not = (bang + filter_atom).set_parse_action(flt.FilterNot) | filter_atom

# AND: not-expressions joined by & (higher precedence than ,)
filter_and = (filter_not + OM(amp + filter_not)).set_parse_action(flt.FilterAnd) | filter_not

# OR: and-groups joined by , (lowest precedence)
filter_or = (filter_and + OM(comma + filter_and)).set_parse_action(flt.FilterOr) | filter_and

filter_expr <<= filter_or

# Optional ? suffix for first-match
filter_keyvalue_first = (filter_expr + S('?')).set_parse_action(flt.FilterKeyValueFirst)

filters = filter_keyvalue_first | filter_expr

# Path segment type restrictions: :type, :(t1, t2), :!type, :!(t1, t2)
_type_name = pp.one_of(list(utypes.TYPE_REGISTRY.keys()))
_type_tuple = S('(') + _type_name + ZM(_comma_ws + _type_name) + S(')')
_type_spec = _type_tuple | _type_name

_type_neg = (colon + S('!') + _type_spec).set_parse_action(
    lambda t: utypes.TypeSpec(*(utypes.TYPE_REGISTRY[n] for n in t), negate=True))
_type_pos = (colon + _type_spec).set_parse_action(
    lambda t: utypes.TypeSpec(*(utypes.TYPE_REGISTRY[n] for n in t)))
type_restriction = _type_neg | _type_pos

def _keycmd_guarded_action(pred_op):
    """
    Parse action for key with optional type restriction, transforms, and value guard.
    """
    def action(t):
        t = list(t)
        guard = t[-1]
        rest = t[:-1]
        tr = None
        xforms = []
        args = []
        filters = []
        for item in rest:
            if isinstance(item, utypes.TypeSpec):
                tr = item
            elif isinstance(item, flt.FilterOp):
                filters.append(item)
            elif isinstance(item, base.Transform):
                xforms.append(item)
            else:
                args.append(item)
        inner = access.Key(*args)
        if tr:
            inner = tr.wrap(inner)
        if filters:
            inner = wrappers.FilterWrap(inner, filters)
        return wrappers.ValueGuard(inner, guard, pred_op=pred_op, transforms=xforms)
    return action

def _keycmd_guarded_dispatch(t):
    """
    Dispatch keycmd guard to the correct pred_op based on the operator string.
    """
    t = list(t)
    op_str = t[-2]
    t_clean = t[:-2] + [t[-1]]
    return _keycmd_guarded_action(predicates.PRED_OPS[op_str])(t_clean)

_keycmd_guarded_all = pp.And([key, Opt(type_restriction), ZM(amp + filters), ZM(pipe + transform), _pred_op_re, value]).set_parse_action(_keycmd_guarded_dispatch)

def _keycmd_action(t):
    """
    Parse action for key with optional type restriction.
    """
    t = list(t)
    tr = None
    args = []
    filters = []
    for item in t:
        if isinstance(item, utypes.TypeSpec):
            tr = item
        elif isinstance(item, flt.FilterOp):
            filters.append(item)
        else:
            args.append(item)
    result = access.Key(*args)
    if tr:
        result = tr.wrap(result)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    return result

keycmd = (key + Opt(type_restriction) + ZM(amp + filters)).set_parse_action(_keycmd_action)

_slotguts = (_commons | numeric_extended | numeric_slot) + ZM(amp + filters)

def _slotcmd_guarded_action(pred_op):
    """
    Parse action for slot with optional type restriction, transforms, and value guard.
    """
    def action(t):
        t = list(t)
        guard = t[-1]
        rest = t[:-1]
        tr = None
        nop = False
        xforms = []
        args = []
        filters = []
        for item in rest:
            if isinstance(item, utypes.TypeSpec):
                tr = item
            elif item == '~':
                nop = True
            elif isinstance(item, flt.FilterOp):
                filters.append(item)
            elif isinstance(item, base.Transform):
                xforms.append(item)
            else:
                args.append(item)
        inner = access.Slot(*args)
        if filters:
            inner = wrappers.FilterWrap(inner, filters)
        if tr:
            inner = tr.wrap(inner)
        if nop:
            inner = wrappers.NopWrap(inner)
        return wrappers.ValueGuard(inner, guard, pred_op=pred_op, transforms=xforms)
    return action

def _slotcmd_guarded_dispatch(t):
    """
    Dispatch slotcmd guard to the correct pred_op based on the operator string.
    """
    t = list(t)
    op_str = t[-2]
    t_clean = t[:-2] + [t[-1]]
    return _slotcmd_guarded_action(predicates.PRED_OPS[op_str])(t_clean)

_slotcmd_guarded_all = pp.And([lb, Opt(L('~')), _slotguts, rb, Opt(type_restriction), ZM(pipe + transform), _pred_op_re, value]).set_parse_action(_slotcmd_guarded_dispatch)

def _slotcmd_action(t):
    """
    Parse action for slot with optional type restriction.
    """
    t = list(t)
    tr = None
    nop = False
    args = []
    filters = []
    for item in t:
        if isinstance(item, utypes.TypeSpec):
            tr = item
        elif item == '~':
            nop = True
        elif isinstance(item, flt.FilterOp):
            filters.append(item)
        else:
            args.append(item)
    result = access.Slot(*args)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    if tr:
        result = tr.wrap(result)
    if nop:
        result = wrappers.NopWrap(result)
    return result

slotcmd =(lb + Opt(L('~')) + _slotguts + rb + Opt(type_restriction)).set_parse_action(_slotcmd_action)

# @~ and ~@ both produce NopWrap (canonical form @~)
def _attr_action(t, nop=False):
    """
    Parse action for attr with optional type restriction.
    """
    t = list(t)
    tr = None
    args = []
    filters = []
    for item in t:
        if isinstance(item, utypes.TypeSpec):
            tr = item
        elif item == '~':
            pass  # consumed by grammar, marks nop
        elif isinstance(item, flt.FilterOp):
            filters.append(item)
        else:
            args.append(item)
    result = access.Attr(*args)
    if tr:
        result = tr.wrap(result)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    if nop:
        result = wrappers.NopWrap(result)
    return result

_attr_nop = ((at + tilde) | (tilde + at)) + (nameop | _common_pats) + Opt(type_restriction) + ZM(amp + filters)
_attr_nop = _attr_nop.set_parse_action(lambda t: _attr_action(t, nop=True))
_attr_plain = (at + (nameop | _common_pats) + Opt(type_restriction) + ZM(amp + filters)).set_parse_action(lambda t: _attr_action(t))
attrcmd = _attr_nop | _attr_plain

slotspecial = (lb + (appender_unique | appender) + rb).set_parse_action(access.SlotSpecial)

slicecmd = (lb + Opt(L('~')) + Opt(slice) + rb).set_parse_action(
    lambda t: wrappers.NopWrap(access.Slice(*t[1:])) if t and t[0] == '~' else access.Slice(*t))
slicefilter = (lb + filters + ZM(amp + filters) + rb).set_parse_action(access.SliceFilter)

# Cut markers: ## = soft cut, # = hard cut (try ## first so ## isn't parsed as # + #)
softcut_marker = L('##')
cut_marker = softcut_marker | L('#')

# Slot grouping: [(*&filter, +)] for disjunction inside slots; [(*&filter#, +)] for cut
def _slot_item_action(t):
    args = []
    filters = []
    for item in t:
        if isinstance(item, flt.FilterOp):
            filters.append(item)
        else:
            args.append(item)
    result = access.Slot(*args)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    return result

_slot_item_plain = _slotguts.copy().set_parse_action(_slot_item_action)
_slot_item_nop = (tilde + _slotguts.copy()).set_parse_action(lambda t: wrappers.NopWrap(_slot_item_action(t[1:])))
_slot_item = _slot_item_nop | _slot_item_plain | (appender_unique | appender).copy().set_parse_action(access.SlotSpecial)
_slot_group_term = pp.Group(_slot_item + Opt(cut_marker))
_slot_group_inner = _slot_group_term + ZM(_comma_ws + _slot_group_term)
slotgroup = (lb + lparen + _slot_group_inner + rparen + rb).set_parse_action(groups.slot_to_opgroup)
slotgroup_first = (lb + lparen + _slot_group_inner + rparen + S('?') + rb).set_parse_action(groups.slot_to_opgroup_first)

# Unified inner expression: single precedence tower for !, &, , operators
# Used both inside parens (inner_grouped) and at top levgroups.
# Atoms are op_seqs (which subsume bare keys as single-item sequences).
# Precedence: ! (tightest) > & > , (loosest)
inner_expr = pp.Forward()
def _grouped_with_type_restriction(parsed_result, first=False):
    """
    Parse action for grouped expressions with optional type restriction.
    Last token may be a TypeSpec; if so, wrap the OpGroup in TypeRestriction.
    """
    items = list(parsed_result)
    tr = None
    if items and isinstance(items[-1], utypes.TypeSpec):
        tr = items.pop()
    if first:
        grp = groups.inner_to_opgroup_first(items)
    else:
        grp = groups.inner_to_opgroup(items)
    if tr:
        grp = tr.wrap(grp)
    return grp

inner_grouped = (lparen + inner_expr + rparen + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
inner_grouped_first = (lparen + inner_expr + rparen + S('?') + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t, first=True))
# Explicit-op grouped: used mid-path where bare-key inference is not allowed.
# Uses a separate precedence tower (_explicit_inner_expr) that only allows
# op_seqs built from _op_seq_cont (no bare keys even in first position).
# The FollowedBy is a fast-fail optimization — if the first char after ( can't
# start an explicit branch, don't even try the tower.
_explicit_op_start = pp.FollowedBy(pp.Char('.@[!~('))
_explicit_inner_expr = pp.Forward()
_inner_grouped_explicit = (lparen + _explicit_op_start + _explicit_inner_expr + rparen + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
_inner_grouped_explicit_first = (lparen + _explicit_op_start + _explicit_inner_expr + rparen + S('?') + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t, first=True))
# Bare-key grouped: first branch does NOT start with an access op.
# Used with prefix shorthand (.(a,b), @(a,b)) where the prefix distributes.
# Allows ! and ~ (modifiers) but rejects . @ [ ( (access ops / nested groups).
_bare_key_start = ~pp.FollowedBy(pp.Char('.@[('))
_inner_grouped_bare = (lparen + _bare_key_start + inner_expr + rparen + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
_inner_grouped_bare_first = (lparen + _bare_key_start + inner_expr + rparen + S('?') + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t, first=True))

# Recursive operator: ** (recursive wildcard) and *pattern (recursive chain-following)
# Depth slice: :start:stop:step — uses sentinel for missing values to preserve position
_rec_none = pp.Empty().set_parse_action(lambda: [None])
_rec_depth_start = colon + (integer | _rec_none)
_rec_depth_stop = colon + (integer | _rec_none)
_rec_depth_step = colon + (integer | _rec_none)
_rec_depth = _rec_depth_start + Opt(_rec_depth_stop + Opt(_rec_depth_step))

def _make_recursive(t, first=False):
    t = list(t)
    inner = t[0]
    rest = t[1:]
    depth_start = depth_stop = depth_step = None
    filt = []
    depth_vals = []
    type_spec = None
    for item in rest:
        if isinstance(item, flt.FilterOp):
            filt.append(item)
        elif isinstance(item, utypes.TypeSpec):
            type_spec = item
        else:
            depth_vals.append(item)
    if len(depth_vals) >= 1:
        depth_start = depth_vals[0]
    if len(depth_vals) >= 2:
        depth_stop = depth_vals[1]
    if len(depth_vals) >= 3:
        depth_step = depth_vals[2]
    cls = recursive.RecursiveFirst if first else recursive.Recursive
    # *(expr) form: inner is an OpGroup or TypeRestriction wrapping one
    accessors = None
    if isinstance(inner, (groups.OpGroup, wrappers.TypeRestriction)):
        accessors = _extract_accessors(inner)
        inner = matchers.Wildcard()
    # Type restriction on the recursive operator (e.g. **:!(str, bytes))
    # Distribute to each accessor branch via TypeRestriction wrapper
    if type_spec is not None:
        if accessors is not None:
            accessors = _distribute_type_restriction(accessors, type_spec)
        else:
            accessors = ((type_spec.wrap(access.Key(inner)),),)
    r = cls(inner, accessors=accessors,
            depth_start=depth_start, depth_stop=depth_stop, depth_step=depth_step)
    if filt:
        r.filters = tuple(filt)
    return r


def _extract_accessors(opgroup):
    """
    Extract accessor branches from an OpGroup for *(expr) syntax.
    Each branch should be a single AccessOp (Key, Slot, Attr).
    Nested OpGroups are flattened (e.g. *((*#, [*]), @*) → *(*#, [*], @*)).
    TypeRestriction wrapping an OpGroup distributes the restriction to each branch.
    Returns a branches tuple preserving cut/softcut sentinels.
    """
    # TypeRestriction wrapping an OpGroup: distribute to each accessor
    if isinstance(opgroup, wrappers.TypeRestriction) and isinstance(opgroup.inner, groups.OpGroup):
        spec = utypes.TypeSpec(*opgroup.types, negate=opgroup.negate)
        return _distribute_type_restriction(_extract_accessors(opgroup.inner), spec)
    result = []
    for item in opgroup.branches:
        if item is utypes.BRANCH_CUT or item is utypes.BRANCH_SOFTCUT:
            result.append(item)
            continue
        branch = item
        if len(branch) == 1 and isinstance(branch[0], groups.OpGroup):
            # Flatten nested group: inline its branches
            result.extend(_extract_accessors(branch[0]))
        elif len(branch) == 1 and isinstance(branch[0], (access.AccessOp, wrappers.TypeRestriction)):
            result.append(branch)
        else:
            raise ValueError(
                f"*(expr) branches must be single access ops (Key, Slot, Attr), "
                f"got {branch!r}"
            )
    return tuple(result)


def _distribute_type_restriction(branches, type_spec):
    """
    Wrap each accessor in branches with a TypeRestriction from type_spec.
    Preserves cut/softcut sentinels.
    """
    result = []
    for item in branches:
        if item is utypes.BRANCH_CUT or item is utypes.BRANCH_SOFTCUT:
            result.append(item)
        else:
            result.append((type_spec.wrap(item[0]),))
    return tuple(result)

# ** and *pattern base expressions (no parse actions — shared by guarded/first/plain variants)
_rec_dstar_prefix = S(L('*') + L('*'))
# *(expr) grouped form: inner is a group expression containing accessor ops
_rec_group_inner = (lparen + inner_expr + rparen + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
_rec_inner = _rec_group_inner | string | regex_first | regex | numeric_quoted | numeric_extended | non_integer | numeric_key | word
_rec_pat_prefix = S(L('*'))

def _dstar_body():
    return _rec_dstar_prefix + Opt(type_restriction) + Opt(_rec_depth) + ZM(amp + filters)

def _pat_body():
    return _rec_pat_prefix + _rec_inner + Opt(type_restriction) + Opt(_rec_depth) + ZM(amp + filters)

# Helpers to separate transforms from other tokens in recursive guarded forms
def _extract_transforms(tokens):
    """
    Extract Transform objects from a token list.
    """
    return [t for t in tokens if isinstance(t, base.Transform)]

def _extract_non_transform(tokens):
    """
    Return tokens with Transform objects removed.
    """
    return [t for t in tokens if not isinstance(t, base.Transform)]

# ValueGuard composition: **=7, *name!=None, **|int=7, etc. (must try before plain forms)
def _rec_dstar_guarded_action(pred_op):
    """
    Parse action for recursive ** with value guard.
    """
    def action(t):
        return wrappers.ValueGuard(
            _make_recursive([matchers.Wildcard()] + _extract_non_transform(list(t[:-1]))),
            t[-1], pred_op=pred_op, transforms=_extract_transforms(list(t[:-1])))
    return action

def _rec_pat_guarded_action(pred_op):
    """
    Parse action for recursive *pattern with value guard.
    """
    def action(t):
        return wrappers.ValueGuard(
            _make_recursive(_extract_non_transform(list(t[:-1]))),
            t[-1], pred_op=pred_op, transforms=_extract_transforms(list(t[:-1])))
    return action

def _rec_dstar_guarded_dispatch(t):
    """
    Dispatch recursive ** guard based on operator string.
    """
    t = list(t)
    op_str = t[-2]
    t_clean = t[:-2] + [t[-1]]
    return _rec_dstar_guarded_action(predicates.PRED_OPS[op_str])(t_clean)

def _rec_pat_guarded_dispatch(t):
    """
    Dispatch recursive *pattern guard based on operator string.
    """
    t = list(t)
    op_str = t[-2]
    t_clean = t[:-2] + [t[-1]]
    return _rec_pat_guarded_action(predicates.PRED_OPS[op_str])(t_clean)

_rec_dstar_guarded_all = (_dstar_body() + ZM(pipe + transform) + _pred_op_re + value).set_parse_action(_rec_dstar_guarded_dispatch)
_rec_pat_guarded_all = (_pat_body() + ZM(pipe + transform) + _pred_op_re + value).set_parse_action(_rec_pat_guarded_dispatch)

# First-match: **?, *name?
rec_dstar_first = (_dstar_body() + S('?')).set_parse_action(
    lambda t: _make_recursive([matchers.Wildcard()] + list(t), first=True))
rec_pat_first = (_pat_body() + S('?')).set_parse_action(
    lambda t: _make_recursive(t, first=True))

# Plain: **, *name
rec_dstar = _dstar_body().set_parse_action(
    lambda t: _make_recursive([matchers.Wildcard()] + list(t)))
rec_pat = _pat_body().set_parse_action(
    lambda t: _make_recursive(t))

recursive_op = (
    _rec_dstar_guarded_all | _rec_pat_guarded_all |
    rec_dstar_first | rec_dstar | rec_pat_first | rec_pat)

empty = pp.Empty().set_parse_action(access.Empty)

# Operation grouping: (.b,[]) for grouping operation sequences
# An op_seq is a sequence of operations like .key, [slot], @attr
# .~key and ~.key both produce NopWrap(key); .key produces key
_dot_keycmd_guarded_nop = (((dot + tilde) | (tilde + dot)) + _keycmd_guarded_all).set_parse_action(
    lambda t: wrappers.NopWrap(t[-1]))
_dot_keycmd_guarded_plain = (dot + _keycmd_guarded_all).set_parse_action(lambda t: t[0])
_dot_keycmd_nop = (((dot + tilde) | (tilde + dot)) + keycmd).set_parse_action(
    lambda t: wrappers.NopWrap(t[-1]))
_dot_keycmd = (dot + keycmd).set_parse_action(lambda t: t[0])
# op_seq_item uses _nop_wrap (defined later); Forward for circular ref
op_seq_item = pp.Forward()     # first item: allows bare (a,b)
_op_seq_cont = pp.Forward()    # continuation: requires explicit ops
op_seq = pp.Group(op_seq_item + ZM(_op_seq_cont))

# Continuation items (absorbed from former multi grammar):
# .~ and ~. with grouped expressions — prefix shorthand requires bare keys inside
_dot_nop_grouped = ((dot + tilde) | (tilde + dot)) + (_inner_grouped_bare_first | _inner_grouped_bare)
_dot_nop_grouped = _dot_nop_grouped.set_parse_action(lambda t: wrappers.NopWrap(t[-1]))
_dot_plain_grouped = (dot + (_inner_grouped_bare_first | _inner_grouped_bare)).set_parse_action(lambda t: t[0])
# .recursive
_dot_recursive = (dot + recursive_op).set_parse_action(lambda t: t[0])
# @(group) — attribute group access: @(a,b) == (@a,@b), prefix requires bare keys
_at_plain_grouped = (at + (_inner_grouped_bare_first | _inner_grouped_bare)).set_parse_action(
    lambda t: groups.attr_transform_opgroup(t[0])
)
_at_nop_grouped = ((at + tilde) | (tilde + at)) + (_inner_grouped_bare_first | _inner_grouped_bare)
_at_nop_grouped = _at_nop_grouped.set_parse_action(
    lambda t: wrappers.NopWrap(groups.attr_transform_opgroup(t[-1]))
)

# NOP (~): match but don't update
_nop_inner = inner_grouped_first | inner_grouped | recursive_op | _keycmd_guarded_all | keycmd | attrcmd | slotgroup_first | slotgroup | _slotcmd_guarded_all | slotcmd | slotspecial | slicefilter | slicecmd
_nop_wrap = (tilde + _nop_inner).set_parse_action(lambda t: wrappers.NopWrap(t[1]))

# Explicit NOP (~): like _nop_wrap but inner must start with an access op (./@/[]).
# Bare keys (keycmd) are excluded — ~key is not allowed inside explicit groups.
_explicit_nop_inner = (
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    recursive_op |
    _dot_keycmd_guarded_nop | _dot_keycmd_guarded_plain |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    _slotcmd_guarded_all | slotcmd |
    slotspecial | slicefilter | slicecmd
)
_explicit_nop_wrap = (tilde + _explicit_nop_inner).set_parse_action(lambda t: wrappers.NopWrap(t[1]))

# Resolve forward: op_seq_item is the atom of op_seq, used in the precedence tower
# Continuation items: everything that can appear after the first item in an op_seq.
# Bare (a,b) is NOT allowed here — only explicit-op groups like (.a,.b).
_op_seq_cont_items = (
    _nop_wrap |
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    recursive_op |
    _keycmd_guarded_all | keycmd |
    _dot_keycmd_guarded_nop | _dot_keycmd_guarded_plain |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    _slotcmd_guarded_all | slotcmd |
    slotspecial | slicefilter | slicecmd
)
_op_seq_cont << _op_seq_cont_items
# First item also allows bare grouped expressions — (a,b) with bare keys.
# This is valid at the start of a path (top-level inference).
op_seq_item << (inner_grouped_first | inner_grouped | _op_seq_cont_items)

# Precedence tower (atoms are op_seqs)
inner_atom = op_seq
inner_not = (bang + inner_atom).set_parse_action(groups.inner_not_action) | inner_atom
inner_and = (inner_not + OM(amp + inner_not)).set_parse_action(groups.inner_and_action) | inner_not
inner_or_term = pp.Group(inner_and + Opt(cut_marker))
inner_or = (inner_or_term + ZM(_comma_ws + inner_or_term)).set_parse_action(groups.inner_or_action) | inner_and
inner_expr <<= inner_or

# ---------------------------------------------------------------------------
# Explicit precedence tower: used inside mid-path groups where every leaf
# must start with an access op (Key/Attr/Slot).  Bare keys are rejected.
# This enforces the rule: "if no access op floats down from parent, you
# must specify one on every branch."
# ---------------------------------------------------------------------------
_explicit_op_seq_cont_items = (
    _explicit_nop_wrap |
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    _dot_keycmd_guarded_nop | _dot_keycmd_guarded_plain |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    _slotcmd_guarded_all | slotcmd |
    slotspecial | slicefilter | slicecmd
)
_explicit_op_seq = pp.Group(_explicit_op_seq_cont_items + ZM(_explicit_op_seq_cont_items))
_explicit_inner_atom = _explicit_op_seq
_explicit_inner_not = (bang + _explicit_inner_atom).set_parse_action(groups.inner_not_action) | _explicit_inner_atom
_explicit_inner_and = (_explicit_inner_not + OM(amp + _explicit_inner_not)).set_parse_action(groups.inner_and_action) | _explicit_inner_not
_explicit_inner_or_term = pp.Group(_explicit_inner_and + Opt(cut_marker))
_explicit_inner_or = (_explicit_inner_or_term + ZM(_comma_ws + _explicit_inner_or_term)).set_parse_action(groups.inner_or_action) | _explicit_inner_and
_explicit_inner_expr <<= _explicit_inner_or

# Top-level: flatten op_seq Groups into flat ops list
def _top_level_flatten(t):
    """
    Flatten top-level expression result for the ops list.
    Plain op_seqs (ParseResults from Group) are unwrapped into individual ops.
    OpGroups are kept as single ops.
    """
    out = []
    for item in t:
        if isinstance(item, (list, tuple, pp.ParseResults)) and not isinstance(item, groups.OpGroup):
            out.extend(item)
        else:
            out.append(item)
    return out

invert = Opt(L('-').set_parse_action(access.Invert))
dotted = pp.Group((invert + (inner_expr | empty)).set_parse_action(_top_level_flatten))

_template_guard = (_pred_op_re + value).set_parse_action(lambda t: [(t[0], t[1])])
template = dotted('ops') + transforms('transforms') + Opt(_template_guard)('guard')
