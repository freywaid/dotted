"""
"""
import decimal
import pyparsing as pp
from pyparsing import pyparsing_common as ppc
from . import elements as el
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
none = pp.Literal('None').set_parse_action(el.NoneValue)
true = pp.Literal('True').set_parse_action(el.Boolean)
false = pp.Literal('False').set_parse_action(el.Boolean)

reserved = '.[]*:|+?/=,@&()!~#{}' # {} added for container syntax
breserved = ''.join('\\' + i for i in reserved)
tilde = L('~')

# atomic ops
appender = pp.Literal('+').set_parse_action(el.Appender)
appender_unique = pp.Literal('+?').set_parse_action(el.AppenderUnique)

_numeric_quoted = S('#') + ((S("'") + ppc.number + S("'")) | (S('"') + ppc.number + S('"')))
numeric_quoted = _numeric_quoted.set_parse_action(el.NumericQuoted)

numeric_key = integer.copy().set_parse_action(el.Numeric)
numeric_slot = ppc.number.copy().set_parse_action(el.Numeric)

# Exclude whitespace so "first )" parses as key "first", not "first "
word = (pp.Optional(backslash) + pp.CharsNotIn(reserved + ' \t\n\r')).set_parse_action(el.Word)
non_integer = pp.Regex(f'[-]?[0-9]+[^0-9{breserved}]+').set_parse_action(el.Word)
nameop = name.copy().set_parse_action(el.Word)

