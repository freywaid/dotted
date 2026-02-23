"""
Container filter values for dotted.

Container classes for pattern matching in filters and value guards:
  Glob           — the ... element (zero or more)
  DictGlobEntry  — a glob entry in a dict pattern
  ContainerList  — [elements] pattern (prefix: l=list, t=tuple)
  ContainerDict  — {k: v, ...} pattern (prefix: d=dict)
  ContainerSet   — {v, v, ...} pattern (prefix: s=set, fs=frozenset)
  StringGlob     — "prefix"..."suffix" string pattern matching
  BytesGlob      — b"prefix"...b"suffix" bytes pattern matching
  ValueGroup     — (val1, val2) value disjunction

Type prefix semantics:
  Unprefixed = loose matching:
    []        matches list or tuple (empty)
    [1, 2]    matches list or tuple
    {}        matches dict (empty)
    {1, 2}    matches set or frozenset
  Prefixed = strict matching:
    l[...]    list only
    t[...]    tuple only
    d{...}    dict only
    s{...}    set only
    fs{...}   frozenset only
"""
import re
from itertools import combinations

from . import utils
from . import base
from . import match


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _element_matches(pattern, val):
    """
    Check if a single value matches a pattern element.
    None means unconstrained (bare glob). Everything else delegates to matches().
    """
    if pattern is None:
        return True
    return any(True for _ in pattern.matches((val,)))


# ---------------------------------------------------------------------------
# Glob — the ... element
# ---------------------------------------------------------------------------

class Glob(base.Op):
    """
    The ... element: zero or more values with optional pattern and count.

    Forms:
      ...              0 to inf, anything
      ...5             0 to 5
      ...2:5           2 to 5
      ...2:            2 to inf
      .../regex/       0 to inf, each matching regex
      .../regex/2:5    2 to 5, each matching regex
    """

    def __init__(self, pattern=None, min_count=0, max_count=None, *args, **kwargs):
        self.pattern = pattern
        self.min_count = min_count
        self.max_count = max_count
        super().__init__(*args, **kwargs)
        self.args = (self.pattern, self.min_count, self.max_count)

    def matches_element(self, val):
        """
        Check if a single element satisfies this glob's pattern constraint.
        """
        return _element_matches(self.pattern, val)

    def __repr__(self):
        parts = ['...']
        if self.pattern is not None:
            parts.append(repr(self.pattern))
        if self.min_count != 0 or self.max_count is not None:
            if self.min_count == 0 and self.max_count is not None:
                parts.append(str(self.max_count))
            elif self.max_count is None:
                parts.append(f'{self.min_count}:')
            else:
                parts.append(f'{self.min_count}:{self.max_count}')
        return ''.join(parts)


# ---------------------------------------------------------------------------
# DictGlobEntry — a glob entry in a dict pattern
# ---------------------------------------------------------------------------

class DictGlobEntry(base.Op):
    """
    A glob entry in a dict: glob on key side with value pattern.

    Forms:
      ...: *                zero or more extra entries
      .../regex/: *         zero or more keys matching regex
      .../regex/1:5: *      1 to 5 keys matching regex
    """

    def __init__(self, key_glob, val_pattern=None, *args, **kwargs):
        self.key_glob = key_glob
        self.val_pattern = val_pattern
        super().__init__(*args, **kwargs)
        self.args = (self.key_glob, self.val_pattern)

    def matches_entry(self, key, val):
        """
        Check if a key-value pair satisfies this glob entry.
        """
        if not self.key_glob.matches_element(key):
            return False
        if self.val_pattern is not None:
            return _element_matches(self.val_pattern, val)
        return True

    def __repr__(self):
        val_repr = repr(self.val_pattern) if self.val_pattern is not None else ''
        key_repr = repr(self.key_glob)
        if val_repr:
            return f'{key_repr}: {val_repr}'
        return key_repr


# ---------------------------------------------------------------------------
# ContainerList — [elements] pattern
# ---------------------------------------------------------------------------

_LIST_TYPES = (list, tuple)


class ContainerList(base.Op):
    """
    List/tuple pattern: [elem, elem, ...].

    Elements can be scalars, Wildcard, Regex, Glob, or nested containers.

    Type prefix controls matching:
      None  — loose: matches list or tuple
      'l'   — strict: matches list only
      't'   — strict: matches tuple only
    """

    def __init__(self, *elements, type_prefix=None, **kwargs):
        self.elements = tuple(elements)
        self.type_prefix = type_prefix
        super().__init__(**kwargs)
        self.args = self.elements

    def _type_ok(self, v):
        """
        Check if v passes the type constraint.
        """
        if self.type_prefix is None:
            return utils.is_list_like(v)
        if self.type_prefix == 'l':
            return isinstance(v, list)
        if self.type_prefix == 't':
            return isinstance(v, tuple)
        return False

    def matches(self, vals):
        """
        Yield values from vals that match the pattern.
        """
        for v in vals:
            if not self._type_ok(v):
                continue
            if _match_list(self.elements, list(v)):
                yield v

    def matchable(self, op, specials=False):
        """
        ContainerList can match against Const values.
        """
        return isinstance(op, match.Const)

    def __repr__(self):
        prefix = self.type_prefix or ''
        return prefix + '[' + ', '.join(repr(e) for e in self.elements) + ']'


