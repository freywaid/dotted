"""
sparkize reads as dotted does: each path's `where` against `dotted.has`
and its `select` against `dotted.get`, on each row as Python sees it,
`row.asDict(recursive=True)`, over a DataFrame of every kind of column.
"""
import datetime
import decimal
import math

import pytest

pytest.importorskip('pyspark')

from pyspark.sql import Row, functions as F

import dotted
from dotted.translators import TranslationError


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

# comparisons, as Python compares
_COMPARISONS = [
    's = "active"', 's != "active"', 's = 30', 's = "30"', 's < "b"', 's >= "a"', 's = None', 's != None',
    'i = 30', 'i = 30.0', 'i = True', 'i != 1', 'i < 1.5', 'i <= 1.5', 'i > -5.5', 'i >= -5', 'i = "30"',
    'l = 1099511627776', 'l > 1e12', 'l < -4.6e18', 'l = 7.0',
    'd = 0.1', 'd > 0', 'd < 1e17', 'd = 7', 'd != 1', 'd = True', 'd >= 10000000000000000',
    'f = 1.5', 'f = 0.1', 'f > 1e300', 'f < 0', 'f = 0',
    'b = True', 'b = 1', 'b = 1.0', 'b != False', 'b < 1', 'b = None',
    'dec = 1', 'dec = 0.1', 'dec > 0.1', 'dec = 7', 'dec < -3.249', 'dec <= -3.25', 'dec = 1.0',
    'st.x = None', 'st.x != 1', 'm.k = None', 'm.k = 1', 'mi[5] = None', 'mi.0 = "zero"',
]

# regexes fullmatch str() of the value
_REGEXES = [
    's = /act.*/', 's = /act/', 's = /\\d+/', 's != /a.*/', 's = /N.*/', 's = /(?i)ACTIVE/',
    's = /\\s*7\\s*/', 's = /a(?=c)ctive/', 's = /[^a]+/', 's = /\\w+/', 's = /(a|x)\\w*/',
    's = /.*\\Z/', 's = /(?s).+/', 's = /(?x) a c t ive /', 's = /\\bactive\\b/',
    's = /[a-c]\\S+/', 's = /(?:x|y)+z/', 's = /\\x61.*/', 'sarr[*] = /^$/', 'sarr[*] = /\\d_?\\d*/',
    'i = /3./', 'i = /-?\\d/', 'd = /1\\.0/', 'd = /0\\.1/', 'd = /1e\\+16/', 'd = /7\\.0/',
    'f = /inf/', 'f = /-0\\.0/', 'f = /1\\.5/', 'b = /True/', 'b = /F.*/', 'dec = /1\\.00/',
    'dt = /2024.*/', 'dt = /None/', 'arr = /x/', 'st = /None/',
]

# transforms, as dotted's
_TRANSFORMS = [
    's|int = 30', 's|int = 7', 's|int > 0', 's|float = 30', 's|float = 7.0', 's|str = "30"',
    'i|str = "30"', 'd|int = 1', 'd|int = 0', 'd|str = "0.1"', 'd|str = "1e+16"', 'f|str = "1.5"',
    'b|str = "True"', 'b|int = 1', 'i|float = 30.0', 'dec|int = 7', 'dec|str = "1.00"',
    'dec|float = 0.1', 'darr[*]|str = "nan"', 'darr[*]|str = "1e-05"',
    'darr[*]|str = "123456789.0"', 'st.x|str = "None"', 'sarr[*]|int = 1000',
    'arr[*]|float', 'arr[0]|str', 'darr[*]|str', 'ast[*].x|str', 'm.*|int',
]

