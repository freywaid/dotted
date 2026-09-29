"""
Data builders for the benchmark suites.
"""


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