# ---------------------------------------------------------------------------
# ContainerDict — {k: v, ...} pattern
# ---------------------------------------------------------------------------

class ContainerDict(base.Op):
    """
    Dict pattern: {key: val, key: val, ...}.

    Entries are (key_pattern, val_pattern) tuples and DictGlobEntry instances.

    Type prefix controls matching:
      None  — matches dict-like values
      'd'   — strict: matches dict only (isinstance)
    """

    def __init__(self, *entries, type_prefix=None, **kwargs):
        self.entries = tuple(entries)
        self.type_prefix = type_prefix
        super().__init__(**kwargs)
        self.args = self.entries

    def _type_ok(self, v):
        """
        Check if v passes the type constraint.
        """
        if self.type_prefix == 'd':
            return isinstance(v, dict)
        return utils.is_dict_like(v)

    def matches(self, vals):
        """
        Yield values from vals that are dict-like and match the pattern.
        """
        for v in vals:
            if not self._type_ok(v):
                continue
            if _match_dict(self.entries, v):
                yield v

    def matchable(self, op, specials=False):
        """
        ContainerDict can match against Const values.
        """
        return isinstance(op, match.Const)

    def __repr__(self):
        prefix = self.type_prefix or ''
        parts = []
        for e in self.entries:
            if isinstance(e, DictGlobEntry):
                parts.append(repr(e))
            else:
                k, v = e
                parts.append(f'{repr(k)}: {repr(v)}')
        return prefix + '{' + ', '.join(parts) + '}'


# ---------------------------------------------------------------------------
# ContainerSet — {v, v, ...} pattern
# ---------------------------------------------------------------------------

class ContainerSet(base.Op):
    """
    Set/frozenset pattern: {elem, elem, ...}.

    Elements can be scalars, Wildcard, Regex, Glob, or nested containers.

    Type prefix controls matching:
      None   — loose: matches set or frozenset
      's'    — strict: matches set only
      'fs'   — strict: matches frozenset only
    """

    def __init__(self, *elements, type_prefix=None, **kwargs):
        self.elements = tuple(elements)
        self.type_prefix = type_prefix
        super().__init__(**kwargs)
        self.args = self.elements

    def _type_ok(self, v):
        """
        Check if v passes the type constraint.
        """
        if self.type_prefix is None:
            return utils.is_set_like(v)
        if self.type_prefix == 's':
            return isinstance(v, set)
        if self.type_prefix == 'fs':
            return isinstance(v, frozenset)
        return False

    def matches(self, vals):
        """
        Yield values from vals that match the pattern.
        """
        for v in vals:
            if not self._type_ok(v):
                continue
            if _match_set(self.elements, v):
                yield v

    def matchable(self, op, specials=False):
        """
        ContainerSet can match against Const values.
        """
        return isinstance(op, match.Const)

    def __repr__(self):
        prefix = self.type_prefix or ''
        return prefix + '{' + ', '.join(repr(e) for e in self.elements) + '}'


# ---------------------------------------------------------------------------
# StringGlob — "prefix"..."suffix" string pattern
# ---------------------------------------------------------------------------

class StringGlob(base.Op):
    """
    String glob pattern: quoted fragments with ... between them.

    Forms:
      "hello"...           starts with "hello"
      ..."world"           ends with "world"
      "hello"..."world"    starts with "hello", ends with "world"
      "a"..."b"..."c"      contains substrings in order
      "hello"...5          prefix + at most 5 more chars
      "hello"...2:5"world" 2-5 chars between fragments
    """

    def __init__(self, *parts, **kwargs):
        self.parts = tuple(parts)
        self._pattern = self._compile()
        super().__init__(**kwargs)
        self.args = self.parts

    def _compile(self):
        """
        Build compiled regex from parts.
        """
        regex_parts = []
        for p in self.parts:
            if isinstance(p, str):
                regex_parts.append(re.escape(p))
            elif isinstance(p, Glob):
                if p.pattern is not None and isinstance(p.pattern, match.Regex):
                    char_pat = p.pattern.args[0]
                else:
                    char_pat = '.'
                lo = p.min_count
                hi = p.max_count
                if lo == 0 and hi is None:
                    regex_parts.append(f'{char_pat}*')
                elif lo == 0:
                    regex_parts.append(f'{char_pat}{{0,{hi}}}')
                elif hi is None:
                    regex_parts.append(f'{char_pat}{{{lo},}}')
                else:
                    regex_parts.append(f'{char_pat}{{{lo},{hi}}}')
        return re.compile('^' + ''.join(regex_parts) + '$')

    def matches(self, vals):
        """
        Yield str vals matching the glob pattern.
        """
        for v in vals:
            if isinstance(v, str) and self._pattern.fullmatch(v):
                yield v

    def matchable(self, op, specials=False):
        """
        StringGlob can match against Const values.
        """
        return isinstance(op, match.Const)

    def __repr__(self):
        parts = []
        for p in self.parts:
            if isinstance(p, str):
                parts.append(repr(p))
            else:
                parts.append(repr(p))
        return ''.join(parts)


