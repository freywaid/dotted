"""
Match ops: constants, patterns, and specials for key/value matching.
"""
import functools
import operator
import re
from collections import namedtuple

import pyparsing as pp

from . import base
from .base import MatchOp


class Const(MatchOp):
    @property
    def value(self):
        return self.args[0]
    def matchable(self, op, specials=False):
        return isinstance(op, Const)
    def matches(self, vals):
        return (v for v in vals if self.value == v)


class Numeric(Const):
    def is_int(self):
        try:
            return str(self.args[0]) == str(int(self.args[0]))
        except (ValueError, TypeError):
            return False
    @property
    def value(self):
        return int(self.args[0]) if self.is_int() else float(self.args[0])
    def __repr__(self):
        return f'{self.value}'


class NumericExtended(Numeric):
    """
    Numeric from extended literal forms: scientific notation (1e10, 1e-12),
    underscore separators (1_000), hex (0x1F), octal (0o17), binary (0b1010).
    Stores the converted numeric value (int when possible, float otherwise).
    """
    def __init__(self, *args, **kwargs):
        if len(args) == 3 and isinstance(args[2], pp.ParseResults):
            raw = args[2][0]
            stripped = raw.lstrip('-')
            if len(stripped) > 1 and stripped[0] == '0' and stripped[1] in 'xXoObB':
                val = int(raw, 0)
            else:
                f = float(raw.replace('_', ''))
                val = int(f) if f == int(f) else f
            super().__init__(args[0], args[1], pp.ParseResults([val]), **kwargs)
            self._raw = raw
        else:
            super().__init__(*args, **kwargs)
            self._raw = None

    def quote(self):
        """
        Return the original notation form of this extended numeric literal.
        """
        if self._raw is not None:
            return self._raw
        return repr(self)


class NumericQuoted(Numeric):
    def __repr__(self):
        if self.is_int():
            return super().__repr__()
        return f"#'{str(self.value)}'"


class Word(Const):
    def __repr__(self):
        return f'{self.value}'

    def quote(self):
        """
        Return the dotted notation form of this word.
        """
        from .access import _needs_quoting, _quote_str, _is_numeric_str
        v = self.value
        if _is_numeric_str(v):
            return _quote_str(v)
        if _needs_quoting(v):
            return _quote_str(v)
        return v


class String(Const):
    def __repr__(self):
        return f'{repr(self.value)}'

    def quote(self):
        """
        Return the dotted notation form of this quoted string.
        """
        from .access import _quote_str
        return _quote_str(self.value)


class Bytes(Const):
    """
    Byte string literal: b"..." or b'...'
    """
    @property
    def value(self):
        return self.args[0].encode() if isinstance(self.args[0], str) else self.args[0]
    def __repr__(self):
        return repr(self.value)


class Boolean(Const):
    """
    Wrapper for True/False in filter values
    """
    @property
    def value(self):
        return self.args[0] == 'True'
    def __repr__(self):
        return str(self.value)


class NoneValue(Const):
    """
    Wrapper for None in filter values
    """
    @property
    def value(self):
        return None
    def matches(self, vals):
        return (v for v in vals if v is None)
    def __repr__(self):
        return 'None'


class Pattern(MatchOp):
    def __repr__(self):
        return str(self.value)
    def is_pattern(self):
        """
        True — patterns (wildcards, regexes, etc.) are patterns.
        """
        return True
    def matchable(self, op, specials=False):
        raise NotImplementedError


