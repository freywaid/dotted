"""
Tests for a sigil token carrying a dotted path in argument and value
positions: |min:$amount.value, =^lots.taken. One string, offered whole to
a resolver; a plain string without one.
"""
import dotted
from dotted.api import parse


class References(dotted.ReferenceResolver):
    """
    $x reads the inputs, ^x the view, $$(x) the view too.
    """
    literals = True

    def __init__(self, inputs, view):
        self.inputs = inputs
        self.view = view

    def resolve(self, ref, settings, node=None, parents=()):
        if ref.is_reference():
            return dotted.get(self.view, ref.inner_path)
        value = ref.value
        if isinstance(value, str) and value[:1] == '$' and not value[1:2].isdigit() and value[1:2] != '$':
            return dotted.get(self.inputs, value[1:])
        if isinstance(value, str) and value[:1] == '^':
            return dotted.get(self.view, value[1:])
        return value


INPUTS = {'amount': {'value': 12}, 'fee': 2}
VIEW = {'lots': {'taken': 10, 'balance': 7}}


def test_sigil_path_in_transform_argument():
    """
    $amount.value and ^lots.taken are single tokens the resolver reads whole.
    """
    r = References(INPUTS, VIEW)
    assert dotted.get(VIEW, 'lots.balance|max:0|min:$amount.value', resolver=r) == 7
    assert dotted.get(VIEW, 'lots.balance|add:^lots.taken', resolver=r) == 17
    assert dotted.get(INPUTS, 'amount.value|add:$fee', resolver=r) == 14
    assert dotted.get(INPUTS, 'amount.value|sub:$$(lots.taken)|max:0|min:$$(lots.balance)|max:0', resolver=r) == 2


def test_sigil_path_in_guard_and_filter_value():
    """
    The same tokens as guard and filter values.
    """
    r = References({'amount': {'value': 7}}, VIEW)
    assert dotted.get(VIEW, 'lots.balance=$amount.value', resolver=r) == 7
    assert dotted.get(VIEW, 'lots.balance=^lots.balance', resolver=r) == 7
    assert dotted.get({'rows': [{'v': 10}, {'v': 7}]}, 'rows[*&v=^lots.taken].v', resolver=r) == (10,)


def test_sigil_path_without_a_resolver_is_a_string():
    """
    No resolver: the token is the literal string it spells.
    """
    assert dotted.get({'a': None}, 'a|coalesce:$amount.value') == '$amount.value'
    assert dotted.get({'a': '^x.y'}, 'a=^x.y') == '^x.y'


def test_sigil_path_shapes_and_assembly():
    """
    $$(x), $(x) and $0 keep their shapes; the token assembles as written;
    segments are unaffected, since there the dot separates.
    """
    kinds = {p: type(parse(p).transforms[0].params[0]).__name__
             for p in ['a|add:$$(x)', 'a|add:$(x)', 'a|add:$0', 'a|add:$x.y', 'a|add:^x.y']}
    assert kinds == {'a|add:$$(x)': 'Reference', 'a|add:$(x)': 'Subst', 'a|add:$0': 'Subst',
                     'a|add:$x.y': 'Word', 'a|add:^x.y': 'Word'}
    for p in ['a|min:$amount.value', 'a|add:^lots.taken', 'a=$x.y', 'a=^x']:
        assert parse(p).assemble() == p
    assert parse('$amount.value').segments() == ('$amount', 'value')
    assert dotted.get({'a': 1}, 'a|str:v.%s') == 'v.1'
