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

value = string | wildcard | regex | numeric_quoted | numeric_key

_commons = string | wildcard_first | wildcard | regex_first | regex | numeric_quoted
_key = _commons | non_integer | numeric_key | word

__filter_keyvalue = pp.Group(_key + equal + value)
_filter_keyvalue = __filter_keyvalue + ZM(comma + __filter_keyvalue)
filter_keyvalue = _filter_keyvalue.setParseAction(el.FilterKeyValue)

filters = filter_keyvalue

key_last = (_key + ZM(dot + filters)).setParseAction(el.Key)
key_mid = (_key + ZM(dot + filters) + dot).setParseAction(el.Key)

_slot_guts = _commons | numeric_slot
_slot = (_slot_guts + ZM(dot + filters)) | (filters + ZM(dot + filters))

keyed_slot = _key + lb + _slot + rb




_slot = __slot + ZM(dot + filter_keyvalue) | (filter_keyvalue + ZM(dot + filter_keyvalue))
slot = (lb + _slot + rb).setParseAction(el.Slot)

slotspecial = (lb + (appender_unique | appender) + rb).setParseAction(el.SlotSpecial)
slotslice = (lb + Opt(slice) + rb).setParseAction(el.Slice)

empty = pp.Empty().setParseAction(el.Empty)
empty_filtered = OM(filter_keyvalue).setParseAction(el.Empty)

multi = pp.OneOrMore((dot + key) | slot | slotspecial | slotslice)
invert = Opt(L('-').setParseAction(el.Invert))
dotted_top = empty_filtered | key | slot | slotspecial | slotslice | empty
dotted = invert + dotted_top + ZM(multi)

targ = quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().setParseAction(lambda: [None])
transform = pp.Group(transform_name.copy() + ZM(param))
transforms = ZM(pipe + transform)

template = dotted('ops') + transforms('transforms')
