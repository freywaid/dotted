import pytest
import dotted
from dotted.grammar import template
from dotted.recursive import Recursive, RecursiveFirst
from dotted.matchers import Wildcard, Word
from dotted.wrappers import ValueGuard


# -- Parse / assemble round-trip --

@pytest.mark.parametrize('expr', [
    '**', '*name', '**?', '*name?',
    '**:2', '**:1:3', '**:-1', '**:::2', '**:1:3:2',
])
def test_round_trip(expr):
    """
    Parse then assemble should return the original expression.
    """
    r = template.parse_string(expr, parse_all=True)
    ops = r['ops']
    assembled = ''.join(op.operator(top=(i == 0)) for i, op in enumerate(ops))
    assert assembled == expr


def test_parse_dstar_is_recursive():
    """
    ** parses to Recursive with Wildcard inner.
    """
    r = template.parse_string('**', parse_all=True)
    assert isinstance(r['ops'][0], Recursive)
    assert isinstance(r['ops'][0].inner, Wildcard)


def test_parse_star_key():
    """
    *name parses to Recursive with Word inner.
    """
    r = template.parse_string('*name', parse_all=True)
    assert isinstance(r['ops'][0], Recursive)
    assert isinstance(r['ops'][0].inner, Word)


def test_parse_first():
    """
    **? parses to RecursiveFirst.
    """
    r = template.parse_string('**?', parse_all=True)
    assert isinstance(r['ops'][0], RecursiveFirst)


def test_parse_value_guard():
    """
    **=7 parses to ValueGuard wrapping Recursive.
    """
    r = template.parse_string('**=7', parse_all=True)
    assert isinstance(r['ops'][0], ValueGuard)
    assert isinstance(r['ops'][0].inner, Recursive)


@pytest.mark.parametrize('expr', ['**', '*name', '**?', '*name?', '**:2'])
def test_recursive_is_always_pattern(expr):
    """
    All recursive forms are both recursive and pattern.
    """
    r = template.parse_string(expr, parse_all=True)
    op = r['ops'][0]
    assert op.is_recursive()
    assert op.is_pattern()


def test_value_guard_recursive_delegates():
    """
    ValueGuard wrapping Recursive delegates is_recursive and is_pattern.
    """
    r = template.parse_string('**=7', parse_all=True)
    op = r['ops'][0]
    assert op.is_recursive()
    assert op.is_pattern()


def test_parse_continuation():
    """
    **.first parses to two ops: Recursive then Key.
    """
    r = template.parse_string('**.first', parse_all=True)
    ops = r['ops']
    assert len(ops) == 2
    assert isinstance(ops[0], Recursive)


# -- Get: chain-following --

def test_star_key_follows_chain():
    """
    *first follows the 'first' key chain.
    """
    d = {'first': {'first': 'x'}}
    assert dotted.get(d, '*first') == ({'first': 'x'}, 'x')


def test_star_key_no_match_at_root():
    """
    *first requires root key to be 'first'.
    """
    d = {'a': {'first': 'hello'}}
    assert dotted.get(d, '*first') == ()


def test_dstar_visits_all():
    """
    ** visits all dict values at all depths.
    """
    d = {'a': {'b': 1}, 'c': 2}
    result = dotted.get(d, '**')
    assert result == ({'b': 1}, 1, 2)


def test_dstar_continuation():
    """
    **.first applies continuation to values, not keys.
    Same as *.first but at all depths.
    """
    d = {'a': {'first': 'hi'}, 'first': 'top'}
    result = dotted.get(d, '**.first')
    assert result == ('hi',)


# -- Get: lists --

def test_dstar_with_list():
    """
    ** is dict-key only — does not recurse into lists.
    """
    d = {'a': [{'b': 1}]}
    result = dotted.get(d, '**')
    assert result == ([{'b': 1}],)


def test_dstar_dict_list_with_accessor_group():
    """
    *(*#, [*]) recurses through both dicts and lists.
    """
    d = {'a': [{'b': 1}]}
    result = dotted.get(d, '*(*#, [*])')
    assert result == ([{'b': 1}], {'b': 1}, 1)


