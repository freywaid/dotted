"""
"""
import contextlib
import decimal
import re
import pyparsing as pp
from pyparsing import pyparsing_common as ppc

pp.ParserElement.enable_packrat()
from . import base
from . import groups
from . import matchers
from . import filters as flt
from . import access
from . import predicates
from . import recursive
from . import utypes
from . import wrappers
from . import containers as ct

# ---- parse actions that build groups ----
#
# These turn what the grammar matched into group ops. They run only while a
# path is being parsed, so they live here with the parser, not in groups.py
# with the ops the engine runs.

def _to_branch(item):
    """
    Convert a parse result item (op_seq Group or OpGroup) to a branch tuple.
    """
    if isinstance(item, groups.OpGroup):
        return (item,)
    if isinstance(item, (list, tuple, pp.ParseResults)):
        return tuple(item)
    return (item,)


def inner_not_action(t):
    """
    Parse action for unified negation: ! atom.
    The atom is either an OpGroup (from grouped expression) or an op_seq.
    OpGroupNot takes a single branch as its inner pattern.
    """
    item = t[0]
    if isinstance(item, (list, tuple, pp.ParseResults)) and len(item) == 1:
        item = item[0]  # an op_seq of one op
    if isinstance(item, groups.Pack):
        raise ValueError(f'a packed group cannot be negated: !{item!r}')
    if isinstance(item, groups.OpGroupOr):
        # a parenthesized single path, !(a.b): the group adds nothing, so
        # !(a.b) is the same op as !a.b
        branches = list(base.branches_only(item.branches))
        if len(branches) == 1 and len(branches) == len(item.branches):
            return groups.OpGroupNot(branches[0])
    return groups.OpGroupNot(_to_branch(item))


def inner_and_action(t):
    """
    Parse action for unified conjunction: atom & atom & ...
    """
    branches = [_to_branch(item) for item in t]
    return groups.OpGroupAnd(*branches)


def inner_or_action(t):
    """
    Parse action for unified disjunction: term , term , ...
    Each term is a Group containing [inner_and_result, optional_cut_marker].
    If there's only one term with no cut marker, pass through without wrapping.
    """
    terms = list(t)
    # Single term, no cut marker — pass through (don't wrap in OpGroupOr)
    if len(terms) == 1 and len(terms[0]) == 1:
        return terms[0][0]
    out = []
    for term in terms:
        item = term[0]
        out.append(_to_branch(item))
        if len(term) >= 2:
            if term[1] == '##':
                out.append(base.BRANCH_SOFTCUT)
            elif term[1] == '#':
                out.append(base.BRANCH_CUT)
    return groups.OpGroupOr(*out)


def inner_to_opgroup(parsed_result):
    """
    Parse action: convert (inner_expr) to OpGroup.
    Unwraps single-branch OpGroupOr containing a sole OpGroupAnd/OpGroupNot,
    since the OpGroupOr wrapper is redundant in that case.
    Does NOT unwrap OpGroupNot or OpGroupAnd — those carry semantic meaning.
    """
    items = list(parsed_result)
    if not items:
        return groups.OpGroupOr()
    # If there's a single OpGroup (or packed group) result, use it directly
    if len(items) == 1 and isinstance(items[0], (groups.OpGroup, groups.Pack)):
        inner = items[0]
        # Only unwrap redundant OpGroupOr wrapping a single OpGroupAnd/OpGroupNot
        if isinstance(inner, groups.OpGroupOr):
            branches = list(base.branches_only(inner.branches))
            if (len(branches) == 1 and isinstance(branches[0], tuple)
                    and len(branches[0]) == 1
                    and isinstance(branches[0][0], (groups.OpGroupAnd, groups.OpGroupNot))):
                return branches[0][0]
        return inner
    # Multiple items or single non-OpGroup: treat as a single branch (op_seq)
    # This handles cases like (name.first) where inner_expr flattens to [name, first]
    branch = tuple(items)
    return groups.OpGroupOr(branch)


def inner_to_opgroup_first(parsed_result):
    """
    Parse action: convert (inner_expr)? to OpGroupFirst.
    """
    return groups.OpGroupFirst(*inner_to_opgroup(parsed_result).branches)


def slot_to_opgroup(parsed_result):
    """
    Convert slot grouping [(*&filter, +)] or [(*&filter#, +)] to OpGroup.
    Each slot item becomes a branch; # inserts base.BRANCH_CUT after that branch.
    Parse result items may be ParseResults (from Group), so unwrap to get Slot/SlotSpecial/NopWrap.
    """
    _slot_types = (access.Slot, access.SlotSpecial, wrappers.NopWrap, wrappers.FilterWrap)
    out = []
    for item in parsed_result:
        if isinstance(item, _slot_types):
            out.append((item,))
            continue
        if not (isinstance(item, (list, tuple, pp.ParseResults)) and len(item) >= 1):
            continue
        first = item[0]
        while isinstance(first, (list, tuple, pp.ParseResults)) and len(first) == 1:
            first = first[0]
        if isinstance(first, _slot_types):
            out.append((first,))
            if len(item) >= 2 and item[1] == '##':
                out.append(base.BRANCH_SOFTCUT)
            elif len(item) >= 2 and item[1] == '#':
                out.append(base.BRANCH_CUT)
    return groups.OpGroupOr(*out)


def slot_to_opgroup_first(parsed_result):
    """
    Convert slot grouping [(*&filter, +)?] to OpGroupFirst.
    """
    return groups.OpGroupFirst(*slot_to_opgroup(parsed_result).branches)


def as_attrs_opgroup(group):
    """
    Promote unresolved bare identifiers (parsed as Keys) to Attrs.
    Used by @(group) syntax — the inner group grammar doesn't know the
    access mode, so it produces Keys; this resolves them as Attrs.
    """
    if hasattr(group, 'as_attrs'):
        return group.as_attrs()
    # Single Key not wrapped in OpGroup (e.g. @(a) with single item)
    if type(group) is access.Key:
        return access.Attr(*group.args)
    return group


def _classify_tokens(tokens):
    """
    Classify a list of parse-result tokens into (typespec, filters, transforms, nop, args).

    Returns a tuple of:
        typespec: a single TypeSpec or None
        filters: list of FilterOp instances
        transforms: list of Transform instances
        nop: bool, True if '~' was present
        args: everything else
    """
    typespec = None
    filters = []
    transforms = []
    nop = False
    args = []
    for item in tokens:
        if isinstance(item, utypes.TypeSpec):
            typespec = item
        elif isinstance(item, flt.FilterOp):
            filters.append(item)
        elif isinstance(item, base.Transform):
            transforms.append(item)
        elif item == '~':
            nop = True
        else:
            args.append(item)
    return typespec, filters, transforms, nop, args

S = pp.Suppress
L = pp.Literal
Opt = pp.Optional
ZM = pp.ZeroOrMore
OM = pp.OneOrMore
at = pp.Suppress('@')
equal = pp.Suppress('=')
not_equal = pp.Suppress('!=')
dot = pp.Suppress('.')
amp = pp.Suppress('&')
comma = pp.Suppress(',')
# Optional whitespace after comma so "(a, b)" and "(.a, .b)" parse (path/op group)
_comma_ws = comma + pp.Optional(pp.White()).suppress()
lb = pp.Suppress('[')
rb = pp.Suppress(']')
colon = pp.Suppress(':')
pipe = pp.Suppress('|')
slash = pp.Suppress('/')
backslash = pp.Suppress('\\')
name = pp.Word(pp.alphas + '_', pp.alphanums + '_')
transform_name = pp.Word(pp.alphas + '_', pp.alphanums + '_.')
quoted = pp.QuotedString('"', esc_char='\\') | pp.QuotedString("'", esc_char='\\')
plus = pp.Literal('+')
integer = ppc.signed_integer
none = (pp.Literal('None') | pp.Literal('null')).set_parse_action(matchers.NoneValue)
true = (pp.Literal('True') | pp.Literal('true')).set_parse_action(matchers.Boolean)
false = (pp.Literal('False') | pp.Literal('false')).set_parse_action(matchers.Boolean)

