import dotted


def test_numeric_get_key():
    T = {111: {'stuff': 'hi'}}

    m = dotted.get(T, '111.stuff')
    assert m == 'hi'

    m = dotted.get(T, '111.0.stuff')
    assert m != 'hi'

    # 111.0 == 111
    m = dotted.get(T, '#"111.0".stuff')
    assert m == 'hi'


def test_numeric_get_slot():
    T = {111: {'stuff': 'hi'}}

    m = dotted.get(T, '[111].stuff')
    assert m == 'hi'

    # 111.0 == 111
    m = dotted.get(T, '[111.0].stuff')
    assert m == 'hi'

    # 111.0 == 111
    m = dotted.get(T, '[#"111.0"].stuff')
    assert m == 'hi'


def test_numeric_expand_int():
    T = {111: {'stuff': 'hi'}}

    m = dotted.expand(T, '*.*')
    assert m == ('111.stuff',)

    m = dotted.expand(T, '[*].*')
    assert m == ('[111].stuff',)


def test_numeric_expand_float():
    T = {111.0: {'stuff': 'hi'}}

    m = dotted.expand(T, '*.*')
    assert m == ("#'111.0'.stuff",)

    m = dotted.expand(T, '[*].*')
    assert m == ('[111.0].stuff',)


def test_numeric_update():
    m = dotted.update({}, '07a', 8)
    assert m == {'07a': 8}