def test_dstar_depth0_with_list():
    """
    **:0 returns only depth-0 values.
    """
    d = {'a': [{'b': 1}]}
    result = dotted.get(d, '**:0')
    assert result == ([{'b': 1}],)


def test_star_key_root_list():
    """
    *name on root list: no dict keys to match.
    """
    d = [{'name': 'alice'}, {'name': 'bob'}]
    result = dotted.get(d, '*name')
    assert result == ()


def test_accessor_group_root_list():
    """
    *(name#, [*]:!(str, bytes)) on root list: recurses into list slots
    and dict keys without decomposing strings.
    Returns all matches at every depth — list entries AND name values.
    """
    d = [{'name': 'alice'}, {'name': 'bob'}]
    result = dotted.get(d, '*(name#, [*]:!(str, bytes))')
    assert result == ({'name': 'alice'}, 'alice', {'name': 'bob'}, 'bob')


def test_accessor_group_root_list_with_continuation():
    """
    *(*#, [*]).name on root list: recurse through dicts+lists, then
    continue with .name to extract just the name values.
    """
    d = [{'name': 'alice'}, {'name': 'bob'}]
    result = dotted.get(d, '*(*#, [*]).name')
    assert result == ('alice', 'bob')


# -- Get: depth slicing --

def test_depth_0():
    """
    **:0 returns only depth-0 values.
    """
    d = {'a': {'b': 1}}
    assert dotted.get(d, '**:0') == ({'b': 1},)


def test_depth_1():
    """
    **:1 returns only depth-1 values.
    """
    d = {'a': {'b': 1}}
    assert dotted.get(d, '**:1') == (1,)


def test_leaves():
    """
    **:-1 returns leaf values.
    """
    d = {'a': {'b': 1}, 'c': 2}
    assert dotted.get(d, '**:-1') == (1, 2)


def test_depth_range():
    """
    **:0:1 returns depths 0 through 1.
    """
    d = {'a': {'b': {'c': 1}}}
    assert dotted.get(d, '**:0:1') == ({'b': {'c': 1}}, {'c': 1})


def test_penultimate():
    """
    **:-2 returns penultimate nodes (parents of leaves).
    """
    d = {'a': {'b': 1}}
    assert dotted.get(d, '**:-2') == ({'b': 1},)


def test_negative_stop():
    """
    **::-2 returns all depths up to penultimate.
    """
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}}
    result = dotted.get(d, '**::-2')
    assert {'b': [1, 2, 3]} in result
    assert {'z': [4, 5]} in result


# -- Get: filters --

def test_filter():
    """
    **&x=1 filters recursive matches by key-value.
    """
    d = {'a': {'x': 1, 'y': 2}, 'b': {'x': 1, 'z': 3}}
    result = dotted.get(d, '**&x=1')
    assert {'x': 1, 'y': 2} in result
    assert {'x': 1, 'z': 3} in result


# -- Get: first-match --

def test_first_match():
    """
    **? yields only the first result.
    """
    d = {'a': {'b': 1}, 'c': 2}
    result = dotted.get(d, '**?')
    assert result == ({'b': 1},)


# -- Update --

def test_update_star_key():
    """
    *name updates the first chain-following match.
    """
    d = {'name': {'name': 'old'}}
    dotted.update(d, '*name', 'X')
    assert d == {'name': 'X'}


def test_update_dstar():
    """
    **:-1 updates all leaf values.
    """
    d = {'a': {'b': 1}, 'c': 2}
    dotted.update(d, '**:-1', 0)
    assert d == {'a': {'b': 0}, 'c': 0}


# -- Remove --

def test_remove_star_key():
    """
    *name removes chain-following matches.
    """
    d = {'name': 'val', 'other': 1}
    dotted.remove(d, '*name')
    assert d == {'other': 1}


# -- Match --

def test_dstar_matches_any_path():
    """
    ** matches any dict-key path.
    """
    assert dotted.match('**', 'a.b') == 'a.b'


def test_star_key_matches_chain():
    """
    *name matches a chain of 'name' keys.
    """
    assert dotted.match('*name', 'name.name') == 'name.name'


