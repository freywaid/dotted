"""
Direct tests for _match_from declarations and the generic matchable method.
"""
from dotted.matchers import (
    Const, Word, Numeric, String,
    Wildcard, WildcardFirst, Regex, RegexFirst,
    Special, Appender,
    Subst, Reference, Concat, ConcatPart,
)
from dotted.utypes import ANY, resolve_types


# -- ANY type --

def test_any_isinstance():
    assert isinstance(42, ANY)
    assert isinstance('hello', ANY)
    assert isinstance(None, ANY)
    assert isinstance(Const('x'), ANY)


# -- resolve_types --

def test_resolve_types_strings():
    import dotted.matchers as m
    ns = vars(m)
    resolved = resolve_types(ns, ('Const', 'Special'))
    assert resolved == (Const, Special)


def test_resolve_types_mixed():
    import dotted.matchers as m
    ns = vars(m)
    resolved = resolve_types(ns, ('Const', ANY, 'Regex'))
    assert resolved == (Const, ANY, Regex)


def test_resolve_types_no_strings():
    resolved = resolve_types({}, (Const, Special))
    assert resolved == (Const, Special)


# -- Const matchable --

def test_const_matches_const():
    assert Const('a').matchable(Const('b'))

def test_const_matches_word():
    assert Const('a').matchable(Word('b'))

def test_word_matches_const():
    assert Word('a').matchable(Const('b'))

def test_word_matches_numeric():
    assert Word('a').matchable(Numeric('1'))

def test_const_rejects_special():
    assert not Const('a').matchable(Special('+'))

def test_const_rejects_special_with_specials():
    assert not Const('a').matchable(Special('+'), specials=True)

def test_const_rejects_wildcard():
    assert not Const('a').matchable(Wildcard())


# -- Wildcard matchable --

def test_wildcard_matches_const():
    assert Wildcard().matchable(Const('a'))

def test_wildcard_rejects_special_without_specials():
    assert not Wildcard().matchable(Special('+'))

def test_wildcard_matches_special_with_specials():
    assert Wildcard().matchable(Special('+'), specials=True)

def test_wildcard_matches_wildcard_with_specials():
    assert Wildcard().matchable(Wildcard(), specials=True)

def test_wildcard_matches_regex_with_specials():
    assert Wildcard().matchable(Regex('.*'), specials=True)


# -- WildcardFirst matchable --

def test_wildcardirst_matches_const():
    assert WildcardFirst().matchable(Const('a'))

def test_wildcardirst_rejects_special_without_specials():
    assert not WildcardFirst().matchable(Special('+'))

def test_wildcardirst_matches_special_with_specials():
    assert WildcardFirst().matchable(Special('+'), specials=True)

def test_wildcardirst_matches_wildcardirst_with_specials():
    assert WildcardFirst().matchable(WildcardFirst(), specials=True)

def test_wildcardirst_matches_regexfirst_with_specials():
    assert WildcardFirst().matchable(RegexFirst('.*'), specials=True)

def test_wildcardirst_rejects_plain_wildcard_with_specials():
    assert not WildcardFirst().matchable(Wildcard(), specials=True)

def test_wildcardirst_rejects_plain_regex_with_specials():
    assert not WildcardFirst().matchable(Regex('.*'), specials=True)


# -- Regex matchable --

def test_regex_matches_const():
    assert Regex('.*').matchable(Const('a'))

def test_regex_rejects_special_without_specials():
    assert not Regex('.*').matchable(Special('+'))

def test_regex_matches_special_with_specials():
    assert Regex('.*').matchable(Special('+'), specials=True)

def test_regex_matches_regex_with_specials():
    assert Regex('.*').matchable(Regex('x'), specials=True)

def test_regex_matches_regexfirst_with_specials():
    """RegexFirst is a subclass of Regex, so Regex accepts it."""
    assert Regex('.*').matchable(RegexFirst('x'), specials=True)

def test_regex_rejects_wildcard_with_specials():
    assert not Regex('.*').matchable(Wildcard(), specials=True)


# -- RegexFirst matchable --

def test_regexfirst_matches_const():
    assert RegexFirst('.*').matchable(Const('a'))

def test_regexfirst_matches_special_with_specials():
    assert RegexFirst('.*').matchable(Special('+'), specials=True)

def test_regexfirst_matches_regexfirst_with_specials():
    assert RegexFirst('.*').matchable(RegexFirst('x'), specials=True)

def test_regexfirst_rejects_plain_regex_with_specials():
    """RegexFirst should NOT match plain Regex."""
    assert not RegexFirst('.*').matchable(Regex('x'), specials=True)

def test_regexfirst_rejects_wildcard_with_specials():
    assert not RegexFirst('.*').matchable(Wildcard(), specials=True)


# -- Special matchable --

def test_special_matches_special():
    assert Special('+').matchable(Special('-'))

def test_special_matches_appender():
    """Appender is a subclass of Special."""
    assert Special('+').matchable(Appender())

def test_special_ignores_specials_flag():
    """Special always matches Special, regardless of specials flag."""
    assert Special('+').matchable(Special('-'), specials=False)

def test_special_rejects_const():
    assert not Special('+').matchable(Const('a'))


# -- Appender matchable --

def test_appender_matches_appender():
    assert Appender().matchable(Appender())

def test_appender_rejects_special():
    """Appender is more specific — doesn't accept plain Special."""
    assert not Appender().matchable(Special('+'))

def test_appender_rejects_const():
    assert not Appender().matchable(Const('a'))


# -- Subst / Reference: never matchable --

def test_subst_never_matchable():
    assert not Subst('0').matchable(Const('a'))
    assert not Subst('0').matchable(Wildcard(), specials=True)

def test_reference_never_matchable():
    assert not Reference('path').matchable(Const('a'))
    assert not Reference('path').matchable(Wildcard(), specials=True)


# -- Concat matchable --

def test_concat_matches_const():
    c = Concat(ConcatPart(Word('a'), ()), ConcatPart(Word('b'), ()))
    assert c.matchable(Const('ab'))

def test_concat_rejects_special():
    c = Concat(ConcatPart(Word('a'), ()), ConcatPart(Word('b'), ()))
    assert not c.matchable(Special('+'))

def test_concat_rejects_special_with_specials():
    c = Concat(ConcatPart(Word('a'), ()), ConcatPart(Word('b'), ()))
    assert not c.matchable(Special('+'), specials=True)
