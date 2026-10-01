"""
The benchmark cases, grouped by suite.
"""
import dotted

from .data import large, small
from .harness import Case

SIZES = (('n=10', small), ('n=1000', large))

CASES = []


def add(suite, name, fn, sizes=SIZES, **kwargs):
    """
    Register `fn` as one case per data size.
    """
    for label, make in sizes:
        CASES.append(Case(suite, f'{name} ({label})', make, fn, **kwargs))


# -- reads

add('get', 'users.u5.address.geo.lat',
    lambda d: dotted.get(d, 'users.u5.address.geo.lat'))
add('get', 'users.*.score',
    lambda d: dotted.get(d, 'users.*.score'))
add('get', 'users.*.address.geo.lat',
    lambda d: dotted.get(d, 'users.*.address.geo.lat'))
add('get', 'rows[*].address.city',
    lambda d: dotted.get(d, 'rows[*].address.city'))
add('get', 'users.*.tags[*]',
    lambda d: dotted.get(d, 'users.*.tags[*]'))
add('get', 'users./u1.*/.score',
    lambda d: dotted.get(d, 'users./u1.*/.score'))
add('get', '**.lat',
    lambda d: dotted.get(d, '**.lat'))
add('pluck', 'users.*.score',
    lambda d: dotted.pluck(d, 'users.*.score'))
add('expand', 'users.*.score',
    lambda d: dotted.expand(d, 'users.*.score'))

# -- writes in place

add('update', 'users.u5.address.geo.lat',
    lambda d: dotted.update(d, 'users.u5.address.geo.lat', 0))
# a late key and an absent one: a concrete key's cost depends on where it sits
add('update', 'users.u995.address.geo.lat',
    lambda d: dotted.update(d, 'users.u995.address.geo.lat', 0), sizes=SIZES[1:])
add('remove', 'users.absent.address.geo.lat',
    lambda d: dotted.remove(d, 'users.absent.address.geo.lat'), sizes=SIZES[1:])
add('update', 'users.*.score',
    lambda d: dotted.update(d, 'users.*.score', 0))
add('update', 'rows[*].address.city',
    lambda d: dotted.update(d, 'rows[*].address.city', 'x'))
add('remove', 'users.u5.address.geo.lat',
    lambda d: dotted.remove(d, 'users.u5.address.geo.lat'), fresh=True)
add('remove', 'users.*.score',
    lambda d: dotted.remove(d, 'users.*.score'), fresh=True)

# -- writes on a copy

add('update mutable=False', 'users.u5.address.geo.lat',
    lambda d: dotted.update(d, 'users.u5.address.geo.lat', 0, mutable=False),
    copies=True)
add('update mutable=False', 'users.*.score',
    lambda d: dotted.update(d, 'users.*.score', 0, mutable=False),
    copies=True)
add('update_multi mutable=False', '3 paths',
    lambda d: dotted.update_multi(d, (
        ('users.u5.score', 0),
        ('users.u5.address.city', 'x'),
        ('meta.version', 2),
    ), mutable=False),
    copies=True)
add('remove mutable=False', 'users.u5.address.geo.lat',
    lambda d: dotted.remove(d, 'users.u5.address.geo.lat', mutable=False),
    copies=True)
add('remove mutable=False', 'users.*.score',
    lambda d: dotted.remove(d, 'users.*.score', mutable=False),
    copies=True)

# -- build copies its leaves with the stdlib, whichever copier is installed

add('build', 'users.*.address',
    lambda d: dotted.build(d, 'users.*.address'))