reserved = '.[]*:|+?/=,@&()!~#{}$<>;' # {} for container syntax, $ for substitution, <> for comparisons, ; for packing
breserved = ''.join('\\' + i for i in reserved)
tilde = L('~')

# atomic ops
appender = pp.Literal('+').set_parse_action(matchers.Appender)
appender_unique = pp.Literal('+?').set_parse_action(matchers.AppenderUnique)

_numeric_quoted = S('#') + ((S("'") + ppc.number + S("'")) | (S('"') + ppc.number + S('"')))
numeric_quoted = _numeric_quoted.set_parse_action(matchers.NumericQuoted)

numeric_key = integer.copy().set_parse_action(matchers.Numeric)
numeric_slot = ppc.number.copy().set_parse_action(matchers.Numeric)

# Extended numeric literals: scientific (1e10), underscore (1_000), hex (0x1F), octal (0o17), binary (0b1010)
_numeric_extended_re = pp.Regex(
    r'[-]?0[xX][0-9a-fA-F]+'        # hex
    r'|[-]?0[oO][0-7]+'             # octal
    r'|[-]?0[bB][01]+'              # binary
    r'|[-]?[0-9][0-9_]*[eE][+-]?[0-9]+'  # scientific notation
    r'|[-]?[0-9]+(?:_[0-9]+)+'      # underscore separators
)
numeric_extended = _numeric_extended_re.copy().set_parse_action(matchers.NumericExtended)

# Exclude whitespace so "first )" parses as key "first", not "first "
word = (pp.Optional(backslash) + pp.CharsNotIn(reserved + ' \t\n\r')).set_parse_action(matchers.Word)
non_integer = pp.Regex(f'[-]?[0-9]+[^0-9{breserved}]+').set_parse_action(matchers.Word)
nameop = name.copy().set_parse_action(matchers.Word)

string = quoted.copy().set_parse_action(matchers.String)
bytes_literal = (S(L('b')) + quoted).set_parse_action(matchers.Bytes)
wildcard = pp.Literal('*').set_parse_action(matchers.Wildcard)
wildcard_first = pp.Literal('*?').set_parse_action(matchers.WildcardFirst)
_regex = slash + pp.Regex(r'(\\/|[^/])+') + slash
regex = _regex.copy().set_parse_action(matchers.Regex)
regex_first = (_regex + pp.Suppress(pp.Literal('?'))).set_parse_action(matchers.RegexFirst)
_escaped_dollar = pp.Regex(r'\\\$\${0,2}(\([^)]*\)|[0-9]*)').set_parse_action(
    lambda t: matchers.Word(t[0][1:]))
# $$(path) runs to the ) that closes it, so the path may hold parentheses
# of its own, $$((a[*];)) or $$(a.(b,c)); a ) inside quotes does not count.
_reference = (S(pp.Regex(r'\$\$(?=\()'))
              + pp.original_text_for(pp.nested_expr('(', ')')).add_condition(lambda t: t[0] != '()')
              ).set_parse_action(lambda t: matchers.Reference(t[0][1:-1]))
def _paren_subst_action(t):
    """
    Parse $(content) where content is name_or_index[|transform1|transform2...].
    """
    content = t[0][2:-1]  # strip $( and )
    parts = content.split('|')
    name = parts[0]
    xforms = tuple(base.Transform(p) for p in parts[1:]) if len(parts) > 1 else ()
    try:
        return matchers.Subst(int(name), transforms=xforms)
    except ValueError:
        return matchers.Subst(name, transforms=xforms)

_paren_subst = pp.Regex(r'\$\([^)]+\)').set_parse_action(_paren_subst_action)
_raw_subst = pp.Regex(r'\$[0-9]+').set_parse_action(
    lambda t: matchers.Subst(int(t[0][1:])))
# $str, a $ before a word: a plain string, as ^str is. Not $0, $(name) or $$(path).
_dollar_word = pp.Regex(r'\$(?![0-9($])[^' + re.escape(reserved) + r' \t\n\r]+').set_parse_action(matchers.Word)
var = _escaped_dollar | _reference | _paren_subst | _raw_subst | _dollar_word
# In an argument or value position a sigil token may carry a dotted path,
# |min:$amount.value or =^lots.taken: one string, for a resolver to read
# whole. In a segment position the . separates segments, so not there.
_arg_sigil = pp.Regex(r'(?:\$(?![0-9($])|\^)[^' + re.escape(reserved.replace('.', '')) + r' \t\n\r]+').set_parse_action(matchers.Word)

# ---------------------------------------------------------------------------
# Lexed tokens: what dotted cannot read in a slot, a slice bound, a filter
# value or a guard value is offered to the resolver's lex() (see
# base.Resolver.lex), last, once dotted's own readings have failed.
# ---------------------------------------------------------------------------

# The resolver a path is being parsed for, when it lexes, and its answers
# so far, by (text, position): reading an answer resets pyparsing's
# packrat cache, so the parse may come back to a token it has lexed.
LEXER = None
_ANSWERS = {}

_WORD_CHARS = frozenset(pp.alphanums + '_')


@contextlib.contextmanager
def lexing(lexer):
    """
    Parse with lexer offered what dotted cannot read (see Lexed); None
    for no lexer. Not reentrant: api._parse holds its lock around it.
    """
    global LEXER
    LEXER = lexer
    _ANSWERS.clear()
    try:
        yield
    finally:
        LEXER = None
        _ANSWERS.clear()


def _token_end(s, loc, stops):
    """
    Where the token at loc ends: at the first of stops, whitespace or a
    closing bracket outside the brackets and quotes it opens itself.
    """
    depth = 0
    quote = None
    i = loc
    while i < len(s):
        c = s[i]
        if quote:
            if c == '\\':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
        elif c in '([{':
            depth += 1
        elif depth:
            if c in ')]}':
                depth -= 1
        elif c in stops or c in ')]}' or c.isspace():
            return i
        i += 1
    return i


class Lexed(pp.Token):
    """
    A token dotted has no reading for at position, offered to LEXER. It
    runs to the first of stops (see _token_end) and is offered only when
    one of follow, or with end the end of the path, comes after it. The
    text the lexer answers is read whole with reading, without LEXER.
    """
    def __init__(self, position, reading, stops, follow=None, end=False):
        super().__init__()
        self.position = position
        self.reading = reading
        self.stops = stops
        self.follow = stops if follow is None else follow
        self.end = end
        self.mayReturnEmpty = False
        self.mayIndexError = False
        self.errmsg = f'Expected a token in {position}'

    def _generateDefaultName(self):
        return f'Lexed({self.position})'

    def parseImpl(self, instring, loc, do_actions=True):
        global LEXER
        lexer = LEXER
        if lexer is None:
            raise pp.ParseException(instring, loc, self.errmsg, self)
        stop = _token_end(instring, loc, self.stops)
        if stop == loc:
            raise pp.ParseException(instring, loc, self.errmsg, self)
        if stop == len(instring) and not self.end:
            raise pp.ParseException(instring, loc, self.errmsg, self)
        if stop < len(instring) and instring[stop] not in self.follow:
            raise pp.ParseException(instring, loc, self.errmsg, self)
        text = instring[loc:stop]
        key = (text, self.position)
        if key not in _ANSWERS:
            try:
                _ANSWERS[key] = lexer.lex(text, self.position)
            except Exception as e:
                raise pp.ParseFatalException(instring, loc, f'{text!r} in {self.position}: {e}') from e
        lexed = _ANSWERS[key]
        if lexed is None:
            raise pp.ParseException(instring, loc, self.errmsg, self)
        LEXER = None
        try:
            tokens = self.reading.parse_string(lexed, parse_all=True)
        except pp.ParseBaseException:
            raise pp.ParseFatalException(
                instring, loc, f'{text!r} lexed as {lexed!r}, not one token in {self.position}') from None
        finally:
            LEXER = lexer
        return stop, list(tokens)


