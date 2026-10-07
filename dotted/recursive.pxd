# Type declarations for the compiler; see base.pxd.
from .access cimport BaseOp


cdef class Recursive(BaseOp):
    # default_branches is a lazyprop, cached in the instance dictionary
    cdef dict __dict__
    cdef public object inner
    cdef public object accessors
    cdef public object depth_start
    cdef public object depth_stop
    cdef public object depth_step

    cpdef is_pattern(self)
    cpdef _effective_branches(self)
    cpdef _has_negative_depth(self)
    cpdef in_depth_range(self, depth, max_dtl=*)
    cpdef _max_depth_to_leaf(self, node, seen=*, depths=*, settings=*)
    cpdef _depth_to_leaf(self, node, seen, depths, settings)
    cpdef _assign(self, acc, node, k, v)


cdef class RecursiveFirst(Recursive):
    pass
