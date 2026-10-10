# Type declarations for the compiler. They apply only when base.py is
# compiled (see setup.py); base.py itself is unchanged by them.
#
# Every class here becomes an extension type: its fields are C struct
# fields instead of dictionary entries, and the cpdef methods are called
# directly, without a lookup, wherever the compiler knows the type.
#
# Only a method without closures (generator expressions, nested
# functions) can be cpdef, and every override of it in a compiled
# subclass must be closure-free too.
#
# The other .pxd files cimport this one as `cimport dotted.base as base`:
# the compiler's dependency scanner follows that spelling (and
# `from .base cimport Name`) but not `from . cimport base`, which left a
# change here unbuilt in the modules that use it.

cdef class MatchResult:
    cdef public object val


cdef class Op:
    cdef public tuple args
    cdef public object parsed

    cpdef match_keys(self, node)
    cpdef is_recursive(self)
    cpdef is_slice(self)
    cpdef is_variadic(self)


cdef class Settings:
    cdef public object root
    cdef public object parents
    cdef public object strict

    cpdef below(self, node)


cdef class Frame:
    cdef public tuple ops
    cdef public object node
    cdef public tuple prefix
    cdef public object depth
    cdef public object seen_paths
    cdef public Settings settings


cdef class DepthStack:
    cdef public object _stacks
    cdef public object level
    cdef public object current
    cdef public tuple transforms

    cpdef push(self, frame)
    cpdef pop(self)
    cpdef push_level(self)
    cpdef pop_level(self)


cdef class TraversalOp(Op):
    cpdef push_children(self, DepthStack stack, Frame frame, paths)
    cpdef update_items(self, node, settings=*)
    cpdef to_branches(self)
    cpdef leaf_op(self)
    cpdef excluded_keys(self, node)


cdef class MatchOp(Op):
    cpdef quote(self)
    cpdef quote_top(self)
    cpdef to_branches(self)


cdef class Transform(Op):
    cdef public object name
    cdef public tuple params