def test_star_key_rejects_non_chain():
    """
    *name rejects paths that don't start with 'name'.
    """
    assert dotted.match('*name', 'a.b.name') is None


def test_dstar_with_continuation_match():
    """
    **.foo matches paths ending in foo.
    """
    assert dotted.match('**.foo', 'a.b.foo') == 'a.b.foo'


def test_dstar_single_key():
    """
    ** matches a single key.
    """
    assert dotted.match('**', 'a') == 'a'


# -- ValueGuard composition --

def test_dstar_eq():
    """
    **=7 returns only values equal to 7.
    """
    d = {'a': {'b': 7, 'c': 3}}
    result = dotted.get(d, '**=7')
    assert result == (7,)


def test_dstar_neq():
    """
    **!=None returns all non-None values.
    """
    d = {'a': None, 'b': {'c': None, 'd': 1}}
    result = dotted.get(d, '**!=None')
    assert 1 in result
    assert None not in result


def test_dstar_eq_update():
    """
    **=7 update replaces matching values.
    """
    d = {'a': {'b': 7, 'c': 3}, 'd': 7}
    result = dotted.update(d, '**=7', 99)
    assert result == {'a': {'b': 99, 'c': 3}, 'd': 99}


def test_dstar_eq_update_no_match():
    """
    **=7 update with no matches leaves data unchanged.
    """
    d = {'a': {'b': 1, 'c': 2}}
    result = dotted.update(d, '**=7', 99)
    assert result == {'a': {'b': 1, 'c': 2}}


def test_dstar_neq_update():
    """
    **!=None updates all non-None values bottom-up.
    """
    d = {'a': None, 'b': {'c': None, 'd': 1}}
    result = dotted.update(d, '**!=None', 'found')
    assert result == {'a': None, 'b': 'found'}


def test_dstar_eq_remove():
    """
    **=7 removes matching values from dicts.
    """
    d = {'a': {'b': 7, 'c': 3}, 'd': 7}
    result = dotted.remove(d, '**=7')
    assert result == {'a': {'c': 3}}


def test_dstar_eq_remove_from_list():
    """
    ** is dict-only — doesn't recurse into the list, so =7 can't
    find the 7s inside.  The list is a leaf value.
    """
    d = {'a': [1, 7, 3, 7]}
    result = dotted.remove(d, '**=7')
    assert result == {'a': [1, 7, 3, 7]}


def test_accessor_group_eq_remove_from_list():
    """
    *(*, [*])=7 recurses into dicts AND lists, finding and removing 7s.
    """
    d = {'a': [1, 7, 3, 7]}
    result = dotted.remove(d, '*(*, [*])=7')
    assert result == {'a': [1, 3]}


# -- Complex paths --

USERS = {
    'users': {
        'alice': {'age': 30, 'scores': [90, 85]},
        'bob': {'age': 25, 'scores': [70, 95]},
    },
    'meta': {'version': 1}
}


def test_recursive_continuation_get():
    """
    **.age extracts age from all depths.
    """
    assert dotted.get(USERS, '**.age') == (30, 25)


def test_recursive_group_continuation():
    """
    **(.age, .scores) extracts both age and scores at all depths.
    """
    result = dotted.get(USERS, '**(.age, .scores)')
    assert result == (30, [90, 85], 25, [70, 95])


def test_recursive_continuation_pluck_paths():
    """
    **.age pluck includes full paths.
    """
    result = dotted.pluck(USERS, '**.age')
    assert result == (('users.alice.age', 30), ('users.bob.age', 25))


def test_recursive_continuation_pluck_list_paths():
    """
    **.scores pluck includes full paths.
    """
    result = dotted.pluck(USERS, '**.scores')
    assert result == (('users.alice.scores', [90, 85]), ('users.bob.scores', [70, 95]))


def test_recursive_list_continuation():
    """
    ** is dict-only — doesn't enter the list, so .name finds nothing.
    """
    d = {'items': [{'name': 'a'}, {'name': 'b'}]}
    assert dotted.get(d, '**.name') == ()


