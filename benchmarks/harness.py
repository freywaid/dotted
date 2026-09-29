"""
Benchmark harness: case definition, timing, and the deepcopy switch.

A Case times one callable against one piece of data. Cases that deep-copy
(`copies=True`) are timed once per deepcopy implementation; the rest are
timed once, since the copier cannot affect them. Cases that consume their
input (`fresh=True`, e.g. an in-place remove) get a new copy of the data
for every call, made outside the timed region.
"""
import contextlib
import copy
import time

from dotted import utils

# Seconds each timing repeat should run for, how many repeats to take the
# best of, and the most seconds to spend on one case per copier.
TARGET = 0.1
REPEATS = 5
BUDGET = 1.0


class Case:
    """
    One benchmark: `fn(data)` timed against the data built by `make()`.
    """
    def __init__(self, suite, name, make, fn, copies=False, fresh=False):
        self.suite = suite
        self.name = name
        self.make = make
        self.fn = fn
        self.copies = copies
        self.fresh = fresh

    @property
    def id(self):
        return f'{self.suite}: {self.name}'


def copiers():
    """
    Available deepcopy implementations by name, stdlib first.
    """
    found = {'copy': copy.deepcopy}
    try:
        import copium
    except ImportError:
        return found
    found['copium'] = copium.deepcopy
    return found


@contextlib.contextmanager
def using(copier):
    """
    Run dotted with the named deepcopy implementation.
    """
    prev = utils.deepcopy
    utils.deepcopy = copiers()[copier]
    try:
        yield
    finally:
        utils.deepcopy = prev


def _once(case, data, number):
    """
    Run `number` calls of the case. Returns (seconds in the calls, seconds
    overall); the two differ for fresh cases, which copy their input first.
    """
    fn = case.fn
    begin = time.perf_counter()
    if not case.fresh:
        for _ in range(number):
            fn(data)
        took = time.perf_counter() - begin
        return took, took
    # the quickest copier: these copies are setup, not what is being timed
    clone = list(copiers().values())[-1]
    inputs = [clone(data) for _ in range(number)]
    start = time.perf_counter()
    for d in inputs:
        fn(d)
    end = time.perf_counter()
    return end - start, end - begin


def measure(case):
    """
    Best per-call time in seconds, over REPEATS runs sized to take about
    TARGET each, setup included. A case too slow to fit BUDGET gets fewer
    repeats, down to the sizing run alone.
    """
    data = case.make()
    number = 1
    while True:
        took, overall = _once(case, data, number)
        if overall >= TARGET / 10 or number >= 1_000_000:
            break
        number *= 10
    if overall >= TARGET:
        # already a full-sized run: count it, and spend what is left of BUDGET
        best = took / number
        repeats = min(REPEATS - 1, int((BUDGET - overall) / overall))
    else:
        best = None
        number = max(1, int(number * TARGET / max(overall, 1e-9)))
        repeats = REPEATS
    for _ in range(repeats):
        took, _ = _once(case, data, number)
        best = took / number if best is None else min(best, took / number)
    return best


def run(cases, names):
    """
    Time every case under each named copier, yielding (case, {copier: seconds})
    as each case finishes. Cases that do not copy are timed under the first
    copier only.
    """
    for case in cases:
        times = {}
        for name in names if case.copies else names[:1]:
            with using(name):
                times[name] = measure(case)
        yield case, times
