# Type declarations for the compiler; see base.pxd.
from . cimport base


cdef class FilterOp(base.MatchOp):
    pass


cdef class FilterKey(base.MatchOp):
    cdef public tuple parts
    cdef public object value


cdef class FilterKeyValue(FilterOp):
    cdef public object key
    cdef public object val
    cdef public tuple transforms

    cpdef _eq_match(self, node)
    cpdef is_filtered(self, node)


cdef class FilterKeyValueNot(FilterKeyValue):
    pass


cdef class FilterKeyValueLt(FilterKeyValue):
    pass


cdef class FilterKeyValueGt(FilterKeyValue):
    pass


cdef class FilterKeyValueLe(FilterKeyValue):
    pass


cdef class FilterKeyValueGe(FilterKeyValue):
    pass


cdef class FilterGroup(FilterOp):
    cdef public object inner


cdef class FilterAnd(FilterOp):
    cdef public tuple filters


cdef class FilterOr(FilterOp):
    cdef public tuple filters


cdef class FilterKeyValueFirst(FilterOp):
    cdef public object inner


cdef class FilterNot(FilterOp):
    cdef public object inner
