import pytest
import dotted


def test_assemble_wildcard():
    parsed = dotted.parse('*')
    assembled = dotted.assemble(parsed)
    assert assembled == '*'


def test_assemble_appender():
    parsed = dotted.parse('[+]')
    assembled = dotted.assemble(parsed)
    assert assembled == '[+]'


def test_assemble_regex():
    parsed = dotted.parse('/A.+/')
    assembled = dotted.assemble(parsed)
    assert assembled == '/A.+/'


# =============================================================================
# Path group round-trips
# =============================================================================

def test_assemble_path_or():
    """
    Path disjunction round-trips through assemble.
    """
    assert dotted.assemble(dotted.parse('(a,b)')) == '(a,b)'


def test_assemble_path_or_hard_cut():
    """
    Path disjunction with hard cut round-trips.
    """
    assert dotted.assemble(dotted.parse('(a#,b)')) == '(a#,b)'


def test_assemble_path_or_soft_cut():
    """
    Path disjunction with soft cut round-trips.
    """
    assert dotted.assemble(dotted.parse('(a##,b)')) == '(a##,b)'


def test_assemble_opgroup_or():
    """
    Op group disjunction assembles stably.
    """
    a = dotted.assemble(dotted.parse('a(.b,.c)'))
    assert dotted.assemble(dotted.parse(a)) == a


def test_assemble_opgroup_and():
    """
    Op group conjunction assembles stably.
    """
    a = dotted.assemble(dotted.parse('a(.b&.c)'))
    assert dotted.assemble(dotted.parse(a)) == a


def test_assemble_opgroup_not():
    """
    Op group negation assembles stably.
    """
    a = dotted.assemble(dotted.parse('a(!.b)'))
    assert dotted.assemble(dotted.parse(a)) == a


def test_assemble_path_not():
    """
    Path negation round-trips through assemble.
    """
    assert dotted.assemble(dotted.parse('(!a)')) == '(!a)'


def test_assemble_path_not_multi():
    """
    Path negation of multiple keys round-trips.
    """
    assert dotted.assemble(dotted.parse('(!(a,b))')) == '(!(a,b))'


def test_assemble_opgroup_first():
    """
    First-match op group assembles stably.
    """
    a = dotted.assemble(dotted.parse('a(.b,.c)?'))
    assert dotted.assemble(dotted.parse(a)) == a
