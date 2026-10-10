# Type declarations for the compiler; see base.pxd.
cimport cython
cimport dotted.base as base


cpdef _needs_parents(ops)
cpdef build_default(ops)
cpdef build(ops, node, deepcopy=*, strict=*, settings=*)
cpdef simple_get(chain, node, strict=*)

@cython.locals(op=base.TraversalOp)
cpdef advance(base.Frame frame, base.DepthStack stack, paths)

cpdef base.Settings settings_for(ops, node, strict, settings)
cpdef _is_container(obj)
cpdef _format_path(segments)
cpdef updates(ops, node, val, has_defaults=*, _path=*, nop=*, strict=*, settings=*)
cpdef removes(ops, node, val=*, nop=*, strict=*, settings=*)
