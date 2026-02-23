"""
Type specification and registry for dotted path type restrictions.
"""

# Registry of recognized type names for path segment type restrictions.
_TYPE_REGISTRY = {
    'str': str, 'bytes': bytes, 'int': int, 'float': float,
    'dict': dict, 'list': list, 'tuple': tuple,
    'set': set, 'frozenset': frozenset, 'bool': bool,
}


class _TypeSpec:
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
