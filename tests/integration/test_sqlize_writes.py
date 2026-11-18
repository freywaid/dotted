"""
sqlize writes as dotted does: each write's `as_update`, run as an
`UPDATE` of the seeded table (rolled back), against `dotted.update` on
each row's `data`. A row dotted's update raises on fails the statement
with a `dotted update:` message.
"""
import copy
import json

import pytest

import dotted
from dotted import sqlize

from .conftest import _seed_rows


_WRITES = [
    'data.user.role', 'data.user.nick', 'data.users[0].age', 'data.users[+]',
    'data.users[*].active', 'data.users[*&age>30].flag',
    'data.(user.role, user.nick)', 'data.user.(age#, nick)', 'data.user.(nick#, age)',
    'data.users[(*&age=25#, +)]', 'data.(user.x&user.y)', 'data.(user.age&users[*].x)',
    'data.user.(nick, role)?', 'data.user.(missing, other)?', 'data.users[*].(age, flag)',
    'data.(user.~role#, user.role)', 'data.users[(0#, +)]',
]

_VALUES = ['V', 7, None]


def _expected(path, value):
    """
    dotted's update of each seeded row's data, by id, or the error it
    raises on one.
    """
    out = {}
    for rid, _, _, _, data in _seed_rows():
        try:
            row = dotted.update({'data': copy.deepcopy(data)}, path, value)
        except Exception as e:
            return e
        out[rid] = json.loads(json.dumps(row['data']))
    return out


@pytest.mark.parametrize('path', _WRITES)
def test_writes_as_dotted(query, path):
    """
    For each value, the UPDATE gives every row dotted's result, or fails
    where dotted's update raises.
    """
    r = sqlize(path, driver=query.driver)
    for value in _VALUES:
        want = _expected(path, value)
        sql, args = r.build('WITH up AS (UPDATE dotted_test.items SET ' + r.as_update(value)
                            + ' RETURNING id, data) SELECT id, data::text AS data FROM up')
        if isinstance(want, Exception):
            with pytest.raises(Exception, match='dotted update'):
                query(sql, args)
            continue
        got = {row['id']: json.loads(row['data']) for row in query(sql, args)}
        assert got == want, (path, value)


@pytest.mark.parametrize('path', ['data.(a##, b)', 'data.(!a)', 'data.**.a', 'data.a[1:2]'])
def test_unsupported_writes_raise(path):
    """
    A soft cut, a negation, `**` and slices in a write raise for now.
    """
    with pytest.raises(dotted.TranslationError):
        sqlize(path, driver='asyncpg').as_update(1)
