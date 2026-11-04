"""
Data builders for the benchmark suites.
"""
import collections


def record(i):
    """
    One JSON-like record.
    """
    return {
        'id': i,
        'name': f'user-{i}',
        'score': i * 1.5,
        'active': i % 2 == 0,
        'tags': ['a', 'b', 'c'],
        'address': {
            'city': f'city-{i % 10}',
            'geo': {'lat': 1.0 + i, 'lng': 2.0 + i},
        },
    }


def doc(n):
    """
    A document holding n records twice: keyed under `users`, listed under `rows`.
    """
    return {
        'meta': {'version': 1, 'source': 'bench'},
        'users': {f'u{i}': record(i) for i in range(n)},
        'rows': [record(i) for i in range(n)],
    }


def small():
    return doc(10)


def large():
    return doc(1000)


def chained(n):
    """
    doc(n) behind a layer of overrides in a ChainMap, the way settings are
    layered: a read of anything but `meta` falls through to the document.
    """
    return collections.ChainMap({'meta': {'version': 2}}, doc(n))


def small_chained():
    return chained(10)


def large_chained():
    return chained(1000)
