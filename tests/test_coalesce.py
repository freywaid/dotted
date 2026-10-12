"""
Tests for the coalesce transform: the first of the value and its fallbacks
that is not None.
"""
import dotted


def test_coalesce_none():
    """
    None takes the fallback; anything else, falsy included, is kept.
    """
    assert dotted.get({'n': None}, 'n|coalesce:0') == 0
    assert dotted.get({'n': 5}, 'n|coalesce:0') == 5
    assert dotted.get({'n': 0}, 'n|coalesce:9') == 0
    assert dotted.get({'n': ''}, 'n|coalesce:"x"') == ''


def test_coalesce_several_fallbacks():
    """
    The first fallback that is not None wins.
    """
    d = {'n': None, 'defaults': {'n': 7}, 'empty': {'n': None}}
    assert dotted.get(d, 'n|coalesce:$$(defaults.n):0') == 7
    assert dotted.get(d, 'n|coalesce:$$(empty.n):0') == 0
    assert dotted.get(d, 'n|coalesce:null:0') == 0
    assert dotted.get(d, 'n|coalesce:null') is None


def test_coalesce_on_a_pattern_and_update():
    """
    Each yielded None is replaced; on update the written value is coalesced.
    """
    d = {'a': None, 'b': 2}
    assert dotted.get(d, '*|coalesce:0') == (0, 2)
    assert dotted.update({'a': 1}, 'a|coalesce:0', None) == {'a': 0}


def test_coalesce_does_not_fill_a_missing_key():
    """
    A transform runs on a yielded value only; a missing key yields nothing.
    """
    assert dotted.get({}, 'n|coalesce:0', default='missing') == 'missing'
