"""
PySpark flavor for dotted.translators.

Defines the `PySparkMixin` — the flavor's hooks for the neutral
`Translator` walk — and `PySparkResolver`, registered with `.spark`
via `@flavor('pyspark')`.

A path reads as dotted reads it. Columns are native Spark types, and
a step reads a node by its type, as dotted reads a Python value by its
own: a struct and a map as a dict, an array as a list, as
`Row.asDict(recursive=True)` hands them to Python. The DataFrame's
schema, which `build` takes, says what each node is. Comparisons,
regexes and transforms go as Python's do.

Translation makes steps; `build` reads them against the schema and
makes the Columns, through `pyspark.sql.functions` and Column methods
only, so Spark Connect sessions work as classic ones do.
"""
import datetime
import decimal
import fractions
import functools
import math
import operator
import re
import sys

from . import core
from .core import Hooks, TranslationError, Translator
from .spark import ColumnFragment, SparkResolver, flavor, _merged


def _functions():
    """
    `pyspark.sql.functions`, imported on first use so that dotted
    imports without pyspark installed.
    """
    from pyspark.sql import functions
    return functions


def _types():
    """
    `pyspark.sql.types`, imported on first use.
    """
    from pyspark.sql import types
    return types


def _quoted(name):
    """
    A column name backticked for `col`, so Spark never splits it on
    dots.
    """
    return '`' + name.replace('`', '``') + '`'


# ---- What a node is -------------------------------------------------

_NUMERIC = ('integral', 'boolean', 'fractional', 'decimal')

# bits of each integral type
_INTEGRAL_BITS = {'ByteType': 8, 'ShortType': 16, 'IntegerType': 32, 'LongType': 64}


def _classes(T, *names):
    """
    The classes of `pyspark.sql.types` named, those this pyspark has.
    """
    return tuple(getattr(T, name) for name in names if hasattr(T, name))


def _kind(dtype):
    """
    What a value of the Spark type `dtype` is to dotted: 'struct' and
    'map' (a dict), 'array' (a list), 'string', 'integral', 'boolean',
    'fractional', 'decimal', 'null' (None), 'binary' (a bytearray),
    'date', 'timestamp', or 'other'.
    """
    T = _types()
    if isinstance(dtype, T.StructType):
        return 'struct'
    if isinstance(dtype, T.MapType):
        return 'map'
    if isinstance(dtype, T.ArrayType):
        return 'array'
    if isinstance(dtype, _classes(T, 'StringType', 'CharType', 'VarcharType')):
        return 'string'
    if type(dtype).__name__ in _INTEGRAL_BITS:
        return 'integral'
    if isinstance(dtype, T.BooleanType):
        return 'boolean'
    if isinstance(dtype, (T.FloatType, T.DoubleType)):
        return 'fractional'
    if isinstance(dtype, T.DecimalType):
        return 'decimal'
    if isinstance(dtype, T.NullType):
        return 'null'
    if isinstance(dtype, T.BinaryType):
        return 'binary'
    if isinstance(dtype, T.DateType):
        return 'date'
    if isinstance(dtype, _classes(T, 'TimestampType', 'TimestampNTZType')):
        return 'timestamp'
    return 'other'


# ---- Static truth ---------------------------------------------------
#
# A test is True, False, or a Boolean Column that is never null, so `~`
# negates it as Python's `not` does.

def _and(*tests):
    """
    The tests all hold.
    """
    out = True
    for test in tests:
        if test is False:
            return False
        if test is True:
            continue
        out = test if out is True else out & test
    return out


def _or(*tests):
    """
    Some test holds.
    """
    out = False
    for test in tests:
        if test is True:
            return True
        if test is False:
            continue
        out = test if out is False else out | test
    return out


def _not(test):
    """
    The test fails.
    """
    if isinstance(test, bool):
        return not test
    return ~test


def _cond(cond):
    """
    A part's condition as a test: None holds always.
    """
    return True if cond is None else cond


def _lit(test):
    """
    The test as a Boolean Column.
    """
    if isinstance(test, bool):
        return _functions().lit(test)
    return test


def _fail(what):
    """
    A Column that fails the job when Spark reads it, where dotted raises
    or Spark can't give what dotted would: `dotted: <what>`. It is made
    in a lambda, which subexpression elimination doesn't read into, so
    it is never read ahead of the test that guards it: that pass reads
    both sides of an `AND` or `OR`.
    """
    F = _functions()
    return F.get(F.transform(F.array(F.lit(0)), lambda v: F.raise_error(f'dotted: {what}')), 0)


def _failing(what):
    """
    A test that fails the job when it's read (`_fail`).
    """
    return _fail(what).isNull()


def _failing_where(cond, what):
    """
    A test that fails the job where `cond` holds, else false: a `CASE`,
    as subexpression elimination reads no branch but the first test
    ahead.
    """
    if cond is False:
        return False
    if cond is True:
        return _failing(what)
    return _functions().when(cond, _failing(what)).otherwise(_functions().lit(False))


# ---- Nodes ------------------------------------------------------------

class _Node:
    """
    A node a path reaches: its value `col`, a Column of the Spark type
    `dtype`, null where the value is None; where a soft cut or a
    negation needs it, its `path` from the node they are at, an
    array<string> Column, else None; and `there`, the test that the path
    reaches it, from the column on, which a failure there holds to (see
    `_Fail`). An element a lambda reads is there.
    """

    __slots__ = ('col', 'dtype', 'path', 'there')

    def __init__(self, col, dtype, path=None, there=True):
        self.col = col
        self.dtype = dtype
        self.path = path
        self.there = there

    @property
    def kind(self):
        return _kind(self.dtype)

    def child(self, col, dtype, segment, there=None):
        """
        A node under this one, at `segment`, a string Column: there where
        this one is, unless `there` says.
        """
        there = self.there if there is None else there
        if self.path is None:
            return _Node(col, dtype, None, there)
        F = _functions()
        return _Node(col, dtype, F.concat(self.path, F.array(segment)), there)

    def element(self, col, dtype, segment):
        """
        An element of this node a lambda reads, at `segment`.
        """
        return self.child(col, dtype, segment, True)

    def within(self, cond):
        """
        This node, there only where `cond` holds too.
        """
        if cond is None or cond is True:
            return self
        return _Node(self.col, self.dtype, self.path, _and(self.there, cond))

    def like(self, col, dtype):
        """
        Another value at this node's place.
        """
        return _Node(col, dtype, self.path, self.there)

    def bare(self):
        """
        This node without its path.
        """
        return self if self.path is None else _Node(self.col, self.dtype, None, self.there)

    def rooted(self):
        """
        This node with a path: its own, or an empty one from itself.
        """
        if self.path is not None:
            return self
        return _Node(self.col, self.dtype, _functions().array().cast('array<string>'), self.there)


class _One:
    """
    A node a step reaches, there where `cond` holds (None: always).
    """

    __slots__ = ('cond', 'node')

    def __init__(self, cond, node, within=True):
        self.cond = cond
        self.node = node.within(cond) if within else node


class _Many:
    """
    Nodes a step reaches over the array Column `arr`, never null:
    `make(x, i)` gives the parts its element `x`, at index `i`, holds.
    """

    __slots__ = ('arr', 'make')

    def __init__(self, arr, make):
        self.arr = arr
        self.make = make


class _Fail:
    """
    Where `cond` holds (None: always) at `node`, there, dotted raises, or
    Spark can't give what dotted would: reading there fails the job
    (`_fail`). A part of its own, so no static test folds it away; its
    test holds to where the node is there, so a copy Spark reads ahead
    of the tests that guard it fails only where dotted does.
    """

    __slots__ = ('cond', 'what')

    def __init__(self, node, cond, what):
        self.cond = _and(_cond(node.there), _cond(cond))
        self.what = what


def _guarded(cond, part):
    """
    `part`, there only where `cond` holds too.
    """
    if cond is True:
        return part
    if isinstance(part, _One):
        return _One(_and(cond, _cond(part.cond)), part.node.within(cond), within=False)
    if isinstance(part, _Fail):
        guarded = _Fail.__new__(_Fail)
        guarded.cond = _and(cond, _cond(part.cond))
        guarded.what = part.what
        return guarded
    return _Many(_functions().filter(part.arr, lambda x: _lit(cond)), part.make)


# ---- Matches ----------------------------------------------------------

class _Found:
    """
    The matches a path reaches, in dotted's order: the array Column
    `arr`, never null, each element a match as `mode` reads it ('value'
    its value, 'node' a (path, value) struct, 'path' its path alone),
    the values of the Spark type `dtype` (None for 'path').
    """

    __slots__ = ('arr', 'dtype', 'mode')

    def __init__(self, arr, dtype, mode):
        self.arr = arr
        self.dtype = dtype
        self.mode = mode


def _elem_type(dtype, mode):
    """
    The Spark type of a match read as `mode`.
    """
    T = _types()
    if mode == 'path':
        return T.ArrayType(T.StringType())
    if mode == 'value':
        return dtype
    return T.StructType([T.StructField('path', T.ArrayType(T.StringType())),
                         T.StructField('value', dtype)])


def _empty_of(dtype):
    """
    An empty array Column of the Spark type `dtype`'s elements.
    """
    return _functions().array().cast(_types().ArrayType(dtype))


def _empty(dtype, mode):
    """
    No matches, read as `mode`.
    """
    return _empty_of(_elem_type(dtype, mode))


def _leaf(node, mode):
    """
    The node as a match read as `mode`.
    """
    F = _functions()
    if mode == 'value':
        return node.col
    if mode == 'path':
        return node.path
    return F.struct(node.path.alias('path'), node.col.alias('value'))


def _single(node, mode):
    """
    The node as the one match.
    """
    return _Found(_functions().array(_leaf(node, mode)), node.dtype, mode)


def _when(cond, found):
    """
    `found` where `cond` holds, else no matches.
    """
    if found is None or cond is False:
        return None
    if cond is None or cond is True:
        return found
    F = _functions()
    return _Found(F.when(cond, found.arr).otherwise(_empty(found.dtype, found.mode)),
                  found.dtype, found.mode)


def _shape(dtype):
    """
    `dtype` with every nullable flag set, to compare types by.
    """
    T = _types()
    if isinstance(dtype, T.StructType):
        return T.StructType([T.StructField(f.name, _shape(f.dataType)) for f in dtype.fields])
    if isinstance(dtype, T.ArrayType):
        return T.ArrayType(_shape(dtype.elementType))
    if isinstance(dtype, T.MapType):
        return T.MapType(_shape(dtype.keyType), _shape(dtype.valueType))
    return dtype


def _common(dtypes):
    """
    The one Spark type matches of `dtypes` are read as, the values they
    are to Python kept: ints as a long, floats as a double. Matches of
    other types can't share a Spark array: that raises.
    """
    T = _types()
    shapes = []
    for dtype in dtypes:
        shape = _shape(dtype)
        if shape not in shapes:
            shapes.append(shape)
    real = [s for s in shapes if _kind(s) != 'null']
    if not real:
        return shapes[0]
    if len(real) == 1:
        return real[0]
    kinds = {_kind(s) for s in real}
    if kinds == {'integral'}:
        return T.LongType()
    if kinds == {'fractional'}:
        return T.DoubleType()
    if kinds == {'string'}:
        return T.StringType()
    names = ', '.join(s.simpleString() for s in real)
    raise TranslationError(
        f'matches of more than one type ({names}): a Spark array holds one'
    )


