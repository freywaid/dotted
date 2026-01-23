"""
Tests for API functions
"""
import pytest
import dotted


# quote

def test_quote_string():
    assert dotted.quote('hello') == 'hello'
    # spaces and dots don't require quoting
    assert dotted.quote('has space') == 'has space'
    assert dotted.quote('has.dot') == 'has.dot'


def test_quote_numeric():
    assert dotted.quote(7) == '7'
    assert dotted.quote(7.2) == "#'7.2'"
    assert dotted.quote(7.2, as_key=False) == '7.2'


def test_quote_string_numeric():
    # string that looks numeric needs quoting
    assert dotted.quote('7') == "'7'"


# is_inverted

def test_is_inverted_true():
    assert dotted.is_inverted('-hello') is True
    assert dotted.is_inverted('-hello.there') is True
    assert dotted.is_inverted('-*') is True


def test_is_inverted_false():
    assert dotted.is_inverted('hello') is False
    assert dotted.is_inverted('hello.there') is False
    assert dotted.is_inverted('*') is False


# build / build_multi

def test_build_simple():
    assert dotted.build({}, 'hello') == {'hello': None}
    assert dotted.build({}, 'hello.there') == {'hello': {'there': None}}


def test_build_with_list():
    assert dotted.build({}, 'hello[]') == {'hello': []}


def test_build_with_index():
    assert dotted.build({}, 'items[0]') == {'items': [None]}
    assert dotted.build({}, 'items[2]') == {'items': [None, None, None]}


def test_build_nested_index():
    assert dotted.build({}, 'items[0].name') == {'items': [{'name': None}]}


def test_build_multi():
    result = dotted.build_multi({}, ('hello.there', 'hello.bye'))
    assert result == {'hello': {'there': None, 'bye': None}}


# get_multi

def test_get_multi_basic():
    d = {'hello': 7, 'there': 9, 'bye': 11}
    result = list(dotted.get_multi(d, ['hello', 'there']))
    assert result == [7, 9]


def test_get_multi_missing():
    d = {'hello': 7, 'there': 9}
    result = list(dotted.get_multi(d, ['hello', 'missing', 'there']))
    assert result == [7, 9]


def test_get_multi_nested():
    d = {'a': {'b': 1}, 'c': {'d': 2}}
    result = list(dotted.get_multi(d, ['a.b', 'c.d']))
    assert result == [1, 2]


# has

def test_has_exists():
    d = {'hello': {'there': [1, 2, 3]}}
    assert dotted.has(d, 'hello') is True
    assert dotted.has(d, 'hello.there') is True
    assert dotted.has(d, 'hello.there[0]') is True


def test_has_not_exists():
    d = {'hello': {'there': [1, 2, 3]}}
    assert dotted.has(d, 'bye') is False
    assert dotted.has(d, 'hello.bye') is False
    assert dotted.has(d, 'hello.there[10]') is False


def test_has_pattern():
    d = {'hello': {'there': 1}, 'bye': {'there': 2}}
    assert dotted.has(d, '*.there') is True
    assert dotted.has(d, '*.missing') is False


# setdefault

def test_setdefault_exists():
    d = {'hello': 'there'}
    result = dotted.setdefault(d, 'hello', 'world')
    assert result == {'hello': 'there'}  # unchanged


def test_setdefault_not_exists():
    d = {'hello': 'there'}
    result = dotted.setdefault(d, 'bye', 'world')
    assert result == {'hello': 'there', 'bye': 'world'}


def test_setdefault_nested():
    result = dotted.setdefault({}, 'a.b.c', 7)
    assert result == {'a': {'b': {'c': 7}}}


def test_setdefault_nested_partial():
    d = {'a': {'b': {'c': 1}}}
    result = dotted.setdefault(d, 'a.b.d', 2)
    assert result == {'a': {'b': {'c': 1, 'd': 2}}}
    # existing key unchanged
    result = dotted.setdefault(d, 'a.b.c', 999)
    assert result == {'a': {'b': {'c': 1, 'd': 2}}}


# setdefault_multi

def test_setdefault_multi_list():
    result = dotted.setdefault_multi({'a': 1}, [('a', 999), ('b', 2)])
    assert result == {'a': 1, 'b': 2}


def test_setdefault_multi_dict():
    result = dotted.setdefault_multi({'debug': True}, {'debug': False, 'timeout': 30})
    assert result == {'debug': True, 'timeout': 30}


def test_setdefault_multi_nested():
    result = dotted.setdefault_multi({}, {'a.b': 1, 'a.c': 2})
    assert result == {'a': {'b': 1, 'c': 2}}


# update_multi

def test_update_multi_list():
    result = dotted.update_multi({}, [('hello.there', 7), ('my.my', 9)])
    assert result == {'hello': {'there': 7}, 'my': {'my': 9}}


