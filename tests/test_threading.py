import threading
import dotted


def test_concurrent_parse():
    """
    Parser must be thread-safe: concurrent dotted.get calls
    should never raise ParseError due to shared parser state.
    """
    errors = []

    def run(i):
        try:
            obj = {"data": {"services": ["piano"]}}
            for _ in range(100):
                assert dotted.get(obj, "data.services") == ["piano"]
        except Exception as e:
            errors.append((i, e))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Threads failed: {errors}"


def test_concurrent_parse_varied_keys():
    """
    Concurrent parsing of different keys exercises the lock on cache misses.
    """
    keys = [f"a.b.c{i}" for i in range(20)]
    data = {"a": {"b": {f"c{i}": i for i in range(20)}}}
    errors = []

    def run(thread_id):
        try:
            for _ in range(50):
                for i, key in enumerate(keys):
                    assert dotted.get(data, key) == i
        except Exception as e:
            errors.append((thread_id, e))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Threads failed: {errors}"
