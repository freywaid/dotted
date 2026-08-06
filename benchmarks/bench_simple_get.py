"""
Benchmark: get()'s simple-path fast path vs the walk() machinery (issue #58).

Simple paths (literal Key/Attr/Slot chains) resolve via engine.simple_get();
everything else goes through walk(). This times both on the same paths by
driving the walk machinery directly, so the comparison stays valid as long
as both code paths exist.

Run: python benchmarks/bench_simple_get.py
"""
import timeit

import dotted
from dotted import engine
from dotted.api import parse

obj = {
    'amount': {'value': 125.50, 'currency': 'USD'},
    'journal': 'ledger-1',
    'references': {'venmo': {'transaction_id': 'abc123'}},
}

PATHS = [
    'amount.value',
    'journal',
    'references.venmo.transaction_id',
    'references.venmo.missing',
    'amount.value|int',
]


def walk_get(ops, obj, default=None):
    """
    Resolve a non-pattern path through the walk machinery, as get() does
    when the fast path doesn't apply.
    """
    vals = engine.iter_until_cut(engine.gets(ops, obj, strict=False))
    vals = (ops.apply(v) for v in vals)
    found = tuple(vals)
    return found[0] if found else default


def main():
    n = 100_000
    print(f'{n:,} iterations per case\n')
    print(f'{"path":40}{"walk":>12}{"fast":>12}{"speedup":>10}')
    for p in PATHS:
        ops = parse(p)
        t_walk = timeit.timeit(lambda: walk_get(ops, obj), number=n)
        t_fast = timeit.timeit(lambda: dotted.get(obj, p), number=n)
        print(f'{p:40}{t_walk:>10.3f}s{t_fast:>10.3f}s{t_walk / t_fast:>9.1f}x')

    # pre-parsed Dotted: skips parse() and its cache lookup entirely
    ops = parse('references.venmo.transaction_id')
    t_pre = timeit.timeit(lambda: dotted.get(obj, ops), number=n)
    print(f'\n{"pre-parsed Dotted, fast path":40}{t_pre:>22.3f}s')


if __name__ == '__main__':
    main()