def _cast(found, dtype):
    """
    `found` with its values read as the Spark type `dtype`.
    """
    if found.mode == 'path' or _shape(found.dtype) == _shape(dtype):
        return found
    T = _types()
    return _Found(found.arr.cast(T.ArrayType(_elem_type(dtype, found.mode))), dtype, found.mode)


def _concat(founds):
    """
    The matches of `founds` in turn; None where there are none.
    """
    founds = [found for found in founds if found is not None]
    if not founds:
        return None
    if len(founds) == 1:
        return founds[0]
    mode = founds[0].mode
    if mode != 'path':
        dtype = _common([found.dtype for found in founds])
        founds = [_cast(found, dtype) for found in founds]
    return _Found(_functions().concat(*(found.arr for found in founds)), founds[0].dtype, mode)


def _case(pairs):
    """
    The matches of the first of `pairs`, (test, found), whose test
    holds; none where none does. A found of None is no matches.
    """
    real = [found for _, found in pairs if found is not None]
    if not real:
        return None
    mode = real[0].mode
    dtype = None if mode == 'path' else _common([found.dtype for found in real])
    empty = _empty(dtype, mode)
    out = None
    for test, found in pairs:
        if test is False:
            continue
        arr = empty if found is None else _cast(found, dtype).arr
        if test is True:
            return _Found(arr if out is None else out.otherwise(arr), dtype, mode)
        out = _functions().when(test, arr) if out is None else out.when(test, arr)
    if out is None:
        return None
    return _Found(out.otherwise(empty), dtype, mode)


def _test_case(pairs, otherwise):
    """
    The test of the first of `pairs`, (test, outcome), whose test
    holds; `otherwise` where none does.
    """
    out = None
    for test, outcome in pairs:
        if test is False:
            continue
        if test is True:
            return outcome if out is None else out.otherwise(_lit(outcome))
        F = _functions()
        out = F.when(test, _lit(outcome)) if out is None else out.when(test, _lit(outcome))
    if out is None:
        return otherwise
    return out.otherwise(_lit(otherwise))


# ---- Reaching parts --------------------------------------------------

def _parts_exist(parts, fn):
    """
    The test that some node of `parts` passes `fn`, a test of a node.
    """
    return _or(*(_part_exists(part, fn) for part in parts))


def _part_exists(part, fn):
    """
    `_parts_exist` for one part.
    """
    if isinstance(part, _One):
        return _and(_cond(part.cond), fn(part.node))
    if isinstance(part, _Fail):
        return _failing_where(part.cond, part.what)
    F = _functions()
    seen = []

    def held(x, i):
        test = _parts_exist(part.make(x, i), fn)
        seen.append(test)
        return _lit(test)
    kept = F.filter(part.arr, held)
    if seen[0] is False:
        return False
    if seen[0] is True:
        return F.size(part.arr) > 0
    return F.size(kept) > 0


def _parts_collect(parts, fn, mode):
    """
    The matches `fn` gives from each node of `parts`, in turn, read as
    `mode`.
    """
    return _concat([_part_collect(part, fn, mode) for part in parts])


def _part_collect(part, fn, mode):
    """
    `_parts_collect` for one part.
    """
    if isinstance(part, _One):
        return _when(part.cond, fn(part.node))
    F = _functions()
    if isinstance(part, _Fail):
        if part.cond is False:
            return None
        T = _types()
        arr = F.when(_lit(_cond(part.cond)), _fail(part.what)).otherwise(F.array())
        if mode == 'path':
            arr = arr.cast(T.ArrayType(T.ArrayType(T.StringType())))
        return _Found(arr, T.NullType(), mode)
    seen = []

    def each(x, i):
        found = _parts_collect(part.make(x, i), fn, mode)
        seen.append(found)
        return F.array() if found is None else found.arr
    arr = F.transform(part.arr, each)
    found = seen[0]
    if found is None:
        return None
    return _Found(F.flatten(arr), found.dtype, found.mode)


def _segment_of(value):
    """
    A path segment, a string Column, for a literal key or index.
    """
    return _functions().lit(value).cast('string')


def _field(node, f):
    """
    The struct node's field `f` as a node.
    """
    return node.child(node.col.getField(f.name), f.dataType, _segment_of(f.name))


def _fields(node):
    """
    The struct node's fields, each a part.
    """
    there = node.col.isNotNull()
    return [_One(there, _field(node, f)) for f in node.dtype.fields]


def _is_number(value):
    """
    True for a Python int, float, bool or Decimal.
    """
    return isinstance(value, (int, float, decimal.Decimal))


_NO_KEY = object()


def _as_float32(value):
    """
    The float `value` if a float32 holds it exactly, else None.
    """
    import struct
    try:
        return value if struct.unpack('f', struct.pack('f', value))[0] == value else None
    except OverflowError:
        return None


def _key_literal(ktype, key):
    """
    The literal Column of the Spark type `ktype` a map's key equal to
    the Python `key` holds, as dict lookup finds keys: by `==`, a bool
    as an int. `_NO_KEY` where no key of the type is equal.
    """
    F = _functions()
    kind = _kind(ktype)
    if kind == 'string':
        return F.lit(key).cast(ktype) if isinstance(key, str) else _NO_KEY
    if kind not in _NUMERIC or not _is_number(key):
        return _NO_KEY
    q = _exact(key)
    if isinstance(q, str):
        return _NO_KEY
    if isinstance(q, float):
        return F.lit(q).cast(ktype) if kind == 'fractional' else _NO_KEY
    if kind == 'boolean':
        return F.lit(q == 1).cast(ktype) if q in (0, 1) else _NO_KEY
    if kind == 'integral':
        lo, hi = _int_range(ktype)
        ok = q.denominator == 1 and lo <= q <= hi
        return F.lit(int(q)).cast(ktype) if ok else _NO_KEY
    if kind == 'fractional':
        try:
            d = float(q)
        except OverflowError:
            return _NO_KEY
        if fractions.Fraction(d) != q:
            return _NO_KEY
        if type(ktype).__name__ == 'FloatType' and _as_float32(d) is None:
            return _NO_KEY
        return F.lit(d).cast(ktype)
    lo, hi, step = _decimal_grid(ktype)
    if (q / step).denominator != 1 or not lo <= q <= hi:
        return _NO_KEY
    return F.lit(_decimal_of(q, ktype.scale)).cast(ktype)


def _members(node, key):
    """
    The dict node's member at the Python `key`: a struct's field named
    it, a map's value at a key equal to it.
    """
    kind = node.kind
    if kind == 'struct':
        if not isinstance(key, str):
            return []
        return [_One(node.col.isNotNull(), _field(node, f))
                for f in node.dtype.fields if f.name == key][:1]
    if kind != 'map':
        return []
    k = _key_literal(node.dtype.keyType, key)
    if k is _NO_KEY:
        return []
    F = _functions()
    there = _and(node.col.isNotNull(), F.array_contains(F.map_keys(node.col), k))
    return [_One(there, node.child(node.col[k], node.dtype.valueType, k.cast('string')))]


def _at_index(node, at):
    """
    The list or string node's element at `at`, a 0-based int Column.
    """
    F = _functions()
    if node.kind == 'array':
        return node.child(F.get(node.col, at), node.dtype.elementType, at.cast('string'))
    return node.child(node.col.substr(at + 1, F.lit(1)), _types().StringType(),
                      at.cast('string'))


def _size(node):
    """
    The list or string node's length, a Column.
    """
    F = _functions()
    return F.size(node.col) if node.kind == 'array' else F.length(node.col)


def _indexed(node, key):
    """
    The list or string node's element at the Python int `key`, negative
    from the end.
    """
    if isinstance(key, bool):
        key = int(key)
    if not isinstance(key, int):
        return []
    F = _functions()
    size = _size(node)
    if key >= 0:
        return [_One(_and(node.col.isNotNull(), size > key), _at_index(node, F.lit(key)))]
    return [_One(_and(node.col.isNotNull(), size >= -key), _at_index(node, size + key))]


def _same_family(a, b):
    """
    True if values of the kinds `a` and `b` can be equal: strings, or
    numbers (a bool among them).
    """
    if a == 'string' or b == 'string':
        return a == b
    return a in _NUMERIC and b in _NUMERIC


def _by_node(node, key):
    """
    The node's member or element at the key another node holds, read
    where both are there: a reference's.
    """
    F = _functions()
    kind = node.kind
    there = _and(node.col.isNotNull(), key.col.isNotNull())
    if kind == 'struct':
        if key.kind != 'string':
            return []
        return [_One(_and(there, key.col == f.name), _field(node, f)) for f in node.dtype.fields]
    if kind == 'map':
        ktype = node.dtype.keyType
        if not _same_family(_kind(ktype), key.kind):
            return []
        k = key.col.cast(ktype)
        cond = _and(there, k == key.col, F.array_contains(F.map_keys(node.col), k))
        return [_One(cond, node.child(node.col[k], node.dtype.valueType, k.cast('string')))]
    if kind not in ('array', 'string') or key.kind not in ('integral', 'boolean'):
        return []
    i = key.col.cast('long')
    size = _size(node)
    inside = ((i >= 0) & (i < size)) | ((i < 0) & (-i <= size))
    return [_One(_and(there, inside), _at_index(node, F.when(i >= 0, i).otherwise(size + i)))]


def _got(node, key):
    """
    What a key or slot reads at the node, `key` a Python value or a
    node: a dict's member equal to it, a list's element or a string's
    character at an int.
    """
    if node.kind == 'binary':
        raise TranslationError('reading into a binary value not supported in sparkize')
    if isinstance(key, _Node):
        return _by_node(node, key)
    if node.kind in ('struct', 'map'):
        return _members(node, key)
    if node.kind in ('array', 'string'):
        return _indexed(node, key)
    return []


def _map_values(node):
    """
    The map node's values, a part.
    """
    F = _functions()
    T = _types()
    vt = node.dtype.valueType
    if node.path is None:
        arr = F.coalesce(F.map_values(node.col), _empty_of(vt))
        return _Many(arr, lambda x, i: [_One(None, _Node(x, vt))])
    entry = T.StructType([T.StructField('key', node.dtype.keyType, False),
                          T.StructField('value', vt)])
    arr = F.coalesce(F.map_entries(node.col), _empty_of(entry))
    return _Many(arr, lambda e, i: [_One(None, node.element(e['value'], vt, e['key'].cast('string')))])


def _elements(node):
    """
    The list node's elements, a part.
    """
    et = node.dtype.elementType
    arr = _functions().coalesce(node.col, _empty_of(et))
    return _Many(arr, lambda x, i: [_One(None, node.element(x, et, i.cast('string')))])


