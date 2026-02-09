"""
"""
import decimal
import pyparsing as pp
from pyparsing import pyparsing_common as ppc
from . import elements as el

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

reserved = '.[]*:|+?/=,@&()!~#'  # # is cut in path/op groups; also used in numeric_quoted
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
wildcard = pp.Literal('*').set_parse_action(el.Wildcard)
wildcard_first = pp.Literal('*?').set_parse_action(el.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().set_parse_action(el.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).set_parse_action(el.RegexFirst)
slice = pp.Optional(integer | plus) + ':' + pp.Optional(integer | plus) \
         + pp.Optional(':') + pp.Optional(integer | plus)

_common_pats = wildcard_first | wildcard | regex_first | regex
_commons = string | _common_pats | numeric_quoted
value = string | wildcard | regex | numeric_quoted | none | true | false | numeric_key
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

keycmd = (key + ZM(amp + filters)).set_parse_action(el.Key)

_slotguts = (_commons | numeric_slot) + ZM(amp + filters)
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

# Cut marker: branch suffix # means "if this branch matches, don't try later branches" (per-horn cut)
cut_marker = L('#')

# Slot grouping: [(*&filter, +)] for disjunction inside slots; [(*&filter#, +)] for cut
_slot_item = _slotguts.copy().set_parse_action(el.Slot) | (appender_unique | appender).copy().set_parse_action(el.SlotSpecial)
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

empty = pp.Empty().set_parse_action(el.Empty)

# Operation grouping: (.b,[]) for grouping operation sequences
# An op_seq is a sequence of operations like .key, [slot], @attr
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
        if len(item) >= 2 and item[1] == '#':
            out.append(el._BRANCH_CUT)
    return el.OpGroup(*out)
op_group_or = (lparen + op_group_or_inner + rparen).set_parse_action(_op_group_from_parse)
op_group_first = (lparen + op_group_or_inner + rparen + S('?')).set_parse_action(
    lambda t: el.OpGroupFirst(*_op_group_from_parse(t).branches))

# Negation: (!.b) or (!(.a,.b))
op_group_not = (lparen + bang + (op_group_or | op_seq) + rparen).set_parse_action(el.OpGroupNot)

op_grouped = op_group_first | op_group_and | op_group_not | op_group_or

# NOP (~): match but don't update. At top assemble to ~@/~.; else .~/@~
dotted_top_inner = path_grouped | op_grouped | keycmd | attrcmd | slotgroup_first | slotgroup | slotcmd | slotspecial | slicefilter | slicecmd | empty
_nop_wrap = (tilde + dotted_top_inner).set_parse_action(lambda t: el.NopWrap(t[1]))
dotted_top = _nop_wrap | dotted_top_inner
# Resolve forward: op_seq_item can be _nop_wrap so ~(name.first) parses; path_grouped for (a&b).c; op_grouped for ((a,b),c)
op_seq_item << (_nop_wrap | path_grouped | op_grouped | keycmd | _dot_keycmd | attrcmd | slotgroup_first | slotgroup | slotcmd | slotspecial | slicefilter | slicecmd)

# ~. and .~ both produce NopWrap (canonical form .~)
_dot_nop = ((dot + tilde) | (tilde + dot)) + (path_grouped | keycmd)
_dot_nop = _dot_nop.set_parse_action(lambda t: el.NopWrap(t[-1]))
_dot_plain = (dot + (path_grouped | keycmd)).set_parse_action(lambda t: t[0])
_dot_segment = _dot_nop | _dot_plain
multi = OM(_dot_segment | attrcmd | slotgroup_first | slotgroup | slotcmd | slotspecial | slicefilter | slicecmd | op_grouped)
invert = Opt(L('-').set_parse_action(el.Invert))
dotted = invert + dotted_top + ZM(multi)

targ = quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().set_parse_action(lambda: [None])
transform = pp.Group(transform_name.copy() + ZM(param))
transforms = ZM(pipe + transform)

template = dotted('ops') + transforms('transforms')