# After a token dotted reads, while a lexer is in force: no word character
# follows, so null in =nullable or 3 in [:3x] is not read as a prefix of
# the token, which is the lexer's to read whole.
_ends = pp.Empty().leave_whitespace().add_condition(
    lambda s, loc, t: LEXER is None or s[loc:loc + 1] not in _WORD_CHARS, call_during_try=True)

# A slice bound: an integer, + (the length), or a $$(path) reference or
# $(name)/$N substitution standing for one. A start bound is lexed only
# before its :, so [k] is not offered as one.
_slice_read = integer | plus | _reference | _paren_subst | _raw_subst
_slice_bound = (_slice_read + _ends) | Lexed('slices', _slice_read, ':]')
_slice_start = (_slice_read + _ends) | Lexed('slices', _slice_read, ':]', follow=':')
slice = pp.Optional(_slice_start) + ':' + pp.Optional(_slice_bound) \
         + pp.Optional(':') + pp.Optional(_slice_bound)

_common_pats = wildcard_first | wildcard | var | regex_first | regex
_commons = bytes_literal | string | _common_pats | numeric_quoted


# ---------------------------------------------------------------------------
# Container grammar — pattern containers (for value/filter context)
# ---------------------------------------------------------------------------

# Comma with optional surrounding whitespace for container interiors
_ccomma = pp.Optional(pp.White()).suppress() + comma + pp.Optional(pp.White()).suppress()

# Forward for recursive container values
container_value = pp.Forward()

# Scalar atoms usable inside containers (same as value atoms minus containers).
# Uses numeric_slot (ppc.number) so floats parse in value position.
_val_atoms = bytes_literal | string | wildcard | var | regex | numeric_quoted | none | true | false | numeric_extended | numeric_slot

# Glob inside containers: ... with optional /regex/ then optional count
# Regex pattern for glob (unsuppressed slashes handled inline)
_glob_regex = (slash + pp.Regex(r'(\\/|[^/])+') + slash).set_parse_action(matchers.Regex)

# Count forms: min:max, min:, max (single number)
_glob_count_full = integer + S(':') + integer       # min:max
_glob_count_min = integer + S(':')                   # min:
_glob_count_max = integer.copy()                     # max

# Glob: ...  with optional /regex/ then optional count
_ellipsis = S(L('.') + L('.') + L('.'))

def _make_glob_action(has_regex, has_count):
    """
    Build a parse action for a glob form.
    """
    def action(t):
        t = list(t)
        i = 0
        pattern = None
        min_count = 0
        max_count = None
        if has_regex:
            pattern = t[i]
            i += 1
        if has_count == 'full':
            min_count = t[i]
            max_count = t[i + 1]
        elif has_count == 'min':
            min_count = t[i]
        elif has_count == 'max':
            max_count = t[i]
        return ct.Glob(pattern=pattern, min_count=min_count, max_count=max_count)
    return action

# Glob variants — most specific first
_glob_re_full = (_ellipsis + _glob_regex + _glob_count_full).set_parse_action(_make_glob_action(True, 'full'))
_glob_re_min = (_ellipsis + _glob_regex + _glob_count_min).set_parse_action(_make_glob_action(True, 'min'))
_glob_re_max = (_ellipsis + _glob_regex + _glob_count_max).set_parse_action(_make_glob_action(True, 'max'))
_glob_re_bare = (_ellipsis + _glob_regex).set_parse_action(_make_glob_action(True, None))
_glob_bare_full = (_ellipsis + _glob_count_full).set_parse_action(_make_glob_action(False, 'full'))
_glob_bare_min = (_ellipsis + _glob_count_min).set_parse_action(_make_glob_action(False, 'min'))
_glob_bare_max = (_ellipsis + _glob_count_max).set_parse_action(_make_glob_action(False, 'max'))
_glob_bare = _ellipsis.copy().set_parse_action(lambda: [ct.Glob()])

container_glob = (
    _glob_re_full | _glob_re_min | _glob_re_max | _glob_re_bare |
    _glob_bare_full | _glob_bare_min | _glob_bare_max | _glob_bare
)

# String/bytes glob: "prefix"..."suffix", b"hello"..."world", etc.
# Must have at least one string/bytes AND one glob (otherwise it's a plain string or bare glob).
# If any part is b"...", produces BytesGlob (naked strings encoded to bytes);
# otherwise produces StringGlob.
_strblob_atom = bytes_literal.copy() | quoted.copy() | var.copy()
_strblob_part = _strblob_atom | container_glob
_strblob_a = _strblob_atom + container_glob + ZM(_strblob_part)  # starts with str/bytes
_strblob_b = container_glob + _strblob_atom + ZM(_strblob_part)  # starts with glob
def _make_strblob(t):
    """
    Produce StringGlob or BytesGlob depending on whether any b"..." part is present.
    Subst/Reference parts are kept as-is for later resolution.
    """
    has_bytes = any(isinstance(p, matchers.Bytes) for p in t)
    if has_bytes:
        def to_bytes(p):
            if isinstance(p, matchers.Bytes):
                return p.value
            if isinstance(p, str):
                return p.encode()
            return p
        return ct.BytesGlob(*tuple(to_bytes(p) for p in t))
    return ct.StringGlob(*t)
string_glob = (_strblob_a | _strblob_b).set_parse_action(lambda t: [_make_strblob(t)])

# Container element: can be a nested container, glob, string/bytes glob, or scalar/pattern atom
_container_elem = container_value | string_glob | container_glob | _val_atoms

# List: [elem, elem, ...]
_container_list_inner = _container_elem + ZM(_ccomma + _container_elem)
_container_list_body = S('[') + Opt(_container_list_inner) + S(']')

def _make_container_list(prefix):
    """
    Parse action for container list with given type prefix.
    """
    def action(t):
        return ct.ContainerList(*t, type_prefix=prefix)
    return action

# Unprefixed: [...]
container_list = _container_list_body.copy().set_parse_action(_make_container_list(None))

# Prefixed: l[...], t[...]
container_list_l = (S(L('l')) + _container_list_body.copy()).set_parse_action(_make_container_list('l'))
container_list_t = (S(L('t')) + _container_list_body.copy()).set_parse_action(_make_container_list('t'))

# Dict entry: key_pattern : val_pattern
_dict_key = _container_elem
_dict_val = _container_elem
_dict_entry = (_dict_key + S(':') + pp.Optional(pp.White()).suppress() + _dict_val).set_parse_action(lambda t: [(t[0], t[1])])

