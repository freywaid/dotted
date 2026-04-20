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


# --- Floats in RHS (value) positions ---

def test_rhs_float_guard():
    """
    a=0.9 parses as a float guard, not (a=0, .9).
    """
    p = dotted.parse('a=0.9')
    assert dotted.assemble(p) == 'a=0.9'
    assert dotted.get({'a': 0.9}, 'a=0.9') == 0.9
    assert dotted.get({'a': 1}, 'a=0.9') is None

def test_rhs_float_dotted_keycmd_guard():
    """
    a.b=7.1 must parse as (a, b=7.1), not (a, b=7, 1).
    """
    p = dotted.parse('a.b=7.1')
    assert dotted.assemble(p) == 'a.b=7.1'
    assert dotted.get({'a': {'b': 7.1}}, 'a.b=7.1') == 7.1

def test_rhs_float_negative():
    assert dotted.get({'a': -0.9}, 'a=-0.9') == -0.9

def test_rhs_float_scientific():
    assert dotted.get({'a': 150.0}, 'a=1.5e2') == 150.0

def test_rhs_float_in_filter():
    data = [{'x': 0.9}, {'x': 1.1}]
    assert dotted.get(data, '[x=0.9]') == [{'x': 0.9}]

def test_rhs_float_in_slot_guard():
    assert dotted.get([0.1, 0.9, 0.5], '[*]=0.9') == (0.9,)

def test_rhs_float_in_container():
    p = dotted.parse('a=[0.1, 0.2]')
    assert dotted.assemble(p) == 'a=[0.1, 0.2]'
    assert dotted.get({'a': [0.1, 0.2]}, 'a=[0.1, 0.2]') == [0.1, 0.2]

def test_rhs_float_in_transform_arg():
    """
    Transform arguments accept floats.
    """
    assert dotted.get({'a': 0.5}, 'a|round:0') == 0


# --- Guarded ops are terminal within op_seq ---

def test_guard_terminal_keycmd():
    """
    a=1.b: guard `a=1` cannot have a continuation `.b` after it.
    """
    import pytest
    from dotted.api import ParseError
    with pytest.raises(ParseError):
        dotted.parse('a=1.b')
    with pytest.raises(ParseError):
        dotted.parse('a=1.0b')

def test_guard_terminal_dotted_keycmd():
    """
    a.b=7.c: guard `b=7` is already terminal; adding `.c` after must fail.
    """
    import pytest
    from dotted.api import ParseError
    with pytest.raises(ParseError):
        dotted.parse('a.b=7.c')

def test_guard_terminal_slot():
    """
    a[0]=7.b: slot guard `[0]=7` must be terminal.
    """
    import pytest
    from dotted.api import ParseError
    with pytest.raises(ParseError):
        dotted.parse('a[0]=7.b')
    with pytest.raises(ParseError):
        dotted.parse('[0]=7.a')

def test_guard_terminal_recursive_dstar():
    """
    **=7.a: recursive wildcard guard must be terminal.
    """
    import pytest
    from dotted.api import ParseError
    with pytest.raises(ParseError):
        dotted.parse('**=7.a')

def test_guard_terminal_recursive_pattern():
    """
    *x=7.a: recursive pattern guard must be terminal.
    """
    import pytest
    from dotted.api import ParseError
    with pytest.raises(ParseError):
        dotted.parse('*x=7.a')