# ---------------------------------------------------------------------------
# BytesGlob — b"prefix"...b"suffix" bytes pattern
# ---------------------------------------------------------------------------

class BytesGlob(base.Op):
    """
    Bytes glob pattern: byte-string fragments with ... between them.

    Forms:
      b"hello"...            starts with b"hello"
      ...b"world"            ends with b"world"
      b"hello"...b"world"    starts with b"hello", ends with b"world"
      b"a"...b"b"...b"c"     contains substrings in order
      b"hello"...5           prefix + at most 5 more bytes
      b"hello"...2:5b"world" 2-5 bytes between fragments
    """

    def __init__(self, *parts, **kwargs):
        self.parts = tuple(parts)
        self._pattern = self._compile()
        super().__init__(**kwargs)
        self.args = self.parts

    def _compile(self):
        """
        Build compiled bytes regex from parts.
        """
        regex_parts = []
        for p in self.parts:
            if isinstance(p, bytes):
                regex_parts.append(re.escape(p))
            elif isinstance(p, Glob):
                if p.pattern is not None and isinstance(p.pattern, match.Regex):
                    char_pat = p.pattern.args[0].encode()
                else:
                    char_pat = b'.'
                lo = p.min_count
                hi = p.max_count
                if lo == 0 and hi is None:
                    regex_parts.append(char_pat + b'*')
                elif lo == 0:
                    regex_parts.append(char_pat + b'{0,' + str(hi).encode() + b'}')
                elif hi is None:
                    regex_parts.append(char_pat + b'{' + str(lo).encode() + b',}')
                else:
                    regex_parts.append(char_pat + b'{' + str(lo).encode() + b',' + str(hi).encode() + b'}')
        return re.compile(b'^' + b''.join(regex_parts) + b'$')

    def matches(self, vals):
        """
        Yield bytes vals matching the glob pattern.
        """
        for v in vals:
            if isinstance(v, bytes) and self._pattern.fullmatch(v):
                yield v

    def matchable(self, op, specials=False):
        """
        BytesGlob can match against Const values.
        """
        return isinstance(op, match.Const)

    def __repr__(self):
        parts = []
        for p in self.parts:
            if isinstance(p, bytes):
                parts.append(repr(p))
            else:
                parts.append(repr(p))
        return ''.join(parts)


# ---------------------------------------------------------------------------
# ValueGroup — (val1, val2) value disjunction
# ---------------------------------------------------------------------------

class ValueGroup(base.Op):
    """
    Value group: (val1, val2, ...) — matches any alternative.

    Alternatives can be any value pattern: String, Regex, Wildcard,
    Container*, StringGlob, scalars.
    """

    def __init__(self, *alternatives, **kwargs):
        self.alternatives = tuple(alternatives)
        super().__init__(**kwargs)
        self.args = self.alternatives

    def matches(self, vals):
        """
        Yield vals matching any alternative.
        """
        for v in vals:
            for alt in self.alternatives:
                if any(True for _ in alt.matches((v,))):
                    yield v
                    break

    def matchable(self, op, specials=False):
        """
        ValueGroup can match against Const values.
        """
        return isinstance(op, match.Const)

    def __repr__(self):
        return '(' + ', '.join(repr(a) for a in self.alternatives) + ')'


# ---------------------------------------------------------------------------
# Matching algorithms
# ---------------------------------------------------------------------------

def _match_list(elements, actual):
    """
    Match a list pattern against an actual list/tuple.
    Recursive backtracking for Glob elements.
    """
    return _match_list_rec(elements, 0, actual, 0)


