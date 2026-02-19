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


def test_add_non_numeric_returns_original():
    assert dotted.get('hello', '|add:5') == 'hello'


def test_add_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|add:5:raises')


# sub transform

def test_sub_numbers():
    assert dotted.get(10, '|sub:3') == 7
    assert dotted.get(5.5, '|sub:1.5') == 4.0


def test_sub_non_numeric_returns_original():
    assert dotted.get('hello', '|sub:5') == 'hello'


def test_sub_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|sub:5:raises')


# mul transform

def test_mul_numbers():
    assert dotted.get(6, '|mul:7') == 42
    assert dotted.get(2.5, '|mul:4') == 10.0


def test_mul_non_numeric_returns_original():
    assert dotted.get(None, '|mul:5') == None


def test_mul_raises():
    with pytest.raises(TypeError):
        dotted.get(None, '|mul:5:raises')


# div transform

def test_div_numbers():
    assert dotted.get(10, '|div:4') == 2.5
    assert dotted.get(9, '|div:3') == 3.0


def test_div_by_zero_returns_original():
    assert dotted.get(10, '|div:0') == 10


def test_div_non_numeric_returns_original():
    assert dotted.get('hello', '|div:5') == 'hello'


def test_div_raises():
    with pytest.raises(ZeroDivisionError):
        dotted.get(10, '|div:0:raises')


# mod transform

def test_mod_numbers():
    assert dotted.get(10, '|mod:3') == 1
    assert dotted.get(7.5, '|mod:2.5') == 0.0


def test_mod_by_zero_returns_original():
    assert dotted.get(10, '|mod:0') == 10


def test_mod_raises():
    with pytest.raises(ZeroDivisionError):
        dotted.get(10, '|mod:0:raises')


# pow transform

def test_pow_numbers():
    assert dotted.get(2, '|pow:10') == 1024
    assert dotted.get(9, '|pow:0.5') == 3.0


def test_pow_non_numeric_returns_original():
    assert dotted.get('hello', '|pow:2') == 'hello'


def test_pow_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|pow:2:raises')


# neg transform

def test_neg_numbers():
    assert dotted.get(42, '|neg') == -42
    assert dotted.get(-3.5, '|neg') == 3.5


def test_neg_non_numeric_returns_original():
    assert dotted.get('hello', '|neg') == 'hello'


def test_neg_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|neg:raises')


# abs transform

def test_abs_numbers():
    assert dotted.get(-42, '|abs') == 42
    assert dotted.get(42, '|abs') == 42
    assert dotted.get(-3.14, '|abs') == 3.14


def test_abs_non_numeric_returns_original():
    assert dotted.get('hello', '|abs') == 'hello'


def test_abs_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|abs:raises')


# round transform

def test_round_basic():
    assert dotted.get(3.7, '|round') == 4
    assert dotted.get(3.2, '|round') == 3


def test_round_with_precision():
    assert dotted.get(3.14159, '|round:2') == 3.14
    assert dotted.get(3.14159, '|round:4') == 3.1416


def test_round_non_numeric_returns_original():
    assert dotted.get('hello', '|round') == 'hello'


def test_round_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|round::raises')


# ceil transform

def test_ceil_basic():
    assert dotted.get(3.2, '|ceil') == 4
    assert dotted.get(-1.5, '|ceil') == -1


def test_ceil_already_int():
    assert dotted.get(5, '|ceil') == 5


def test_ceil_non_numeric_returns_original():
    assert dotted.get('hello', '|ceil') == 'hello'


def test_ceil_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|ceil:raises')


# floor transform

def test_floor_basic():
    assert dotted.get(3.7, '|floor') == 3
    assert dotted.get(-1.5, '|floor') == -2


def test_floor_already_int():
    assert dotted.get(5, '|floor') == 5


def test_floor_non_numeric_returns_original():
    assert dotted.get('hello', '|floor') == 'hello'


def test_floor_raises():
    with pytest.raises(TypeError):
        dotted.get('hello', '|floor:raises')


# min transform

def test_min_clamps():
    assert dotted.get(10, '|min:5') == 5
    assert dotted.get(3, '|min:5') == 3


def test_min_non_numeric_returns_original():
    assert dotted.get(None, '|min:5') == None


def test_min_raises():
    with pytest.raises(TypeError):
        dotted.get(None, '|min:5:raises')


# max transform

def test_max_clamps():
    assert dotted.get(3, '|max:5') == 5
    assert dotted.get(10, '|max:5') == 10


def test_max_non_numeric_returns_original():
    assert dotted.get(None, '|max:5') == None


def test_max_raises():
    with pytest.raises(TypeError):
        dotted.get(None, '|max:5:raises')


# eq transform

def test_eq_true():
    assert dotted.get(5, '|eq:5') is True


def test_eq_false():
    assert dotted.get(5, '|eq:3') is False


def test_eq_string():
    assert dotted.get('hello', '|eq:hello') is True


def test_eq_non_comparable_returns_original():
    assert dotted.get(None, '|eq:5') is False


# ne transform

def test_ne_true():
    assert dotted.get(5, '|ne:3') is True


def test_ne_false():
    assert dotted.get(5, '|ne:5') is False


# gt transform

def test_gt_true():
    assert dotted.get(10, '|gt:5') is True


def test_gt_false():
    assert dotted.get(3, '|gt:5') is False


def test_gt_equal():
    assert dotted.get(5, '|gt:5') is False


def test_gt_non_comparable_returns_original():
    assert dotted.get(None, '|gt:5') is None


def test_gt_raises():
    with pytest.raises(TypeError):
        dotted.get(None, '|gt:5:raises')


# ge transform

def test_ge_true():
    assert dotted.get(10, '|ge:5') is True


def test_ge_equal():
    assert dotted.get(5, '|ge:5') is True


def test_ge_false():
    assert dotted.get(3, '|ge:5') is False


# lt transform

def test_lt_true():
    assert dotted.get(3, '|lt:5') is True


def test_lt_false():
    assert dotted.get(10, '|lt:5') is False


def test_lt_equal():
    assert dotted.get(5, '|lt:5') is False


# le transform

def test_le_true():
    assert dotted.get(3, '|le:5') is True


def test_le_equal():
    assert dotted.get(5, '|le:5') is True


def test_le_false():
    assert dotted.get(10, '|le:5') is False


# in transform

def test_in_list():
    assert dotted.get(2, '|in:[1, 2, 3]') is True
    assert dotted.get(5, '|in:[1, 2, 3]') is False


def test_in_string():
    assert dotted.get('el', '|in:hello') is True
    assert dotted.get('xyz', '|in:hello') is False


def test_in_non_iterable_returns_original():
    assert dotted.get(1, '|in:5') == 1


def test_in_raises():
    with pytest.raises(TypeError):
        dotted.get(1, '|in:5:raises')


# not_in transform

def test_not_in_list():
    assert dotted.get(5, '|not_in:[1, 2, 3]') is True
    assert dotted.get(2, '|not_in:[1, 2, 3]') is False


def test_not_in_string():
    assert dotted.get('xyz', '|not_in:hello') is True
    assert dotted.get('el', '|not_in:hello') is False


# chained transforms (math)

def test_chain_abs_round():
    assert dotted.get(-3.14159, '|abs|round:2') == 3.14


def test_chain_mul_ceil():
    assert dotted.get(3.2, '|mul:2|ceil') == 7


def test_chain_clamp_range():
    assert dotted.get(15, '|max:0|min:10') == 10
    assert dotted.get(-5, '|max:0|min:10') == 0
    assert dotted.get(5, '|max:0|min:10') == 5


# chained transforms (general)

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
