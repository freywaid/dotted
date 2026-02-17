import pytest
import dotted
from dotted.grammar import template
from dotted.elements import Recursive, RecursiveFirst, Wildcard, Word, ValueGuard


# Parse / assemble round-trip

class TestParseAssemble:
    @pytest.mark.parametrize('expr', [
        '**', '*name', '**?', '*name?',
        '**:2', '**:1:3', '**:-1', '**:::2', '**:1:3:2',
    ])
    def test_round_trip(self, expr):
        r = template.parse_string(expr, parse_all=True)
        ops = r['ops']
        assembled = ''.join(op.operator(top=(i == 0)) for i, op in enumerate(ops))
        assert assembled == expr

    def test_parse_dstar_is_recursive(self):
        r = template.parse_string('**', parse_all=True)
        assert isinstance(r['ops'][0], Recursive)
        assert isinstance(r['ops'][0].inner, Wildcard)

    def test_parse_star_key(self):
        r = template.parse_string('*name', parse_all=True)
        assert isinstance(r['ops'][0], Recursive)
        assert isinstance(r['ops'][0].inner, Word)

    def test_parse_first(self):
        r = template.parse_string('**?', parse_all=True)
        assert isinstance(r['ops'][0], RecursiveFirst)

    def test_parse_value_guard(self):
        r = template.parse_string('**=7', parse_all=True)
        assert isinstance(r['ops'][0], ValueGuard)
        assert isinstance(r['ops'][0].inner, Recursive)

    @pytest.mark.parametrize('expr', ['**', '*name', '**?', '*name?', '**:2'])
    def test_recursive_is_always_pattern(self, expr):
        r = template.parse_string(expr, parse_all=True)
        op = r['ops'][0]
        assert op.is_recursive()
        assert op.is_pattern()

    def test_value_guard_recursive_delegates(self):
        r = template.parse_string('**=7', parse_all=True)
        op = r['ops'][0]
        assert op.is_recursive()
        assert op.is_pattern()

    def test_parse_continuation(self):
        r = template.parse_string('**.first', parse_all=True)
        ops = r['ops']
        assert len(ops) == 2
        assert isinstance(ops[0], Recursive)


# Get -- chain-following

class TestGetChainFollowing:
    def test_star_key_follows_chain(self):
        d = {'first': {'first': 'x'}}
        assert dotted.get(d, '*first') == ({'first': 'x'}, 'x')

    def test_star_key_no_match_at_root(self):
        d = {'a': {'first': 'hello'}}
        assert dotted.get(d, '*first') == ()

    def test_dstar_visits_all(self):
        d = {'a': {'b': 1}, 'c': 2}
        result = dotted.get(d, '**')
        assert result == ({'b': 1}, 1, 2)

    def test_dstar_continuation(self):
        """**.first applies continuation to values, not keys.
        Same as *.first but at all depths."""
        d = {'a': {'first': 'hi'}, 'first': 'top'}
        result = dotted.get(d, '**.first')
        assert result == ('hi',)


# Get -- lists (increment depth)

class TestGetLists:
    def test_dstar_with_list(self):
        d = {'a': [{'b': 1}]}
        result = dotted.get(d, '**')
        # depth 0: [{'b': 1}], depth 1: {'b': 1}, depth 2: 1
        assert result == ([{'b': 1}], {'b': 1}, 1)

    def test_dstar_depth0_with_list(self):
        d = {'a': [{'b': 1}]}
        result = dotted.get(d, '**:0')
        assert result == ([{'b': 1}],)

    def test_star_key_root_list(self):
        """Root list iterates elements looking for key matches."""
        d = [{'name': 'alice'}, {'name': 'bob'}]
        result = dotted.get(d, '*name')
        assert result == ('alice', 'bob')


# Get -- depth slicing

class TestGetDepthSlicing:
    def test_depth_0(self):
        d = {'a': {'b': 1}}
        assert dotted.get(d, '**:0') == ({'b': 1},)

    def test_depth_1(self):
        d = {'a': {'b': 1}}
        assert dotted.get(d, '**:1') == (1,)

    def test_leaves(self):
        d = {'a': {'b': 1}, 'c': 2}
        assert dotted.get(d, '**:-1') == (1, 2)

    def test_depth_range(self):
        d = {'a': {'b': {'c': 1}}}
        assert dotted.get(d, '**:0:1') == ({'b': {'c': 1}}, {'c': 1})

    def test_penultimate(self):
        d = {'a': {'b': 1}}
        # -2 = penultimate: nodes whose children are all leaves
        assert dotted.get(d, '**:-2') == ({'b': 1},)

    def test_negative_stop(self):
        d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}}
        # ::-2 = all depths from 0 up to penultimate (excludes leaves)
        result = dotted.get(d, '**::-2')
        assert [1, 2, 3] in result
        assert [4, 5] in result
        assert 1 not in result  # leaves excluded


# Get -- filters

class TestGetFilters:
    def test_filter(self):
        d = {'a': {'x': 1, 'y': 2}, 'b': {'x': 1, 'z': 3}}
        result = dotted.get(d, '**&x=1')
        # Only values where x=1
        assert {'x': 1, 'y': 2} in result
        assert {'x': 1, 'z': 3} in result


# Get -- first-match

class TestGetFirstMatch:
    def test_first_match(self):
        d = {'a': {'b': 1}, 'c': 2}
        result = dotted.get(d, '**?')
        # Should yield only the first result (as a tuple)
        assert result == ({'b': 1},)


# Update

class TestUpdate:
    def test_update_star_key(self):
        d = {'name': {'name': 'old'}}
        dotted.update(d, '*name', 'X')
        assert d == {'name': 'X'}

    def test_update_dstar(self):
        d = {'a': {'b': 1}, 'c': 2}
        dotted.update(d, '**:-1', 0)
        assert d == {'a': {'b': 0}, 'c': 0}


