"""
Dotted result model and transform registry.
"""
import itertools

from . import utils
from .access import Invert


class rdoc(str):
    def expandtabs(*args, **kwargs):
        title = 'Supported transforms\n\n'
        return title + '\n'.join(f'{name}\t{fn.__doc__ or ""}' for name,fn in Dotted._registry.items())


class Dotted:
    _registry = {}

    def registry(self):
        return self._registry

    @classmethod
    def register(cls, name, fn):
        cls._registry[name] = fn

    def __init__(self, results):
        self.ops = tuple(results['ops'])
        self.transforms = tuple(results.get('transforms', ()))
        guard_raw = results.get('guard', ())
        if guard_raw:
            op, val = guard_raw
            self.guard = val
            self.guard_negate = (op == '!=')
        else:
            self.guard = None
            self.guard_negate = False

    def guard_matches(self, val):
        """
        True if val passes the template-level guard (or if no guard is set).
        """
        if self.guard is None:
            return True
        matched = any(True for _ in self.guard.matches((val,)))
        return not matched if self.guard_negate else matched

    def assemble(self, start=0, pedantic=False):
        return assemble(self, start, pedantic=pedantic, transforms=self.transforms)
    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.ops)}, {list(self.transforms)})'
    @staticmethod
    def _hashable(obj):
        """
        Recursively convert unhashable types to hashable equivalents.
        """
        if utils.is_list_like(obj):
            return tuple(Dotted._hashable(x) for x in obj)
        if utils.is_set_like(obj):
            return frozenset(Dotted._hashable(x) for x in obj)
        if utils.is_dict_like(obj):
            if hasattr(obj, 'items') and callable(obj.items):
                iterable = obj.items()
            else:
                iterable = ((k, obj[k]) for k in obj)
            return tuple(sorted((k, Dotted._hashable(v)) for k, v in iterable))
        return obj
    def __hash__(self):
        try:
            return hash((self.ops, self.transforms, self.guard, self.guard_negate))
        except TypeError:
            return hash((self.ops, Dotted._hashable(self.transforms), self.guard, self.guard_negate))
    def __len__(self):
        return len(self.ops)
    def __iter__(self):
        return iter(self.ops)
    def __eq__(self, ops):
        return (self.ops == ops.ops and self.transforms == ops.transforms
                and self.guard == ops.guard and self.guard_negate == ops.guard_negate)
    def __getitem__(self, key):
        return self.ops[key]
    def resolve(self, bindings, partial=False):
        """
        Return a new Dotted with all $N resolved in ops, transforms, and guard.
        """
        new_ops = tuple(op.resolve(bindings, partial) for op in self.ops)
        new_transforms = tuple(t.resolve(bindings, partial) for t in self.transforms)
        new_guard = (
            self.guard.resolve(bindings, partial)
            if self.guard is not None and hasattr(self.guard, 'resolve')
            else self.guard)
        if (all(no is oo for no, oo in zip(new_ops, self.ops))
                and all(nt is ot for nt, ot in zip(new_transforms, self.transforms))
                and new_guard is self.guard):
            return self
        guard_raw = (('!=' if self.guard_negate else '='), new_guard) if new_guard is not None else ()
        return Dotted({'ops': new_ops, 'transforms': new_transforms, 'guard': guard_raw})

    def apply(self, val):
        return apply_transforms(val, self.transforms)

Dotted.registry.__doc__ = rdoc()


def apply_transforms(val, transforms):
    """
    Apply a sequence of transforms to a value.
    Each transform is a Transform object.
    """
    for t in transforms:
        fn = Dotted._registry[t.name]
        val = fn(val, *t.params)
    return val


def assemble(ops, start=0, pedantic=False, transforms=()):
    """
    Reassemble ops into a dotted notation string.

    By default, strips a redundant trailing [] (e.g. hello[] -> hello)
    unless it follows another [] (hello[][] is preserved as-is).
    Set pedantic=True to always preserve trailing [].
    """
    parts = []
    top = True
    for op in itertools.islice(ops, start, None):
        parts.append(op.operator(top))
        if not isinstance(op, Invert):
            top = False
    if not pedantic and not top and len(parts) > 1 and parts[-1] == '[]' and parts[-2] != '[]':
        parts.pop()
    for t in transforms:
        parts.append('|' + t.operator())
    return ''.join(parts)
