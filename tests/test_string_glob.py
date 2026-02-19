import dotted
from dotted.grammar import value
from dotted.containers import StringGlob, BytesGlob, ValueGroup
from dotted.elements import Bytes


# ---------------------------------------------------------------------------
# StringGlob — parse / repr round-trip
# ---------------------------------------------------------------------------

def test_sglob_parse_prefix():
    r = value.parse_string('"hello"...')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "'hello'..."


def test_sglob_parse_suffix():
    r = value.parse_string('..."world"')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "...'world'"


def test_sglob_parse_prefix_suffix():
    r = value.parse_string('"hello"..."world"')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "'hello'...'world'"


def test_sglob_parse_three_parts():
    r = value.parse_string('"a"..."b"..."c"')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "'a'...'b'...'c'"


def test_sglob_parse_prefix_count():
    r = value.parse_string('"hello"...5')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "'hello'...5"


def test_sglob_parse_count_between():
    r = value.parse_string('"a"...2:5"b"')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "'a'...2:5'b'"


def test_sglob_parse_suffix_min_count():
    r = value.parse_string('...3:"world"')[0]
    assert isinstance(r, StringGlob)
    assert repr(r) == "...3:'world'"


# ---------------------------------------------------------------------------
# StringGlob — matching
# ---------------------------------------------------------------------------

def test_sglob_prefix_match():
    r = value.parse_string('"hello"...')[0]
    assert list(r.matches(('hello world',))) == ['hello world']
    assert list(r.matches(('hello',))) == ['hello']


def test_sglob_prefix_no_match():
    r = value.parse_string('"hello"...')[0]
    assert list(r.matches(('goodbye world',))) == []


def test_sglob_suffix_match():
    r = value.parse_string('..."world"')[0]
    assert list(r.matches(('hello world',))) == ['hello world']
    assert list(r.matches(('world',))) == ['world']


def test_sglob_suffix_no_match():
    r = value.parse_string('..."world"')[0]
    assert list(r.matches(('hello earth',))) == []


def test_sglob_prefix_suffix_match():
    r = value.parse_string('"hello"..."world"')[0]
    assert list(r.matches(('hello beautiful world',))) == ['hello beautiful world']
    assert list(r.matches(('hello world',))) == ['hello world']


def test_sglob_prefix_suffix_no_match():
    r = value.parse_string('"hello"..."world"')[0]
    assert list(r.matches(('hello earth',))) == []
    assert list(r.matches(('goodbye world',))) == []


def test_sglob_three_parts_match():
    r = value.parse_string('"a"..."b"..."c"')[0]
    assert list(r.matches(('axbxc',))) == ['axbxc']
    assert list(r.matches(('abc',))) == ['abc']


def test_sglob_three_parts_no_match():
    r = value.parse_string('"a"..."b"..."c"')[0]
    assert list(r.matches(('axcxb',))) == []


def test_sglob_count_max():
    r = value.parse_string('"hello"...5')[0]
    assert list(r.matches(('hello!!',))) == ['hello!!']
    assert list(r.matches(('hello123456',))) == []  # 6 extra > 5


def test_sglob_count_range():
    r = value.parse_string('"a"...2:4"b"')[0]
    assert list(r.matches(('axxb',))) == ['axxb']      # 2 between
    assert list(r.matches(('axxxxb',))) == ['axxxxb']   # 4 between
    assert list(r.matches(('axb',))) == []              # 1 < 2
    assert list(r.matches(('axxxxxb',))) == []          # 5 > 4


def test_sglob_only_matches_strings_and_bytes():
    r = value.parse_string('"hello"...')[0]
    assert list(r.matches((42,))) == []
    assert list(r.matches(([1, 2],))) == []
    assert list(r.matches((None,))) == []


def test_sglob_no_match_bytes():
    r = value.parse_string('"hello"...')[0]
    assert list(r.matches((b'hello world',))) == []


# ---------------------------------------------------------------------------
# Bytes literal — parse / repr / matching
# ---------------------------------------------------------------------------

def test_bytes_parse_double():
    r = value.parse_string('b"hello"')[0]
    assert isinstance(r, Bytes)
    assert r.value == b'hello'
    assert repr(r) == "b'hello'"


def test_bytes_parse_single():
    r = value.parse_string("b'hello'")[0]
    assert isinstance(r, Bytes)
    assert r.value == b'hello'


def test_bytes_match():
    r = value.parse_string('b"hello"')[0]
    assert list(r.matches((b'hello',))) == [b'hello']


def test_bytes_no_match_str():
    r = value.parse_string('b"hello"')[0]
    assert list(r.matches(('hello',))) == []


def test_bytes_no_match_wrong():
    r = value.parse_string('b"hello"')[0]
    assert list(r.matches((b'world',))) == []


def test_bytes_value_guard():
    d = {'a': b'hello', 'b': b'world', 'c': 'hello'}
    assert dotted.get(d, '*=b"hello"') == (b'hello',)


