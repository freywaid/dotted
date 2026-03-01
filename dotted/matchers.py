"""
Match ops: constants, patterns, and specials for key/value matching.
"""
import re

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
    Base class for substitution ops ($0, $(name), etc.).
    """
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


class PositionalSubst(Subst):
    """
    Substitution op for captured match groups: $0, $1, $2, etc.
    """
    def resolve(self, bindings, partial=False):
        """
        Resolve this substitution against bindings.
        Returns ResolvedValue on success, self if partial and out of range,
        or raises IndexError if not partial and out of range.
        """
        idx = self.value
        if idx < len(bindings):
            return ResolvedValue(str(bindings[idx]))
        if partial:
            return self
        raise IndexError(
            f'${idx} out of range ({len(bindings)} bindings)')
    def __repr__(self):
        return f'${self.args[0]}'


class NamedSubst(Subst):
    """
    Substitution op for named bindings: $(name), $(key), etc.
    """
    def resolve(self, bindings, partial=False):
        """
        Resolve this substitution against a dict of bindings.
        Returns ResolvedValue on success, self if partial and missing,
        or raises KeyError if not partial and missing.
        """
        name = self.value
        if name in bindings:
            return ResolvedValue(str(bindings[name]))
        if partial:
            return self
        raise KeyError(
            f'$({name}) not found in bindings')
    def __repr__(self):
        return f'$({self.args[0]})'


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
