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
appender_if = pp.Literal('+?').setParseAction(el.AppenderIf)
integer = num.copy().setParseAction(el.Integer)
word = pp.Word(pp.alphanums + '_').setParseAction(el.Word)
string = quoted.copy().setParseAction(el.String)
wildcard = pp.Literal('*').setParseAction(el.Wildcard)
regex = (slash + pp.Regex(r'(\\/|[^/])+') + slash).setParseAction(el.Regex)
slice = pp.Optional(num | plus) + ':' + pp.Optional(num | plus) \
         + pp.Optional(':') + pp.Optional(num | plus)

key = (word | string | wildcard | regex).setParseAction(el.Key)
slot = (lb + (integer | string | wildcard | regex) + rb).setParseAction(el.Slot)
slotspecial = (lb + (appender_if | appender) + rb).setParseAction(el.SlotSpecial)
slotslice = (lb + pp.Optional(slice) + rb).setParseAction(el.Slice)

multi = pp.OneOrMore((dot + key) | slot | slotspecial | slotslice)
dotted = (key | slot | slotspecial | slotslice) + pp.ZeroOrMore(multi)

targ = quoted | ppc.number | pp.Regex(r'[^|:]*')
transform = pp.Group(name.copy() + pp.ZeroOrMore(colon + targ))
transforms = pp.ZeroOrMore(pipe + transform)

template = dotted('ops') + transforms('transforms')