def test_update_multi_dict():
    result = dotted.update_multi({}, {'stuff.more': 'mine', 'other': 'value'})
    assert result == {'stuff': {'more': 'mine'}, 'other': 'value'}


# remove_multi

def test_remove_multi_keys():
    d = {'hello': {'there': 7}, 'my': {'precious': 9}, 'keep': 'this'}
    result = dotted.remove_multi(d, ['hello', 'my.precious'])
    assert result == {'my': {}, 'keep': 'this'}


def test_remove_multi_keyvalues():
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.remove_multi(d, [('a', 1), ('b', 999)], keys_only=False)
    # only 'a' removed because value matched, 'b' stays because value didn't match
    assert result == {'b': 2, 'c': 3}


def test_remove_multi_dict():
    d = {'a': 1, 'b': 2, 'c': 3}
    result = dotted.remove_multi(d, {'a': 1, 'c': 3}, keys_only=False)
    assert result == {'b': 2}


# match_multi

def test_match_multi_basic():
    result = list(dotted.match_multi('/h.*/', ['hello', 'there', 'hi']))
    assert result == ['hello', 'hi']


def test_match_multi_wildcard():
    result = list(dotted.match_multi('*.there', ['hello.there', 'bye.there', 'nope']))
    assert result == ['hello.there', 'bye.there']


def test_match_multi_groups():
    result = list(dotted.match_multi('*.*', ['a.b', 'c.d'], groups=True))
    assert result == [('a.b', ('a', 'b')), ('c.d', ('c', 'd'))]


def test_match_multi_partial():
    result = list(dotted.match_multi('*', ['a.b.c', 'd.e'], partial=True))
    assert result == ['a.b.c', 'd.e']

    result = list(dotted.match_multi('*', ['a.b.c', 'd.e'], partial=False))
    assert result == []


# apply / apply_multi

def test_apply_basic():
    d = {'hello': 7}
    result = dotted.apply(d, 'hello|str')
    assert result == {'hello': '7'}


def test_apply_nested():
    d = {'user': {'age': '25'}}
    result = dotted.apply(d, 'user.age|int')
    assert result == {'user': {'age': 25}}


def test_apply_multi():
    d = {'hello': 7, 'there': 9}
    result = dotted.apply_multi(d, ('*|float', 'hello|str'))
    assert result == {'hello': '7.0', 'there': 9.0}


def test_apply_pattern():
    d = {'a': '1', 'b': '2', 'c': '3'}
    result = dotted.apply(d, '*|int')
    assert result == {'a': 1, 'b': 2, 'c': 3}


# register / transform decorator / registry

def test_register_transform():
    dotted.register('double', lambda x: x * 2)
    assert dotted.get(5, '|double') == 10
    assert dotted.get('hi', '|double') == 'hihi'


def test_transform_decorator():
    @dotted.transform('triple')
    def triple(x):
        return x * 3

    assert dotted.get(5, '|triple') == 15


def test_transform_with_args():
    @dotted.transform('multiply')
    def multiply(x, factor=2):
        return x * factor

    assert dotted.get(5, '|multiply') == 10
    assert dotted.get(5, '|multiply:3') == 15


def test_registry():
    reg = dotted.api.registry()
    assert 'str' in reg
    assert 'int' in reg
    assert 'float' in reg
    assert 'double' in reg  # registered above
    assert 'triple' in reg  # registered above


# ParseError - malformed dotted notation

def test_parse_error_unclosed_bracket():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'field[')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_unclosed_bracket_with_content():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'field[0')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_bracket_at_start():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, '[invalid')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_double_dot():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'a..b')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_trailing_dot():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'hello.')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_trailing_pipe():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'hello|')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_unclosed_regex():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, '/unclosed')
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_invalid_slice():
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'field[1:2:3:4]')  # too many colons
    assert 'Invalid dotted notation' in str(exc.value)


def test_parse_error_empty_brackets():
    # Empty brackets are actually valid (creates empty list)
    # So this should NOT raise
    result = dotted.build({}, 'items[]')
    assert result == {'items': []}


def test_parse_error_message_format():
    """Verify error message includes helpful pointer to error location."""
    with pytest.raises(dotted.api.ParseError) as exc:
        dotted.get({}, 'a.b.[')
    error_msg = str(exc.value)
    assert 'Invalid dotted notation' in error_msg
    assert "'a.b.['" in error_msg
    assert '^' in error_msg  # caret pointing to error location


def test_parse_error_on_update():
    with pytest.raises(dotted.api.ParseError):
        dotted.update({}, 'field[', 'value')


def test_parse_error_on_remove():
    with pytest.raises(dotted.api.ParseError):
        dotted.remove({}, 'field[')


def test_parse_error_on_has():
    with pytest.raises(dotted.api.ParseError):
        dotted.has({}, 'field[')