# Dict glob entry: glob : val_pattern  (glob on key side)
_dict_glob_entry = (container_glob + S(':') + pp.Optional(pp.White()).suppress() + _container_elem).set_parse_action(
    lambda t: [ct.DictGlobEntry(t[0], t[1])])
# Dict glob entry bare: glob alone (no value constraint)
_dict_glob_entry_bare = container_glob.copy().set_parse_action(
    lambda t: [ct.DictGlobEntry(t[0], None)])

_dict_any_entry = _dict_glob_entry | _dict_glob_entry_bare | _dict_entry
_container_dict_inner = _dict_any_entry + ZM(_ccomma + _dict_any_entry)
_container_dict_body = S('{') + _container_dict_inner + S('}')

def _make_container_dict(prefix):
    """
    Parse action for container dict with given type prefix.
    """
    def action(t):
        return ct.ContainerDict(*t, type_prefix=prefix)
    return action

# Unprefixed: {...: ...}
container_dict = _container_dict_body.copy().set_parse_action(_make_container_dict(None))

# Prefixed: d{...}
container_dict_d = (S(L('d')) + _container_dict_body.copy()).set_parse_action(_make_container_dict('d'))

# Set: {elem, elem, ...} — no colons (disambiguated from dict by trying dict first)
_container_set_inner = _container_elem + ZM(_ccomma + _container_elem)
_container_set_body = S('{') + _container_set_inner + S('}')

def _make_container_set(prefix):
    """
    Parse action for container set with given type prefix.
    """
    def action(t):
        return ct.ContainerSet(*t, type_prefix=prefix)
    return action

# Unprefixed: {elem, ...}
container_set = _container_set_body.copy().set_parse_action(_make_container_set(None))

# Prefixed: s{...}, fs{...}
container_set_s = (S(L('s')) + _container_set_body.copy()).set_parse_action(_make_container_set('s'))
container_set_fs = (S(L('fs')) + _container_set_body.copy()).set_parse_action(_make_container_set('fs'))

# Empty containers
_empty_list_body = S('[') + S(']')
container_empty_list = _empty_list_body.copy().set_parse_action(lambda: [ct.ContainerList(type_prefix=None)])
container_empty_list_l = (S(L('l')) + _empty_list_body.copy()).set_parse_action(lambda: [ct.ContainerList(type_prefix='l')])
container_empty_list_t = (S(L('t')) + _empty_list_body.copy()).set_parse_action(lambda: [ct.ContainerList(type_prefix='t')])

_empty_brace_body = S('{') + S('}')
container_empty_dict = _empty_brace_body.copy().set_parse_action(lambda: [ct.ContainerDict(type_prefix=None)])
container_empty_dict_d = (S(L('d')) + _empty_brace_body.copy()).set_parse_action(lambda: [ct.ContainerDict(type_prefix='d')])
container_empty_set_s = (S(L('s')) + _empty_brace_body.copy()).set_parse_action(lambda: [ct.ContainerSet(type_prefix='s')])
container_empty_set_fs = (S(L('fs')) + _empty_brace_body.copy()).set_parse_action(lambda: [ct.ContainerSet(type_prefix='fs')])

# Resolve forward: try prefixed before unprefixed, empties before non-empty, dict before set
container_value <<= (
    container_list_l | container_list_t |
    container_empty_list_l | container_empty_list_t | container_empty_list |
    container_list |
    container_dict_d |
    container_empty_dict_d | container_empty_dict |
    container_empty_set_fs | container_empty_set_s |
    container_set_fs | container_set_s |
    container_dict | container_set
)


# ---------------------------------------------------------------------------
# Concrete containers (for transform args — no patterns/globs allowed)
# ---------------------------------------------------------------------------

_concrete_atoms = quoted.copy().set_parse_action(lambda t: [t[0]]) | ppc.number | none | true | false

# Forward for recursive concrete values
concrete_value = pp.Forward()
_concrete_elem = concrete_value | _concrete_atoms

# Concrete list: [elem, ...]
_concrete_list_inner = _concrete_elem + ZM(_ccomma + _concrete_elem)
_concrete_list_body = S('[') + Opt(_concrete_list_inner) + S(']')

concrete_list = _concrete_list_body.copy().set_parse_action(lambda t: [list(t)])
concrete_list_t = (S(L('t')) + _concrete_list_body.copy()).set_parse_action(lambda t: [tuple(t)])
concrete_list_l = (S(L('l')) + _concrete_list_body.copy()).set_parse_action(lambda t: [list(t)])

# Concrete dict entry: scalar : scalar
_concrete_dict_entry = (_concrete_elem + S(':') + pp.Optional(pp.White()).suppress() + _concrete_elem).set_parse_action(lambda t: [(t[0], t[1])])
_concrete_dict_inner = _concrete_dict_entry + ZM(_ccomma + _concrete_dict_entry)
_concrete_dict_body = S('{') + _concrete_dict_inner + S('}')

concrete_dict = _concrete_dict_body.copy().set_parse_action(lambda t: [dict(t.as_list())])
concrete_dict_d = (S(L('d')) + _concrete_dict_body.copy()).set_parse_action(lambda t: [dict(t.as_list())])

# Concrete set: {elem, ...}
_concrete_set_inner = _concrete_elem + ZM(_ccomma + _concrete_elem)
_concrete_set_body = S('{') + _concrete_set_inner + S('}')

concrete_set = _concrete_set_body.copy().set_parse_action(lambda t: [set(t)])
concrete_set_s = (S(L('s')) + _concrete_set_body.copy()).set_parse_action(lambda t: [set(t)])
concrete_set_fs = (S(L('fs')) + _concrete_set_body.copy()).set_parse_action(lambda t: [frozenset(t)])

# Concrete empties
_concrete_empty_list = (S('[') + S(']')).set_parse_action(lambda: [[]])
_concrete_empty_list_l = (S(L('l')) + S('[') + S(']')).set_parse_action(lambda: [[]])
_concrete_empty_list_t = (S(L('t')) + S('[') + S(']')).set_parse_action(lambda: [()])
_concrete_empty_dict = (S('{') + S('}') ).set_parse_action(lambda: [{}])
_concrete_empty_dict_d = (S(L('d')) + S('{') + S('}')).set_parse_action(lambda: [{}])
_concrete_empty_set_s = (S(L('s')) + S('{') + S('}')).set_parse_action(lambda: [set()])
_concrete_empty_set_fs = (S(L('fs')) + S('{') + S('}')).set_parse_action(lambda: [frozenset()])

# Resolve forward: try prefixed before unprefixed, empties before non-empty, dict before set
concrete_value <<= (
    concrete_list_l | concrete_list_t |
    _concrete_empty_list_l | _concrete_empty_list_t | _concrete_empty_list |
    concrete_list |
    concrete_dict_d |
    _concrete_empty_dict_d | _concrete_empty_dict |
    _concrete_empty_set_fs | _concrete_empty_set_s |
    concrete_set_fs | concrete_set_s |
    concrete_dict | concrete_set
)


# ---------------------------------------------------------------------------
# value and key (updated to include containers)
# ---------------------------------------------------------------------------

# value is a Forward to allow value_group to reference it recursively
value = pp.Forward()
_value_group_inner = value + OM(_ccomma + value)
value_group = (S('(') + _value_group_inner + S(')')).set_parse_action(lambda t: [ct.ValueGroup(*t)])
value <<= value_group | container_value | string_glob | bytes_literal | string | wildcard | _arg_sigil | var | regex | numeric_quoted | none | true | false | numeric_extended | numeric_slot
# A filter's or guard's value, else a token for the lexer: it runs to what
# may follow a value, a dot not among them, so =lot.x is one token.
_value_stops = '&,)]|?#;'
filter_value = (value + _ends) | Lexed('filters', value, _value_stops, end=True)
guard_value = (value + _ends) | Lexed('guards', value, _value_stops, end=True)
# ---------------------------------------------------------------------------
# Concat: part+part for key construction using Python's native + operator.
# Each part can carry per-part transforms: part|xform+part|xform
# ---------------------------------------------------------------------------

