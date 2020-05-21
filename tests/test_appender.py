import dotted


def test_appender_flat():
    m = dotted.update({}, 'hello.there[+]', 9)
    assert m == {'hello': {'there': [9]}}

    m = dotted.update(m, 'hello.there[+]', 10)
    assert m == {'hello': {'there': [9, 10]}}


def test_appender_flat_str():
    m = dotted.update('', '[+]', '1')
    assert m == '1'


def test_appender_nested_from_empty():
    m = dotted.update({}, 'hello[+].name', 'Hello')
    assert m == {'hello': [{'name': 'Hello'}]}


def test_appender_nested_from_non_empty():
    m = dotted.update({'hello': [{'name': 'Hello'}]}, 'hello[+].name', 'Bye')
    assert m == {'hello': [{'name': 'Hello'}, {'name': 'Bye'}]}


def test_appender_embedded():
    m = dotted.update({'hello': {'there': 7}}, 'hello.list[+]', 9)
    assert m == {'hello': {'there': 7, 'list': [9]}}


def test_appender_flat_if():
    m = dotted.update({}, 'hello.there[+?]', 9)
    assert m == {'hello': {'there': [9]}}

    m = dotted.update(m, 'hello.there[+?]', 9)
    assert m == {'hello': {'there': [9]}}


def test_appender_nested_if():
    m = dotted.update({}, 'hello[+?].name', 'Hello')
    assert m == {'hello': [{'name': 'Hello'}]}

    m = dotted.update(m, 'hello[+?].name', 'Hello')
    assert m == {'hello': [{'name': 'Hello'}]}


def test_appender_match():
    r = dotted.match('[*]', '[+]')
    assert r == '[+]'

    r = dotted.match('[+]', '[+?]')
    assert r == '[+?]'

    r = dotted.match('[+?]', '[+]')
    assert r is None