string = quoted.copy().set_parse_action(el.String)
bytes_literal = (S(L('b')) + quoted).set_parse_action(el.Bytes)
wildcard = pp.Literal('*').set_parse_action(el.Wildcard)
wildcard_first = pp.Literal('*?').set_parse_action(el.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().set_parse_action(el.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).set_parse_action(el.RegexFirst)
slice = pp.Optional(integer | plus) + ':' + pp.Optional(integer | plus) \
         + pp.Optional(':') + pp.Optional(integer | plus)

_common_pats = wildcard_first | wildcard | regex_first | regex
_commons = bytes_literal | string | _common_pats | numeric_quoted


# ---------------------------------------------------------------------------
# Container grammar — pattern containers (for value/filter context)
# ---------------------------------------------------------------------------

# Comma with optional surrounding whitespace for container interiors
_ccomma = pp.Optional(pp.White()).suppress() + comma + pp.Optional(pp.White()).suppress()

# Forward for recursive container values
container_value = pp.Forward()

# Scalar atoms usable inside containers (same as value atoms minus containers)
_val_atoms = bytes_literal | string | wildcard | regex | numeric_quoted | none | true | false | numeric_key

# Glob inside containers: ... with optional /regex/ then optional count
# Regex pattern for glob (unsuppressed slashes handled inline)
_glob_regex = (slash + pp.Regex(r'(\\/|[^/])+') + slash).set_parse_action(el.Regex)

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

# String glob: "prefix"..."suffix", ..."suffix", "prefix"..., etc.
# Must have at least one string AND one glob (otherwise it's a plain string or bare glob).
_sglob_str = quoted.copy()  # raw string value, no el.String parse action
_sglob_part = _sglob_str | container_glob
_string_glob_a = _sglob_str + container_glob + ZM(_sglob_part)  # starts with string
_string_glob_b = container_glob + _sglob_str + ZM(_sglob_part)  # starts with glob
string_glob = (_string_glob_a | _string_glob_b).set_parse_action(lambda t: [ct.StringGlob(*t)])

# Bytes glob: b"prefix"...b"suffix", ...b"suffix", b"prefix"..., etc.
# Like string_glob but with bytes_literal parts → BytesGlob.
_bglob_bytes = bytes_literal.copy()  # produces el.Bytes
_bglob_part = _bglob_bytes | container_glob
_bytes_glob_a = _bglob_bytes + container_glob + ZM(_bglob_part)  # starts with bytes
_bytes_glob_b = container_glob + _bglob_bytes + ZM(_bglob_part)  # starts with glob
def _make_bytes_glob(t):
    """
    Convert parsed tokens to BytesGlob, extracting .value from Bytes elements.
    """
    parts = tuple(p.value if isinstance(p, el.Bytes) else p for p in t)
    return ct.BytesGlob(*parts)
bytes_glob = (_bytes_glob_a | _bytes_glob_b).set_parse_action(lambda t: [_make_bytes_glob(t)])

# Container element: can be a nested container, glob, bytes glob, string glob, or scalar/pattern atom
_container_elem = container_value | bytes_glob | string_glob | container_glob | _val_atoms

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
value <<= value_group | container_value | bytes_glob | string_glob | bytes_literal | string | wildcard | regex | numeric_quoted | none | true | false | numeric_key
key = _commons | non_integer | numeric_key | word

# filter_key: dotted paths (user.id), slot paths (tags[*], tags[0]), slice (name[:5], name[-5:]).
# Dot introduces a key part only; slot/slice directly after key. Try slice before slot so [:] parses as slice.
_filter_key_part = string | _common_pats | non_integer | numeric_key | word
filter_key_slice = (lb + slice + rb).set_parse_action(lambda t: el.Slice(*t))
filter_key_slot = (lb + (_commons | numeric_slot) + rb).set_parse_action(lambda t: el.Slot(t[0]))
_filter_key_segment = _filter_key_part | filter_key_slice | filter_key_slot
filter_key = pp.Group(
    _filter_key_segment + ZM(filter_key_slice | filter_key_slot | (dot + _filter_key_part))
).set_parse_action(el.FilterKey)

# Single key=value or key!=value comparison (!= keeps its own repr so reassemble looks right)
filter_single_neq = pp.Group(filter_key + not_equal + value).set_parse_action(el.FilterKeyValueNot)
filter_single = pp.Group(filter_key + equal + value).set_parse_action(el.FilterKeyValue)

# Recursive filter expression with grouping
filter_expr = pp.Forward()

# Atom: single comparison or grouped expression
lparen = pp.Suppress('(')
rparen = pp.Suppress(')')
bang = pp.Suppress('!')
filter_group = (lparen + filter_expr + rparen).set_parse_action(el.FilterGroup)
filter_atom = filter_group | filter_single_neq | filter_single

# NOT: ! prefix binds tightest (higher precedence than &)
filter_not = (bang + filter_atom).set_parse_action(el.FilterNot) | filter_atom

# AND: not-expressions joined by & (higher precedence than ,)
filter_and = (filter_not + OM(amp + filter_not)).set_parse_action(el.FilterAnd) | filter_not

# OR: and-groups joined by , (lowest precedence)
filter_or = (filter_and + OM(comma + filter_and)).set_parse_action(el.FilterOr) | filter_and

filter_expr <<= filter_or

# Optional ? suffix for first-match
filter_keyvalue_first = (filter_expr + S('?')).set_parse_action(el.FilterKeyValueFirst)

filters = filter_keyvalue_first | filter_expr

# Value guard: key=value, key!=value, [slot]=value, [slot]!=value (direct value test)
_guard_eq = equal + value
_guard_neq = not_equal + value

keycmd_guarded_neq = (key + ZM(amp + filters) + _guard_neq).set_parse_action(
    lambda t: el.ValueGuard(el.Key(*t[:-1]), t[-1], negate=True))
keycmd_guarded = (key + ZM(amp + filters) + _guard_eq).set_parse_action(
    lambda t: el.ValueGuard(el.Key(*t[:-1]), t[-1]))

keycmd = (key + ZM(amp + filters)).set_parse_action(el.Key)

_slotguts = (_commons | numeric_slot) + ZM(amp + filters)

def _slotcmd_guarded_action(negate):
    def action(t):
        t = list(t)
        guard = t[-1]
        rest = t[:-1]
        if rest and rest[0] == '~':
            inner = el.NopWrap(el.Slot(*rest[1:]))
        else:
            inner = el.Slot(*rest)
        return el.ValueGuard(inner, guard, negate=negate)
    return action

slotcmd_guarded_neq = (lb + Opt(L('~')) + _slotguts + rb + _guard_neq).set_parse_action(_slotcmd_guarded_action(True))
slotcmd_guarded = (lb + Opt(L('~')) + _slotguts + rb + _guard_eq).set_parse_action(_slotcmd_guarded_action(False))

slotcmd = (lb + Opt(L('~')) + _slotguts + rb).set_parse_action(
    lambda t: el.NopWrap(el.Slot(*t[1:])) if t[0] == '~' else el.Slot(*t))

# @~ and ~@ both produce NopWrap (canonical form @~)
_attr_nop = ((at + tilde) | (tilde + at)) + (nameop | _common_pats) + ZM(amp + filters)
_attr_nop = _attr_nop.set_parse_action(lambda t: el.NopWrap(el.Attr(*t[1:])))  # t=[~,nameop,...] or [nameop,...]
_attr_plain = (at + (nameop | _common_pats) + ZM(amp + filters)).set_parse_action(lambda t: el.Attr(*t))
attrcmd = _attr_nop | _attr_plain

slotspecial = (lb + (appender_unique | appender) + rb).set_parse_action(el.SlotSpecial)

slicecmd = (lb + Opt(L('~')) + Opt(slice) + rb).set_parse_action(
    lambda t: el.NopWrap(el.Slice(*t[1:])) if t and t[0] == '~' else el.Slice(*t))
slicefilter = (lb + filters + ZM(amp + filters) + rb).set_parse_action(el.SliceFilter)

# Cut markers: ## = soft cut, # = hard cut (try ## first so ## isn't parsed as # + #)
softcut_marker = L('##')
cut_marker = softcut_marker | L('#')

# Slot grouping: [(*&filter, +)] for disjunction inside slots; [(*&filter#, +)] for cut
_slot_item_plain = _slotguts.copy().set_parse_action(el.Slot)
_slot_item_nop = (tilde + _slotguts.copy()).set_parse_action(lambda t: el.NopWrap(el.Slot(*t[1:])))
_slot_item = _slot_item_nop | _slot_item_plain | (appender_unique | appender).copy().set_parse_action(el.SlotSpecial)
_slot_group_term = pp.Group(_slot_item + Opt(cut_marker))
_slot_group_inner = _slot_group_term + ZM(_comma_ws + _slot_group_term)
slotgroup = (lb + lparen + _slot_group_inner + rparen + rb).set_parse_action(el._slot_to_opgroup)
slotgroup_first = (lb + lparen + _slot_group_inner + rparen + S('?') + rb).set_parse_action(el._slot_to_opgroup_first)

# Path-level grouping: (a,b) for disjunction, (a&b) for conjunction, (!a) for negation
path_expr = pp.Forward()
path_group_inner = (lparen + path_expr + rparen).set_parse_action(el._path_to_opgroup)
path_group_item = path_group_inner | key.copy()

# NOT: ! prefix binds tightest for paths
path_not = (bang + path_group_item).set_parse_action(el.PathNot) | path_group_item

# AND: path items joined by &
path_group_and = (path_not + OM(amp + path_not)).set_parse_action(el.PathAnd) | path_not

# OR: and-groups joined by ,; (a#, b) has cut on first branch
path_group_or_term = pp.Group(path_group_and + Opt(cut_marker))
path_group_or = (path_group_or_term + ZM(_comma_ws + path_group_or_term)).set_parse_action(el._path_or_with_cut) | path_group_and
path_expr <<= path_group_or
path_group = (lparen + path_expr + rparen).set_parse_action(el._path_to_opgroup)
path_group_first = (lparen + path_expr + rparen + S('?')).set_parse_action(el._path_to_opgroup_first)
path_grouped = path_group_first | path_group

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
    for item in rest:
        if isinstance(item, el.FilterOp):
            filt.append(item)
        else:
            depth_vals.append(item)
    if len(depth_vals) >= 1:
        depth_start = depth_vals[0]
    if len(depth_vals) >= 2:
        depth_stop = depth_vals[1]
    if len(depth_vals) >= 3:
        depth_step = depth_vals[2]
    cls = el.RecursiveFirst if first else el.Recursive
    r = cls(inner, depth_start=depth_start, depth_stop=depth_stop, depth_step=depth_step)
    if filt:
        r.filters = tuple(filt)
    return r

# ** and *pattern base expressions (no parse actions — shared by guarded/first/plain variants)
_rec_dstar_prefix = S(L('*') + L('*'))
_rec_inner = string | regex_first | regex | numeric_quoted | non_integer | numeric_key | word
_rec_pat_prefix = S(L('*'))

def _dstar_body():
    return _rec_dstar_prefix + Opt(_rec_depth) + ZM(amp + filters)

def _pat_body():
    return _rec_pat_prefix + _rec_inner + Opt(_rec_depth) + ZM(amp + filters)

# ValueGuard composition: **=7, *name!=None, etc. (must try before plain forms)
rec_dstar_guarded_neq = (_dstar_body() + _guard_neq).set_parse_action(
    lambda t: el.ValueGuard(_make_recursive([el.Wildcard()] + list(t[:-1])), t[-1], negate=True))
rec_dstar_guarded = (_dstar_body() + _guard_eq).set_parse_action(
    lambda t: el.ValueGuard(_make_recursive([el.Wildcard()] + list(t[:-1])), t[-1]))
rec_pat_guarded_neq = (_pat_body() + _guard_neq).set_parse_action(
    lambda t: el.ValueGuard(_make_recursive(t[:-1]), t[-1], negate=True))
rec_pat_guarded = (_pat_body() + _guard_eq).set_parse_action(
    lambda t: el.ValueGuard(_make_recursive(t[:-1]), t[-1]))

# First-match: **?, *name?
rec_dstar_first = (_dstar_body() + S('?')).set_parse_action(
    lambda t: _make_recursive([el.Wildcard()] + list(t), first=True))
rec_pat_first = (_pat_body() + S('?')).set_parse_action(
    lambda t: _make_recursive(t, first=True))

# Plain: **, *name
rec_dstar = _dstar_body().set_parse_action(
    lambda t: _make_recursive([el.Wildcard()] + list(t)))
rec_pat = _pat_body().set_parse_action(
    lambda t: _make_recursive(t))

recursive_op = (rec_dstar_guarded_neq | rec_dstar_guarded | rec_pat_guarded_neq | rec_pat_guarded |
                rec_dstar_first | rec_dstar | rec_pat_first | rec_pat)

empty = pp.Empty().set_parse_action(el.Empty)

# Operation grouping: (.b,[]) for grouping operation sequences
# An op_seq is a sequence of operations like .key, [slot], @attr
_dot_keycmd_guarded_neq = (dot + Opt(L('~')) + keycmd_guarded_neq).set_parse_action(
    lambda t: el.NopWrap(t[1]) if len(t) == 2 else t[0])
_dot_keycmd_guarded = (dot + Opt(L('~')) + keycmd_guarded).set_parse_action(
    lambda t: el.NopWrap(t[1]) if len(t) == 2 else t[0])
_dot_keycmd = (dot + Opt(L('~')) + keycmd).set_parse_action(
    lambda t: el.NopWrap(t[1]) if len(t) == 2 else t[0])
# op_seq_item uses _nop_wrap (defined later); Forward for circular ref
op_seq_item = pp.Forward()
op_seq = pp.Group(OM(op_seq_item))

# OpGroup with AND/OR/NOT semantics:
# (.b,.c)  - disjunction: get both a.b and a.c
# (.b#,.c) - disjunction with cut: first branch that matches wins (commit, don't try rest)
# (.b&.c)  - conjunction: get both only if both exist
# (!.b)    - negation: get all except b
op_group_and_inner = op_seq + OM(amp + op_seq)
op_group_and = (lparen + op_group_and_inner + rparen).set_parse_action(el.OpGroupAnd)

op_group_or_term = pp.Group(op_seq + Opt(cut_marker))
op_group_or_inner = op_group_or_term + ZM(_comma_ws + op_group_or_term)
def _op_group_from_parse(t):
    items = t  # list of terms (op_group_or_inner result)
    out = []
    for item in items:
        b = item[0]  # op_seq result (Group of op_seq_items)
        if isinstance(b, (list, tuple, pp.ParseResults)) and len(b) == 1:
            b = b[0]
        # Unwrap so branch is always tuple of elements, never raw ParseResults
        branch = tuple(b) if isinstance(b, (list, tuple, pp.ParseResults)) else (b,)
        out.append(branch)
        if len(item) >= 2 and item[1] == '##':
            out.append(el._BRANCH_SOFTCUT)
        elif len(item) >= 2 and item[1] == '#':
            out.append(el._BRANCH_CUT)
    return el.OpGroupOr(*out)
op_group_or = (lparen + op_group_or_inner + rparen).set_parse_action(_op_group_from_parse)
op_group_first = (lparen + op_group_or_inner + rparen + S('?')).set_parse_action(
    lambda t: el.OpGroupFirst(*_op_group_from_parse(t).branches))

# Negation: (!.b) or (!(.a,.b))
op_group_not = (lparen + bang + (op_group_or | op_seq) + rparen).set_parse_action(el.OpGroupNot)

op_grouped = op_group_first | op_group_and | op_group_not | op_group_or

# NOP (~): match but don't update. At top assemble to ~@/~.; else .~/@~
dotted_top_inner = path_grouped | op_grouped | recursive_op | keycmd_guarded_neq | keycmd_guarded | keycmd | attrcmd | slotgroup_first | slotgroup | slotcmd_guarded_neq | slotcmd_guarded | slotcmd | slotspecial | slicefilter | slicecmd | empty
_nop_wrap = (tilde + dotted_top_inner).set_parse_action(lambda t: el.NopWrap(t[1]))
dotted_top = _nop_wrap | dotted_top_inner
# Resolve forward: op_seq_item can be _nop_wrap so ~(name.first) parses; path_grouped for (a&b).c; op_grouped for ((a,b),c)
op_seq_item << (_nop_wrap | path_grouped | op_grouped | recursive_op | keycmd_guarded_neq | keycmd_guarded | keycmd | _dot_keycmd_guarded_neq | _dot_keycmd_guarded | _dot_keycmd | attrcmd | slotgroup_first | slotgroup | slotcmd_guarded_neq | slotcmd_guarded | slotcmd | slotspecial | slicefilter | slicecmd)

# ~. and .~ both produce NopWrap (canonical form .~)
_dot_nop_guarded = ((dot + tilde) | (tilde + dot)) + (keycmd_guarded_neq | keycmd_guarded)
_dot_nop_guarded = _dot_nop_guarded.set_parse_action(lambda t: el.NopWrap(t[-1]))
_dot_plain_guarded = (dot + (keycmd_guarded_neq | keycmd_guarded)).set_parse_action(lambda t: t[0])
_dot_segment_guarded = _dot_nop_guarded | _dot_plain_guarded
_dot_nop = ((dot + tilde) | (tilde + dot)) + (path_grouped | keycmd)
_dot_nop = _dot_nop.set_parse_action(lambda t: el.NopWrap(t[-1]))
_dot_plain = (dot + (path_grouped | keycmd)).set_parse_action(lambda t: t[0])
_dot_segment = _dot_nop | _dot_plain
_nop_op_grouped = (tilde + op_grouped).set_parse_action(lambda t: el.NopWrap(t[1]))
_dot_recursive = (dot + recursive_op).set_parse_action(lambda t: t[0])
multi = OM(_dot_segment_guarded | _dot_segment | _dot_recursive | attrcmd | slotgroup_first | slotgroup | slotcmd_guarded_neq | slotcmd_guarded | slotcmd | slotspecial | slicefilter | slicecmd | _nop_op_grouped | op_grouped)
invert = Opt(L('-').set_parse_action(el.Invert))
dotted = invert + dotted_top + ZM(multi)

targ = concrete_value | quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().set_parse_action(lambda: [None])
transform = pp.Group(transform_name.copy() + ZM(param))
transforms = ZM(pipe + transform)

template = dotted('ops') + transforms('transforms')