# keys, slots, wildcards and slices, each node read by its type
_READS = [
    's', 'i', 'l', 'd', 'f', 'b', 'dec', 'dt', 'arr', 'st', 'm', 'nest',
    'arr[0]', 'arr[-1]', 'arr[5]', 'arr.0', 'arr[*]', 'arr[*] = 1', 'arr[*] != 1', 'arr[*] = None',
    'arr[*] > 2', 'arr[1:]', 'arr[:-1]', 'arr[::2]', 'arr[::-1]', 'arr[1:2]', 'arr.*', 'arr.k',
    'arr[*?]', 's[0]', 's[-1]', 's[*]', 's[1:]', 's[::-1]', 's.0', 's[5]',
    'sarr[*]', 'sarr[0]', 'sarr[*] = ""',
    'm.k', 'm.z', 'm.*', 'm[*]', 'm.* = 2', 'm.*?', "m['k']", 'm.7', "m['7']",
    'mi.0', 'mi[0]', 'mi.1', 'mi.k', 'mi.*', 'mi[5]',
    'st.x', 'st.y', 'st.z', 'st.z[0]', 'st.w', 'st.z[*]',
    'ast[*]', 'ast[*].x', 'ast[*].y', 'ast[*].x = 1', 'ast.x', 'ast[0].x',
    'mm.*', 'mm.*.role', 'mm.p.role', 'nest.a.role', 'nest.b.*.role',
    'mi.$$(i)', 'arr.$$(i)', 'arr[$$(i)]', 'm.$$(s)', 'mi.$$(i) = "one"',
]

# filters and slice filters
_FILTERS = [
    'ast[*&x=1]', 'ast[*&x!=1]', 'ast[*&y=None]', 'ast[*&x>1].y', 'ast[x=1]', 'ast[x!=1]',
    'ast[*&!x=1]', 'ast[*&(x=1, x=5)]', 'ast[*&x=1&y="a"]', 'mm.*&role="admin"',
    'mm[*&role="admin"].x', 'm[k=1]', 'st[x=1]', 'arr[x=1]', 'i[x=1]', 's[x=1]',
    'mm.**&role="admin"', 'nest.**&k=1',
]

# groups, cuts, soft cuts, first matches, negations, aligned, **
_GROUPS = [
    'mm.**.role', 'mm.**.role = "admin"', 'nest.**.role = "admin"', 'nest.(a.role, b.*.role)',
    'nest.(a.role, b.z.role)?', 'nest.(b.*.role, a.role)?', 'nest.b.(z, y)',
    'm.(k, z)', 'm.(k#, m)', 'm.(z, k)?', 'm.(!k)', 'mm.(!p)', 'm.(k##, *)', 'm.(*##, k)',
    'mm.(p##, *)', 'st.*?',
    'ast[*+].x', 'ast[*+&x=1].y', 'arr[*+]', 'arr[*+] = 1', 'mm.*+.role', 'sarr[*+]'
    'mm.**+.role', 'nest.b.**+.role',
]

PATHS = _COMPARISONS + _REGEXES + _TRANSFORMS + _READS + _FILTERS + _GROUPS

# their matches are of more than one type, which no Spark array holds:
# `where` reads, `select` raises
MIXED = [
    'st.*', 'st[*]', 'st.**', 'ast[*].(x, y)', 'ast[*].(x = 1 & y = "a")', 'mm.**', 'nest.**',
    'nest.**.role', 'nest.**.k', 'nest.*', 'nest[*]', 'nest.a.*', 'nest.(a, b)', 'nest.(a#, b)',
    'nest.(a##, b)', '(s, i)', '(s = "active", i = 30)', '(s#, i)', '(i, s)?', 'st.(!x)',
    'mm.(!*.role)', 'sarr[*]|int', 'st.$$(s)', 'mm.(*.role##, *.x)',
]


@pytest.fixture(scope='module')
def kinds(spark):
    """
    The DataFrame of every kind of column, and its rows as Python sees
    them, by id.
    """
    df = spark.createDataFrame(ROWS, SCHEMA)
    return df, [row.asDict(recursive=True) for row in df.orderBy('id').collect()]


def _same(a, b):
    """
    Python's ==, types and NaN told apart as dotted's values are.
    """
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def _py(value):
    """
    A selected Spark value as Python sees a row's.
    """
    if isinstance(value, Row):
        return {k: _py(v) for k, v in value.asDict().items()}
    if isinstance(value, list):
        return [_py(v) for v in value]
    if isinstance(value, dict):
        return {k: _py(v) for k, v in value.items()}
    return value


def _dotted(rows, path, bindings):
    """
    dotted's (has, get) per row, or the error it raises on one.
    """
    out = []
    for row in rows:
        try:
            b = bindings(row) if bindings else None
            out.append((dotted.has(row, path, bindings=b), dotted.get(row, path, bindings=b)))
        except Exception as e:
            return e
    return out


