"""
Default transforms
"""
import decimal
import math
from .results import Dotted


def transform(name):
    """
    Transform decorator
    """
    def _fn(fn):
        Dotted.register(name, fn)
        return fn
    return _fn


@transform('str')
def transform_str(val, fmt=None, *modes):
    """
    Transform to string with optional str format notation
      <dotted>|str                  str(val)
      <dotted>|str:<fmt>            <fmt> % val
      <dotted>|str:<fmt>:raises     <fmt> % val or raises
    """
    try:
        if not fmt:
            return str(val)
        return fmt % val
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('int')
def transform_int(val, base=None, *modes):
    """
    Transform to an int with optional base notation
        <dotted>|int                int(val)
        <dotted>|int:<base>         int(val, base=<base>)
        <dotted>|int::raises        int(val) or raises
    >>> import dotted
    >>> dotted.get('10', '|int')
    10
    >>> dotted.get('hello', '|int::raises')
    Traceback (most recent call last):
    ...
    ValueError: invalid literal for int() with base 10: 'hello'
    """
    try:
        if base is None:
            return int(val)
        return int(str(val), base=base or 10)
    except (ValueError, TypeError):
        if 'raises' in modes:
            raise
    return val


@transform('float')
def transform_float(val, *modes):
    """
    Transform to a float
        <dotted>|float              float(val)
        <dotted>|float:raises       float(val) or raises
    """
    try:
        return float(val)
    except ValueError:
        if 'raises' in modes:
            raise
    return val


@transform('decimal')
def transform_decimal(val, *modes):
    """
    Transform to a decimal
        <dotted>|decimal            Decimal(val)
        <dotted>|decimal:raises     Decimal(val) or raises
    >>> import dotted
    >>> dotted.get('10', '|decimal')
    Decimal('10')
    """
    try:
        return decimal.Decimal(val)
    except (TypeError, decimal.InvalidOperation):
        if 'raises' in modes:
            raise
    return val


@transform('none')
def transform_none(val, *none_vals):
    """
    Transform to None
       <dotted>|none                None if not val else val
       <dotted>|none::hello         None if val in ('', 'hello') else val
    """
    if not none_vals:
        return None if not val else val
    return None if val in none_vals else val


@transform('strip')
def transform_strip(val, chars=None, *modes):
    """
    Strip val of chars
        <dotted>|strip              val.strip()
        <dotted>|strip:abc          val.strip('abc')
        <dotted>|strip::raises      val.strip() or raises
    """
    try:
        return val.strip(chars or None)
    except AttributeError:
        if 'raises' in modes:
            raise
    return val


@transform('len')
def transform_len(val, default=None):
    """
    Calculate length
        <dotted>|len                len(val) or raises
        <dotted>|len:<default>      len(val) or <default>
    """
    try:
        return len(val)
    except TypeError:
        if default is not None:
            return default
        raise
    return val


@transform('lowercase')
def transform_lowercase(val, *modes):
    """
    Convert to lowercase
        <dotted>|lowercase          string to lowercase
        <dotted>|lowercase:raises   string to lowercase or raises
    """
    try:
        return val.lower()
    except AttributeError:
        if 'raises' in modes:
            raise
    return val


@transform('uppercase')
def transform_uppercase(val, *modes):
    """
    Convert to uppercase
        <dotted>|uppercase          string to uppercase
        <dotted>|uppercase:raises   string to uppercase or raises
    """
    try:
        return val.upper()
    except AttributeError:
        if 'raises' in modes:
            raise
    return val


@transform('add')
def transform_add(val, rhs, *modes):
    """
    Add rhs to val
        <dotted>|add:<rhs>          add <rhs> to value
        <dotted>|add:<rhs>:raises   add <rhs> to value or raises
    """
    try:
        return val + rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('sub')
def transform_sub(val, rhs, *modes):
    """
    Subtract rhs from val
        <dotted>|sub:<rhs>          subtract <rhs> from value
        <dotted>|sub:<rhs>:raises   subtract <rhs> from value or raises
    """
    try:
        return val - rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('mul')
def transform_mul(val, rhs, *modes):
    """
    Multiply val by rhs
        <dotted>|mul:<rhs>          multiply value by <rhs>
        <dotted>|mul:<rhs>:raises   multiply value by <rhs> or raises
    """
    try:
        return val * rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('div')
def transform_div(val, rhs, *modes):
    """
    Divide val by rhs
        <dotted>|div:<rhs>          divide value by <rhs>
        <dotted>|div:<rhs>:raises   divide value by <rhs> or raises
    """
    try:
        return val / rhs
    except (TypeError, ZeroDivisionError):
        if 'raises' in modes:
            raise
    return val


@transform('mod')
def transform_mod(val, rhs, *modes):
    """
    Modulo val by rhs
        <dotted>|mod:<rhs>          value modulo <rhs>
        <dotted>|mod:<rhs>:raises   value modulo <rhs> or raises
    """
    try:
        return val % rhs
    except (TypeError, ZeroDivisionError):
        if 'raises' in modes:
            raise
    return val


