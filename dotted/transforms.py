"""
Default transforms
"""
import decimal
from .elements import transform


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
def transform_add(val, rhs):
    """
    Add rhs to val
        <dotted>|add:<rhs>          add <rhs> to value
    """
    return val + rhs


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