def _columns(df, path, n, select=True, built=None):
    """
    The path's `where` and `select` Columns, aliased by `n`. A guard's
    `select` is the value it tests, as in sqlize: only its `where` is
    compared.
    """
    r = dotted.sparkize(path, flavor='pyspark')
    guarded = (not dotted.is_pattern(path)
               and type(dotted.parse(path).ops[-1]).__name__ == 'ValueGuard')
    cols = []
    if r.where is not None:
        cols.append(r.build(r.where, schema=df.schema, **(built or {})).alias(f'w{n}'))
    if select and not guarded:
        cols.append(r.build(r.select, schema=df.schema, **(built or {})).alias(f's{n}'))
    return cols


def _compare(path, n, rows, want, got):
    """
    Assert the Columns of path `n` gave dotted's answers, row by row.
    """
    for row, (has, value), g in zip(rows, want, got):
        if f'w{n}' in g:
            assert bool(g[f'w{n}']) == has, (path, row['id'], 'where')
        if f's{n}' not in g:
            continue
        sv = _py(g[f's{n}'])
        if dotted.is_pattern(path):
            assert _same(list(value or ()), sv or []), (path, row['id'], value, sv)
        else:
            assert _same(value, sv), (path, row['id'], value, sv)


def _check(kinds, cases, select=True):
    """
    Assert each case's `where`, and its `select` unless not `select`,
    gives dotted's answer on every row, the paths read in one job; a
    path dotted raises on fails its own job with a `dotted:` message.
    A case is a path, or (path, Spark bindings, dotted's bindings of a
    row).
    """
    df, rows = kinds
    cases = [(case, None, None) if isinstance(case, str) else case for case in cases]
    cols = []
    judged = []
    for n, (path, built, bindings) in enumerate(cases):
        want = _dotted(rows, path, bindings)
        made = _columns(df, path, n, select, built)
        if isinstance(want, Exception):
            with pytest.raises(Exception, match='dotted:'):
                df.select('id', *made).collect()
            continue
        cols.extend(made)
        judged.append((path, n, want))
    got = [row.asDict() for row in df.select('id', *cols).orderBy('id').collect()]
    for path, n, want in judged:
        _compare(path, n, rows, want, got)


@pytest.mark.parametrize('paths', [
    _COMPARISONS, _REGEXES, _TRANSFORMS, _READS, _FILTERS, _GROUPS,
], ids=['comparisons', 'regexes', 'transforms', 'reads', 'filters', 'groups'])
def test_reads_as_dotted(kinds, paths):
    """
    `where` is `has` and `select` is `get`, row by row.
    """
    _check(kinds, paths)


def test_mixed_matches_where_reads_select_raises(kinds):
    """
    Matches of more than one type can't share a Spark array: `where`
    reads as `has`; `select` raises.
    """
    df, _ = kinds
    _check(kinds, MIXED, select=False)
    for path in MIXED:
        r = dotted.sparkize(path, flavor='pyspark')
        with pytest.raises(TranslationError, match='more than one type'):
            r.build(r.select, schema=df.schema)


def test_bindings_compare_as_python(kinds):
    """
    A bound value compares as Python compares it.
    """
    _check(kinds, [
        ('i = $(v)', {'v': 30}, lambda row: {'v': 30}),
        ('i >= $(v)', {'v': 1.5}, lambda row: {'v': 1.5}),
        ('s = $(v)', {'v': None}, lambda row: {'v': None}),
        ('s != $(v)', {'v': 'active'}, lambda row: {'v': 'active'}),
        ('arr[*] = $(v)', {'v': True}, lambda row: {'v': True}),
        ('d < $(v)', {'v': D('0.1')}, lambda row: {'v': D('0.1')}),
        ('ast[*&x=$(v)].y', {'v': 1}, lambda row: {'v': 1}),
    ])


def test_column_bindings_compare_by_type(kinds):
    """
    A Column bound compares by its type, read in the session: as dotted
    with the row's own value bound.
    """
    def bound(column):
        return lambda row: {'v': row[column]}
    _check(kinds, [(path, {'v': F.col(column)}, bound(column)) for path, column in [
        ('i = $(v)', 'l'),
        ('d > $(v)', 'i'),
        ('i < $(v)', 'd'),
        ('s = $(v)', 's'),
        ('b = $(v)', 'i'),
    ]])
