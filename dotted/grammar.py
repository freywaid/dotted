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
dot = pp.Suppress('.')
amp = pp.Suppress('&')
comma = pp.Suppress(',')
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

reserved = '.[]*:|+?/=,@&()'
breserved = ''.join('\\' + i for i in reserved)

# atomic ops
appender = pp.Literal('+').set_parse_action(el.Appender)
appender_unique = pp.Literal('+?').set_parse_action(el.AppenderUnique)

_numeric_quoted = S('#') + ((S("'") + ppc.number + S("'")) | (S('"') + ppc.number + S('"')))
numeric_quoted = _numeric_quoted.set_parse_action(el.NumericQuoted)

numeric_key = integer.copy().set_parse_action(el.Numeric)
numeric_slot = ppc.number.copy().set_parse_action(el.Numeric)

word = (pp.Optional(backslash) + pp.CharsNotIn(reserved)).set_parse_action(el.Word)
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

# filter_key allows dotted paths like user.id or config.db.host
# also supports wildcards and regex patterns for matching
_filter_key_part = string | _common_pats | non_integer | numeric_key | word
filter_key = pp.Group(_filter_key_part + ZM(dot + _filter_key_part)).set_parse_action(el.FilterKey)

# Single key=value comparison
filter_single = pp.Group(filter_key + equal + value).set_parse_action(el.FilterKeyValue)

# Recursive filter expression with grouping
filter_expr = pp.Forward()

# Atom: single comparison or grouped expression
lparen = pp.Suppress('(')
rparen = pp.Suppress(')')
filter_group = (lparen + filter_expr + rparen).set_parse_action(el.FilterGroup)
filter_atom = filter_group | filter_single

# AND: atoms joined by & (higher precedence)
filter_and = (filter_atom + OM(amp + filter_atom)).set_parse_action(el.FilterAnd) | filter_atom

# OR: and-groups joined by , (lower precedence)
filter_or = (filter_and + OM(comma + filter_and)).set_parse_action(el.FilterOr) | filter_and

filter_expr <<= filter_or

# Optional ? suffix for first-match
filter_keyvalue_first = (filter_expr + S('?')).set_parse_action(el.FilterKeyValueFirst)

filters = filter_keyvalue_first | filter_expr

keycmd = (key + ZM(amp + filters)).set_parse_action(el.Key)

_slotguts = (_commons | numeric_slot) + ZM(amp + filters)
slotcmd = (lb + _slotguts + rb).set_parse_action(el.Slot)

attrcmd = (at + (nameop | _common_pats) + ZM(amp + filters)).set_parse_action(el.Attr)

slotspecial = (lb + (appender_unique | appender) + rb).set_parse_action(el.SlotSpecial)

slicecmd = (lb + Opt(slice) + rb).set_parse_action(el.Slice)
slicefilter = (lb + filters + ZM(amp + filters) + rb).set_parse_action(el.SliceFilter)

# Path-level grouping: (a,b) for disjunction, (a&b) for conjunction
path_expr = pp.Forward()
path_group_inner = (lparen + path_expr + rparen).set_parse_action(el.PathGroup)
path_group_item = path_group_inner | key.copy()
path_group_and = (path_group_item + OM(amp + path_group_item)).set_parse_action(el.PathAnd) | path_group_item
path_group_or = (path_group_and + OM(comma + path_group_and)).set_parse_action(el.PathOr) | path_group_and
path_expr <<= path_group_or
path_group = (lparen + path_expr + rparen).set_parse_action(el.PathGroup)
path_group_first = (lparen + path_expr + rparen + S('?')).set_parse_action(el.PathGroupFirst)
path_grouped = path_group_first | path_group

empty = pp.Empty().set_parse_action(el.Empty)

multi = OM((dot + (path_grouped | keycmd)) | attrcmd | slotcmd | slotspecial | slicefilter | slicecmd)
invert = Opt(L('-').set_parse_action(el.Invert))
dotted_top = path_grouped | keycmd | attrcmd | slotcmd | slotspecial | slicefilter | slicecmd | empty
dotted = invert + dotted_top + ZM(multi)

targ = quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().set_parse_action(lambda: [None])
transform = pp.Group(transform_name.copy() + ZM(param))
transforms = ZM(pipe + transform)

template = dotted('ops') + transforms('transforms')