def _match_list_rec(elements, ei, actual, ai):
    """
    Recursive list matcher.
    ei: current index in elements
    ai: current index in actual
    """
    if ei == len(elements):
        return ai == len(actual)

    elem = elements[ei]

    if isinstance(elem, Glob):
        lo = elem.min_count
        hi = elem.max_count if elem.max_count is not None else len(actual) - ai
        hi = min(hi, len(actual) - ai)

        if lo > len(actual) - ai:
            return False

        for count in range(lo, hi + 1):
            consumed = actual[ai:ai + count]
            if all(elem.matches_element(c) for c in consumed):
                if _match_list_rec(elements, ei + 1, actual, ai + count):
                    return True
        return False

    else:
        if ai >= len(actual):
            return False
        if _element_matches(elem, actual[ai]):
            return _match_list_rec(elements, ei + 1, actual, ai + 1)
        return False


def _match_dict(entries, actual):
    """
    Match a dict pattern against an actual dict.

    1. Concrete entries must each match exactly one key
    2. No globs → no remaining keys allowed (exact)
    3. With globs → remaining keys must satisfy glob constraints
    """
    concrete = []
    globs = []
    for e in entries:
        if isinstance(e, DictGlobEntry):
            globs.append(e)
        else:
            concrete.append(e)

    consumed = set()
    actual_keys = list(actual.keys())

    for key_pat, val_pat in concrete:
        found = False
        for k in actual_keys:
            if k in consumed:
                continue
            if _element_matches(key_pat, k) and _element_matches(val_pat, actual[k]):
                consumed.add(k)
                found = True
                break
        if not found:
            return False

    remaining = [k for k in actual_keys if k not in consumed]

    if not globs:
        return len(remaining) == 0

    if len(globs) == 1:
        return _match_dict_single_glob(globs[0], remaining, actual)
    return _match_dict_multi_glob(globs, 0, remaining, actual)


def _match_dict_single_glob(glob_entry, remaining_keys, actual):
    """
    Check remaining keys against a single DictGlobEntry.
    """
    matching = []
    for k in remaining_keys:
        if glob_entry.matches_entry(k, actual[k]):
            matching.append(k)
        else:
            return False

    count = len(matching)
    if count < glob_entry.key_glob.min_count:
        return False
    if glob_entry.key_glob.max_count is not None and count > glob_entry.key_glob.max_count:
        return False
    return True


def _match_dict_multi_glob(globs, gi, remaining_keys, actual):
    """
    Backtracking partition of remaining keys among multiple globs.
    """
    if gi == len(globs):
        return len(remaining_keys) == 0

    glob_entry = globs[gi]
    lo = glob_entry.key_glob.min_count
    hi = glob_entry.key_glob.max_count
    if hi is None:
        hi = len(remaining_keys)
    hi = min(hi, len(remaining_keys))

    eligible = [k for k in remaining_keys if glob_entry.matches_entry(k, actual[k])]

    if len(eligible) < lo:
        return False

    for count in range(lo, min(hi, len(eligible)) + 1):
        for combo in combinations(eligible, count):
            leftover = [k for k in remaining_keys if k not in combo]
            if _match_dict_multi_glob(globs, gi + 1, leftover, actual):
                return True
    return False


def _match_set(elements, actual):
    """
    Match a set pattern against an actual set/frozenset.

    1. Concrete patterns must each match one member
    2. No globs → no remaining members (exact)
    3. With globs → remaining must satisfy glob constraints
    """
    concrete = []
    globs = []
    for e in elements:
        if isinstance(e, Glob):
            globs.append(e)
        else:
            concrete.append(e)

    remaining = set(actual)

    for pat in concrete:
        found = False
        for member in list(remaining):
            if _element_matches(pat, member):
                remaining.discard(member)
                found = True
                break
        if not found:
            return False

    if not globs:
        return len(remaining) == 0

    if len(globs) == 1:
        return _match_set_single_glob(globs[0], remaining)
    return _match_set_multi_glob(globs, 0, remaining)


def _match_set_single_glob(glob, remaining):
    """
    Check remaining set members against a single Glob.
    """
    matching = []
    for m in remaining:
        if glob.matches_element(m):
            matching.append(m)
        else:
            return False

    count = len(matching)
    if count < glob.min_count:
        return False
    if glob.max_count is not None and count > glob.max_count:
        return False
    return True


def _match_set_multi_glob(globs, gi, remaining):
    """
    Backtracking partition of remaining set members among multiple globs.
    """
    if gi == len(globs):
        return len(remaining) == 0

    glob = globs[gi]
    lo = glob.min_count
    hi = glob.max_count
    if hi is None:
        hi = len(remaining)
    hi = min(hi, len(remaining))

    eligible = [m for m in remaining if glob.matches_element(m)]

    if len(eligible) < lo:
        return False

    for count in range(lo, min(hi, len(eligible)) + 1):
        for combo in combinations(eligible, count):
            leftover = remaining - set(combo)
            if _match_set_multi_glob(globs, gi + 1, leftover):
                return True
    return False
