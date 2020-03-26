import dotted


def test_appender_flat():
    d = {}
    m = dotted.update(d, 'hello.there[+]', 9)
    assert m == {'hello': {'there': [9]}}

    m = dotted.update(d, 'hello.there[+]', 10)
    assert m == {'hello': {'there': [9, 10]}}


def test_appender_nested():
    d = {}
    m = dotted.update(d, 'hello[+].name', 'Hello')
    assert m == {'hello': [{'name': 'Hello'}]}

    m = dotted.update(d, 'hello[+].name', 'Bye')
    assert m == {'hello': [{'name': 'Hello'}, {'name': 'Bye'}]}


def test_appender_embedded():
    m = dotted.update({'hello': {'there': 7}}, 'hello.list[+]', 9)
    assert m == {'hello': {'there': 7, 'list': [9]}}
