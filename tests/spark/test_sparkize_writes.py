"""
sparkize writes as dotted does: each write's `as_update`, applied with
`df.withColumns`, against `dotted.update` on each row as Python sees
it. A row Spark can't hold dotted's result for, or one dotted's update
raises on, fails the job with a `dotted update:` message.
"""
import copy

import pytest

pytest.importorskip('pyspark')

from pyspark.sql import functions as F

import dotted
from dotted.translators import TranslationError

from .kinds import fits, same


_WRITES = [
    'st.x', 'st.y', 'st.z[0]', 'st.z[5]', 'st.z[-1]', 'st.z[-9]', 'st.z[+]', 'st.z[+?]',
    'st.z[*]', 'st.*', 'st.*?', 'st.~x', 'st.w', 'st.w.q',
    'm.k', 'm.z', 'm.*', 'm.*?', 'm.k.q', 'm.0', 'm.* = 1', 'm.~k', 'mi.0', 'mi[9]', 'mi.k',
    'arr[0]', 'arr[5]', 'arr[-1]', 'arr[-5]', 'arr.5', 'arr[*]', 'arr.*', 'arr[+]', 'arr[+?]',
    'arr[*] = 1', 'arr[*?]', 'arr.k', 'arr[~0]',
    'ast[*].x', 'ast[*&x=1].y', 'ast[0].w', 'ast[+]', 'ast[*].x = 1', 'ast[*?].x', 'ast[5].x',
    'mm.p.role', 'mm.*.role', 'mm.*&role="admin"', 'mm.new.role',
    'nest.a.role', 'nest.b.z.k', 'nest.b.*.role', 'nest.b.new.role',
    's', 'i', 'st', 'st = 1', 'st.x = 5', 's.k', 'i[0]', 'sarr[+?]',
]

_VALUES = ['V', 7, None, True, 1.5, {'x': 9, 'y': 'q'}, [1, 2]]


def _updated(rows, path, value):
    """
    dotted's update of each row, or the error it raises.
    """
    out = []
    for row in rows:
        try:
            out.append(dotted.update(copy.deepcopy(row), path, value))
        except Exception as e:
            out.append(e)
    return out


@pytest.mark.parametrize('path', _WRITES)
def test_writes_as_dotted(kinds, path):
    """
    For each value: the rows whose result Spark's new schema holds give
    dotted's result; the rest fail the job, as dotted's update raises or
    as Spark can't hold what it writes.
    """
    df, rows = kinds
    r = dotted.sparkize(path, flavor='pyspark')
    for value in _VALUES:
        want = _updated(rows, path, value)
        cols = r.build(r.as_update(value), schema=df.schema)
        schema = df.withColumns(cols).schema
        held = [w for w in want if not isinstance(w, Exception)
                and all(fits(w.get(f.name), f.dataType) for f in schema.fields)]
        ids = [w['id'] for w in held]
        got = (df.where(F.col('id').isin(ids)).withColumns(cols).orderBy('id').collect()
               if ids else [])
        for w, g in zip(held, got):
            assert same(w, g.asDict(recursive=True)), (path, value, w['id'])
        rest = [row['id'] for row, w in zip(rows, want) if isinstance(w, Exception) or w not in held]
        if rest:
            with pytest.raises(Exception, match='dotted update'):
                df.where(F.col('id').isin(rest)).withColumns(cols).collect()


def test_writes_a_column_binding(kinds):
    """
    A Column written is read by its type.
    """
    df, rows = kinds
    r = dotted.sparkize('st.x', flavor='pyspark')
    cols = r.build(r.as_update(F.col('i')), schema=df.schema)
    got = df.where(F.col('st').isNotNull()).withColumns(cols).orderBy('id').collect()
    for g in got:
        want = next(row for row in rows if row['id'] == g.id)
        assert same(dotted.update(copy.deepcopy(want), 'st.x', want['i']), g.asDict(recursive=True))


def test_build_of_a_write_is_a_dict(kinds):
    """
    `build` makes a write a dict of the columns it touches.
    """
    df, _ = kinds
    r = dotted.sparkize('m.k', flavor='pyspark')
    assert list(r.build(r.as_update(1), schema=df.schema)) == ['m']


@pytest.mark.parametrize('path', [
    'st.(x, y)', 'st.(!x)', 'nest.**.role', 'arr[1:2]',
])
def test_unsupported_writes_raise(path):
    """
    Groups, negations, `**` and slices in a write raise, as in sqlize.
    """
    with pytest.raises(TranslationError):
        dotted.sparkize(path, flavor='pyspark').as_update(1)