def test_bytes_filter():
    data = [{'data': b'yes'}, {'data': b'no'}, {'data': 'yes'}]
    assert dotted.get(data, '[*&data=b"yes"]') == ({'data': b'yes'},)


# ---------------------------------------------------------------------------
# BytesGlob — parse / repr round-trip
# ---------------------------------------------------------------------------

def test_bglob_parse_prefix():
    r = value.parse_string('b"hello"...')[0]
    assert isinstance(r, BytesGlob)
    assert repr(r) == "b'hello'..."


def test_bglob_parse_suffix():
    r = value.parse_string('...b"world"')[0]
    assert isinstance(r, BytesGlob)
    assert repr(r) == "...b'world'"


def test_bglob_parse_prefix_suffix():
    r = value.parse_string('b"hello"...b"world"')[0]
    assert isinstance(r, BytesGlob)
    assert repr(r) == "b'hello'...b'world'"


def test_bglob_parse_three_parts():
    r = value.parse_string('b"a"...b"b"...b"c"')[0]
    assert isinstance(r, BytesGlob)
    assert repr(r) == "b'a'...b'b'...b'c'"


def test_bglob_parse_prefix_count():
    r = value.parse_string('b"hello"...5')[0]
    assert isinstance(r, BytesGlob)
    assert repr(r) == "b'hello'...5"


# ---------------------------------------------------------------------------
# BytesGlob — matching
# ---------------------------------------------------------------------------

def test_bglob_prefix_match():
    r = value.parse_string('b"hello"...')[0]
    assert list(r.matches((b'hello world',))) == [b'hello world']
    assert list(r.matches((b'hello',))) == [b'hello']


def test_bglob_prefix_no_match():
    r = value.parse_string('b"hello"...')[0]
    assert list(r.matches((b'goodbye world',))) == []


def test_bglob_suffix_match():
    r = value.parse_string('...b"world"')[0]
    assert list(r.matches((b'hello world',))) == [b'hello world']


def test_bglob_prefix_suffix_match():
    r = value.parse_string('b"hello"...b"world"')[0]
    assert list(r.matches((b'hello beautiful world',))) == [b'hello beautiful world']


def test_bglob_three_parts_match():
    r = value.parse_string('b"a"...b"b"...b"c"')[0]
    assert list(r.matches((b'axbxc',))) == [b'axbxc']


def test_bglob_count_max():
    r = value.parse_string('b"hello"...5')[0]
    assert list(r.matches((b'hello!!',))) == [b'hello!!']
    assert list(r.matches((b'hello123456',))) == []


def test_bglob_only_matches_bytes():
    r = value.parse_string('b"hello"...')[0]
    assert list(r.matches(('hello world',))) == []
    assert list(r.matches((42,))) == []


def test_bglob_multiple_vals():
    r = value.parse_string('b"user_"...')[0]
    assert list(r.matches((b'user_alice', b'admin_bob', b'user_carol'))) == [
        b'user_alice', b'user_carol',
    ]


def test_bglob_value_guard():
    d = {'a': b'hello world', 'b': b'goodbye world'}
    assert dotted.get(d, '*=b"hello"...') == (b'hello world',)


def test_bglob_filter():
    data = [{'data': b'user_alice'}, {'data': b'admin_bob'}]
    assert dotted.get(data, '[*&data=b"user_"...]') == ({'data': b'user_alice'},)


def test_sglob_multiple_vals():
    r = value.parse_string('"user_"...')[0]
    assert list(r.matches(('user_alice', 'admin_bob', 'user_carol'))) == [
        'user_alice', 'user_carol',
    ]


# ---------------------------------------------------------------------------
# StringGlob — integration with dotted API
# ---------------------------------------------------------------------------

def test_bglob_naked_str_suffix():
    r = value.parse_string('b"hello"..."world"')[0]
    assert isinstance(r, BytesGlob)
    assert list(r.matches((b'hello beautiful world',))) == [b'hello beautiful world']


def test_bglob_naked_str_prefix():
    r = value.parse_string('"hello"...b"world"')[0]
    assert isinstance(r, BytesGlob)
    assert list(r.matches((b'hello beautiful world',))) == [b'hello beautiful world']


def test_bglob_naked_str_middle():
    r = value.parse_string('b"a"..."b"...b"c"')[0]
    assert isinstance(r, BytesGlob)
    assert list(r.matches((b'axbxc',))) == [b'axbxc']


# ---------------------------------------------------------------------------
# StringGlob — integration with dotted API
# ---------------------------------------------------------------------------

def test_sglob_value_guard_prefix():
    d = {'greeting': 'hello world', 'farewell': 'goodbye world', 'name': 'alice'}
    assert dotted.get(d, '*="hello"...') == ('hello world',)


def test_sglob_value_guard_suffix():
    d = {'greeting': 'hello world', 'farewell': 'goodbye world', 'name': 'alice'}
    assert dotted.get(d, '*=..."world"') == ('hello world', 'goodbye world')