def _chars(node):
    """
    The string node's characters, a part.
    """
    F = _functions()
    n = F.length(node.col)
    at = F.when(node.col.isNotNull() & (n > 0), F.sequence(F.lit(0), n - 1)).otherwise(
        _empty_of(_types().IntegerType()))
    return _Many(at, lambda i, _: [_One(None, _at_index(node, i))])


def _each(node, how):
    """
    What a wildcard reads at the node: `.*` ('key') a dict's members,
    `[*]` ('slot') a dict's values, a list's elements or a string's
    characters.
    """
    kind = node.kind
    if kind == 'struct':
        return _fields(node)
    if kind == 'map':
        return [_map_values(node)]
    if how == 'key':
        return []
    if kind == 'array':
        return [_elements(node)]
    if kind == 'string':
        return [_chars(node)]
    if kind == 'binary':
        raise TranslationError('reading into a binary value not supported in sparkize')
    return []


def _descend(node):
    """
    What `**` reads at the node: every value under it by keys, through
    dicts only, each member before what is under it.
    """
    kind = node.kind
    if kind == 'struct':
        parts = []
        for one in _fields(node):
            parts.append(one)
            parts.extend(_guarded(one.cond, part) for part in _descend(one.node))
        return parts
    if kind != 'map':
        return []
    values = _map_values(node)
    make = values.make

    def under(x, i):
        out = []
        for one in make(x, i):
            out.append(one)
            out.extend(_descend(one.node))
        return out
    return [_Many(values.arr, under)]


def _bound(n, value, default, low, high):
    """
    A slice bound as Python's `slice.indices` reads it, against the
    length Column `n`.
    """
    F = _functions()
    if value is None:
        return default
    if value < 0:
        return F.greatest(n + value, low)
    return F.least(F.lit(value), high)


def _sliced(arr, n, start, stop, step):
    """
    The array Column `arr`, of length `n`, sliced as Python slices.
    """
    F = _functions()
    zero = F.lit(0)
    if step is None or step > 0:
        step = step or 1
        s = _bound(n, start, zero, zero, n)
        e = _bound(n, stop, n, zero, n)
        if step == 1:
            return F.filter(arr, lambda x, i: (i >= s) & (i < e))
        return F.filter(arr, lambda x, i: (i >= s) & (i < e) & (((i - s) % step) == 0))
    last = n - 1
    s = _bound(n, start, last, F.lit(-1), last)
    e = _bound(n, stop, F.lit(-1), F.lit(-1), last)
    return F.reverse(F.filter(arr, lambda x, i: (i <= s) & (i > e) & (((s - i) % -step) == 0)))


def _slice(node, start, stop, step):
    """
    What a slice reads at the node: a list's or string's slice, one
    value.
    """
    F = _functions()
    kind = node.kind
    segment = F.lit(f'{start}:{stop}:{step}')
    if kind == 'array':
        value = _sliced(node.col, F.size(node.col), start, stop, step)
        return [_One(node.col.isNotNull(), node.child(value, node.dtype, segment))]
    if kind != 'string':
        return []
    n = F.length(node.col)
    chars = F.transform(F.when(n > 0, F.sequence(F.lit(0), n - 1)).otherwise(
        _empty_of(_types().IntegerType())), lambda i: node.col.substr(i + 1, F.lit(1)))
    value = F.array_join(_sliced(chars, n, start, stop, step), '')
    return [_One(node.col.isNotNull(), node.child(value, node.dtype, segment))]


def _list_filtered(node, pred_fn, keeps_scalar, env):
    """
    What a slice filter reads at the node: a list's elements it keeps,
    one value. A dict iterates its keys, scalars, so it gives `{}` where
    the filters keep no scalar, and another dict Spark can't make where
    they do keep its keys. Iterating None or another scalar raises.
    """
    F = _functions()
    T = _types()
    kind = node.kind
    segment = F.lit('[]')
    none = _Fail(node, node.col.isNull(), 'a slice filter on None')
    there = node.col.isNotNull()
    if kind == 'array':
        et = node.dtype.elementType
        kept = F.filter(node.col, lambda x: _lit(pred_fn(_Node(x, et), env)))
        return [_One(there, node.child(kept, node.dtype, segment)), none]
    if kind in ('struct', 'map'):
        dtype = node.dtype if kind == 'map' else T.MapType(T.StringType(), T.StringType())
        empty = node.child(F.create_map().cast(dtype), dtype, segment)
        if not keeps_scalar:
            return [_One(there, empty), none]
        if kind == 'struct' and node.dtype.fields:
            raise TranslationError(
                "a slice filter that keeps a dict's keys gives a dict Spark can't make"
            )
        size = F.size(node.col)
        return [_One(_and(there, size == 0), empty), none,
                _Fail(node, _and(there, size > 0), "a slice filter that keeps a dict's keys")]
    if kind == 'string':
        # dotted gives a generator's repr: there, but nothing Spark can write
        garbage = node.child(_fail("a slice filter on a string, which dotted reads as a "
                                   "generator's repr"), T.NullType(), segment)
        return [_One(there, garbage), none]
    if kind == 'binary':
        raise TranslationError('a slice filter on a binary value not supported in sparkize')
    return [_Fail(node, None, 'a slice filter on a value that is not a list or dict')]


def _reach(node, step, env):
    """
    The parts `step`, one that reaches nodes, reaches from `node`.
    """
    kind = step[0]
    if kind == 'get':
        return _got(node, step[1](env))
    if kind == 'member':
        if node.kind not in ('struct', 'map'):
            return []
        return _members(node, step[1](env))
    if kind == 'each':
        return _each(node, step[1])
    if kind == 'descend':
        return _descend(node)
    if kind == 'slice':
        return _slice(node, *step[1:])
    if kind == 'listfilter':
        return _list_filtered(node, step[1], step[2], env)
    if kind == 'transform':
        return _transformed(node, step[1])
    if kind == 'not':
        return _complement(node, step, env)
    raise TranslationError(f'unknown step: {kind}')


# ---- Pattern steps --------------------------------------------------
#
# A path becomes a tuple of steps, read from its column against the
# schema when `build` makes the Column:
#   ('get', key_fn)    — a key's or slot's literal or reference: a dict's
#                        member equal to key_fn(env), a list's element or
#                        a string's character at it
#   ('member', key_fn) — a filter key's part: a dict's member only
#   ('each', how)      — a wildcard: 'key' (`.*`) a dict's members,
#                        'slot' (`[*]`) its values, a list's elements or
#                        a string's characters; ('each', how, 'aligned')
#                        for `[*+]`
#   ('descend',)       — `**`; ('descend', 'aligned') for `**+`
#   ('slice', start, stop, step)
#   ('listfilter', pred_fn, keeps_scalar) — a slice filter
#   ('transform', transforms)
#   ('cond', pred_fn)  — keep the node only where pred_fn(node, env)
#   ('group', kind, branches, cuts, softs)
#   ('first', branches)
#   ('not', steps, kinds)
#
# `_exists` reads whether steps reach anything, `_collect` the array of
# what they reach. A soft cut and a negation read matches with their
# paths (`_Found.mode`).

def _exists(node, steps, env):
    """
    The test that `steps` reach something from `node`.
    """
    if node is None:
        return False
    if not steps:
        return True
    step, rest = steps[0], steps[1:]
    kind = step[0]
    if kind == 'cond':
        return _and(step[1](node, env), _exists(node, rest, env))
    if kind == 'group':
        return _group_exists(node, step, rest, env)
    if kind == 'first':
        return _first_exists(node, step[1], rest, env)
    if step[-1] == 'aligned':
        # each node it reaches is a match where it passes its own filters
        conds = _leading_conds(rest)
        return _parts_exist(_reach(node.bare(), step[:-1], env),
                            lambda ch: _and(*(c(ch, env) for c in conds)))
    return _parts_exist(_reach(node.bare(), step, env), lambda ch: _exists(ch, rest, env))


def _leading_conds(steps):
    """
    The conditions that open `steps`.
    """
    out = []
    for step in steps:
        if step[0] != 'cond':
            break
        out.append(step[1])
    return out


def _collect(node, steps, env, mode):
    """
    The matches `steps` reach from `node`, read as `mode`; None where
    they can reach nothing.
    """
    if node is None:
        return None
    if not steps:
        return _single(node, mode)
    step, rest = steps[0], steps[1:]
    kind = step[0]
    if kind == 'cond':
        return _when(step[1](node, env), _collect(node, rest, env, mode))
    if kind == 'group':
        return _group_collect(node, step, rest, env, mode)
    if kind == 'first':
        return _first_collect(node, step[1], rest, env, mode)
    if step[-1] == 'aligned':
        return _aligned_collect(node, step[:-1], rest, env, mode)
    return _parts_collect(_reach(node, step, env), lambda ch: _collect(ch, rest, env, mode), mode)


def _aligned_collect(node, step, rest, env, mode):
    """
    An aligned step, `[*+]` or `**+`: what the rest reaches from each
    node it reaches that is a match, or one null where it reaches
    nothing.
    """
    if mode != 'value':
        raise TranslationError('aligned (+) under a soft cut not supported yet')
    conds = _leading_conds(rest)
    rest = rest[len(conds):]
    F = _functions()
    T = _types()

    def filled(ch):
        found = _collect(ch, rest, env, mode)
        if found is None:
            found = _Found(F.array(F.lit(None)), T.NullType(), mode)
        else:
            found = _Found(F.when(F.size(found.arr) > 0, found.arr).otherwise(
                F.array(F.lit(None).cast(found.dtype))), found.dtype, mode)
        return _when(_and(*(c(ch, env) for c in conds)), found)
    return _parts_collect(_reach(node, step, env), filled, mode)


def _group_exists(node, step, rest, env):
    """
    The test that the group step reaches something from `node`, the
    rest after each branch: some branch for 'or', every one for 'and'.
    A cut or soft cut changes which branches yield, not whether any does.
    """
    _, kind, branches, _, _ = step
    found = [_exists(node, branch + rest, env) for branch in branches]
    return _and(*found) if kind == 'and' else _or(*found)


def _group_collect(node, step, rest, env, mode):
    """
    What the group step reaches from `node`: each branch's matches in
    turn, the rest after each; for 'and', none unless every branch
    reaches something.
    """
    _, kind, branches, _, softs = step
    if any(softs):
        return _softcut_collect(node, step, rest, env, mode)
    found = [_collect(node, branch + rest, env, mode) for branch in branches]
    if kind == 'or':
        return _cut_concat(node, step, rest, env, found)
    return _when(_group_exists(node.bare(), step, rest, env), _concat(found))


def _cut_concat(node, step, rest, env, found):
    """
    The 'or' group's branch matches, `found`, in turn, ending after the
    first branch a cut follows that reaches something from `node`.
    """
    _, _, branches, cuts, _ = step
    out = found[-1]
    for branch, cut, matches in reversed(list(zip(branches, cuts, found))[:-1]):
        if cut:
            out = _when(_not(_exists(node.bare(), branch + rest, env)), out)
        out = _concat([matches, out])
    return out


def _tagged(found, branch, cut, soft):
    """
    A soft cut's branch matches, each tagged with its branch.
    """
    F = _functions()
    return F.transform(found.arr, lambda n: F.struct(
        n.alias('node'), F.lit(branch).alias('branch'),
        F.lit(cut).alias('cut'), F.lit(soft).alias('soft')))


