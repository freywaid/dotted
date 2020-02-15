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
endof = pp.Literal('+')
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
quoted = pp.QuotedString('"', escChar='\\') | pp.QuotedString("'", escChar='\\')

# atomic ops
integer = num.copy().setParseAction(el.Integer)
word = name.copy().setParseAction(el.Word)
string = quoted.copy().setParseAction(el.String)
appender = endof.copy().setParseAction(el.Appender)
wildcard = pp.Literal('*').setParseAction(el.Wildcard)
regex = (slash + pp.Regex(r'(\\/|[^/])+') + slash).setParseAction(el.Regex)
slice = pp.Optional(num | endof) + ':' + pp.Optional(num | endof) \
         + pp.Optional(':') + pp.Optional(num | endof)

key = (word | wildcard | regex).setParseAction(el.Key)
slot = (lb + (integer | string | wildcard | appender | regex) + rb).setParseAction(el.Slot)
slotslice = (lb + pp.Optional(slice) + rb).setParseAction(el.Slice)

multi = pp.OneOrMore((dot + key) | slot | slotslice)
dotted = (key | slot | slotslice) + pp.ZeroOrMore(multi)

targ = quoted | ppc.number | pp.Regex(r'[^|:]*')
transform = pp.Group(name.copy() + pp.ZeroOrMore(colon + targ))
transforms = pp.ZeroOrMore(pipe + transform)

template = dotted('ops') + transforms('transforms')
