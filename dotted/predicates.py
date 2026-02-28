"""
Predicate operators for comparison filtering.

PredOp is the base; subclasses define pred().
Engine interface: matches(vals, reference).
"""
from .base import Op


class PredOp(Op):
    """
    Base predicate operator.
    Subclasses define pred(); engine interface is matches(vals, reference).
    """
    op = None

    def __init__(self):
        pass

    def matches(self, vals, reference):
        """
        Yield values from vals that satisfy pred() against reference.
        Duck-types on reference: .value for simple match objects (fast path),
        .values() for accessor-like references.
        """
        if hasattr(reference, 'value'):
            ref = reference.value
            return (v for v in vals if self.pred(v, ref))
        if hasattr(reference, 'values'):
            ref_vals = reference.values(vals)
            return (v for v in vals if any(self.pred(v, rv) for rv in ref_vals))
        raise TypeError(f"reference {reference!r} has no value or values()")

    def pred(self, actual, reference):
        """
        Return True if actual satisfies the predicate against reference.
        Both arguments are raw Python values.
        """
        raise NotImplementedError

    def __repr__(self):
        return self.op

    def __hash__(self):
        return hash(self.op)

    def __eq__(self, other):
        return type(self) is type(other)


class EqPred(PredOp):
    """
    Equality predicate (=).
    Preserves pattern semantics via reference.matches().
    """
    op = '='

    def matches(self, vals, reference):
        """
        Yield values from vals that match reference.
        Delegates to reference.matches() for pattern semantics.
        """
        return reference.matches(vals)

    def pred(self, actual, reference):
        """
        Return True if actual equals reference.
        """
        return actual == reference


class NePred(PredOp):
    """
    Not-equal predicate (!=).
    Preserves pattern semantics via reference.matches(), negated.
    """
    op = '!='

    def matches(self, vals, reference):
        """
        Yield values from vals that do not match reference.
        Delegates to reference.matches() and negates.
        """
        matched = set(id(v) for v in reference.matches(vals))
        return (v for v in vals if id(v) not in matched)

    def pred(self, actual, reference):
        """
        Return True if actual does not equal reference.
        """
        return actual != reference


class LtPred(PredOp):
    """
    Less-than predicate (<).
    """
    op = '<'

    def pred(self, actual, reference):
        """
        Return True if actual < reference.
        """
        try:
            return actual < reference
        except TypeError:
            return False


class GtPred(PredOp):
    """
    Greater-than predicate (>).
    """
    op = '>'

    def pred(self, actual, reference):
        """
        Return True if actual > reference.
        """
        try:
            return actual > reference
        except TypeError:
            return False


class LePred(PredOp):
    """
    Less-than-or-equal predicate (<=).
    """
    op = '<='

    def pred(self, actual, reference):
        """
        Return True if actual <= reference.
        """
        try:
            return actual <= reference
        except TypeError:
            return False


class GePred(PredOp):
    """
    Greater-than-or-equal predicate (>=).
    """
    op = '>='

    def pred(self, actual, reference):
        """
        Return True if actual >= reference.
        """
        try:
            return actual >= reference
        except TypeError:
            return False


# Singleton instances.
EQ = EqPred()
NE = NePred()
LT = LtPred()
GT = GtPred()
LE = LePred()
GE = GePred()

# Lookup by operator string (longest operators first for grammar precedence).
PRED_OPS = {
    '<=': LE,
    '>=': GE,
    '!=': NE,
    '<': LT,
    '>': GT,
    '=': EQ,
}