# Remove

class TestRemove:
    def test_remove_star_key(self):
        d = {'name': 'val', 'other': 1}
        dotted.remove(d, '*name')
        assert d == {'other': 1}


# Match

class TestMatch:
    def test_dstar_matches_any_path(self):
        assert dotted.match('**', 'a.b') == 'a.b'

    def test_star_key_matches_chain(self):
        assert dotted.match('*name', 'name.name') == 'name.name'

    def test_star_key_rejects_non_chain(self):
        assert dotted.match('*name', 'a.b.name') is None

    def test_dstar_with_continuation(self):
        assert dotted.match('**.foo', 'a.b.foo') == 'a.b.foo'

    def test_dstar_single_key(self):
        assert dotted.match('**', 'a') == 'a'


# ValueGuard composition

class TestValueGuard:
    def test_dstar_eq(self):
        d = {'a': {'b': 7, 'c': 3}}
        result = dotted.get(d, '**=7')
        assert result == (7,)

    def test_dstar_neq(self):
        d = {'a': None, 'b': {'c': None, 'd': 1}}
        result = dotted.get(d, '**!=None')
        # Should get all non-None values
        assert 1 in result
        assert None not in result

    def test_dstar_eq_update(self):
        d = {'a': {'b': 7, 'c': 3}, 'd': 7}
        result = dotted.update(d, '**=7', 99)
        assert result == {'a': {'b': 99, 'c': 3}, 'd': 99}

    def test_dstar_eq_update_no_match(self):
        d = {'a': {'b': 1, 'c': 2}}
        result = dotted.update(d, '**=7', 99)
        assert result == {'a': {'b': 1, 'c': 2}}

    def test_dstar_neq_update(self):
        # **!=None matches all non-None values at every depth;
        # bottom-up: d=1 -> 'found', then b={c:None,d:'found'} -> 'found'
        d = {'a': None, 'b': {'c': None, 'd': 1}}
        result = dotted.update(d, '**!=None', 'found')
        assert result == {'a': None, 'b': 'found'}

    def test_dstar_eq_remove(self):
        d = {'a': {'b': 7, 'c': 3}, 'd': 7}
        result = dotted.remove(d, '**=7')
        assert result == {'a': {'c': 3}}

    def test_dstar_eq_remove_from_list(self):
        d = {'a': [1, 7, 3, 7]}
        result = dotted.remove(d, '**=7')
        assert result == {'a': [1, 3]}


# Complex paths

class TestComplexPaths:
    """Tests with deeper nesting, continuations, and combined operators."""

    USERS = {
        'users': {
            'alice': {'age': 30, 'scores': [90, 85]},
            'bob': {'age': 25, 'scores': [70, 95]},
        },
        'meta': {'version': 1}
    }

    def test_recursive_continuation_get(self):
        assert dotted.get(self.USERS, '**.age') == (30, 25)

    def test_recursive_group_continuation(self):
        result = dotted.get(self.USERS, '**(.age, .scores)')
        assert result == (30, [90, 85], 25, [70, 95])

    def test_recursive_continuation_pluck_paths(self):
        result = dotted.pluck(self.USERS, '**.age')
        assert result == (('users.alice.age', 30), ('users.bob.age', 25))

    def test_recursive_continuation_pluck_list_paths(self):
        result = dotted.pluck(self.USERS, '**.scores')
        assert result == (('users.alice.scores', [90, 85]), ('users.bob.scores', [70, 95]))

    def test_recursive_list_continuation(self):
        d = {'items': [{'name': 'a'}, {'name': 'b'}]}
        assert dotted.get(d, '**.name') == ('a', 'b')

    def test_recursive_list_continuation_pluck_paths(self):
        d = {'items': [{'name': 'a'}, {'name': 'b'}]}
        result = dotted.pluck(d, '**.name')
        assert result == (('items[0].name', 'a'), ('items[1].name', 'b'))

    def test_star_key_chain(self):
        d = {'cfg': {'cfg': {'cfg': 'deep'}}, 'other': 1}
        assert dotted.get(d, '*cfg') == ({'cfg': {'cfg': 'deep'}}, {'cfg': 'deep'}, 'deep')

    def test_depth_range(self):
        d = {'a': {'b': {'c': {'d': 'deep'}}}}
        assert dotted.get(d, '**:1:3') == ({'c': {'d': 'deep'}}, {'d': 'deep'}, 'deep')

    def test_leaves_pluck_paths(self):
        d = {'a': {'b': {'c': 1, 'd': 2}, 'e': 3}, 'f': 4}
        result = dotted.pluck(d, '**:-1')
        assert result == (('a.b.c', 1), ('a.b.d', 2), ('a.e', 3), ('f', 4))

    def test_update_with_continuation(self):
        d = {'a': {'x': {'val': 1}}, 'b': {'x': {'val': 2}}}
        dotted.update(d, '**:-2.val', 0)
        assert d == {'a': {'x': {'val': 0}}, 'b': {'x': {'val': 0}}}

    def test_remove_with_continuation(self):
        d = {'a': {'x': {'val': 1, 'keep': 2}}, 'b': {'x': {'val': 3, 'keep': 4}}}
        dotted.remove(d, '**:-2.val')
        assert d == {'a': {'x': {'keep': 2}}, 'b': {'x': {'keep': 4}}}

    def test_unpack_pattern(self):
        d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'hello': {'there': 'bye'}}
        result = dotted.pluck(d, '**:-2(.*, [])')
        assert result == (('a.b', [1, 2, 3]), ('x.y.z', [4, 5]), ('hello.there', 'bye'))
