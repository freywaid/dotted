"""
Command line for the benchmark suites.

    python -m benchmarks                     all cases, every available copier
    python -m benchmarks update remove       only cases whose id contains a filter
    python -m benchmarks --copier copy       one deepcopy implementation
    python -m benchmarks --save base.json    record results
    python -m benchmarks --compare base.json show change against a recording
"""
import argparse
import datetime
import json
import platform
import sys

from . import harness
from .suites import CASES

try:
    from importlib.metadata import version
except ImportError:
    version = None


def installed(package):
    """
    Installed version of a package, or None.
    """
    if version is None:
        return None
    try:
        return version(package)
    except Exception:
        return None


def fmt(seconds):
    """
    Render a per-call time with a unit that keeps three significant digits.
    """
    if seconds is None:
        return '-'
    if seconds < 1e-3:
        return f'{seconds * 1e6:.2f}us'
    if seconds < 1:
        return f'{seconds * 1e3:.2f}ms'
    return f'{seconds:.2f}s'


def saved(base, other):
    """
    Percent of `base`'s time that `other` saves; negative when it is slower.
    """
    if base is None or other is None:
        return '-'
    return f'{(base - other) / base * 100:+.1f}%'


def header(width, names, baseline):
    head = f'{"case":{width}}' + ''.join(f'{n:>12}' for n in names)
    if len(names) > 1:
        head += f'{"time saved":>12}'
    if baseline is not None:
        head += ''.join(f'{"vs " + n:>12}' for n in names)
    return head


def row(case, times, width, names, baseline):
    cols = [times.get(n) for n in names]
    line = f'{case.id:{width}}' + ''.join(f'{fmt(t):>12}' for t in cols)
    if len(names) > 1:
        line += f'{saved(cols[0], cols[-1]):>12}'
    if baseline is None:
        return line
    return line + ''.join(
        f'{saved(baseline.get(n, {}).get(case.id), t):>12}'
        for n, t in zip(names, cols))


def progress(text):
    """
    Show what is running on the terminal's current line; rows overwrite it.
    Silent when stderr is not a terminal, so redirected output stays clean.
    """
    if not sys.stderr.isatty():
        return
    sys.stderr.write('\r\033[K' + text)
    sys.stderr.flush()


def main():
    parser = argparse.ArgumentParser(prog='python -m benchmarks')
    parser.add_argument('filters', nargs='*', help='run cases whose id contains any of these')
    parser.add_argument('--copier', choices=sorted(harness.copiers()), help='time one deepcopy implementation')
    parser.add_argument('--save', metavar='FILE', help='write results as JSON')
    parser.add_argument('--compare', metavar='FILE', help='show percent time saved against recorded results')
    args = parser.parse_args()

    names = [args.copier] if args.copier else list(harness.copiers())
    cases = [c for c in CASES if not args.filters or any(f in c.id for f in args.filters)]
    if not cases:
        parser.error('no cases match')

    meta = {
        'date': datetime.datetime.now().isoformat(timespec='seconds'),
        'python': platform.python_version(),
        'platform': platform.platform(),
        'dotted': installed('dotted_notation'),
        'copium': installed('copium'),
    }
    print(' '.join(f'{k}={v}' for k, v in meta.items()))
    print()

    baseline = None
    if args.compare:
        with open(args.compare) as f:
            baseline = json.load(f)['results']

    width = max(len(c.id) for c in cases) + 2
    head = header(width, names, baseline)
    print(head)
    print('-' * len(head))

    results = {name: {} for name in names}
    progress(f'[1/{len(cases)}] {cases[0].id} ...')
    for i, (case, times) in enumerate(harness.run(cases, names), 1):
        progress('')
        print(row(case, times, width, names, baseline), flush=True)
        for name, t in times.items():
            results[name][case.id] = t
        if i < len(cases):
            progress(f'[{i + 1}/{len(cases)}] {cases[i].id} ...')

    if not args.save:
        return
    with open(args.save, 'w') as f:
        json.dump({'meta': meta, 'results': results}, f, indent=2)
    print(f'\nsaved {args.save}')


if __name__ == '__main__':
    main()