def _softcut_collect(node, step, rest, env, mode):
    """
    What a group step with a soft cut reaches from `node`: each branch's
    matches in turn, read with their paths from the group's node, less
    those that overlap a match a soft-cut branch kept (`_uncovered`);
    for 'and', none unless every branch reaches something.
    """
    F = _functions()
    _, kind, branches, cuts, softs = step
    start = node.rooted()
    inner = 'path' if mode == 'path' else 'node'
    founds = [(_collect(start, branch + rest, env, inner), j)
              for j, branch in enumerate(branches)]
    founds = [(found, j) for found, j in founds if found is not None]
    if not founds:
        return None
    dtype = None
    if inner == 'node':
        dtype = _common([found.dtype for found, _ in founds])
        founds = [(_cast(found, dtype), j) for found, j in founds]
    tagged = F.concat(*(_tagged(found, j, cuts[j], softs[j]) for found, j in founds))
    kept = _uncovered(tagged, inner == 'path')
    if kind == 'and':
        held = _group_exists(node.bare(), step, rest, env)
        if held is False:
            return None
        if held is not True:
            kept = F.filter(kept, lambda x: held)
    if mode == 'value':
        return _Found(F.transform(kept, lambda n: n['value']), dtype, mode)
    return _Found(kept, dtype, mode)


def _overlap(p, q):
    """
    Boolean Column: one of the paths `p` and `q` starts the other.
    """
    F = _functions()
    return (F.slice(q, 1, F.size(p)) == p) | (F.slice(p, 1, F.size(q)) == q)


def _uncovered(tagged, paths):
    """
    Array Column of the nodes of `tagged`, (node, branch, cut, soft)
    structs in order, that a soft cut didn't cover, in dotted's order:
    one whose path overlaps the path of one a soft-cut branch kept is
    dropped; a soft-cut branch's own nodes are kept as they pass; and
    after a branch a cut follows keeps one, the branches after it are
    dropped. A node is its path where `paths`, else a (path, value).
    """
    F = _functions()

    def path_of(node):
        return node if paths else node['path']
    init = F.struct(
        F.array().cast('array<array<string>>').alias('kept'),
        F.slice(F.transform(tagged, lambda t: t['node']), 1, 0).alias('out'),
        F.lit(-1).alias('branch'),
        F.lit(False).alias('cut'),
        F.lit(False).alias('found'),
        F.lit(False).alias('stop'))

    def merge(acc, t):
        moved = t['branch'] != acc['branch']
        stop = acc['stop'] | (moved & acc['cut'] & acc['found'])
        path = path_of(t['node'])
        covered = F.exists(acc['kept'], lambda s: _overlap(s, path))
        take = ~stop & ~covered
        return F.struct(
            F.when(take & t['soft'], F.concat(acc['kept'], F.array(path)))
             .otherwise(acc['kept']).alias('kept'),
            F.when(take, F.concat(acc['out'], F.array(t['node'])))
             .otherwise(acc['out']).alias('out'),
            t['branch'].alias('branch'),
            t['cut'].alias('cut'),
            F.when(moved, take).otherwise(acc['found'] | take).alias('found'),
            stop.alias('stop'))

    return F.aggregate(tagged, init, merge, lambda acc: acc['out'])


# A step whose parts are a branch's matches themselves
_REACHING = ('get', 'member', 'each', 'descend', 'slice', 'listfilter', 'transform', 'not')


def _candidates(node, branches, env, mode):
    """
    The first-match group's candidates at `node`, in dotted's order:
    (test, node) pairs, the group's first match the node of the first
    whose test holds; a node of None where reaching it fails. A branch
    of one step reads its parts, so matches of other types never share
    an array; a longer one reads its matches' first.
    """
    F = _functions()
    start = node if mode == 'value' else node.rooted()
    out = []
    for branch in branches:
        simple = len(branch) == 1 and branch[0][0] in _REACHING and branch[0][-1] != 'aligned'
        pairs = _part_candidates(_reach(start, branch[0], env)) if simple else None
        if pairs is not None:
            out.extend(pairs)
            continue
        found = _collect(start, branch, env, 'value' if mode == 'value' else 'node')
        if found is None:
            continue
        first = F.get(found.arr, 0)
        if found.mode == 'value':
            out.append((F.size(found.arr) > 0, _Node(first, found.dtype)))
        else:
            out.append((F.size(found.arr) > 0, _Node(first['value'], found.dtype, first['path'])))
    return out


def _part_candidates(parts):
    """
    The candidates of a step's parts, in order: each node, there where
    its part's condition holds; a list's or map's first element where
    it has one. None where a part can't be read so. A condition already
    met before, the very same, can't pick a later node.
    """
    F = _functions()
    out = []
    seen = []
    for part in parts:
        if isinstance(part, _Fail):
            out.append((_failing_where(part.cond, part.what), None))
            continue
        if isinstance(part, _One):
            test, chosen = _cond(part.cond), part.node
        else:
            made = part.make(F.get(part.arr, 0), F.lit(0))
            if len(made) != 1 or not isinstance(made[0], _One) or made[0].cond is not None:
                return None
            test, chosen = F.size(part.arr) > 0, made[0].node
        if any(test is earlier for earlier in seen):
            continue
        seen.append(test)
        out.append((test, chosen))
        if test is True:
            break
    return out


def _first_exists(node, branches, rest, env):
    """
    The test that the first match of the first branch to have one
    reaches something by the rest.
    """
    pairs = [(test, False if chosen is None else _exists(chosen, rest, env))
             for test, chosen in _candidates(node.bare(), branches, env, 'value')]
    return _test_case(pairs, False)


def _first_collect(node, branches, rest, env, mode):
    """
    What the rest reaches from the first match of the first branch to
    have one.
    """
    pairs = [(test, None if chosen is None else _collect(chosen, rest, env, mode))
             for test, chosen in _candidates(node, branches, env, mode)]
    return _case(pairs)


# ---- Negation -------------------------------------------------------

def _starts(path, prefix):
    """
    Boolean Column: the path `prefix` starts `path`.
    """
    F = _functions()
    return F.slice(path, 1, F.size(prefix)) == prefix


def _complement(node, step, env):
    """
    The negation step's complement at `node`, as parts: the largest
    subtrees that hold no match of its branch, in order. The branch's
    matches are read as paths; then, level by level as the branch reads
    them, a child no match path reaches is kept whole, one a match ends
    at is dropped, and one a match runs through is split in turn.
    """
    _, steps, kinds = step
    start = node.rooted()
    found = _collect(start, steps, env, 'path')
    matched = (found.arr if found is not None
               else _functions().array().cast('array<array<string>>'))
    return _split(start, matched, kinds)


def _children(node, kind):
    """
    The node's children as a negation splits them: 'key' a dict's
    members, 'slot' a list's elements.
    """
    if kind == 'key' and node.kind == 'struct':
        return _fields(node)
    if kind == 'key' and node.kind == 'map':
        return [_map_values(node)]
    if kind == 'slot' and node.kind == 'array':
        return [_elements(node)]
    return []


def _split(node, matched, kinds):
    """
    The subtrees under `node` that hold none of the `matched` paths, as
    parts: its children, read as `kinds[0]` says, each kept, dropped or
    split by `kinds[1:]` in turn.
    """
    out = []
    for part in _children(node, kinds[0]):
        if isinstance(part, _One):
            out.extend(_split_child(part.cond, part.node, matched, kinds[1:]))
            continue
        make = part.make
        out.append(_Many(part.arr, lambda x, i, make=make: [
            p for one in make(x, i)
            for p in _split_child(one.cond, one.node, matched, kinds[1:])]))
    return out


def _split_child(cond, child, matched, kinds):
    """
    One child of a negation's node as parts: itself where no match path
    reaches it; where one runs through it, its own split.
    """
    F = _functions()
    reached = F.exists(matched, lambda m: _starts(m, child.path))
    whole = [_One(_and(_cond(cond), ~reached), child)]
    if not kinds:
        return whole
    ends = F.exists(matched, lambda m: m == child.path)
    deeper = _and(_cond(cond), reached, ~ends)
    return whole + [_guarded(deeper, part) for part in _split(child, matched, kinds)]


# ---- Comparing, as Python compares ----------------------------------

_OPS = {
    '=': operator.eq,
    '!=': operator.ne,
    '<': operator.lt,
    '<=': operator.le,
    '>': operator.gt,
    '>=': operator.ge,
}

_FLIP = {'=': '=', '!=': '!=', '<': '>', '<=': '>=', '>': '<', '>=': '<='}


def _int_range(dtype):
    """
    The least and greatest value of an integral Spark type.
    """
    bits = _INTEGRAL_BITS[type(dtype).__name__]
    return -2 ** (bits - 1), 2 ** (bits - 1) - 1


def _decimal_grid(dtype):
    """
    The least and greatest value of a decimal Spark type, and its step,
    as Fractions.
    """
    step = fractions.Fraction(1, 10 ** dtype.scale)
    top = fractions.Fraction(10 ** (dtype.precision - dtype.scale)) - step
    return -top, top, step


def _decimal_of(q, scale):
    """
    The Fraction `q`, a multiple of 10**-scale, as a Decimal.
    """
    return decimal.Decimal(int(q * 10 ** scale)).scaleb(-scale)


def _exact(value):
    """
    A Python number as a Fraction, exactly; a non-finite one as 'nan'
    or a float infinity.
    """
    if isinstance(value, bool):
        return fractions.Fraction(int(value))
    if isinstance(value, float):
        if math.isnan(value):
            return 'nan'
        if math.isinf(value):
            return value
    if isinstance(value, decimal.Decimal) and not value.is_finite():
        return 'nan' if value.is_nan() else float(value)
    return fractions.Fraction(value)


def _literal_kind(value):
    """
    What the Python `value` a comparison takes is, as `_kind` says;
    one Spark can't compare as Python does raises.
    """
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integral'
    if isinstance(value, float):
        return 'fractional'
    if isinstance(value, decimal.Decimal):
        return 'decimal'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, (bytes, bytearray)):
        return 'binary'
    if isinstance(value, datetime.datetime):
        raise TranslationError('comparing with a datetime not supported in sparkize')
    if isinstance(value, datetime.date):
        return 'date'
    raise TranslationError(f'comparing with a {type(value).__name__} not supported in sparkize')


def _compared(node, op, value, env):
    """
    The test that Python's `<the node's value> <op> value` holds, the
    node there. `value` is a Python value or a Spark Column.
    """
    if op == '!=':
        return _not(_compared(node, '=', value, env))
    if _is_column(value):
        return _against(node, op, _Node(value, _column_type(value, env)))
    if value is None:
        if op != '=':
            return False
        return True if node.kind == 'null' else node.col.isNull()
    if node.kind == 'null':
        return False
    kind = _literal_kind(value)
    if node.kind in _NUMERIC and kind in _NUMERIC:
        test = _vs_number(node, op, value)
    elif node.kind == kind and kind in ('string', 'binary', 'date'):
        test = _OPS[op](node.col, _functions().lit(value))
    else:
        return False
    return _and(node.col.isNotNull(), test)


