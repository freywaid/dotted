# Type declarations for the compiler; see base.pxd.
from . cimport base
from . cimport matchers


cdef class BaseOp(base.TraversalOp):
    cdef public tuple filters

    cpdef filtered(self, items)


cdef class SimpleOp(BaseOp):
    cpdef items(self, node, settings=*, filtered=*)


cdef class Empty(SimpleOp):
    pass


cdef class AccessOp(SimpleOp):
    cdef public object op

    cpdef quote(self)
    cpdef is_pattern(self)
    cpdef is_template(self)
    cpdef is_reference(self)


cdef class Key(AccessOp):
    cpdef _const_items(self, node)
    cpdef operator(self, top=*)
    cpdef default(self)
    cpdef match(self, op, specials=*)


cdef class Attr(Key):
    pass


cdef class Slot(Key):
    pass


cdef class SlotSpecial(Slot):
    pass


cdef class SliceFilter(BaseOp):
    pass


cdef class Slice(SimpleOp):
    pass


cdef class Invert(SimpleOp):
    pass