@transform('pow')
def transform_pow(val, rhs, *modes):
    """
    Raise val to the power of rhs
        <dotted>|pow:<rhs>          value ** <rhs>
        <dotted>|pow:<rhs>:raises   value ** <rhs> or raises
    """
    try:
        return val ** rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('neg')
def transform_neg(val, *modes):
    """
    Negate val
        <dotted>|neg                -value
        <dotted>|neg:raises         -value or raises
    """
    try:
        return -val
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('abs')
def transform_abs(val, *modes):
    """
    Absolute value
        <dotted>|abs                abs(value)
        <dotted>|abs:raises         abs(value) or raises
    """
    try:
        return abs(val)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('round')
def transform_round(val, ndigits=None, *modes):
    """
    Round val
        <dotted>|round              round(value)
        <dotted>|round:<n>          round(value, <n>)
        <dotted>|round::raises      round(value) or raises
    """
    try:
        if ndigits is None:
            return round(val)
        return round(val, ndigits)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('ceil')
def transform_ceil(val, *modes):
    """
    Ceiling of val
        <dotted>|ceil               math.ceil(value)
        <dotted>|ceil:raises        math.ceil(value) or raises
    """
    try:
        return math.ceil(val)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('floor')
def transform_floor(val, *modes):
    """
    Floor of val
        <dotted>|floor              math.floor(value)
        <dotted>|floor:raises       math.floor(value) or raises
    """
    try:
        return math.floor(val)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('min')
def transform_min(val, bound, *modes):
    """
    Clamp val to upper bound
        <dotted>|min:<bound>          min(value, <bound>)
        <dotted>|min:<bound>:raises   min(value, <bound>) or raises
    """
    try:
        return min(val, bound)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('max')
def transform_max(val, bound, *modes):
    """
    Clamp val to lower bound
        <dotted>|max:<bound>          max(value, <bound>)
        <dotted>|max:<bound>:raises   max(value, <bound>) or raises
    """
    try:
        return max(val, bound)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('eq')
def transform_eq(val, rhs, *modes):
    """
    Equal comparison
        <dotted>|eq:<rhs>           value == <rhs>
        <dotted>|eq:<rhs>:raises    value == <rhs> or raises
    """
    try:
        return val == rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('ne')
def transform_ne(val, rhs, *modes):
    """
    Not-equal comparison
        <dotted>|ne:<rhs>           value != <rhs>
        <dotted>|ne:<rhs>:raises    value != <rhs> or raises
    """
    try:
        return val != rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('gt')
def transform_gt(val, rhs, *modes):
    """
    Greater-than comparison
        <dotted>|gt:<rhs>           value > <rhs>
        <dotted>|gt:<rhs>:raises    value > <rhs> or raises
    """
    try:
        return val > rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('ge')
def transform_ge(val, rhs, *modes):
    """
    Greater-than-or-equal comparison
        <dotted>|ge:<rhs>           value >= <rhs>
        <dotted>|ge:<rhs>:raises    value >= <rhs> or raises
    """
    try:
        return val >= rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('lt')
def transform_lt(val, rhs, *modes):
    """
    Less-than comparison
        <dotted>|lt:<rhs>           value < <rhs>
        <dotted>|lt:<rhs>:raises    value < <rhs> or raises
    """
    try:
        return val < rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('le')
def transform_le(val, rhs, *modes):
    """
    Less-than-or-equal comparison
        <dotted>|le:<rhs>           value <= <rhs>
        <dotted>|le:<rhs>:raises    value <= <rhs> or raises
    """
    try:
        return val <= rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('in')
def transform_in(val, rhs, *modes):
    """
    Membership test
        <dotted>|in:<rhs>           value in <rhs>
        <dotted>|in:<rhs>:raises    value in <rhs> or raises
    """
    try:
        return val in rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('not_in')
def transform_not_in(val, rhs, *modes):
    """
    Negative membership test
        <dotted>|not_in:<rhs>           value not in <rhs>
        <dotted>|not_in:<rhs>:raises    value not in <rhs> or raises
    """
    try:
        return val not in rhs
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('list')
def transform_list(val, *modes):
    """
    Transform to list
        <dotted>|list               list(val)
        <dotted>|list:raises        list(val) or raises
    """
    try:
        return list(val)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('tuple')
def transform_tuple(val, *modes):
    """
    Transform to tuple
        <dotted>|tuple              tuple(val)
        <dotted>|tuple:raises       tuple(val) or raises
    """
    try:
        return tuple(val)
    except TypeError:
        if 'raises' in modes:
            raise
    return val


@transform('set')
def transform_set(val, *modes):
    """
    Transform to set
        <dotted>|set                set(val)
        <dotted>|set:raises         set(val) or raises
    """
    try:
        return set(val)
    except TypeError:
        if 'raises' in modes:
            raise
    return val
