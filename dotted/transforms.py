"""
Default transforms
"""
import decimal
from .elements import transform

@transform('str')
def transform_str(val, fmt=None, mode=None):
    """
    Transform to string with optional str format notation
      <dotted>|str                  str(val)
      <dotted>|str:<fmt>            <fmt> % val
      <dotted>|str:<fmt>:force      <fmt> % val or raises
    """
    try:
        if not fmt:
            return str(val)
        return fmt % val
    except TypeError:
        if mode == 'force':
            raise
    return val

@transform('int')
def transform_int(val, base=None, mode=None):
    """
    Transform to an int with optional base notation
        <dotted>|int                int(val)
        <dotted>|int:<base>         int(val, base=<base>)
        <dotted>|int::force         int(val) or raises
    """
    try:
        return int(val, base=base or 10)
    except (ValueError, TypeError):
        if mode == 'force':
            raise
    return val

@transform('float')
def transform_float(val, mode=None):
    """
    Transform to a float
        <dotted>|float              float(val)
        <dotted>|float:force        float(val) or raises
    """
    try:
        return float(val)
    except ValueError:
        if node == 'force':
            raise
    return val

@transform('decimal')
def transform_decimal(val, mode=None):
    """
    Transform to a decimal
        <dotted>|decimal            Decimal(val)
        <dotted>|decimal:force      Decimal(val) or raises
    """
    try:
        return decimal.Decimal(val)
    except decimal.InvalidOperation:
        if mode == 'force':
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
def transform_strip(val, chars=None, mode=None):
    """
    Strip val of chars
        <dotted>|strip              val.strip()
        <dotted>|strip:abc          val.strip('abc')
        <dotted>|strip::force       val.strip() or raises
    """
    try:
        return val.strip(chars or None)
    except AttributeError:
        if mode == 'force':
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
def transform_lowercase(val, mode=None):
    """
    Convert to lowercase
        <dotted>|lowercase          string to lowercase
        <dotted>|lowercase:force    string to lowercase or raises
    """
    try:
        return val.lower()
    except TypeError:
        if mode == 'force':
            raise
    return val

@transform('uppercase')
def transform_uppercase(val, mode=None):
    """
    Convert to uppercase
        <dotted>|uppercase          string to uppercase
        <dotted>|uppercase:force    string to uppercase or raises
    """
    try:
        return val.upper()
    except TypeError:
        if mode == 'force':
            raise
    return val

@transform('add')
def transform_add(val, rhs):
    """
    Add rhs to val
        <dotted>|add:<rhs>          add <rhs> to value
    """
    return val + rhs