_key_atom = _commons | numeric_extended | non_integer | numeric_key | word
_slot_atom = _commons | numeric_extended | numeric_slot

# Transform: |name or |name:param — defined early so filters and guards can reference it
targ = concrete_value | _arg_sigil | var | quoted | ppc.number | none | true | false | pp.CharsNotIn('|:')
param = (colon + targ) | colon.copy().set_parse_action(lambda: [None])
transform = (transform_name.copy() + ZM(param)).set_parse_action(lambda s, loc, t: base.Transform(*t))
transforms = ZM(pipe + transform)

def _concat_part_action(t):
    """
    Build a ConcatPart from an atom with optional trailing transforms.
    """
    t = list(t)
    op = t[0]
    xforms = tuple(x for x in t[1:] if isinstance(x, base.Transform))
    return matchers.ConcatPart(op, xforms)

def _concat_action(t):
    """
    Build a Concat from two or more ConcatParts.
    If all parts are Const with no transforms, collapse via + at parse time.
    """
    parts = list(t)
    all_const = all(isinstance(p.op, matchers.Const) and not p.transforms
                    for p in parts)
    if all_const:
        import functools, operator
        vals = [p.op.value for p in parts]
        result = functools.reduce(operator.add, vals)
        if isinstance(result, int):
            return matchers.Numeric(result)
        if isinstance(result, float):
            return matchers.Numeric(result)
        return matchers.Word(str(result))
    return matchers.Concat(*parts)

_key_concat_part = (_key_atom + ZM(pipe + transform)).set_parse_action(_concat_part_action)
_concat_key = (_key_concat_part + OM(S('+') + _key_concat_part)).set_parse_action(_concat_action)

# Slot concat allows word in addition to slot atoms (only valid inside concat, where + disambiguates)
_slot_concat_atom = _slot_atom | word
_slot_concat_part = (_slot_concat_atom + ZM(pipe + transform)).set_parse_action(_concat_part_action)
_slot_concat = (_slot_concat_part + OM(S('+') + _slot_concat_part)).set_parse_action(_concat_action)

_attr_atom = nameop | _common_pats
_attr_concat_part = (_attr_atom + ZM(pipe + transform)).set_parse_action(_concat_part_action)
_concat_attr = (_attr_concat_part + OM(S('+') + _attr_concat_part)).set_parse_action(_concat_action)

key = _concat_key | _key_atom

# filter_key: dotted paths (user.id), slot paths (tags[*], tags[0]), slice (name[:5], name[-5:]).
# Dot introduces a key part only; slot/slice directly after key. Try slice before slot so [:] parses as slice.
_filter_key_part = string | _common_pats | numeric_extended | non_integer | numeric_key | word
filter_key_slice = (lb + slice + rb).set_parse_action(lambda t: access.Slice(*t))
filter_key_slot = (lb + (_commons | numeric_extended | numeric_slot) + rb).set_parse_action(lambda t: access.Slot(t[0]))
# A packed group may lead a filter key, [(p[*];)|sum>2]; set below, once
# the group grammar exists.
_filter_key_group = pp.Forward()
_filter_key_segment = _filter_key_group | _filter_key_part | filter_key_slice | filter_key_slot
filter_key = pp.Group(
    _filter_key_segment + ZM(filter_key_slice | filter_key_slot | (dot + _filter_key_part))
).set_parse_action(flt.FilterKey)

# Single operator regex: longest-first via regex alternation.
_pred_op_re = pp.Regex('|'.join(predicates.PRED_OPS))

def _filter_single_action(t):
    """
    Dispatch filter to the correct class based on the operator string.
    """
    items = list(t)
    val = items[-1]
    op_str = items[-2]
    rest = items[:-2]
    cls = flt.FILTER_PRED_CLS[op_str]
    return cls(rest + [val])

_filter_single_all = (filter_key + ZM(pipe + transform) + _pred_op_re + filter_value).set_parse_action(_filter_single_action)

# Recursive filter expression with grouping
filter_expr = pp.Forward()

# Atom: single comparison or grouped expression
lparen = pp.Suppress('(')
rparen = pp.Suppress(')')
bang = pp.Suppress('!')
filter_group = (lparen + filter_expr + rparen).set_parse_action(flt.FilterGroup)
filter_atom = filter_group | _filter_single_all

# NOT: ! prefix binds tightest (higher precedence than &)
filter_not = (bang + filter_atom).set_parse_action(flt.FilterNot) | filter_atom

# AND: not-expressions joined by & (higher precedence than ,)
filter_and = (filter_not + OM(amp + filter_not)).set_parse_action(flt.FilterAnd) | filter_not

# OR: and-groups joined by , (lowest precedence)
filter_or = (filter_and + OM(comma + filter_and)).set_parse_action(flt.FilterOr) | filter_and

filter_expr <<= filter_or

# Optional ? suffix for first-match
filter_keyvalue_first = (filter_expr + S('?')).set_parse_action(flt.FilterKeyValueFirst)

filters = filter_keyvalue_first | filter_expr

# Path segment type restrictions: :type, :(t1, t2), :!type, :!(t1, t2)
_type_name = pp.one_of(list(utypes.TYPE_REGISTRY.keys()))
_type_tuple = S('(') + _type_name + ZM(_comma_ws + _type_name) + S(')')
_type_spec = _type_tuple | _type_name

_type_neg = (colon + S('!') + _type_spec).set_parse_action(
    lambda t: utypes.TypeSpec(*(utypes.TYPE_REGISTRY[n] for n in t), negate=True))
_type_pos = (colon + _type_spec).set_parse_action(
    lambda t: utypes.TypeSpec(*(utypes.TYPE_REGISTRY[n] for n in t)))
type_restriction = _type_neg | _type_pos

def _keycmd_action(t):
    """
    Parse action for key with optional type restriction.
    """
    tr, filters, _, _, args = _classify_tokens(list(t))
    result = access.Key(*args)
    if tr:
        result = tr.wrap(result)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    return result

# A key's type, filters, guard and transforms, a:dict&k=v|int=5, are the
# level-3 postfix (see _postfix), not part of the segment: !a&k=v filters
# what !a yields. The exception is a type restriction or filter the path
# continues past, a:dict.b or a&k=v.b: it tests a's value and the path
# goes on into it, so it stays on the segment. At the end of an
# expression the postfix takes it, which keeps !a:dict as (!a):dict.
_continues = pp.Forward()  # lookahead: an op_seq continuation follows (set below)
_seg_mid = ((type_restriction + ZM(amp + filters)) | OM(amp + filters)) + _continues
keycmd = (key + Opt(_seg_mid)).set_parse_action(_keycmd_action)

_slotguts = (_slot_concat | _commons | numeric_extended | numeric_slot) + ZM(amp + filters)

def _slotcmd_action(t):
    """
    Parse action for slot with optional type restriction.
    """
    tr, filters, _, nop, args = _classify_tokens(list(t))
    result = access.Slot(*args)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    if tr:
        result = tr.wrap(result)
    if nop:
        result = wrappers.NopWrap(result)
    return result

