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


# Extended numeric literals

def test_numeric_extended_scientific():
    lst = list(range(11))
    assert dotted.get(lst, '[1e1]') == 10
    d = {10000000000: 'big'}
    assert dotted.get(d, '1e10') == 'big'
    assert dotted.get(d, '1e+10') == 'big'
    # negative exponent — float key
    d2 = {1e-12: 'tiny'}
    assert dotted.get(d2, '1e-12') == 'tiny'


def test_numeric_extended_underscore():
    lst = list(range(1001))
    assert dotted.get(lst, '[1_000]') == 1000
    d = {1000: 'thousand'}
    assert dotted.get(d, '1_000') == 'thousand'


def test_numeric_extended_hex():
    lst = list(range(32))
    assert dotted.get(lst, '[0x1F]') == 31
    d = {31: 'hex'}
    assert dotted.get(d, '0x1F') == 'hex'


def test_numeric_extended_octal():
    lst = list(range(16))
    assert dotted.get(lst, '[0o17]') == 15
    d = {15: 'octal'}
    assert dotted.get(d, '0o17') == 'octal'


def test_numeric_extended_binary():
    lst = list(range(11))
    assert dotted.get(lst, '[0b1010]') == 10
    d = {10: 'binary'}
    assert dotted.get(d, '0b1010') == 'binary'


def test_numeric_extended_negative_slot():
    lst = [None] * 5 + ['neg_sci']
    assert dotted.get(lst, '[-1e1]') is None  # -10, out of range for len 6
    d = {-100000: 'neg_sci'}
    assert dotted.get(d, '[-1e5]') == 'neg_sci'
