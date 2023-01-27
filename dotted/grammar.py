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
equal = pp.Suppress('=')
dot = pp.Suppress('.')
comma = pp.Suppress(',')
lb = pp.Suppress('[')
rb = pp.Suppress(']')
colon = pp.Suppress(':')
pipe = pp.Suppress('|')
slash = pp.Suppress('/')
backslash = pp.Suppress('\\')
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
transform_name = pp.Word(pp.alphas + '_', pp.alphanums + '_.')
quoted = pp.QuotedString('"', escChar='\\') | pp.QuotedString("'", escChar='\\')
plus = pp.Literal('+')
integer = ppc.signed_integer
none = pp.Literal('None').setParseAction(pp.tokenMap(lambda a: None))
true = pp.Literal('True').setParseAction(pp.tokenMap(lambda a: True))
false = pp.Literal('False').setParseAction(pp.tokenMap(lambda a: False))

reserved = '.[]*:|+?/=,'
breserved = ''.join('\\' + i for i in reserved)

# atomic ops
appender = pp.Literal('+').setParseAction(el.Appender)
appender_unique = pp.Literal('+?').setParseAction(el.AppenderUnique)

_numeric_quoted = S('#') + ((S("'") + ppc.number + S("'")) | (S('"') + ppc.number + S('"')))
numeric_quoted = _numeric_quoted.setParseAction(el.NumericQuoted)

numeric_key = integer.copy().setParseAction(el.Numeric)
numeric_slot = ppc.number.copy().setParseAction(el.Numeric)

word = (pp.Optional(backslash) + pp.CharsNotIn(reserved)).setParseAction(el.Word)
non_integer = pp.Regex(f'[-]?[0-9]+[^0-9{breserved}]+').setParseAction(el.Word)

string = quoted.copy().setParseAction(el.String)
wildcard = pp.Literal('*').setParseAction(el.Wildcard)
wildcard_first = pp.Literal('*?').setParseAction(el.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().setParseAction(el.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).setParseAction(el.RegexFirst)
slice = pp.Optional(integer | plus) + ':' + pp.Optional(integer | plus) \
         + pp.Optional(':') + pp.Optional(integer | plus)

_commons = string | wildcard_first | wildcard | regex_first | regex | numeric_quoted
value = string | wildcard | regex | numeric_quoted | numeric_key
key = _commons | non_integer | numeric_key | word

__filter_keyvalue = pp.Group(key + equal + value)
_filter_keyvalue = __filter_keyvalue + ZM(comma + __filter_keyvalue)

filter_keyvalue = _filter_keyvalue.copy().setParseAction(el.FilterKeyValue)
filter_keyvalue_first = (_filter_keyvalue + S('?')).setParseAction(el.FilterKeyValueFirst)

filters = filter_keyvalue_first | filter_keyvalue

keycmd = (key + ZM(dot + filters)).setParseAction(el.Key)

_slotguts = (_commons | numeric_slot) + ZM(dot + filters)
slotcmd = (lb + _slotguts + rb).setParseAction(el.Slot)

slotspecial = (lb + (appender_unique | appender) + rb).setParseAction(el.SlotSpecial)

slicecmd = (lb + Opt(slice) + rb).setParseAction(el.Slice)
slicefilter = (lb + filters + ZM(dot + filters) + rb).setParseAction(el.SliceFilter)

empty = pp.Empty().setParseAction(el.Empty)

multi = OM((dot + keycmd) | slotcmd | slotspecial | slicefilter | slicecmd)
invert = Opt(L('-').setParseAction(el.Invert))
dotted_top = keycmd | slotcmd | slotspecial | slicefilter | slicecmd | empty
dotted = invert + dotted_top + ZM(multi)

targ = quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().setParseAction(lambda: [None])
transform = pp.Group(transform_name.copy() + ZM(param))
transforms = ZM(pipe + transform)

template = dotted('ops') + transforms('transforms')
