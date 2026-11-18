"""
A DataFrame of every kind of column, for the sparkize tests that check
it against dotted, and Python's view of what Spark gives back. pyspark
is imported where it's used: doctest collection imports this module.
"""
import datetime
import decimal
import math


D = decimal.Decimal

SCHEMA = ', '.join([
    'id INT', 's STRING', 'i INT', 'l BIGINT', 'd DOUBLE', 'f FLOAT', 'b BOOLEAN',
    'dec DECIMAL(10,2)', 'dt DATE',
    'arr ARRAY<INT>', 'sarr ARRAY<STRING>', 'darr ARRAY<DOUBLE>',
    'm MAP<STRING,INT>', 'mi MAP<INT,STRING>',
    'st STRUCT<x: INT, y: STRING, z: ARRAY<INT>>',
    'ast ARRAY<STRUCT<x: INT, y: STRING>>',
    'mm MAP<STRING, STRUCT<x: INT, role: STRING>>',
    'nest STRUCT<a: STRUCT<role: STRING, k: INT>, b: MAP<STRING, STRUCT<role: STRING, k: INT>>>',
])

ROWS = [
    (1, 'active', 1, 1, 1.0, 1.5, True, D('1.00'), datetime.date(2024, 1, 2),
     [1, 2, 3], ['a', 'b'], [1.0, float('nan'), 2.5],
     {'k': 1, 'm': 2}, {0: 'zero', 1: 'one'},
     {'x': 1, 'y': 'u', 'z': [1]},
     [{'x': 1, 'y': 'a'}, {'x': 2, 'y': None}, None],
     {'p': {'x': 1, 'role': 'admin'}, 'q': {'x': 2, 'role': 'user'}},
     {'a': {'role': 'admin', 'k': 1}, 'b': {'z': {'role': 'user', 'k': 2}}}),
    (2, '30', 30, 2 ** 40, 0.1, 0.1, False, D('0.10'), None,
     [], [], [],
     {}, {},
     {'x': None, 'y': None, 'z': None},
     [],
     {},
     {'a': None, 'b': {}}),
    (3, None, None, None, None, None, None, None, None,
     None, None, None, None, None, None, None, None, None),
    (4, 'xyz', -5, -(2 ** 62), 1e16, float('inf'), True, D('-3.25'), datetime.date(1999, 12, 31),
     [5, None], ['', 'x'], [1e-5, 123456789.0],
     {'k': None}, {5: None},
     {'x': 5, 'y': 'xyz', 'z': [5, 6]},
     [{'x': 5, 'y': 'xyz'}],
     {'r': None},
     {'a': {'role': None, 'k': None}, 'b': {'y': None}}),
    (5, ' 7 ', 7, 7, 7.0, -0.0, None, D('7.00'), None,
     [7], ['7', '1_000'], [7.0],
     {'7': 7}, {7: '7'},
     None,
     [{'x': 7, 'y': '7'}, {'x': 1, 'y': 'a'}],
     {'s': {'x': 7, 'role': 'admin'}},
     None),
]

def same(a, b):
    """
    Python's ==, types and NaN told apart as dotted's values are.
    """
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def py(value):
    """
    A selected Spark value as Python sees a row's.
    """
    from pyspark.sql import Row
    if isinstance(value, Row):
        return {k: py(v) for k, v in value.asDict().items()}
    if isinstance(value, list):
        return [py(v) for v in value]
    if isinstance(value, dict):
        return {k: py(v) for k, v in value.items()}
    return value


def fits(value, dtype):
    """
    True if a value of the Spark type `dtype` can be the Python `value`,
    as asDict hands it back.
    """
    from pyspark.sql import types as T
    if value is None:
        return True
    if isinstance(dtype, T.StructType):
        names = [f.name for f in dtype.fields]
        return (isinstance(value, dict) and len(value) == len(names) and set(value) == set(names)
                and all(fits(value[f.name], f.dataType) for f in dtype.fields))
    if isinstance(dtype, T.MapType):
        return isinstance(value, dict) and all(
            fits(k, dtype.keyType) and fits(v, dtype.valueType) for k, v in value.items())
    if isinstance(dtype, T.ArrayType):
        return isinstance(value, list) and all(fits(v, dtype.elementType) for v in value)
    if isinstance(dtype, T.StringType):
        return isinstance(value, str)
    if isinstance(dtype, T.BooleanType):
        return isinstance(value, bool)
    if isinstance(dtype, (T.ByteType, T.ShortType, T.IntegerType, T.LongType)):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(dtype, (T.FloatType, T.DoubleType)):
        return isinstance(value, float)
    if isinstance(dtype, T.DecimalType):
        return isinstance(value, decimal.Decimal)
    if isinstance(dtype, T.DateType):
        return isinstance(value, datetime.date)
    return False
