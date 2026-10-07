# Type declarations for the compiler; see base.pxd.
from .base cimport MatchOp


cdef class Const(MatchOp):
    # value is a lazyprop, cached in the instance dictionary
    cdef dict __dict__


cdef class Numeric(Const):
    cpdef is_int(self)


cdef class NumericExtended(Numeric):
    cdef public object _raw


cdef class NumericQuoted(Numeric):
    pass


cdef class Word(Const):
    pass


cdef class String(Const):
    pass


cdef class Bytes(Const):
    pass


cdef class Boolean(Const):
    pass


cdef class NoneValue(Const):
    pass


cdef class Pattern(MatchOp):
    pass


cdef class Subst(MatchOp):
    cdef public tuple transforms


cdef class Reference(MatchOp):
    pass


cdef class Wildcard(Pattern):
    pass


cdef class WildcardFirst(Wildcard):
    pass


cdef class Regex(Pattern):
    pass


cdef class RegexFirst(Regex):
    pass


cdef class Special(MatchOp):
    pass


cdef class Appender(Special):
    pass


cdef class AppenderUnique(Appender):
    pass


cdef class ResolvedValue(Const):
    pass


cdef class Concat(MatchOp):
    cdef public tuple _parts