def _vs_number(node, op, value):
    """
    The test `<node's number> <op> value` for a Python number, exactly
    as Python compares: a bool as 1 or 0, an int and a float by their
    exact values. `op` is not `!=`.
    """
    kind = node.kind
    c = node.col.cast('int') if kind == 'boolean' else node.col
    q = _exact(value)
    if q == 'nan':
        return False
    if kind == 'fractional':
        return _vs_double(c, op, q)
    if isinstance(q, float):
        return {'<': q > 0, '<=': q > 0, '>': q < 0, '>=': q < 0, '=': False}[op]
    if kind == 'decimal':
        lo, hi, step = _decimal_grid(node.dtype)
    else:
        (lo, hi), step = ((0, 1) if kind == 'boolean' else _int_range(node.dtype)), 1
    below = math.floor(q / step) * step
    above = math.ceil(q / step) * step
    if op == '=' and below != q:
        return False
    bound = {'=': below, '<': above, '<=': below, '>': below, '>=': above}[op]
    if bound > hi:
        return op in ('<', '<=')
    if bound < lo:
        return op in ('>', '>=')
    if kind == 'decimal':
        return _OPS[op](c, _functions().lit(_decimal_of(bound, node.dtype.scale)))
    return _OPS[op](c, _functions().lit(int(bound)))


def _vs_double(c, op, q):
    """
    The test `c <op> q`, `c` a float Column, `q` a Fraction or a float
    infinity: a number between two doubles compares as the bound on
    its side; a NaN orders before nothing and after nothing.
    """
    if isinstance(q, float):
        return _ordered(c, op, q)
    try:
        d = float(q)
    except OverflowError:
        d = math.inf if q > 0 else -math.inf
    if not math.isinf(d) and fractions.Fraction(d) == q:
        return _ordered(c, op, d)
    if op == '=':
        return False
    if math.isinf(d):
        lo, hi = (sys.float_info.max, math.inf) if q > 0 else (-math.inf, -sys.float_info.max)
    elif fractions.Fraction(d) > q:
        lo, hi = math.nextafter(d, -math.inf), d
    else:
        lo, hi = d, math.nextafter(d, math.inf)
    if op in ('<', '<='):
        return _ordered(c, '<=', lo)
    return _ordered(c, '>=', hi)


def _ordered(c, op, d):
    """
    The test `c <op> d`, `c` a float Column, `d` a float not NaN. Spark
    orders a NaN above every number; Python orders it nowhere.
    """
    F = _functions()
    test = _OPS[op](c, F.lit(d))
    if op == '=':
        return test
    return ~F.isnan(c) & test


def _against(a, op, b):
    """
    The test that Python's `<a's value> <op> <b's value>` holds, both
    nodes Columns there. `op` is not `!=`.
    """
    nulls = a.col.isNull() & b.col.isNull()
    both = a.col.isNotNull() & b.col.isNotNull()
    test = False
    if a.kind in _NUMERIC and b.kind in _NUMERIC:
        test = _numbers(a, op, b)
    elif a.kind == b.kind and a.kind in ('string', 'binary', 'date', 'timestamp'):
        test = _OPS[op](a.col, b.col)
    held = _and(both, test)
    if op == '=':
        return _or(nulls, held)
    return held


def _numbers(a, op, b):
    """
    The test `a <op> b` for two number nodes, exactly as Python
    compares.
    """
    F = _functions()

    def real(n):
        return n.kind in ('fractional', 'decimal')

    def whole(n):
        return n.col.cast('long') if n.kind == 'boolean' else n.col
    if not real(a) and not real(b):
        return _OPS[op](whole(a), whole(b))
    if a.kind == b.kind == 'fractional':
        if op == '=':
            return ~F.isnan(a.col) & ~F.isnan(b.col) & (a.col == b.col)
        return ~F.isnan(a.col) & ~F.isnan(b.col) & _OPS[op](a.col, b.col)
    if a.kind == b.kind == 'decimal':
        return _OPS[op](a.col, b.col)
    if real(a) and real(b):
        raise TranslationError('comparing a decimal with a float not supported in sparkize')
    if real(a):
        return _int_vs_real(whole(b), _FLIP[op], a)
    return _int_vs_real(whole(a), op, b)


def _int_vs_real(whole, op, real):
    """
    The test `whole <op> real`, `whole` an int Column, `real` a float or
    decimal node, exactly: an int is below a real where it's below the
    real's ceiling, and so on.
    """
    F = _functions()
    v = real.col
    nan = F.isnan(v) if real.kind == 'fractional' else F.lit(False)
    d = v.cast('decimal(38,18)') if real.kind == 'fractional' else v
    w = whole.cast('decimal(20,0)')
    lo, hi = F.floor(d), F.ceil(d)
    body = {'<': w < hi, '<=': w <= lo, '>': w > lo, '>=': w >= hi,
            '=': (lo == hi) & (w == lo)}[op]
    inside = (v > -1e19) & (v < 1e19)
    return ~nan & (F.when(inside, body).when(v >= 1e19, F.lit(op in ('<', '<=')))
                   .otherwise(F.lit(op in ('>', '>='))))


def _is_column(value):
    """
    True for a Spark Column, classic or Connect.
    """
    return type(value).__name__ == 'Column' and type(value).__module__.startswith('pyspark')


def _column_type(col, env):
    """
    The Spark type of a Column handed in as a binding, read against the
    schema in the active session.
    """
    from pyspark.sql import SparkSession
    session = SparkSession.getActiveSession()
    if session is None:
        try:
            from pyspark.sql.connect.session import SparkSession as Remote
            session = Remote.getActiveSession()
        except ImportError:
            session = None
    if session is None:
        raise TranslationError('a Column binding needs an active SparkSession to read its type')
    return session.createDataFrame([], env.schema).select(col).schema[0].dataType


# ---- Python's text of a value ---------------------------------------

def _let(value, fn):
    """
    The Column `fn` makes of the Column `value`, read once: `fn` gets a
    lambda's variable that stands for it. A Column is a tree, so one
    used twice is copied whole; a variable is not.
    """
    F = _functions()
    return F.get(F.transform(F.array(value), fn), 0)


def _float_digits(d):
    """
    The double Column `d`, not 0, NaN or infinite, as a struct of its
    shortest significant digits, `ds`, and the exponent of the first,
    `xs`: Java's digits, one dropped where the double still reads back.
    A Java before 19 writes some doubles with a digit more than the
    shortest.
    """
    F = _functions()

    def from_java(body):
        whole = F.regexp_extract(body, r'^([0-9]+)', 1)
        frac = F.regexp_extract(body, r'^[0-9]+\.([0-9]+)', 1)
        e = F.coalesce(F.regexp_extract(body, r'E(-?[0-9]+)$', 1).cast('int'), F.lit(0))
        digits = F.concat(whole, frac)
        lead = F.regexp_replace(digits, '^0+', '')
        return F.struct(F.regexp_replace(lead, '0+$', '').alias('sig'),
                        (F.length(whole) - 1 - (F.length(digits) - F.length(lead)) + e).alias('x'))

    def shortened(p):
        sig, x = p['sig'], p['x']
        k = F.length(sig)
        head = sig.substr(F.lit(1), k - 1)
        up = F.when(sig.substr(k, F.lit(1)) >= '5',
                    (head.cast('long') + 1).cast('string')).otherwise(head)

        def tried(u):
            shorter = F.regexp_replace(u, '0+$', '')
            sx = x + F.length(u) - (k - 1)
            back = F.concat(shorter.substr(F.lit(1), F.lit(1)), F.lit('.'),
                            F.when(F.length(shorter) > 1, shorter.substr(F.lit(2), F.lit(99)))
                             .otherwise('0'),
                            F.lit('E'), sx.cast('string')).cast('double')
            use = (k >= 2) & (back == F.abs(d))
            return F.struct(F.when(use, shorter).otherwise(sig).alias('ds'),
                            F.when(use, sx).otherwise(x).alias('xs'))
        return _let(up, tried)
    return _let(_let(F.regexp_replace(d.cast('string'), '^-', ''), from_java), shortened)


def _py_float_text(c):
    """
    A string Column of Python's str() of the float Column `c`: its
    shortest digits (`_float_digits`) in Python's notation.
    """
    F = _functions()

    def written(p):
        ds, xs = p['ds'], p['xs']
        zeros = F.lit('0' * 17)
        rest = ds.substr(xs + 2, F.lit(99))
        fixed = F.when(xs >= 0, F.concat(
            F.concat(ds, zeros).substr(F.lit(1), xs + 1), F.lit('.'),
            F.when(rest == '', F.lit('0')).otherwise(rest))).otherwise(
            F.concat(F.lit('0.'), zeros.substr(F.lit(1), -xs - 1), ds))
        mag = F.abs(xs).cast('string')
        sci = F.concat(
            ds.substr(F.lit(1), F.lit(1)),
            F.when(F.length(ds) > 1, F.concat(F.lit('.'), ds.substr(F.lit(2), F.lit(99))))
             .otherwise(''),
            F.lit('e'), F.when(xs < 0, F.lit('-')).otherwise(F.lit('+')),
            F.when(F.abs(xs) < 10, F.concat(F.lit('0'), mag)).otherwise(mag))
        return F.when((xs >= -4) & (xs < 16), fixed).otherwise(sci)

    def text(d):
        sign = F.when(d.cast('string').startswith('-'), F.lit('-')).otherwise(F.lit(''))
        return (F.when(F.isnan(d), F.lit('nan'))
                 .when(d == math.inf, F.lit('inf'))
                 .when(d == -math.inf, F.lit('-inf'))
                 .when(d == 0, F.concat(sign, F.lit('0.0')))
                 .otherwise(F.concat(sign, _let(_float_digits(d), written))))
    return _let(c.cast('double'), text)


def _py_text(node):
    """
    A string Column of Python's str() of the node's value, not None, for
    the kinds Python writes as Spark can; None for the rest.
    """
    F = _functions()
    kind = node.kind
    c = node.col
    if kind == 'string':
        return c
    if kind in ('integral', 'decimal', 'date'):
        return c.cast('string')
    if kind == 'fractional':
        return _py_float_text(c)
    if kind == 'boolean':
        return F.when(c, F.lit('True')).otherwise(F.lit('False'))
    return None


_REPR_STARTS = {'struct': '{', 'map': '{', 'array': '[', 'binary': 'b'}


def _repr_what(node, what):
    """
    The failure's message for Python's repr of the node's value, a
    dict, list or bytearray, which Spark can't write: a struct's or
    map's key order, a bytearray's escapes. Other kinds raise.
    """
    if node.kind not in _REPR_STARTS:
        raise TranslationError(f'{what} of a {node.dtype.simpleString()} not supported in sparkize')
    return f'{what} of a {node.kind}, which Python writes as its repr'