def test_recursive_list_continuation_with_accessor_group():
    """
    *(*#, [*]).name recurses into dicts AND lists, finding names inside.
    """
    d = {'items': [{'name': 'a'}, {'name': 'b'}]}
    assert dotted.get(d, '*(*#, [*]).name') == ('a', 'b')


def test_recursive_list_continuation_pluck_paths():
    """
    ** is dict-only — list not entered, no paths found.
    """
    d = {'items': [{'name': 'a'}, {'name': 'b'}]}
    result = dotted.pluck(d, '**.name')
    assert result == ()


def test_recursive_list_continuation_pluck_paths_with_accessor_group():
    """
    *(*#, [*]).name recurses into dicts AND lists, yielding full paths.
    """
    d = {'items': [{'name': 'a'}, {'name': 'b'}]}
    result = dotted.pluck(d, '*(*#, [*]).name')
    assert result == (('items[0].name', 'a'), ('items[1].name', 'b'))


def test_star_key_chain():
    """
    *cfg follows the 'cfg' key chain.
    """
    d = {'cfg': {'cfg': {'cfg': 'deep'}}, 'other': 1}
    assert dotted.get(d, '*cfg') == ({'cfg': {'cfg': 'deep'}}, {'cfg': 'deep'}, 'deep')


def test_depth_range_complex():
    """
    **:1:3 returns depths 1 through 3.
    """
    d = {'a': {'b': {'c': {'d': 'deep'}}}}
    assert dotted.get(d, '**:1:3') == ({'c': {'d': 'deep'}}, {'d': 'deep'}, 'deep')


def test_leaves_pluck_paths():
    """
    **:-1 pluck returns leaf paths and values.
    """
    d = {'a': {'b': {'c': 1, 'd': 2}, 'e': 3}, 'f': 4}
    result = dotted.pluck(d, '**:-1')
    assert result == (('a.b.c', 1), ('a.b.d', 2), ('a.e', 3), ('f', 4))


def test_update_with_continuation():
    """
    **:-2.val updates val on penultimate nodes.
    """
    d = {'a': {'x': {'val': 1}}, 'b': {'x': {'val': 2}}}
    dotted.update(d, '**:-2.val', 0)
    assert d == {'a': {'x': {'val': 0}}, 'b': {'x': {'val': 0}}}


def test_remove_with_continuation():
    """
    **:-2.val removes val from penultimate nodes.
    """
    d = {'a': {'x': {'val': 1, 'keep': 2}}, 'b': {'x': {'val': 3, 'keep': 4}}}
    dotted.remove(d, '**:-2.val')
    assert d == {'a': {'x': {'keep': 2}}, 'b': {'x': {'keep': 4}}}


def test_unpack_pattern():
    """
    **:-2(.*, []) extracts leaf paths for unpack.
    """
    d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'hello': {'there': 'bye'}}
    result = dotted.pluck(d, '**:-2(.*, [])')
    assert result == (('a.b', [1, 2, 3]), ('x.y.z', [4, 5]), ('hello.there', 'bye'))


# -- Cycle detection --

def test_cycle_self_referencing_dict_dstar():
    """
    ** on a self-referencing dict terminates without infinite loop.
    The cyclic ref is yielded as a value but not recursed into again.
    """
    d = {'a': 1}
    d['self'] = d
    result = dotted.get(d, '**')
    assert 1 in result
    assert d in result


def test_cycle_mutual_reference_dstar():
    """
    ** on mutually referencing dicts terminates.
    """
    a = {'val': 1}
    b = {'val': 2}
    a['other'] = b
    b['other'] = a
    root = {'a': a, 'b': b}
    result = dotted.get(root, '**')
    assert 1 in result
    assert 2 in result


def test_cycle_star_key_chain():
    """
    *next on a circular linked-list terminates.
    """
    a = {'next': None, 'val': 'a'}
    b = {'next': None, 'val': 'b'}
    c = {'next': None, 'val': 'c'}
    a['next'] = b
    b['next'] = c
    c['next'] = a  # cycle
    result = dotted.get(a, '*next')
    assert len(result) == 3  # a, b, c — cycle back to a is skipped