# Immutable container operations

def test_tuple_update_index():
    t = (1, 2, 3)
    result = dotted.update(t, '[1]', 'X')
    assert result == (1, 'X', 3)
    assert t == (1, 2, 3)  # original unchanged


def test_tuple_update_first():
    result = dotted.update((1, 2, 3), '[0]', 'first')
    assert result == ('first', 2, 3)


def test_tuple_update_last():
    result = dotted.update((1, 2, 3), '[2]', 'last')
    assert result == (1, 2, 'last')


def test_tuple_update_pattern():
    result = dotted.update((1, 2, 3), '[*]', 0)
    assert result == (0, 0, 0)


def test_tuple_append():
    result = dotted.update((1, 2, 3), '[+]', 4)
    assert result == (1, 2, 3, 4)


def test_tuple_remove():
    result = dotted.remove((1, 2, 3), '[1]')
    assert result == (1, 3)


def test_tuple_nested_in_dict():
    d = {'items': (1, 2, 3)}
    result = dotted.update(d, 'items[1]', 'X')
    assert result == {'items': (1, 'X', 3)}


def test_string_update_index():
    result = dotted.update('hello', '[0]', 'H')
    assert result == 'Hello'


def test_string_update_last():
    result = dotted.update('hello', '[4]', '!')
    assert result == 'hell!'


def test_string_update_pattern():
    result = dotted.update('aaa', '[*]', 'b')
    assert result == 'bbb'


# namedtuple operations

def test_namedtuple_get_attr():
    from collections import namedtuple
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    assert dotted.get(p, '@x') == 1
    assert dotted.get(p, '@y') == 2


def test_namedtuple_update_attr():
    from collections import namedtuple
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    result = dotted.update(p, '@x', 10)
    assert result == Point(10, 2)
    assert p == Point(1, 2)  # original unchanged


def test_namedtuple_update_pattern():
    from collections import namedtuple
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    result = dotted.update(p, '@*', 0)
    assert result == Point(0, 0)


def test_namedtuple_update_index():
    from collections import namedtuple
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    result = dotted.update(p, '[0]', 10)
    assert result == Point(10, 2)


def test_namedtuple_update_index_pattern():
    from collections import namedtuple
    Point = namedtuple('Point', ['x', 'y'])
    p = Point(1, 2)
    result = dotted.update(p, '[*]', 0)
    assert result == Point(0, 0)


# frozen dataclass operations

def test_frozen_dataclass_get():
    from dataclasses import dataclass
    @dataclass(frozen=True)
    class FrozenPoint:
        x: int
        y: int
    fp = FrozenPoint(1, 2)
    assert dotted.get(fp, '@x') == 1


def test_frozen_dataclass_update():
    from dataclasses import dataclass
    @dataclass(frozen=True)
    class FrozenPoint:
        x: int
        y: int
    fp = FrozenPoint(1, 2)
    result = dotted.update(fp, '@x', 10)
    assert result == FrozenPoint(10, 2)
    assert fp == FrozenPoint(1, 2)  # original unchanged


def test_frozen_dataclass_update_pattern():
    from dataclasses import dataclass
    @dataclass(frozen=True)
    class FrozenPoint:
        x: int
        y: int
    fp = FrozenPoint(1, 2)
    result = dotted.update(fp, '@*', 0)
    assert result == FrozenPoint(0, 0)


# frozenset operations

def test_frozenset_append():
    fs = frozenset([1, 2, 3])
    result = dotted.update(fs, '[+]', 4)
    assert result == frozenset([1, 2, 3, 4])
    assert fs == frozenset([1, 2, 3])  # original unchanged


def test_frozenset_append_unique():
    fs = frozenset([1, 2, 3])
    result = dotted.update(fs, '[+?]', 3)  # already exists
    assert result == fs
    result = dotted.update(fs, '[+?]', 4)  # new
    assert result == frozenset([1, 2, 3, 4])


# None handling in nested updates

def test_update_nested_none_to_dict():
    d = {'a': None}
    result = dotted.update(d, 'a.b', 1)
    assert result == {'a': {'b': 1}}


def test_update_nested_none_to_list():
    d = {'a': None}
    result = dotted.update(d, 'a[0]', 1)
    assert result == {'a': [1]}


def test_update_deeply_nested_none():
    d = {'a': {'b': None}}
    result = dotted.update(d, 'a.b.c.d', 1)
    assert result == {'a': {'b': {'c': {'d': 1}}}}


def test_update_none_in_list():
    d = {'items': [None, None]}
    result = dotted.update(d, 'items[0].x', 1)
    assert result == {'items': [{'x': 1}, None]}


def test_update_top_level_none_errors():
    with pytest.raises(TypeError) as exc:
        dotted.update(None, 'x', 1)
    assert 'Cannot update None' in str(exc.value)
