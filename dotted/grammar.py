"""
"""
import decimal
import pyparsing as pp
from pyparsing import pyparsing_common as ppc
from . import elements as el

dot = pp.Suppress('.')
lb = pp.Suppress('[')
rb = pp.Suppress(']')
colon = pp.Suppress(':')
pipe = pp.Suppress('|')
slash = pp.Suppress('/')
num = ppc.signed_integer
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
quoted = pp.QuotedString('"', escChar='\\') | pp.QuotedString("'", escChar='\\')
plus = pp.Literal('+')

# atomic ops
appender = pp.Literal('+').setParseAction(el.Appender)
appender_unique = pp.Literal('+?').setParseAction(el.AppenderUnique)
integer = num.copy().setParseAction(el.Integer)
word = pp.Word(pp.alphanums + '_').setParseAction(el.Word)
string = quoted.copy().setParseAction(el.String)
wildcard = pp.Literal('*').setParseAction(el.Wildcard)
wildcard_first = pp.Literal('*?').setParseAction(el.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().setParseAction(el.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).setParseAction(el.RegexFirst)
slice = pp.Optional(num | plus) + ':' + pp.Optional(num | plus) \
         + pp.Optional(':') + pp.Optional(num | plus)

_commons = string | wildcard_first | wildcard | regex_first | regex
key = (word | _commons).setParseAction(el.Key)
slot = (lb + (integer | _commons) + rb).setParseAction(el.Slot)
slotspecial = (lb + (appender_unique | appender) + rb).setParseAction(el.SlotSpecial)
slotslice = (lb + pp.Optional(slice) + rb).setParseAction(el.Slice)

multi = pp.OneOrMore((dot + key) | slot | slotspecial | slotslice)
dotted = (key | slot | slotspecial | slotslice) + pp.ZeroOrMore(multi)

targ = quoted | ppc.number | pp.Regex(r'[^|:]*')
transform = pp.Group(name.copy() + pp.ZeroOrMore(colon + targ))
transforms = pp.ZeroOrMore(pipe + transform)

template = dotted('ops') + transforms('transforms')