def _regex_holds(node, pattern):
    """
    The test that `pattern` fullmatches Python's str() of the node's
    value, as dotted's regex guard does: None reads as 'None'.
    """
    F = _functions()
    none = re.fullmatch(pattern, 'None') is not None
    kind = node.kind
    if kind == 'null':
        return none
    text = _py_text(node)
    if text is not None:
        held = text.rlike(_java_regex(pattern))
    elif kind not in _REPR_STARTS:
        raise TranslationError(f'a regex on a {node.dtype.simpleString()} not supported in sparkize')
    elif core.may_start_with(pattern, _REPR_STARTS[kind]):
        held = _failing(_repr_what(node, 'a regex'))
    else:
        held = False
    return F.when(node.col.isNull(), F.lit(none)).otherwise(_lit(held))


# ---- Python regexes as Java's ---------------------------------------
#
# Spark's `rlike` runs a Java regex and searches; dotted fullmatches a
# Python one. The Python regex is parsed (`core.regex_parser`) and
# written anew in Java's syntax, anchored, each construct as Python
# reads it: `.` without DOTALL every character but \n, `$` the end or
# before a final \n, `\d`, `\w` and `\s` Python's Unicode classes.

# Python's str.isspace(), as a Java class
_J_SPACE = r'[\t\n\x{0b}\f\r\x{1c}-\x{1f}\x{85}\p{Z}]'
_J_ANY = r'[\x{0}-\x{10FFFF}]'


def _j_char(code):
    """
    A Java regex of one code point.
    """
    c = chr(code)
    if c.isascii() and c.isalnum():
        return c
    return f'\\x{{{code:x}}}'


def _j_category(category, flags, sre):
    """
    A Java regex of a Python category: `\\d`, `\\w`, `\\s`, or one's
    negation.
    """
    ascii_ = bool(flags & re.ASCII)
    classes = {
        sre.CATEGORY_DIGIT: '[0-9]' if ascii_ else r'\p{Nd}',
        sre.CATEGORY_WORD: '[a-zA-Z0-9_]' if ascii_ else r'[\p{L}\p{N}_]',
        sre.CATEGORY_SPACE: r'[ \t\n\r\f\x{0b}]' if ascii_ else _J_SPACE,
    }
    negated = {
        sre.CATEGORY_NOT_DIGIT: sre.CATEGORY_DIGIT,
        sre.CATEGORY_NOT_WORD: sre.CATEGORY_WORD,
        sre.CATEGORY_NOT_SPACE: sre.CATEGORY_SPACE,
    }
    if category in classes:
        return classes[category]
    if category in negated:
        return f'(?:(?!{classes[negated[category]]}){_J_ANY})'
    raise TranslationError(f'regex category not supported in sparkize: {category}')


def _j_class(items, flags, sre):
    """
    A Java regex of a Python character class, each member one of its
    alternatives: Java's nested and negated classes differ by version.
    """
    negate = bool(items) and items[0][0] is sre.NEGATE
    members = []
    for op, av in items:
        if op is sre.NEGATE:
            continue
        if op is sre.LITERAL:
            members.append(_j_char(av))
        elif op is sre.RANGE:
            members.append(f'[{_j_char(av[0])}-{_j_char(av[1])}]')
        elif op is sre.CATEGORY:
            members.append(_j_category(av, flags, sre))
        else:
            raise TranslationError(f'regex class member not supported in sparkize: {op}')
    alts = '|'.join(members) or '(?!)'
    if negate:
        return f'(?:(?!{alts}){_J_ANY})'
    return f'(?:{alts})'


def _j_word(flags, sre):
    """
    A Java regex of a Python word character.
    """
    return _j_category(sre.CATEGORY_WORD, flags, sre)


def _j_items(items, flags, sre):
    """
    A Java regex of a parsed run of Python regex items.
    """
    return ''.join(_j_item(op, av, flags, sre) for op, av in items)


def _j_cased(text, flags):
    """
    `text` matched ignoring case where IGNORECASE is on.
    """
    if flags & re.IGNORECASE:
        return f'(?iu:{text})'
    return text


def _j_item(op, av, flags, sre):
    """
    A Java regex of one parsed Python regex item.
    """
    if op is sre.LITERAL:
        return _j_cased(_j_char(av), flags)
    if op is sre.NOT_LITERAL:
        return _j_cased(f'(?:(?!{_j_char(av)}){_J_ANY})', flags)
    if op is sre.ANY:
        return _J_ANY if flags & re.DOTALL else r'[^\n]'
    if op is sre.IN:
        return _j_cased(_j_class(av, flags, sre), flags)
    if op is sre.BRANCH:
        return '(?:' + '|'.join(_j_items(list(alt), flags, sre) for alt in av[1]) + ')'
    if op is sre.SUBPATTERN:
        group, add, delete, body = av
        inner = _j_items(list(body), (flags | add) & ~delete, sre)
        return f'({inner})' if group is not None else f'(?:{inner})'
    if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, getattr(sre, 'POSSESSIVE_REPEAT', None)):
        low, high, body = av
        top = '' if high is sre.MAXREPEAT else str(high)
        lazy = {sre.MIN_REPEAT: '?', sre.MAX_REPEAT: ''}.get(op, '+')
        return f'(?:{_j_items(list(body), flags, sre)}){{{low},{top}}}{lazy}'
    if op is sre.AT:
        return _j_at(av, flags, sre)
    if op is sre.GROUPREF:
        return _j_cased(f'(?:\\{av})', flags)
    if op in (sre.ASSERT, sre.ASSERT_NOT):
        direction, body = av
        sign = '=' if op is sre.ASSERT else '!'
        look = '' if direction == 1 else '<'
        return f'(?{look}{sign}{_j_items(list(body), flags, sre)})'
    if op is getattr(sre, 'ATOMIC_GROUP', None):
        return f'(?>{_j_items(list(av), flags, sre)})'
    raise TranslationError(f'regex construct not supported in sparkize: {op}')


def _j_at(at, flags, sre):
    """
    A Java regex of a Python anchor.
    """
    multiline = flags & re.MULTILINE
    if at is sre.AT_BEGINNING:
        return r'(?:\A|(?<=\n))' if multiline else r'\A'
    if at is sre.AT_BEGINNING_STRING:
        return r'\A'
    if at is sre.AT_END:
        return r'(?=\n|\z)' if multiline else r'(?=\n?\z)'
    if at is sre.AT_END_STRING:
        return r'\z'
    word = _j_word(flags, sre)
    boundary = f'(?:(?<={word})(?!{word})|(?<!{word})(?={word}))'
    if at is sre.AT_BOUNDARY:
        return boundary
    if at is sre.AT_NON_BOUNDARY:
        return f'(?!{boundary})'
    raise TranslationError(f'regex anchor not supported in sparkize: {at}')


@functools.lru_cache(maxsize=256)
def _java_regex(pattern):
    """
    A Java regex that matches where the Python `pattern` fullmatches.
    """
    try:
        re.compile(pattern)
    except re.error as e:
        raise TranslationError(f'invalid regex {pattern!r}: {e}')
    sre = core.regex_parser()
    parsed = sre.parse(pattern)
    flags = parsed.state.flags
    if flags & re.LOCALE:
        raise TranslationError(f'a LOCALE regex not supported in sparkize: {pattern!r}')
    return r'\A(?:' + _j_items(list(parsed), flags, sre) + r')\z'


# ---- Transforms, as dotted's --------------------------------------

# What Python's int() and float() take from a string, as Java regexes
_J_DIGITS = r'[0-9](?:_?[0-9])*'
_J_ANY_DIGITS = r'\p{Nd}(?:_?\p{Nd})*'


def _j_number(digits):
    """
    Java regexes of the strings Python's int() and float() take, with
    `digits` a run of digits.
    """
    s = _J_SPACE + '*'
    integer = rf'\A{s}[-+]?{digits}{s}\z'
    real = (rf'\A{s}[-+]?(?:{digits}(?:\.(?:{digits})?)?|\.{digits})'
            rf'(?:[eE][-+]?{digits})?{s}\z')
    return integer, real


_J_INT, _J_FLOAT = _j_number(_J_DIGITS)
_J_ANY_INT, _J_ANY_FLOAT = _j_number(_J_ANY_DIGITS)
_J_NONFINITE = rf'\A{_J_SPACE}*[-+]?(?i:inf|infinity|nan){_J_SPACE}*\z'
_J_STRIP = _J_SPACE + '|_'


def _int_of(node):
    """
    dotted's `int` on the node, as parts: a number truncated, a bool as
    1 or 0, a string int() reads as that int; anything else as it was,
    as `int` keeps what int() raises on. An int a long can't hold, and
    int() of inf, which `int` doesn't catch, fail.
    """
    F = _functions()
    T = _types()
    kind = node.kind
    c = node.col
    long_ = T.LongType()
    if kind == 'boolean':
        return [_One(None, node.like(c.cast('long'), long_))]
    if kind in ('fractional', 'decimal'):
        kept = c.isNull() | (F.isnan(c) if kind == 'fractional' else F.lit(False))
        fits = (c >= -2.0 ** 63) & (c < 2.0 ** 63)
        return [_One(kept, node), _One(~kept & fits, node.like(c.cast('long'), long_)),
                _Fail(node, ~kept & ~fits, 'int() of a number a long cannot hold, or of inf')]
    if kind == 'string':
        parsed = F.regexp_replace(c, _J_STRIP, '').cast('long')
        ascii_ = c.isNotNull() & c.rlike(_J_INT)
        other = c.isNotNull() & ~ascii_ & c.rlike(_J_ANY_INT)
        return [_One(ascii_ & parsed.isNotNull(), node.like(parsed, long_)),
                _Fail(node, ascii_ & parsed.isNull(), 'int() of a number a long cannot hold'),
                _Fail(node, other, 'int() of digits Spark cannot read'),
                _One(~ascii_ & ~other, node)]
    if kind == 'binary':
        raise TranslationError('int() of a binary value not supported in sparkize')
    return [_One(None, node)]


def _float_of(node):
    """
    dotted's `float` on the node, as parts: a number as a float, a bool
    as 1.0 or 0.0, a string float() reads as that float; another string
    as it was. float() raises a TypeError `float` doesn't catch on None
    and on a container: those fail.
    """
    F = _functions()
    T = _types()
    kind = node.kind
    c = node.col
    double = T.DoubleType()
    none = _Fail(node, c.isNull(), 'float() of None')
    there = c.isNotNull()
    if kind in _NUMERIC:
        made = F.when(c, 1.0).otherwise(0.0) if kind == 'boolean' else c.cast('double')
        return [none, _One(there, node.like(made, double))]
    if kind == 'string':
        cleaned = F.regexp_replace(c, _J_STRIP, '')
        number = there & c.rlike(_J_FLOAT)
        word = there & c.rlike(_J_NONFINITE)
        other = there & ~number & ~word & c.rlike(_J_ANY_FLOAT)
        nonfinite = (F.when(F.lower(cleaned).contains('nan'), F.lit(math.nan))
                      .when(cleaned.startswith('-'), F.lit(-math.inf))
                      .otherwise(F.lit(math.inf)))
        made = F.when(number, cleaned.cast('double')).otherwise(nonfinite)
        return [none, _One(number | word, node.like(made, double)),
                _Fail(node, other, 'float() of digits Spark cannot read'),
                _One(there & ~number & ~word & ~other, node)]
    if kind == 'binary':
        raise TranslationError('float() of a binary value not supported in sparkize')
    return [_Fail(node, None, 'float() of a value that is not a number or string')]