def test_cycle_star_key_chain_with_continuation():
    """
    *next.val follows chain and extracts val at each node.
    """
    a = {'next': None, 'val': 'a'}
    b = {'next': None, 'val': 'b'}
    c = {'next': None, 'val': 'c'}
    a['next'] = b
    b['next'] = c
    c['next'] = a  # cycle
    result = dotted.get(a, '*next.val')
    assert set(result) == {'a', 'b', 'c'}


def test_cycle_dstar_continuation():
    """
    **.key on a cyclic structure terminates and finds nested keys.
    """
    inner = {'key': 'found'}
    d = {'nested': inner}
    d['self'] = d
    result = dotted.get(d, '**.key')
    assert result == ('found',)


def test_cycle_dstar_first():
    """
    **? on a cyclic structure returns the first match without looping.
    """
    d = {'a': 1}
    d['self'] = d
    result = dotted.get(d, '**?')
    assert len(result) == 1


def test_cycle_update_dstar():
    """
    ** update on a cyclic structure terminates and updates all values.
    """
    d = {'a': 1, 'b': 2}
    d['self'] = d
    dotted.update(d, '**', 0)
    assert d['a'] == 0
    assert d['b'] == 0
    # Cyclic ref is also a matched value, so it's replaced too
    assert d['self'] == 0


def test_cycle_update_star_key():
    """
    *next update on a circular chain terminates.
    """
    a = {'next': None, 'val': 'a'}
    b = {'next': None, 'val': 'b'}
    a['next'] = b
    b['next'] = a  # cycle
    dotted.update(a, '*next.val', 'x')
    assert a['val'] == 'x'
    assert b['val'] == 'x'


def test_cycle_remove_dstar():
    """
    ** remove on a cyclic structure terminates.
    """
    d = {'a': 1, 'b': 2}
    d['self'] = d
    dotted.remove(d, '**')
    assert d == {}


def test_cycle_remove_dstar_by_value():
    """
    ** remove with val on a cyclic structure only removes matching values.
    """
    d = {'a': 1, 'b': 2}
    d['self'] = d
    dotted.remove(d, '**', 1)
    assert 'a' not in d
    assert d['b'] == 2


def test_remove_dstar_by_value():
    """
    ** remove with val only removes keys whose value matches (no cycle).
    """
    d = {'a': {'x': 1, 'y': 2}, 'b': {'x': 3, 'y': 1}}
    dotted.remove(d, '**', 1)
    assert d == {'a': {'y': 2}, 'b': {'x': 3}}


def test_cycle_remove_star_key():
    """
    *next remove on a circular chain terminates.
    """
    a = {'next': None, 'val': 'a'}
    b = {'next': None, 'val': 'b'}
    a['next'] = b
    b['next'] = a  # cycle
    dotted.remove(a, '*next.val')
    assert 'val' not in a
    assert 'val' not in b


def test_cycle_deep_self_reference():
    """
    ** handles a cycle deeper in the structure.
    """
    root = {'a': {'b': {'c': 1}}}
    root['a']['b']['loop'] = root['a']
    result = dotted.get(root, '**')
    assert 1 in result


def test_cycle_pluck_dstar():
    """
    pluck with ** on a cyclic structure returns correct paths.
    """
    d = {'a': 1}
    d['self'] = d
    result = dotted.pluck(d, '**')
    paths = [p for p, _ in result]
    vals = [v for _, v in result]
    assert 'a' in paths
    assert 1 in vals


def test_cycle_dstar_depth_slice():
    """
    **:0 on a cyclic structure terminates — depth-0 only.
    """
    d = {'a': 1}
    d['self'] = d
    result = dotted.get(d, '**:0')
    assert 1 in result


def test_cycle_accessor_group():
    """
    *(*#, [*]) on a structure with cyclic dict ref terminates.
    """
    d = {'items': [1, 2, 3]}
    d['self'] = d
    result = dotted.get(d, '*(*#, [*])')
    assert 1 in result
    assert 2 in result
    assert 3 in result
