"""
"""
import decimal
import pyparsing as pp
from pyparsing import pyparsing_common as ppc
from . import elements as el

S = pp.Suppress
dot = pp.Suppress('.')
lb = pp.Suppress('[')
rb = pp.Suppress(']')
colon = pp.Suppress(':')
pipe = pp.Suppress('|')
slash = pp.Suppress('/')
backslash = pp.Suppress('\\')
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
quoted = pp.QuotedString('"', escChar='\\') | pp.QuotedString("'", escChar='\\')
plus = pp.Literal('+')
integer = ppc.signed_integer

reserved = '.[]*:|+?/'
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
key = (_commons | non_integer | numeric_key | word).setParseAction(el.Key)
slot = (lb + (_commons | numeric_slot) + rb).setParseAction(el.Slot)
slotspecial = (lb + (appender_unique | appender) + rb).setParseAction(el.SlotSpecial)
slotslice = (lb + pp.Optional(slice) + rb).setParseAction(el.Slice)

multi = pp.OneOrMore((dot + key) | slot | slotspecial | slotslice)
invert = pp.Optional(pp.Literal('-').setParseAction(el.Invert))
dotted_top = key | slot | slotspecial | slotslice
dotted = invert + dotted_top + pp.ZeroOrMore(multi)

targ = quoted | ppc.number | pp.CharsNotIn('|:')
transform = pp.Group(name.copy() + pp.ZeroOrMore(colon + targ))
transforms = pp.ZeroOrMore(pipe + transform)

template = dotted('ops') + transforms('transforms')