slotcmd =(lb + Opt(L('~')) + _slotguts + rb + Opt(_seg_mid)).set_parse_action(_slotcmd_action)

# @~ and ~@ both produce NopWrap (canonical form @~)
def _attr_action(t, nop=False):
    """
    Parse action for attr with optional type restriction.
    """
    tr, filters, _, _, args = _classify_tokens(list(t))
    result = access.Attr(*args)
    if tr:
        result = tr.wrap(result)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    if nop:
        result = wrappers.NopWrap(result)
    return result

_attr_nop = ((at + tilde) | (tilde + at)) + (_concat_attr | nameop | _common_pats) + Opt(_seg_mid)
_attr_nop = _attr_nop.set_parse_action(lambda t: _attr_action(t, nop=True))
_attr_plain = (at + (_concat_attr | nameop | _common_pats) + Opt(_seg_mid)).set_parse_action(lambda t: _attr_action(t))
attrcmd = _attr_nop | _attr_plain

slotspecial = (lb + (appender_unique | appender) + rb).set_parse_action(access.SlotSpecial)

slicecmd = (lb + Opt(L('~')) + Opt(slice) + rb).set_parse_action(
    lambda t: wrappers.NopWrap(access.Slice(*t[1:])) if t and t[0] == '~' else access.Slice(*t))
slicefilter = (lb + filters + ZM(amp + filters) + rb).set_parse_action(access.SliceFilter)

# A slot dotted cannot read, [k] or [a.b], offered to the lexer: tried
# after every other bracket, so [a.b=1] stays a filter.
_slot_read = _slot_concat | _commons | numeric_extended | numeric_slot
lexedcmd = (lb + Opt(L('~')) + Lexed('slots', _slot_read, ']&') + ZM(amp + filters) + rb
            + Opt(_seg_mid)).set_parse_action(_slotcmd_action)

# Cut markers: ## = soft cut, # = hard cut (try ## first so ## isn't parsed as # + #)
softcut_marker = L('##')
cut_marker = softcut_marker | L('#')

# Pack marker: a ; just inside either paren, (a,b;) or (;a,b), packs the
# group's matches into one tuple (see groups.Pack).
pack_marker = L(';')

# Slot grouping: [(*&filter, +)] for disjunction inside slots; [(*&filter#, +)] for cut
def _slot_item_action(t):
    _, filters, _, _, args = _classify_tokens(list(t))
    result = access.Slot(*args)
    if filters:
        result = wrappers.FilterWrap(result, filters)
    return result

_slot_item_plain = _slotguts.copy().set_parse_action(_slot_item_action)
_slot_item_nop = (tilde + _slotguts.copy()).set_parse_action(lambda t: wrappers.NopWrap(_slot_item_action(t[1:])))
_slot_item = _slot_item_nop | _slot_item_plain | (appender_unique | appender).copy().set_parse_action(access.SlotSpecial)
_slot_group_term = pp.Group(_slot_item + Opt(cut_marker))
_slot_group_inner = _slot_group_term + ZM(_comma_ws + _slot_group_term)
slotgroup = (lb + lparen + _slot_group_inner + rparen + rb).set_parse_action(slot_to_opgroup)
slotgroup_first = (lb + lparen + _slot_group_inner + rparen + S('?') + rb).set_parse_action(slot_to_opgroup_first)

# Unified inner expression: single precedence tower for !, &, , operators
# Used both inside parens (inner_grouped) and at top levgroups.
# Atoms are op_seqs (which subsume bare keys as single-item sequences).
# Precedence: ! (tightest) > & > , (loosest)
inner_expr = pp.Forward()
def _grouped_with_type_restriction(parsed_result, first=False):
    """
    Parse action for grouped expressions with optional type restriction.
    Last token may be a TypeSpec; if so, wrap the OpGroup in TypeRestriction.
    A ; token at either end packs the group (see groups.Pack); the type
    restriction then guards the tuple.
    """
    items = list(parsed_result)
    packed = any(isinstance(i, str) and i == ';' for i in items)
    items = [i for i in items if not (isinstance(i, str) and i == ';')]
    tr = None
    if items and isinstance(items[-1], utypes.TypeSpec):
        tr = items.pop()
    if first:
        grp = inner_to_opgroup_first(items)
    else:
        grp = inner_to_opgroup(items)
    if packed:
        grp = groups.Pack(grp)
    if tr:
        grp = tr.wrap(grp)
    return grp

_packable = lambda expr: Opt(pack_marker) + expr + Opt(pack_marker)


def _filter_key_pack_action(s, loc, t):
    """
    Parse action for a group leading a filter key: it must be packed,
    the tuple being the value compared; an unpacked group has no single
    value there, so it fails to parse as it always did.
    """
    grp = _grouped_with_type_restriction(t)
    if not isinstance(grp, groups.Pack):
        raise pp.ParseException(s, loc, 'a group in a filter key must be packed, (a,b;)')
    return grp

_filter_key_group <<= (lparen + _packable(inner_expr) + rparen).set_parse_action(_filter_key_pack_action)

inner_grouped = (lparen + _packable(inner_expr) + rparen).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
inner_grouped_first = (lparen + _packable(inner_expr) + rparen + S('?')).set_parse_action(
    lambda t: _grouped_with_type_restriction(t, first=True))
# Explicit-op grouped: used mid-path where bare-key inference is not allowed.
# Uses a separate precedence tower (_explicit_inner_expr) that only allows
# op_seqs built from _op_seq_cont (no bare keys even in first position).
# The FollowedBy is a fast-fail optimization — if the first char after ( can't
# start an explicit branch, don't even try the tower.
_explicit_op_start = pp.FollowedBy(pp.Char('.@[!~(;'))
_explicit_inner_expr = pp.Forward()
_inner_grouped_explicit = (lparen + _explicit_op_start + _packable(_explicit_inner_expr) + rparen).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
_inner_grouped_explicit_first = (lparen + _explicit_op_start + _packable(_explicit_inner_expr) + rparen + S('?')).set_parse_action(
    lambda t: _grouped_with_type_restriction(t, first=True))
# Bare-key grouped: first branch does NOT start with an access op.
# Used with prefix shorthand (.(a,b), @(a,b)) where the prefix distributes.
# Allows ! and ~ (modifiers) but rejects . @ [ ( (access ops / nested groups).
_bare_key_start = ~pp.FollowedBy(pp.Char('.@[('))
_inner_grouped_bare = (lparen + _bare_key_start + _packable(inner_expr) + rparen).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
_inner_grouped_bare_first = (lparen + _bare_key_start + _packable(inner_expr) + rparen + S('?')).set_parse_action(
    lambda t: _grouped_with_type_restriction(t, first=True))

# Recursive operator: ** (recursive wildcard) and *pattern (recursive chain-following)
# Depth slice: :start:stop:step — uses sentinel for missing values to preserve position
_rec_none = pp.Empty().set_parse_action(lambda: [None])
_rec_depth_start = colon + (integer | _rec_none)
_rec_depth_stop = colon + (integer | _rec_none)
_rec_depth_step = colon + (integer | _rec_none)
_rec_depth = _rec_depth_start + Opt(_rec_depth_stop + Opt(_rec_depth_step))

