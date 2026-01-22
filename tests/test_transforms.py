"""
Tests for built-in transforms
"""
import decimal
import pytest
import dotted


# str transform

def test_str_basic():
    assert dotted.get(123, '|str') == '123'
    assert dotted.get(12.5, '|str') == '12.5'
    assert dotted.get(None, '|str') == 'None'


def test_str_format():
    assert dotted.get(7, '|str:number=%d') == 'number=7'
    assert dotted.get(3.14159, '|str:pi=%.2f') == 'pi=3.14'
    assert dotted.get('hello', '|str:%s world') == 'hello world'


def test_str_format_type_error():
    # format mismatch returns original value without raises
    assert dotted.get('hello', '|str:%d') == 'hello'


def test_str_format_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|str:%d:raises')


# int transform

def test_int_basic():
    assert dotted.get('10', '|int') == 10
    assert dotted.get(10.9, '|int') == 10
    assert dotted.get('  42  ', '|int') == 42


def test_int_with_base():
    assert dotted.get('ff', '|int:16') == 255
    assert dotted.get('1010', '|int:2') == 10
    assert dotted.get('77', '|int:8') == 63


def test_int_invalid_returns_original():
    assert dotted.get('hello', '|int') == 'hello'
    assert dotted.get('12.5', '|int') == '12.5'


def test_int_raises():
    with pytest.raises(ValueError):
        dotted.get('hello', '|int::raises')


# float transform

def test_float_basic():
    assert dotted.get('3.14', '|float') == 3.14
    assert dotted.get(10, '|float') == 10.0
    assert dotted.get('  2.5  ', '|float') == 2.5


def test_float_invalid_returns_original():
    assert dotted.get('hello', '|float') == 'hello'


def test_float_raises():
    with pytest.raises(ValueError):
        dotted.get('hello', '|float:raises')


# decimal transform

def test_decimal_basic():
    assert dotted.get('10', '|decimal') == decimal.Decimal('10')
    assert dotted.get('3.14159', '|decimal') == decimal.Decimal('3.14159')
    assert dotted.get(42, '|decimal') == decimal.Decimal(42)


def test_decimal_invalid_returns_original():
    assert dotted.get('hello', '|decimal') == 'hello'


def test_decimal_raises():
    with pytest.raises(decimal.InvalidOperation):
        dotted.get('hello', '|decimal:raises')


# none transform

def test_none_falsy():
    assert dotted.get('', '|none') is None
    assert dotted.get(0, '|none') is None
    assert dotted.get([], '|none') is None
    assert dotted.get({}, '|none') is None


def test_none_truthy():
    assert dotted.get('hello', '|none') == 'hello'
    assert dotted.get(42, '|none') == 42
    assert dotted.get([1, 2], '|none') == [1, 2]


def test_none_with_values():
    assert dotted.get('hello', '|none::hello') is None
    assert dotted.get('world', '|none::hello:world') is None
    assert dotted.get('other', '|none::hello:world') == 'other'


# strip transform

def test_strip_basic():
    assert dotted.get('  hello  ', '|strip') == 'hello'
    assert dotted.get('\n\thello\n\t', '|strip') == 'hello'


def test_strip_chars():
    assert dotted.get('xxhelloxx', '|strip:x') == 'hello'
    assert dotted.get('abchelloabc', '|strip:abc') == 'hello'


def test_strip_non_string_returns_original():
    assert dotted.get(123, '|strip') == 123
    assert dotted.get(['a', 'b'], '|strip') == ['a', 'b']


def test_strip_raises():
    with pytest.raises(AttributeError):
        dotted.get(123, '|strip::raises')


# len transform

def test_len_basic():
    assert dotted.get('hello', '|len') == 5
    assert dotted.get([1, 2, 3], '|len') == 3
    assert dotted.get({'a': 1, 'b': 2}, '|len') == 2


def test_len_default():
    assert dotted.get(123, '|len:0') == 0
    assert dotted.get(None, '|len:-1') == -1


def test_len_raises_without_default():
    with pytest.raises(TypeError):
        dotted.get(123, '|len')


# lowercase transform

def test_lowercase_basic():
    assert dotted.get('HELLO', '|lowercase') == 'hello'
    assert dotted.get('HeLLo WoRLD', '|lowercase') == 'hello world'


def test_lowercase_non_string_returns_original():
    assert dotted.get(123, '|lowercase') == 123


def test_lowercase_raises():
    with pytest.raises(AttributeError):
        dotted.get(123, '|lowercase:raises')


# uppercase transform

def test_uppercase_basic():
    assert dotted.get('hello', '|uppercase') == 'HELLO'
    assert dotted.get('HeLLo WoRLD', '|uppercase') == 'HELLO WORLD'


def test_uppercase_non_string_returns_original():
    assert dotted.get(123, '|uppercase') == 123


def test_uppercase_raises():
    with pytest.raises(AttributeError):
        dotted.get(123, '|uppercase:raises')


# add transform

def test_add_numbers():
    assert dotted.get(10, '|add:5') == 15
    assert dotted.get(3.5, '|add:1.5') == 5.0


def test_add_strings():
    assert dotted.get('hello', '|add: world') == 'hello world'


# chained transforms

def test_chain_multiple():
    assert dotted.get('  42  ', '|strip|int') == 42
    assert dotted.get(3.14159, '|str:%.2f|uppercase') == '3.14'


def test_chain_with_nested_data():
    d = {'user': {'age': '25'}}
    assert dotted.get(d, 'user.age|int|add:10') == 35


def test_chain_on_pattern():
    d = {'a': '1', 'b': '2', 'c': '3'}
    result = dotted.get(d, '*|int')
    assert result == (1, 2, 3)


# list transform

def test_list_basic():
    assert dotted.get('hello', '|list') == ['h', 'e', 'l', 'l', 'o']
    assert dotted.get((1, 2, 3), '|list') == [1, 2, 3]
    assert dotted.get({1, 2, 3}, '|list') == sorted(dotted.get({1, 2, 3}, '|list'))  # set order varies


def test_list_invalid_returns_original():
    assert dotted.get(123, '|list') == 123


def test_list_raises():
    with pytest.raises(TypeError):
        dotted.get(123, '|list:raises')


# tuple transform

def test_tuple_basic():
    assert dotted.get('hi', '|tuple') == ('h', 'i')
    assert dotted.get([1, 2, 3], '|tuple') == (1, 2, 3)


def test_tuple_invalid_returns_original():
    assert dotted.get(123, '|tuple') == 123


def test_tuple_raises():
    with pytest.raises(TypeError):
        dotted.get(123, '|tuple:raises')


# set transform

def test_set_basic():
    assert dotted.get([1, 2, 2, 3], '|set') == {1, 2, 3}
    assert dotted.get('aab', '|set') == {'a', 'b'}


def test_set_invalid_returns_original():
    assert dotted.get(123, '|set') == 123


def test_set_raises():
    with pytest.raises(TypeError):
        dotted.get(123, '|set:raises')
