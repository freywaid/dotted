# Type declarations for the compiler; see base.pxd.
cimport dotted.base as base


cdef class Wrap(base.TraversalOp):
    cdef public object inner


cdef class NopWrap(Wrap):
    pass


cdef class ValueGuard(Wrap):
    cdef public object guard
    cdef public object pred_op
    cdef public tuple transforms


cdef class TypeRestriction(Wrap):
    cdef public tuple types
    cdef public object negate

    cpdef allows(self, node)


cdef class FilterWrap(Wrap):
    cdef public tuple filters