def _str_of(node):
    """
    dotted's `str` on the node, as parts: str() of it, None as 'None'.
    Python writes a dict, list or bytearray as its repr, which fails.
    """
    F = _functions()
    string = _types().StringType()
    c = node.col
    if node.kind == 'null':
        return [_One(None, node.like(F.lit('None'), string))]
    text = _py_text(node)
    if text is not None:
        return [_One(None, node.like(F.when(c.isNull(), F.lit('None')).otherwise(text), string))]
    return [_One(c.isNull(), node.like(F.lit('None'), string)),
            _Fail(node, c.isNotNull(), _repr_what(node, 'str()'))]


# dotted's transforms a translation can apply, by name
_TRANSFORMS = {'int': _int_of, 'float': _float_of, 'str': _str_of}


def _checked(transforms):
    """
    `transforms` as a tuple, each one a translation applies: `int`,
    `float` and `str`, without arguments. Any other raises.
    """
    transforms = tuple(transforms or ())
    for transform in transforms:
        if transform.name not in _TRANSFORMS or transform.params:
            raise TranslationError(f'transform not supported in sparkize: {transform.operator()}')
    return transforms


def _transformed(node, transforms):
    """
    The node through dotted's transforms (`_checked`), as parts that
    hold one at a time.
    """
    parts = [_One(None, node)]
    for transform in transforms:
        fn = _TRANSFORMS[transform.name]
        out = []
        for part in parts:
            if isinstance(part, _Fail):
                out.append(part)
                continue
            out.extend(_guarded(_cond(part.cond), made) for made in fn(part.node))
        parts = [part for part in out if part.cond is not False]
    return parts


# ---- Reading a path to Columns --------------------------------------

def _literal_read(node, steps, env):
    """
    The one value a path of no pattern reaches from `node`, a node:
    null where it reaches nothing.
    """
    F = _functions()
    T = _types()
    if node is None:
        return _Node(F.lit(None), T.NullType())
    cond = True
    for step in steps:
        if step[0] != 'get':
            break
        parts = _reach(node, step, env)
        if len(parts) != 1 or not isinstance(parts[0], _One):
            break
        cond = _and(cond, _cond(parts[0].cond))
        node = parts[0].node
        steps = steps[1:]
    else:
        if cond is True:
            return node
        if cond is False:
            return _Node(F.lit(None).cast(node.dtype), node.dtype)
        return _Node(F.when(cond, node.col), node.dtype)
    found = _when(cond, _collect(node, steps, env, 'value'))
    if found is None:
        return _Node(F.lit(None), T.NullType())
    return _Node(F.get(found.arr, 0), found.dtype)


def _all_read(node, steps, env):
    """
    The array Column of the matches a pattern path reaches from `node`.
    """
    found = _collect(node, steps, env, 'value')
    if found is None:
        return _functions().array()
    return found.arr


class _Typed(ColumnFragment):
    """
    A fragment that reads a node: `node(env)` gives it, a `_Node` (None
    for a column the schema lacks), and its Column is the node's value.
    """

    __slots__ = ('node',)

    def __init__(self, node, unbound=None):
        def make(env):
            n = node(env)
            return _functions().lit(None) if n is None else n.col
        super().__init__(make, unbound)
        self.node = node


def _column_node(env, name):
    """
    The column `name` of the schema as a node; None where the schema has
    no such column, as a row's dict lacks the key.
    """
    for f in env.schema.fields:
        if f.name == name:
            return _Node(_functions().col(_quoted(name)), f.dataType)
    return None


def _node_of(frag):
    """
    The node maker of a fragment that reads one.
    """
    if not isinstance(frag, _Typed):
        raise TranslationError('a reference to a pattern path not supported in sparkize')
    return frag.node


# ---- Rows of a table search -----------------------------------------

# the column a gate's count of rows is read from, dropped after
_GATE = '_dotted_gate'


class _TableRows:
    """
    The rows of a group of row steps, not made yet: `make(df, env)`
    gives them from the table's DataFrame `df`, `env` the bindings and
    its schema. `unbound` are the substitutions they need.
    """

    def __init__(self, make, unbound=None):
        self.make = make
        self.unbound = dict(unbound or {})


def _unbound_of(*frags):
    """
    The substitutions `frags` need, a None among them needing none.
    """
    return _merged(*(frag.unbound for frag in frags if frag is not None))


def _gated(out, hits, exists):
    """
    The DataFrame `out`, or no rows of it unless `hits` has a row
    (none, when not `exists`): a cross join with the one-row count of
    `hits`, kept or not by its value.
    """
    F = _functions()
    found = hits.limit(1).agg(F.count(F.lit(1)).alias(_GATE))
    found = found.where(F.col(_GATE) > 0 if exists else F.col(_GATE) == 0)
    return out.crossJoin(found).drop(_GATE)


# ---- PySpark mixin --------------------------------------------------

class PySparkMixin(Translator):
    """
    PySpark-flavored translation hooks. Mix into a `SparkResolver`
    subclass. The walk itself is `Translator`'s; the hooks below return
    `ColumnFragment`s.
    """

    def translate(self, ops, root='column'):
        """
        Walk the parsed ops tree; populate `self.select`, `self.where`
        and `self.unbound`, and a table search's `self.table`,
        `self.offset` and `self.limit`.
        """
        raw, (self.table, self.offset, self.limit) = (
            self._translate_root(ops, root))
        self.select = raw.get('select')
        self.where = raw.get('where')
        self._from = raw.get('from')
        self.unbound = _merged(*(frag.unbound for frag in raw.values()))

    # ---- Translator hooks -------------------------------------------

    def _segment_value(self, value, index):
        """
        A key keeps its value, an int one too: `.0` reads a dict's key 0,
        never '0'.
        """
        return value

    def _column(self, name):
        """
        The named column, as a node.
        """
        return _Typed(lambda env: _column_node(env, name))

    def _all_columns(self):
        """
        `*`.
        """
        return ColumnFragment(lambda env: _functions().col('*'))

    def _row(self, table, columns):
        """
        The row as one struct of its `columns`, those the schema has; of
        all of them where `columns` is None.
        """
        def make(env):
            F = _functions()
            T = _types()
            fields = [f for f in env.schema.fields if columns is None or f.name in columns]
            if columns is not None:
                fields.sort(key=lambda f: columns.index(f.name))
            if not fields:
                empty = T.MapType(T.StringType(), T.StringType())
                return _Node(F.create_map().cast(empty), empty)
            row = F.struct(*(F.col(_quoted(f.name)).alias(f.name) for f in fields))
            return _Node(row, T.StructType(fields))
        return _Typed(make)

    def _select_expr(self, col, prefix):
        """
        The value the column's prefix segments reach: null where they
        reach nothing.
        """
        steps, unbound = self._prefix_steps(prefix)
        make = _node_of(col)
        return _Typed(lambda env: _literal_read(make(env), steps, env),
                      _merged(col.unbound, unbound))

    def _scalar_predicate(self, guard_op, col):
        """
        A guard on the column itself.
        """
        return self._guarded(guard_op, col, ())

    def _nested_predicate(self, guard_op, col, prefix):
        """
        A guard at `prefix` inside the column.
        """
        return self._guarded(guard_op, col, prefix)

    def _guarded(self, guard_op, col, prefix):
        """
        A guard at `prefix` inside the column, on a value that is there.
        """
        steps, unbound = self._prefix_steps(prefix)
        pred_fn, guard_unbound = self._guard_pred(guard_op)
        steps = steps + (('cond', pred_fn),)
        make = _node_of(col)
        return ColumnFragment(lambda env: _lit(_exists(make(env), steps, env)),
                              _merged(col.unbound, unbound, guard_unbound))

    def _combine_and(self, preds):
        """
        Fragments joined with `&`.
        """
        return functools.reduce(operator.and_, preds)

    def _combine_or(self, preds):
        """
        Fragments joined with `|`.
        """
        return functools.reduce(operator.or_, preds)

    def _combine_not(self, pred):
        """
        The fragment negated with `~`.
        """
        return ~pred

    def _combine_not_true(self, pred):
        """
        The fragment false or null: `~coalesce(pred, false)`.
        """
        make = pred._make
        return ColumnFragment(
            lambda env: ~_functions().coalesce(make(env), _functions().lit(False)),
            pred.unbound)

    # ---- rows of a table search -------------------------------------

    def _table_from(self, names):
        """
        None: the caller hands `rows` the table's DataFrame.
        """
        return None

    def _table_rows(self, names, preds, offset, limit):
        """
        The DataFrame's rows `where` the predicates, `offset` / `limit`
        the window.
        """
        pred = self._all_of(preds) if preds else None

        def make(df, env):
            if pred is not None:
                df = df.where(pred._make(env))
            if offset is not None:
                df = df.offset(offset)
            if limit is not None:
                df = df.limit(limit)
            return df
        return _TableRows(make, _unbound_of(pred))

    def _table_rows_kept(self, rows, pred, gates):
        """
        The rows `where` the predicate, each gate a cross join with the
        one-row count of its rows (`_gated`).
        """
        def make(df, env):
            out = rows.make(df, env)
            if pred is not None:
                out = out.where(pred._make(env))
            for hits, hit, exists in gates:
                found = hits.make(df, env)
                if hit is not None:
                    found = found.where(hit._make(env))
                out = _gated(out, found, exists)
            return out
        unbound = _merged(
            rows.unbound, _unbound_of(pred),
            *(_merged(hits.unbound, _unbound_of(hit)) for hits, hit, _ in gates))
        return _TableRows(make, unbound)

    def _table_rows_first(self, rows):
        """
        The rows' first: `limit(1)`.
        """
        return _TableRows(lambda df, env: rows.make(df, env).limit(1), rows.unbound)

    def _table_rows_union(self, parts):
        """
        The parts' rows `unionByName`, each kept as often as it comes.
        """
        def make(df, env):
            return functools.reduce(
                lambda a, b: a.unionByName(b), (part.make(df, env) for part in parts))
        return _TableRows(make, _merged(*(part.unbound for part in parts)))

    def _table_rows_from(self, rows):
        """
        The rows themselves, for `r.rows(df)` to make.
        """
        return rows

    def _pattern(self, col, prefix, ops):
        """
        A pattern path: `where` is whether any match is there, `select`
        the array of the matches; a path no op of which is a pattern,
        `data.a|int`, selects its one value, as `get` gives it.
        """
        prefix_steps, unbound = self._prefix_steps(prefix)
        op_steps, op_unbound = self._ops_steps(ops)
        steps = prefix_steps + op_steps
        unbound = _merged(col.unbound, unbound, op_unbound)
        make = _node_of(col)
        where = ColumnFragment(lambda env: _lit(_exists(make(env), steps, env)), unbound)
        if any(op.is_pattern() for op in ops):
            select = ColumnFragment(lambda env: _all_read(make(env), steps, env), unbound)
        else:
            select = _Typed(lambda env: _literal_read(make(env), steps, env), unbound)
        return {'select': select, 'where': where}

    # ---- segments ---------------------------------------------------

    def _prefix_steps(self, prefix):
        """
        One `get` step per prefix segment, plus the substitutions the
        reference segments need.
        """
        steps = []
        unbound = {}
        for kind, value in prefix:
            if kind == 'lit':
                steps.append(('get', lambda env, value=value: value))
                continue
            make = _node_of(value)
            steps.append(('get', lambda env, make=make: make(env)))
            unbound = _merged(unbound, value.unbound)
        return tuple(steps), unbound

    def _ops_steps(self, ops):
        """
        Translate a run of ops of a pattern path into pattern steps.
        Returns (steps, unbound).
        """
        steps = []
        unbound = {}
        for op in ops:
            op_steps, op_unbound = op.translated(_STEPS, self)
            steps.extend(op_steps)
            unbound = _merged(unbound, op_unbound)
        return tuple(steps), unbound

    def _group_steps(self, kind, branches, cuts, softs):
        """
        The one step for a group of `kind`: each branch's steps, for a
        first match or for a group of its kind, with the `cuts` and
        `softs` after them. Returns (steps, unbound).
        """
        parts = [self._ops_steps(branch) for branch in branches]
        branch_steps = tuple(steps for steps, _ in parts)
        unbound = _merged(*(unbound for _, unbound in parts))
        if kind == 'first':
            return [('first', branch_steps)], unbound
        return [('group', kind, branch_steps, cuts, softs)], unbound

    # ---- predicates -------------------------------------------------

    def _guard_pred(self, guard_op):
        """
        A ValueGuard as `(pred_fn(node, env), unbound)`.
        """
        return self._comparison(guard_op.pred_op.op, guard_op.guard,
                                guard_op.guard_transforms)

    def _comparison(self, op_str, val, transforms):
        """
        `node <op_str> val` as `(pred_fn(node, env), unbound)`, the node
        through the guard's transforms first (`|int`), as dotted's.
        """
        if op_str not in _OPS:
            raise TranslationError(f'unsupported predicate: {op_str}')
        test, unbound = val.translated(_COMPARISON, self, op_str)
        transforms = _checked(transforms)
        if not transforms:
            return test, unbound

        def pred_fn(node, env):
            return _parts_exist(_transformed(node, transforms), lambda n: test(n, env))
        return pred_fn, unbound

    def _filters_pred(self, filters):
        """
        A sequence of bracket filters (all must hold) as
        `(pred_fn(node, env), unbound)`.
        """
        return _joined(_and, (f.translated(_FILTER_PRED, self) for f in filters))

    def _filter_key_steps(self, key):
        """
        The steps from a filtered node to the values its key reaches: a
        key-like part reads a dict's members, the first one where more
        parts follow; a slot part reads as a slot does.
        """
        parts = self._key_parts(key)
        steps = []
        for n, part in enumerate(parts):
            steps.extend(part.translated(_FILTER_KEY_STEP, self, n == len(parts) - 1))
        return tuple(steps)


