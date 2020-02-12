"""
"""
import pyparsing as pp
from pyparsing import pyparsing_common as ppc
import elements as el

dot = pp.Suppress('.')
lb = pp.Suppress('[')
rb = pp.Suppress(']')
num = ppc.signed_integer
nlen = pp.Word('+')

# atomic ops
integer = num.copy().setParseAction(el.Integer)
word = pp.Word(pp.alphas + '_', pp.alphanums + '_').setParseAction(el.Word)
string = pp.QuotedString('"', escChar='\\') | pp.QuotedString("'", escChar='\\').setParseAction(el.String)
appender = pp.Word('+').setParseAction(el.Appender)
wildcard = pp.Word('*').setParseAction(el.Wildcard)
regex = (pp.Suppress('/') + pp.Regex('[^/]+') + pp.Suppress('/')).setParseAction(el.Regex)
slice = pp.Optional(num | nlen) + ':' + pp.Optional(num | nlen) \
         + pp.Optional(':') + pp.Optional(num | nlen)

key = (word | wildcard | regex).setParseAction(el.Key)
slot = (lb + (integer | string | wildcard | appender | regex) + rb).setParseAction(el.Slot)
slotslice = (lb + pp.Optional(slice) + rb).setParseAction(el.Slice)

multi = pp.OneOrMore((dot + key) | slot | slotslice)
template = (key | slot | slotslice) + pp.ZeroOrMore(multi)

