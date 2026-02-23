"""
Type specification, sentinels, and registry for the dotted element system.
"""

# Sentinel used as a "missing" marker and as the ANY constant for remove.
marker = object()
ANY = marker

# When a branch with cut (#) matches, we yield this after its results; consumer stops.
CUT_SENTINEL = object()
# Structural marker: in OpGroup.branches, means "after previous branch, emit CUT_SENTINEL and stop".
BRANCH_CUT = object()
# Structural marker: soft cut (##) — after previous branch, suppress later branches for keys already yielded.
BRANCH_SOFTCUT = object()

# Registry of recognized type names for path segment type restrictions.
TYPE_REGISTRY = {
    'str': str, 'bytes': bytes, 'int': int, 'float': float,
    'dict': dict, 'list': list, 'tuple': tuple,
    'set': set, 'frozenset': frozenset, 'bool': bool,
}


class TypeSpec:
    """
    Intermediate parse result for type restriction grammar rules.
    Holds types and negate flag until the access op parse action wraps it.
    """

    def __init__(self, *types, negate=False):
        self.types = types
        self.negate = negate

    def wrap(self, inner):
        """
        Wrap an access op with a TypeRestriction.
        Import is deferred because TypeRestriction lives in elements.py.
        """
        from . import elements
        return elements.TypeRestriction(inner, *self.types, negate=self.negate)