def test_sglob_value_guard_prefix_suffix():
    d = {'greeting': 'hello world', 'farewell': 'goodbye world'}
    assert dotted.get(d, '*="hello"..."world"') == ('hello world',)


def test_sglob_filter():
    data = [{'name': 'user_alice'}, {'name': 'admin_bob'}, {'name': 'user_carol'}]
    assert dotted.get(data, '[*&name="user_"...]') == (
        {'name': 'user_alice'}, {'name': 'user_carol'},
    )


def test_sglob_filter_negated():
    data = [{'name': 'user_alice'}, {'name': 'admin_bob'}, {'name': 'user_carol'}]
    assert dotted.get(data, '[*&name!="user_"...]') == ({'name': 'admin_bob'},)


# ---------------------------------------------------------------------------
# ValueGroup — parse / repr round-trip
# ---------------------------------------------------------------------------

def test_vgroup_parse_integers():
    r = value.parse_string('(1, 2)')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == '(1, 2)'


def test_vgroup_parse_three_strings():
    r = value.parse_string('("a", "b", "c")')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == "('a', 'b', 'c')"


def test_vgroup_parse_mixed_types():
    r = value.parse_string('("hello", 42, None)')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == "('hello', 42, None)"


def test_vgroup_parse_with_regex():
    r = value.parse_string('("hello", /world/)')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == "('hello', /world/)"


def test_vgroup_parse_with_string_glob():
    r = value.parse_string('("user_"..., /admin_\\d+/)')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == "('user_'..., /admin_\\d+/)"


def test_vgroup_parse_with_wildcard():
    r = value.parse_string('(1, *)')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == '(1, *)'


def test_vgroup_parse_nested_containers():
    r = value.parse_string('([1, ...], {1, ...})')[0]
    assert isinstance(r, ValueGroup)
    assert repr(r) == '([1, ...], {1, ...})'


# ---------------------------------------------------------------------------
# ValueGroup — matching
# ---------------------------------------------------------------------------

def test_vgroup_match_first_alt():
    r = value.parse_string('(1, 2, 3)')[0]
    assert list(r.matches((1,))) == [1]


def test_vgroup_match_second_alt():
    r = value.parse_string('(1, 2, 3)')[0]
    assert list(r.matches((2,))) == [2]


def test_vgroup_no_match():
    r = value.parse_string('(1, 2, 3)')[0]
    assert list(r.matches((4,))) == []


def test_vgroup_multiple_vals():
    r = value.parse_string('(1, 2)')[0]
    assert list(r.matches((1, 2, 3, 4))) == [1, 2]


def test_vgroup_string_alts():
    r = value.parse_string('("hello", "world")')[0]
    assert list(r.matches(('hello', 'goodbye', 'world'))) == ['hello', 'world']


def test_vgroup_mixed_type_match():
    r = value.parse_string('("hello", 42, None)')[0]
    assert list(r.matches(('hello',))) == ['hello']
    assert list(r.matches((42,))) == [42]
    assert list(r.matches((None,))) == [None]
    assert list(r.matches(('other',))) == []


def test_vgroup_regex_alt():
    r = value.parse_string('(1, /user_.*/)')[0]
    assert list(r.matches((1,))) == [1]
    assert list(r.matches(('user_alice',))) == ['user_alice']
    assert list(r.matches(('admin',))) == []


def test_vgroup_string_glob_alt():
    r = value.parse_string('("user_"..., "admin_"...)')[0]
    assert list(r.matches(('user_alice',))) == ['user_alice']
    assert list(r.matches(('admin_bob',))) == ['admin_bob']
    assert list(r.matches(('guest_carol',))) == []


# ---------------------------------------------------------------------------
# ValueGroup — integration with dotted API
# ---------------------------------------------------------------------------

def test_vgroup_filter_status():
    data = [{'status': 1}, {'status': 2}, {'status': 3}]
    assert dotted.get(data, '[*&status=(1, 2)]') == ({'status': 1}, {'status': 2})


def test_vgroup_filter_negated():
    data = [{'status': 1}, {'status': 2}, {'status': 3}]
    assert dotted.get(data, '[*&status!=(1, 2)]') == ({'status': 3},)


def test_vgroup_value_guard():
    d = {'a': 1, 'b': 2, 'c': 3, 'd': 4}
    assert dotted.get(d, '*=(1, 3)') == (1, 3)


def test_vgroup_value_guard_negated():
    d = {'a': 1, 'b': 2, 'c': 3, 'd': 4}
    assert dotted.get(d, '*!=(1, 3)') == (2, 4)


def test_vgroup_combined_glob_regex():
    data = [
        {'name': 'user_alice'},
        {'name': 'admin_bob'},
        {'name': 'guest_carol'},
    ]
    assert dotted.get(data, '[*&name=("user_"..., /admin_.*/)]') == (
        {'name': 'user_alice'}, {'name': 'admin_bob'},
    )