def _make_recursive(t, first=False):
    t = list(t)
    inner = t[0]
    rest = t[1:]
    depth_start = depth_stop = depth_step = None
    filt = []
    depth_vals = []
    type_spec = None
    for item in rest:
        if isinstance(item, flt.FilterOp):
            filt.append(item)
        elif isinstance(item, utypes.TypeSpec):
            type_spec = item
        else:
            depth_vals.append(item)
    if len(depth_vals) >= 1:
        depth_start = depth_vals[0]
    if len(depth_vals) >= 2:
        depth_stop = depth_vals[1]
    if len(depth_vals) >= 3:
        depth_step = depth_vals[2]
    cls = recursive.RecursiveFirst if first else recursive.Recursive
    # *(expr) form: inner is an OpGroup or TypeRestriction wrapping one
    accessors = None
    if isinstance(inner, (groups.OpGroup, wrappers.TypeRestriction)):
        accessors = _extract_accessors(inner)
        inner = matchers.Wildcard()
    # Type restriction on the recursive operator (e.g. **:!(str, bytes))
    # Distribute to each accessor branch via TypeRestriction wrapper
    if type_spec is not None:
        if accessors is not None:
            accessors = _distribute_type_restriction(accessors, type_spec)
        else:
            accessors = ((type_spec.wrap(access.Key(inner)),),)
    r = cls(inner, accessors=accessors,
            depth_start=depth_start, depth_stop=depth_stop, depth_step=depth_step)
    if filt:
        r.filters = tuple(filt)
    return r


def _extract_accessors(opgroup):
    """
    Extract accessor branches from an OpGroup for *(expr) syntax.
    Each branch should be a single AccessOp (Key, Slot, Attr).
    Nested OpGroups are flattened (e.g. *((*#, [*]), @*) → *(*#, [*], @*)).
    TypeRestriction wrapping an OpGroup distributes the restriction to each branch.
    Returns a branches tuple preserving cut/softcut sentinels.
    """
    # TypeRestriction wrapping an OpGroup: distribute to each accessor
    if isinstance(opgroup, wrappers.TypeRestriction) and isinstance(opgroup.inner, groups.OpGroup):
        spec = utypes.TypeSpec(*opgroup.types, negate=opgroup.negate)
        return _distribute_type_restriction(_extract_accessors(opgroup.inner), spec)
    result = []
    for item in opgroup.branches:
        if item is utypes.BRANCH_CUT or item is utypes.BRANCH_SOFTCUT:
            result.append(item)
            continue
        branch = item
        if len(branch) == 1 and isinstance(branch[0], groups.OpGroup):
            # Flatten nested group: inline its branches
            result.extend(_extract_accessors(branch[0]))
        elif len(branch) == 1 and isinstance(branch[0], (access.AccessOp, wrappers.TypeRestriction)):
            result.append(branch)
        else:
            raise ValueError(
                f"*(expr) branches must be single access ops (Key, Slot, Attr), "
                f"got {branch!r}"
            )
    return tuple(result)


def _distribute_type_restriction(branches, type_spec):
    """
    Wrap each accessor in branches with a TypeRestriction from type_spec.
    Preserves cut/softcut sentinels.
    """
    result = []
    for item in branches:
        if item is utypes.BRANCH_CUT or item is utypes.BRANCH_SOFTCUT:
            result.append(item)
        else:
            result.append((type_spec.wrap(item[0]),))
    return tuple(result)

# ** and *pattern base expressions (no parse actions — shared by guarded/first/plain variants)
_rec_dstar_prefix = S(L('*') + L('*'))
# *(expr) grouped form: inner is a group expression containing accessor ops
_rec_group_inner = (lparen + inner_expr + rparen + Opt(type_restriction)).set_parse_action(
    lambda t: _grouped_with_type_restriction(t))
_rec_inner = _rec_group_inner | string | regex_first | regex | numeric_quoted | numeric_extended | non_integer | numeric_key | word
_rec_pat_prefix = S(L('*'))

def _dstar_body():
    return _rec_dstar_prefix + Opt(type_restriction) + Opt(_rec_depth) + ZM(amp + filters)

def _pat_body():
    return _rec_pat_prefix + _rec_inner + Opt(type_restriction) + Opt(_rec_depth) + ZM(amp + filters)

# First-match: **?, *name?
rec_dstar_first = (_dstar_body() + S('?')).set_parse_action(
    lambda t: _make_recursive([matchers.Wildcard()] + list(t), first=True))
rec_pat_first = (_pat_body() + S('?')).set_parse_action(
    lambda t: _make_recursive(t, first=True))

# Plain: **, *name
rec_dstar = _dstar_body().set_parse_action(
    lambda t: _make_recursive([matchers.Wildcard()] + list(t)))
rec_pat = _pat_body().set_parse_action(
    lambda t: _make_recursive(t))

recursive_op = (
    rec_dstar_first | rec_dstar | rec_pat_first | rec_pat)

empty = pp.Empty().set_parse_action(access.Empty)

# Operation grouping: (.b,[]) for grouping operation sequences
# An op_seq is a sequence of operations like .key, [slot], @attr
# .~key and ~.key both produce NopWrap(key); .key produces key
_dot_keycmd_nop = (((dot + tilde) | (tilde + dot)) + keycmd).set_parse_action(
    lambda t: wrappers.NopWrap(t[-1]))
_dot_keycmd = (dot + keycmd).set_parse_action(lambda t: t[0])
# op_seq_item uses _nop_wrap (defined later); Forward for circular ref
op_seq_item = pp.Forward()     # first item: allows bare (a,b)
_op_seq_cont = pp.Forward()    # continuation: requires explicit ops
# op_seq: access ops only (level 1). Guards, filters and transforms are
# the level-3 postfix applied to a whole op_seq, group or negation; see
# _postfix.
op_seq = pp.Group(op_seq_item + ZM(_op_seq_cont))

# Continuation items (absorbed from former multi grammar):
# .~ and ~. with grouped expressions — prefix shorthand requires bare keys inside
_dot_nop_grouped = ((dot + tilde) | (tilde + dot)) + (_inner_grouped_bare_first | _inner_grouped_bare)
_dot_nop_grouped = _dot_nop_grouped.set_parse_action(lambda t: wrappers.NopWrap(t[-1]))
_dot_plain_grouped = (dot + (_inner_grouped_bare_first | _inner_grouped_bare)).set_parse_action(lambda t: t[0])
# .recursive
_dot_recursive = (dot + recursive_op).set_parse_action(lambda t: t[0])
# @(group) — attribute group access: @(a,b) == (@a,@b), prefix requires bare keys
_at_plain_grouped = (at + (_inner_grouped_bare_first | _inner_grouped_bare)).set_parse_action(
    lambda t: as_attrs_opgroup(t[0])
)
_at_nop_grouped = ((at + tilde) | (tilde + at)) + (_inner_grouped_bare_first | _inner_grouped_bare)
_at_nop_grouped = _at_nop_grouped.set_parse_action(
    lambda t: wrappers.NopWrap(as_attrs_opgroup(t[-1]))
)

# NOP (~): match but don't update
_nop_inner = inner_grouped_first | inner_grouped | recursive_op | keycmd | attrcmd | slotgroup_first | slotgroup | slotcmd | slotspecial | slicefilter | slicecmd | lexedcmd
_nop_wrap = (tilde + _nop_inner).set_parse_action(lambda t: wrappers.NopWrap(t[1]))

# Explicit NOP (~): like _nop_wrap but inner must start with an access op (./@/[]).
# Bare keys (keycmd) are excluded — ~key is not allowed inside explicit groups.
_explicit_nop_inner = (
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    recursive_op |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    slotcmd |
    slotspecial | slicefilter | slicecmd | lexedcmd
)
_explicit_nop_wrap = (tilde + _explicit_nop_inner).set_parse_action(lambda t: wrappers.NopWrap(t[1]))

