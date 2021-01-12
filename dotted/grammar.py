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
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
quoted = pp.QuotedString('"', escChar='\\') | pp.QuotedString("'", escChar='\\')
plus = pp.Literal('+')
integer = ppc.signed_integer
numeric_quoted = S('#') + ((S("'") + ppc.number + S("'")) | (S('"') + ppc.number + S('"')))

# atomic ops
appender = pp.Literal('+').setParseAction(el.Appender)
appender_unique = pp.Literal('+?').setParseAction(el.AppenderUnique)

numeric_key = (numeric_quoted | integer).setParseAction(el.NumericQuoted)
numeric_slot = (numeric_quoted | ppc.number).setParseAction(el.Numeric)

word = pp.Word(pp.alphanums + '_').setParseAction(el.Word)
string = quoted.copy().setParseAction(el.String)
wildcard = pp.Literal('*').setParseAction(el.Wildcard)
wildcard_first = pp.Literal('*?').setParseAction(el.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().setParseAction(el.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).setParseAction(el.RegexFirst)
slice = pp.Optional(integer | plus) + ':' + pp.Optional(integer | plus) \
         + pp.Optional(':') + pp.Optional(integer | plus)

_commons = string | wildcard_first | wildcard | regex_first | regex
key = (numeric_key | word | _commons).setParseAction(el.Key)
slot = (lb + (numeric_slot | _commons) + rb).setParseAction(el.Slot)
slotspecial = (lb + (appender_unique | appender) + rb).setParseAction(el.SlotSpecial)
slotslice = (lb + pp.Optional(slice) + rb).setParseAction(el.Slice)

multi = pp.OneOrMore((dot + key) | slot | slotspecial | slotslice)
invert = pp.Optional(pp.Literal('-').setParseAction(el.Invert))
dotted_top = key | slot | slotspecial | slotslice
#dotted_invert = (pp.Suppress('-') + dotted_top).setParseAction(el.Invert)
dotted = invert + dotted_top + pp.ZeroOrMore(multi)

targ = quoted | ppc.number | pp.Regex(r'[^|:]*')
transform = pp.Group(name.copy() + pp.ZeroOrMore(colon + targ))
transforms = pp.ZeroOrMore(pipe + transform)

template = dotted('ops') + transforms('transforms')
