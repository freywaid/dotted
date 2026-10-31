"""
Tests for getter(): a read of a path as a callable, set up once, that
returns what get() would.
"""
import pytest

import dotted


D = {'a': {'b': [1, 2, 3], 'n': '7'}, 'xs': [{'k': 1}, {'k': 2}], 'v': 5, 'inc': 3}


@pytest.mark.parametrize('path', [
    'a.b[0]', 'a.b[1:]', 'a.n|int', 'xs[*].k', 'xs[*&k=2]', 'a.missing', '**.k', 'v|add:$$(inc)',
])
def test_getter_matches_get(path):
    """
    A getter reads what get reads, fast path and full traversal alike.
    """
    assert dotted.getter(path)(D) == dotted.get(D, path)


def test_getter_defaults():
    """
    default for a missing single path, pattern_default for an empty pattern.
    """
    assert dotted.getter('a.missing', default='none')(D) == 'none'
    assert dotted.getter('nope[*]', pattern_default='empty')(D) == 'empty'


def test_getter_options():
    """
    apply_transforms and bindings act as they do on get.
    """
    assert dotted.getter('a.n|int', apply_transforms=False)(D) == '7'
    assert dotted.getter('a.$0', bindings=['n'])(D) == '7'


def test_getter_reuse():
    """
    One getter reads many objects.
    """
    first = dotted.getter('a.b[0]')
    assert [first({'a': {'b': [i]}}) for i in range(3)] == [0, 1, 2]


class Names(dotted.ReferenceResolver):
    """
    A bare name lexes to a reference to it.
    """

    def lex(self, text, position):
        return f'$$({text})'


def test_getter_resolver():
    """
    A getter lexes and resolves with its resolver.
    """
    d = {'merged': {'x': 1}, 'k': 'x'}
    assert dotted.getter('merged[k]', resolver=Names())(d) == 1


def test_getter_settles_process_resolver():
    """
    Without resolver=, the getter keeps the process resolver in force when
    it was made, through later set_resolver() calls.
    """
    d = {'merged': {'x': 1}, 'k': 'x'}
    dotted.set_resolver(Names())
    try:
        read = dotted.getter('merged[k]')
    finally:
        dotted.set_resolver()
    assert read(d) == 1