# Continuation items for the ZM middle of op_seq.
_op_seq_cont_items = (
    _nop_wrap |
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    recursive_op |
    keycmd |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    slotcmd |
    slotspecial | slicefilter | slicecmd | lexedcmd
)
_op_seq_cont << _op_seq_cont_items
_continues <<= pp.FollowedBy(_op_seq_cont)

# First-item items — the same set; the first position also allows a bare
# key, which op_seq_item adds below.
_op_seq_first_items = (
    _nop_wrap |
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    recursive_op |
    keycmd |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    slotcmd |
    slotspecial | slicefilter | slicecmd | lexedcmd
)
# First item also allows bare grouped expressions — (a,b) with bare keys.
# This is valid at the start of a path (top-level inference).
op_seq_item << (inner_grouped_first | inner_grouped | _op_seq_first_items)

# ---------------------------------------------------------------------------
# Level 3: the postfix a path expression may end with — filters, then
# transforms, then a guard with transforms after it:
#     a&k=v   a|int   a|int=5   a|int=5|str   a=5
# It applies to the whole level-2 expression on its left: an op_seq (on
# its last op), a negation (on what it yields) or a parenthesized group
# (distributed to every branch, (a,b)=5 being (a=5,b=5)). Transforms
# before a guard are the guard's, matching only; after it, or with no
# guard, they transform the value.
# ---------------------------------------------------------------------------

_postfix = Opt(type_restriction) + ZM(amp + filters) + ZM(pipe + transform) + Opt(_pred_op_re + guard_value + ZM(pipe + transform))


def _split_postfix(tokens):
    """
    The postfix tokens as (type spec, filters, guard transforms, pred_op,
    guard value, value transforms); the type spec and pred_op are None
    when absent.
    """
    tr = next((t for t in tokens if isinstance(t, utypes.TypeSpec)), None)
    filters = [t for t in tokens if isinstance(t, flt.FilterOp)]
    rest = [t for t in tokens if not isinstance(t, (flt.FilterOp, utypes.TypeSpec))]
    for i, t in enumerate(rest):
        if isinstance(t, str) and t in predicates.PRED_OPS:
            return tr, filters, rest[:i], predicates.PRED_OPS[t], rest[i + 1], rest[i + 2:]
    return tr, filters, [], None, None, rest


def _decorate(op, tr, filters, gxf, pred_op, guard, vxf):
    """
    op with the postfix on it: the type guard, then filters, then the
    guard with its transforms, then the value transforms. A , & or ?
    group gets it on the last op of every branch, through ~ on the group;
    a negation gets it on the last op of its branch when the branch
    continues past the negated op, else on the negation itself, so (!a)=5
    is the non-a values equal to 5.
    """
    if isinstance(op, groups.OpGroupNot) and len(op.inner) > 1:
        return groups.OpGroupNot(op.inner[:-1] + (_decorate(op.inner[-1], tr, filters, gxf, pred_op, guard, vxf),))
    if isinstance(op, groups.OpGroup) and not isinstance(op, groups.OpGroupNot):
        branches = []
        for b in op.branches:
            if not isinstance(b, tuple) or not b:
                branches.append(b)  # cut markers and empty branches pass through
                continue
            branches.append(b[:-1] + (_decorate(b[-1], tr, filters, gxf, pred_op, guard, vxf),))
        return type(op)(*branches)
    if isinstance(op, wrappers.NopWrap) and isinstance(op.inner, groups.OpGroup):
        return wrappers.NopWrap(_decorate(op.inner, tr, filters, gxf, pred_op, guard, vxf))
    if tr is not None:
        op = tr.wrap(op)
    if filters:
        op = wrappers.FilterWrap(op, filters)
    if pred_op is not None:
        op = wrappers.ValueGuard(op, guard, pred_op=pred_op, transforms=gxf)
    if vxf:
        op = wrappers.TransformWrap(op, vxf)
    return op


def _apply_postfix(t):
    """
    Parse action for `expr + _postfix`: the expression's ops with the
    postfix, if any, on the op it binds to. The expression token is an
    op_seq (a ParseResults of ops) or a single op (a negation); the
    result keeps that shape, as one token.
    """
    expr, tokens = t[0], list(t[1:])
    if not tokens:
        return [expr]
    ops = list(expr) if isinstance(expr, (list, tuple, pp.ParseResults)) else [expr]
    ops[-1] = _decorate(ops[-1], *_split_postfix(tokens))
    return [pp.ParseResults(ops)]


def _with_postfix(expr):
    """
    expr followed by the level-3 postfix.
    """
    return (expr + _postfix).set_parse_action(_apply_postfix)


# Precedence tower (atoms are op_seqs): access ops, then !, then the
# postfix, then &, then ,
inner_atom = op_seq
inner_not = _with_postfix((bang + inner_atom).set_parse_action(inner_not_action) | inner_atom)
inner_and = (inner_not + OM(amp + inner_not)).set_parse_action(inner_and_action) | inner_not
inner_or_term = pp.Group(inner_and + Opt(cut_marker))
inner_or = (inner_or_term + ZM(_comma_ws + inner_or_term)).set_parse_action(inner_or_action) | inner_and
inner_expr <<= inner_or

# ---------------------------------------------------------------------------
# Explicit precedence tower: used inside mid-path groups where every leaf
# must start with an access op (Key/Attr/Slot).  Bare keys are rejected.
# This enforces the rule: "if no access op floats down from parent, you
# must specify one on every branch."
# ---------------------------------------------------------------------------
_explicit_op_seq_cont_items = (
    _explicit_nop_wrap |
    _inner_grouped_explicit_first | _inner_grouped_explicit |
    _dot_recursive |
    _dot_keycmd_nop | _dot_keycmd |
    _dot_nop_grouped | _dot_plain_grouped |
    _at_nop_grouped | _at_plain_grouped |
    attrcmd |
    slotgroup_first | slotgroup |
    slotcmd |
    slotspecial | slicefilter | slicecmd | lexedcmd
)
_explicit_op_seq = pp.Group(_explicit_op_seq_cont_items + ZM(_explicit_op_seq_cont_items))
_explicit_inner_atom = _explicit_op_seq
_explicit_inner_not = _with_postfix((bang + _explicit_inner_atom).set_parse_action(inner_not_action) | _explicit_inner_atom)
_explicit_inner_and = (_explicit_inner_not + OM(amp + _explicit_inner_not)).set_parse_action(inner_and_action) | _explicit_inner_not
_explicit_inner_or_term = pp.Group(_explicit_inner_and + Opt(cut_marker))
_explicit_inner_or = (_explicit_inner_or_term + ZM(_comma_ws + _explicit_inner_or_term)).set_parse_action(inner_or_action) | _explicit_inner_and
_explicit_inner_expr <<= _explicit_inner_or

# Top-level: flatten op_seq Groups into flat ops list
def _top_level_flatten(t):
    """
    Flatten top-level expression result for the ops list.
    Plain op_seqs (ParseResults from Group) are unwrapped into individual ops.
    OpGroups are kept as single ops.
    """
    out = []
    for item in t:
        if isinstance(item, (list, tuple, pp.ParseResults)) and not isinstance(item, groups.OpGroup):
            out.extend(item)
        else:
            out.append(item)
    return out

invert = Opt(L('-').set_parse_action(access.Invert))
# an empty path takes the postfix too: '|int' transforms the root
dotted = pp.Group((invert + (inner_expr | _with_postfix(empty))).set_parse_action(_top_level_flatten))

template = dotted('ops')