# ---- Reading ops ----------------------------------------------------

def _joined(join, preds):
    """
    `(pred_fn(node, env), unbound)` that joins the `(pred_fn, unbound)`
    pairs `preds` with `join`, `_and` or `_or`.
    """
    preds = list(preds)
    unbound = _merged(*(u for _, u in preds))
    fns = [fn for fn, _ in preds]
    return (lambda node, env: join(*(fn(node, env) for fn in fns))), unbound


class _Steps(Hooks):
    """
    An op of a pattern path as pattern steps: `(steps, unbound)`.
    """

    def other(self, op, t):
        raise TranslationError(
            f'op not supported in pattern paths: {type(op).__name__}'
        )

    def transform_wrap(self, op, t):
        steps, unbound = op.inner.translated(self, t)
        return steps + [('transform', _checked(op.transforms))], unbound

    def key(self, op, t):
        return op.op.translated(_KEY_STEP, t, 'key'), {}

    def slot(self, op, t):
        return op.op.translated(_KEY_STEP, t, 'slot'), {}

    def recursive(self, op, t):
        plain = (op.accessors is None and op.inner.translated(core.IS_WILDCARD)
                 and (op.depth_start, op.depth_stop, op.depth_step) == (None, None, None))
        if not plain:
            raise TranslationError(
                'a ** with depths or accessors, or of a key, not supported yet'
            )
        step = ('descend', 'aligned') if op.is_aligned() else ('descend',)
        if not op.filters:
            return [step], {}
        pred_fn, unbound = t._filters_pred(op.filters)
        return [step, ('cond', pred_fn)], unbound

    def slot_special(self, op, t):
        # an append slot is past the end: there is never a value there
        return [('cond', lambda node, env: False)], {}

    def slice(self, op, t):
        start, stop, step = op.args
        if not all(a is None or isinstance(a, int) for a in (start, stop, step)):
            raise TranslationError('a slice bound must be resolved: pass bindings= to sparkize')
        if step == 0:
            raise TranslationError('slice step cannot be zero')
        return [('slice', start, stop, step)], {}

    def slice_filter(self, op, t):
        # the filtered list as one value; whether its filters keep a
        # scalar, as they do a dict's keys
        pred_fn, unbound = t._filters_pred(op.filters)
        keeps_scalar = all(f.is_filtered(0) for f in op.filters)
        return [('listfilter', pred_fn, keeps_scalar)], unbound

    def filter_wrap(self, op, t):
        steps, inner_unbound = op.inner.translated(self, t)
        pred_fn, unbound = t._filters_pred(op.filters)
        return steps + [('cond', pred_fn)], _merged(inner_unbound, unbound)

    def value_guard(self, op, t):
        steps, inner_unbound = op.inner.translated(self, t)
        pred_fn, unbound = t._guard_pred(op)
        return steps + [('cond', pred_fn)], _merged(inner_unbound, unbound)

    def group(self, op, kind, branches, cuts, softs, t):
        return t._group_steps(kind, branches, cuts, softs)

    def not_group(self, op, t):
        kinds = t._access_kinds(op.inner)
        if not kinds or kinds[-1] is ...:
            raise TranslationError(
                'a negation reaching any depth not supported yet in sparkize'
            )
        steps, unbound = t._ops_steps(op.inner)
        return [('not', steps, kinds)], unbound


class _KeyStep(Hooks):
    """
    A Key's or Slot's matcher as pattern steps, `how` 'key' or 'slot':
    a literal reads in by its value, a wildcard every member it reads.
    """

    def other(self, m, t, how):
        raise TranslationError(f'unsupported matcher: {type(m).__name__}')

    def const(self, m, t, how):
        value = m.value
        return [('get', lambda env: value)]

    def wildcard(self, m, t, how):
        if m.is_aligned():
            return [('each', how, 'aligned')]
        return [('each', how)]

    def wildcard_first(self, m, t, how):
        # the first of the wildcard's matches alone: its first-match group
        return [('first', ((('each', how),),))]

    def pattern(self, m, t, how):
        raise TranslationError(
            f'unsupported matcher in pattern path: {type(m).__name__}'
        )

    def subst(self, m, t, how):
        raise TranslationError(f'unresolved substitution in pattern path: {m}')

    def reference(self, m, t, how):
        raise TranslationError('references inside pattern paths not supported yet')


class _Comparison(Hooks):
    """
    A guard's or filter's value as `(test(node, env), unbound)`: Python's
    `<value at node> <op_str> value`, the node there. A substitution's
    value is looked up in the bindings.
    """

    def other(self, val, t, op_str):
        raise TranslationError(f'unsupported guard value: {type(val).__name__}')

    def const(self, val, t, op_str):
        value = val.value
        return (lambda node, env: _compared(node, op_str, value, env)), {}

    def none_value(self, val, t, op_str):
        return (lambda node, env: _compared(node, op_str, None, env)), {}

    def subst(self, val, t, op_str):
        name = val.value
        return (lambda node, env: _compared(node, op_str, env[name], env)), {name: name}

    def regex(self, val, t, op_str):
        pattern = val.args[0]
        _java_regex(pattern)
        if op_str == '=':
            return (lambda node, env: _regex_holds(node, pattern)), {}
        if op_str == '!=':
            return (lambda node, env: _not(_regex_holds(node, pattern))), {}
        value = val.value
        return (lambda node, env: _compared(node, op_str, value, env)), {}

    def pattern(self, val, t, op_str):
        raise TranslationError(
            f'pattern guard values not supported in sparkize: {type(val).__name__}'
        )


class _FilterPred(Hooks):
    """
    A bracket filter as `(pred_fn(node, env), unbound)`: a key-value
    filter holds for a dict some value of whose key compares; `!=` is
    `=`'s negation, so it holds for a node that is no dict.
    """

    def other(self, f, t):
        raise TranslationError(f'filter type not supported: {type(f).__name__}')

    def key_value_filter(self, f, t):
        if f.key.is_packed():
            raise TranslationError('a packed filter key not supported in sparkize')
        op_str = f._eq_str
        test, unbound = t._comparison('=' if op_str == '!=' else op_str, f.val,
                                      f.guard_transforms)
        steps = t._filter_key_steps(f.key) + (('cond', test),)

        def pred_fn(node, env):
            held = False
            if node.kind in ('struct', 'map'):
                held = _and(node.col.isNotNull(), _exists(node, steps, env))
            return _not(held) if op_str == '!=' else held
        return pred_fn, unbound

    def and_filter(self, f, t):
        return _joined(_and, (sub.translated(self, t) for sub in f.filters))

    def or_filter(self, f, t):
        return _joined(_or, (sub.translated(self, t) for sub in f.filters))

    def not_filter(self, f, t):
        inner_fn, unbound = f.inner.translated(self, t)
        return (lambda node, env: _not(inner_fn(node, env))), unbound

    def group_filter(self, f, t):
        return f.inner.translated(self, t)


class _FilterKeyStep(Hooks):
    """
    A filter key's part as steps, `last` if no part follows: a key-like
    part a dict's member (a wildcard all of them, or the first where
    parts follow), a slot as a slot reads.
    """

    def other(self, part, t, last):
        raise TranslationError(f'unsupported filter key part: {type(part).__name__}')

    def const(self, part, t, last):
        value = part.value
        return [('member', lambda env: value)]

    def wildcard(self, part, t, last):
        if last:
            return [('each', 'key')]
        return [('first', ((('each', 'key'),),))]

    def slot(self, part, t, last):
        steps = part.op.translated(_KEY_STEP, t, 'slot')
        if last or steps[0][0] != 'each':
            return steps
        return [('first', (tuple(steps),))]

    def slice(self, part, t, last):
        return _STEPS.slice(part, t)[0]


_STEPS = _Steps()
_KEY_STEP = _KeyStep()
_COMPARISON = _Comparison()
_FILTER_PRED = _FilterPred()
_FILTER_KEY_STEP = _FilterKeyStep()


# ---- Flavor classes -------------------------------------------------

@flavor('pyspark')
class PySparkResolver(PySparkMixin, SparkResolver):
    """
    PySpark, classic or Spark Connect.
    """