class Subst(Pattern):
    """
    Substitution op: $0, $(name), $(0|transform), $(name|int), etc.
    Resolves against bindings via __getitem__: list for positional,
    dict for named or numeric keys.  Optional transforms applied
    after lookup.
    """
    def __init__(self, *args, transforms=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.transforms = tuple(transforms)

    @property
    def value(self):
        return self.args[0]

    def is_template(self):
        """
        True — this is a substitution reference.
        """
        return True

    def matchable(self, op, specials=False):
        return False

    def _apply_transforms(self, val):
        """
        Apply this op's transforms to a resolved value.
        """
        if not self.transforms:
            return val
        from .results import apply_transforms
        return apply_transforms(val, self.transforms)

    def _transform_suffix(self):
        """
        Render |transform1|transform2 suffix for quote/repr.
        """
        if not self.transforms:
            return ''
        return ''.join(f'|{t.operator()}' for t in self.transforms)

    def resolve(self, bindings, partial=False):
        """
        Resolve this substitution against bindings.
        """
        try:
            val = self._apply_transforms(bindings[self.value])
            return ResolvedValue(str(val))
        except (KeyError, IndexError, TypeError):
            if partial:
                return self
            raise

    def __repr__(self):
        suffix = self._transform_suffix()
        v = self.args[0]
        if isinstance(v, int) and not suffix:
            return f'${v}'
        return f'$({v}{suffix})'



class Reference(MatchOp):
    """
    Internal reference: $$(dotted.path) resolves against the root object
    during traversal.  Relative references use ^ prefixes:
      $$(^path)   — resolve against current node
      $$(^^path)  — resolve against parent node
      $$(^^^path) — grandparent, etc.
    """
    @property
    def value(self):
        return self.args[0]

    @property
    def depth(self):
        """
        Number of leading ^ characters.
        0 = root, 1 = current node, 2 = parent, 3 = grandparent, etc.
        """
        count = 0
        for ch in self.value:
            if ch != '^':
                break
            count += 1
        return count

    @property
    def inner_path(self):
        """
        The dotted path after stripping ^ prefixes.
        """
        return self.value.lstrip('^')

    def is_reference(self):
        """
        True — this is an internal reference.
        """
        return True

    def is_pattern(self):
        """
        True if the reference path is itself a pattern.
        """
        from .api import is_pattern
        path = self.inner_path
        return is_pattern(path) if path else False

    def matchable(self, op, specials=False):
        return False

    def resolve_ref(self, root, node=None, parents=()):
        """
        Resolve this reference against the appropriate target.
        depth 0 = root, 1 = current node, 2+ = ancestor from parents stack.
        """
        from .api import get
        d = self.depth
        if d == 0:
            target = root
        elif d == 1:
            target = node
        else:
            idx = d - 2
            if idx >= len(parents):
                raise KeyError(f'$$({self.value}): not enough ancestors')
            target = parents[idx]
        path = self.inner_path
        if not path:
            return target
        _marker = object()
        val = get(target, path, default=_marker)
        if val is _marker:
            raise KeyError(f'$$({self.value}) not found')
        return val

    def quote(self):
        """
        Return the dotted notation form of this reference.
        """
        return f'$$({self.args[0]})'

    def __repr__(self):
        return f'$$({self.args[0]})'


class Wildcard(Pattern):
    @property
    def value(self):
        return '*'
    def matches(self, vals):
        return iter(v for v in vals if v is not base.NOP)
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or specials


class WildcardFirst(Wildcard):
    @property
    def value(self):
        return '*?'
    def matches(self, vals):
        v = next(super().matches(vals), base.marker)
        return iter(() if v is base.marker else (v,))
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or \
            (specials and isinstance(op, (Special, WildcardFirst, RegexFirst)))


class Regex(Pattern):
    @property
    def value(self):
        return f'/{self.args[0]}/'
    @property
    def pattern(self):
        return re.compile(self.args[0])
    def matches(self, vals):
        vals = (v for v in vals if v is not base.NOP)
        vals = {v if isinstance(v, (str, bytes)) else str(v): v for v in vals}
        iterable = (self.pattern.fullmatch(v) for v in vals)
        # we want to regex match numerics as strings but return numerics
        # unless they were transformed, of course
        for m in iterable:
            if not m:
                continue
            if m[0] != m.string:
                yield m[0]
            else:
                yield vals[m.string]
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or (specials and isinstance(op, (Special, Regex)))


class RegexFirst(Regex):
    @property
    def value(self):
        return f'/{self.args[0]}/?'
    def matches(self, vals):
        iterable = super().matches(vals)
        v = next(iterable, base.marker)
        return iter(() if v is base.marker else (v,))
    def matchable(self, op, specials=False):
        return isinstance(op, Const) or (specials and isinstance(op, (Special, RegexFirst)))


class Special(MatchOp):
    @property
    def value(self):
        return self.args[0]
    def quote(self):
        """
        Return the dotted notation form of this special op.
        """
        return self.value
    def matchable(self, op, specials=False):
        return isinstance(op, Special)
    def matches(self, vals):
        return (v for v in vals if v == self.value)


class Appender(Special):
    @property
    def value(self):
        return '+'
    def matchable(self, op, specials=False):
        return isinstance(op, Appender)
    def matches(self, vals):
        return (v for v in vals if self.value in v)


class AppenderUnique(Appender):
    @property
    def value(self):
        return '+?'


class ResolvedValue(Const):
    """
    Pre-resolved value from $N substitution.
    repr() returns the raw string so operator()/assemble() splices it verbatim.
    """
    def __repr__(self):
        return str(self.value)


ConcatPart = namedtuple('ConcatPart', ['op', 'transforms'])


class Concat(MatchOp):
    """
    Concatenation of key parts using Python's native + operator.
    Each part is a ConcatPart(op, transforms) where op is a matcher
    and transforms are applied to that part's resolved value before
    the + reduction.

    str + str = concat, int + int = add, mixed = TypeError.
    """
    def __init__(self, *parts):
        self._parts = tuple(parts)
        # MatchOp.__init__ expects args; store parts as args
        super().__init__(*[p.op for p in parts])

    @property
    def parts(self):
        """
        The ConcatPart tuple.
        """
        return self._parts

    def _apply_part_transforms(self, val, transforms):
        """
        Apply per-part transforms to a value.
        """
        if not transforms:
            return val
        from .results import apply_transforms
        return apply_transforms(val, transforms)

    def _reduce(self, values):
        """
        Reduce values with Python's native + operator.
        """
        return functools.reduce(operator.add, values)

    @property
    def value(self):
        """
        Concrete value: reduce all parts' values with +.
        Only valid when all parts are concrete (Const).
        """
        vals = []
        for p in self._parts:
            v = self._apply_part_transforms(p.op.value, p.transforms)
            vals.append(v)
        return self._reduce(vals)

    def matches(self, vals):
        """
        Filter vals by equality with the concatenated value.
        """
        target = self.value
        return (v for v in vals if v == target)

    def matchable(self, op, specials=False):
        """
        Concat can match against concrete keys.
        """
        return isinstance(op, Const)

    def is_template(self):
        """
        True if any part is a substitution reference.
        """
        return any(p.op.is_template() for p in self._parts)

    def is_reference(self):
        """
        True if any part is an internal reference.
        """
        return any(p.op.is_reference() for p in self._parts)

    def is_pattern(self):
        """
        Concat is not a pattern — it resolves to a single concrete key.
        """
        return False

    @property
    def depth(self):
        """
        Max depth of any Reference part (for _needs_parents).
        """
        d = 0
        for p in self._parts:
            if hasattr(p.op, 'depth'):
                d = max(d, p.op.depth)
        return d

    def resolve(self, bindings, partial=False):
        """
        Resolve substitution parts against bindings.
        If all parts become concrete after resolution, collapse via +.
        """
        new_parts = []
        all_concrete = True
        changed = False
        for p in self._parts:
            new_op = p.op.resolve(bindings, partial)
            if new_op is not p.op:
                changed = True
            new_parts.append(ConcatPart(new_op, p.transforms))
            if not isinstance(new_op, Const):
                all_concrete = False
        if not changed:
            return self
        if all_concrete:
            vals = [self._apply_part_transforms(p.op.value, p.transforms)
                    for p in new_parts]
            return ResolvedValue(str(self._reduce(vals)))
        return Concat(*new_parts)

    def resolve_ref(self, root, node=None, parents=()):
        """
        Resolve reference parts, apply per-part transforms, reduce with +.
        """
        vals = []
        for p in self._parts:
            if p.op.is_reference():
                v = p.op.resolve_ref(root, node=node, parents=parents)
            else:
                v = p.op.value
            v = self._apply_part_transforms(v, p.transforms)
            vals.append(v)
        return self._reduce(vals)

    def _transform_suffix(self, transforms):
        """
        Render |transform1|transform2 suffix for a part.
        """
        if not transforms:
            return ''
        return ''.join(f'|{t.operator()}' for t in transforms)

    def quote(self):
        """
        Return the dotted notation form: part1+part2+part3.
        """
        parts = []
        for p in self._parts:
            parts.append(p.op.quote() + self._transform_suffix(p.transforms))
        return '+'.join(parts)

    def __repr__(self):
        return self.quote()

    def __eq__(self, other):
        return isinstance(other, Concat) and self._parts == other._parts

    def __hash__(self):
        return hash(self._parts)
