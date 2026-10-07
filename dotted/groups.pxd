# Type declarations for the compiler; see base.pxd.
from . cimport base


cdef class SoftcutPaths:
    cdef public object count
    cdef public dict by_keys
    cdef public dict by_prefix

    cpdef add(self, path)
    cpdef extend(self, paths)
    cpdef overlaps(self, path)


cdef class OpGroup(base.TraversalOp):
    cdef public tuple branches

    cpdef is_pattern(self)


cdef class OpGroupOr(OpGroup):
    cpdef _next_marker(self, i)


cdef class OpGroupFirst(OpGroup):
    pass


cdef class OpGroupAnd(OpGroup):
    pass


cdef class OpGroupNot(OpGroup):
    cdef public object inner
