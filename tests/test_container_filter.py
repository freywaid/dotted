import pytest
import dotted
from dotted.grammar import value, container_value, targ
from dotted.containers import (
    Glob, DictGlobEntry, ContainerList, ContainerDict, ContainerSet,
)


# ---------------------------------------------------------------------------
# Parse / repr round-trip
# ---------------------------------------------------------------------------

class TestParseRepr:
    """
    Parse a container expression via the value grammar, check repr round-trips.
    """

    def test_list_scalars(self):
        r = value.parse_string('[1, 2, 3]')[0]
        assert isinstance(r, ContainerList)
        assert repr(r) == '[1, 2, 3]'

    def test_list_with_wildcard(self):
        r = value.parse_string('[1, *, 3]')[0]
        assert repr(r) == '[1, *, 3]'

    def test_list_with_glob(self):
        r = value.parse_string('[1, ..., 3]')[0]
        assert repr(r) == '[1, ..., 3]'

    def test_list_glob_count_max(self):
        r = value.parse_string('[...5]')[0]
        assert repr(r) == '[...5]'

    def test_list_glob_count_range(self):
        r = value.parse_string('[...2:5]')[0]
        assert repr(r) == '[...2:5]'

    def test_list_glob_count_min(self):
        r = value.parse_string('[...2:]')[0]
        assert repr(r) == '[...2:]'

    def test_list_glob_regex(self):
        r = value.parse_string('[.../\\d+/]')[0]
        assert repr(r) == '[.../\\d+/]'

    def test_list_glob_regex_count(self):
        r = value.parse_string('[.../\\d+/2:5]')[0]
        assert repr(r) == '[.../\\d+/2:5]'

    def test_list_prefixed_l(self):
        r = value.parse_string('l[1, 2]')[0]
        assert isinstance(r, ContainerList)
        assert r.type_prefix == 'l'
        assert repr(r) == 'l[1, 2]'

    def test_list_prefixed_t(self):
        r = value.parse_string('t[1, 2]')[0]
        assert isinstance(r, ContainerList)
        assert r.type_prefix == 't'
        assert repr(r) == 't[1, 2]'

    def test_empty_list(self):
        r = value.parse_string('[]')[0]
        assert isinstance(r, ContainerList)
        assert r.elements == ()
        assert repr(r) == '[]'

    def test_empty_list_l(self):
        r = value.parse_string('l[]')[0]
        assert r.type_prefix == 'l'
        assert repr(r) == 'l[]'

    def test_empty_list_t(self):
        r = value.parse_string('t[]')[0]
        assert r.type_prefix == 't'
        assert repr(r) == 't[]'

    def test_dict_scalars(self):
        r = value.parse_string('{"a": 1, "b": 2}')[0]
        assert isinstance(r, ContainerDict)
        assert repr(r) == "{'a': 1, 'b': 2}"

    def test_dict_with_glob(self):
        r = value.parse_string('{"a": 1, ...: *}')[0]
        assert isinstance(r, ContainerDict)
        assert repr(r) == "{'a': 1, ...: *}"

    def test_dict_prefixed_d(self):
        r = value.parse_string('d{"a": 1}')[0]
        assert isinstance(r, ContainerDict)
        assert r.type_prefix == 'd'
        assert repr(r) == "d{'a': 1}"

    def test_empty_dict(self):
        r = value.parse_string('{}')[0]
        assert isinstance(r, ContainerDict)
        assert r.entries == ()
        assert repr(r) == '{}'

    def test_empty_dict_d(self):
        r = value.parse_string('d{}')[0]
        assert r.type_prefix == 'd'
        assert repr(r) == 'd{}'

    def test_set_scalars(self):
        r = value.parse_string('{1, 2, 3}')[0]
        assert isinstance(r, ContainerSet)
        assert repr(r) == '{1, 2, 3}'

    def test_set_with_glob(self):
        r = value.parse_string('{1, 2, ...}')[0]
        assert repr(r) == '{1, 2, ...}'

    def test_set_prefixed_s(self):
        r = value.parse_string('s{1, 2}')[0]
        assert isinstance(r, ContainerSet)
        assert r.type_prefix == 's'
        assert repr(r) == 's{1, 2}'

    def test_set_prefixed_fs(self):
        r = value.parse_string('fs{1, 2}')[0]
        assert isinstance(r, ContainerSet)
        assert r.type_prefix == 'fs'
        assert repr(r) == 'fs{1, 2}'

    def test_empty_set_s(self):
        r = value.parse_string('s{}')[0]
        assert isinstance(r, ContainerSet)
        assert r.type_prefix == 's'
        assert repr(r) == 's{}'

    def test_empty_set_fs(self):
        r = value.parse_string('fs{}')[0]
        assert r.type_prefix == 'fs'
        assert repr(r) == 'fs{}'

    def test_nested_list_in_dict(self):
        r = value.parse_string('{"a": [1, ...]}')[0]
        assert isinstance(r, ContainerDict)
        assert repr(r) == "{'a': [1, ...]}"

    def test_nested_dict_in_list(self):
        r = value.parse_string('[{"x": *}]')[0]
        assert isinstance(r, ContainerList)
        assert repr(r) == "[{'x': *}]"

    def test_list_with_string(self):
        r = value.parse_string('["hello", "world"]')[0]
        assert repr(r) == "['hello', 'world']"

    def test_list_with_regex(self):
        r = value.parse_string('[/\\d+/, *]')[0]
        assert repr(r) == '[/\\d+/, *]'


# ---------------------------------------------------------------------------
# Assemble round-trip (full dotted parse → assemble)
# ---------------------------------------------------------------------------

class TestAssembleRoundTrip:
    """
    Full parse → assemble round-trip through dotted.parse/assemble.
    """

    def test_value_guard_list(self):
        p = dotted.parse('*=[1, 2, 3]')
        assert dotted.assemble(p) == '*=[1, 2, 3]'

    def test_value_guard_list_glob(self):
        p = dotted.parse('*=[1, ..., 3]')
        assert dotted.assemble(p) == '*=[1, ..., 3]'

    def test_value_guard_empty_list(self):
        p = dotted.parse('*=[]')
        assert dotted.assemble(p) == '*=[]'

    def test_value_guard_dict(self):
        p = dotted.parse('*={"a": 1}')
        assert dotted.assemble(p) == "*={'a': 1}"

    def test_value_guard_dict_glob(self):
        p = dotted.parse('*={"a": 1, ...: *}')
        assert dotted.assemble(p) == "*={'a': 1, ...: *}"

    def test_value_guard_set(self):
        p = dotted.parse('*={1, 2}')
        assert dotted.assemble(p) == '*={1, 2}'

    def test_value_guard_prefixed_list(self):
        p = dotted.parse('*=t[1, 2]')
        assert dotted.assemble(p) == '*=t[1, 2]'

    def test_value_guard_prefixed_set(self):
        p = dotted.parse('*=s{1, 2}')
        assert dotted.assemble(p) == '*=s{1, 2}'

    def test_filter_with_container(self):
        p = dotted.parse('[*&tags=[1, ...]]')
        assert dotted.assemble(p) == '[*.tags=[1, ...]]'

    def test_filter_dict_container(self):
        p = dotted.parse('[*&cfg={"a": 1, ...: *}]')
        assert dotted.assemble(p) == "[*.cfg={'a': 1, ...: *}]"


# ---------------------------------------------------------------------------
# List container matching
# ---------------------------------------------------------------------------

class TestListMatching:
    """
    ContainerList.matches() against actual values.
    """

    def test_exact_match(self):
        r = value.parse_string('[1, 2, 3]')[0]
        assert list(r.matches(([1, 2, 3],))) == [[1, 2, 3]]

    def test_exact_no_match(self):
        r = value.parse_string('[1, 2, 3]')[0]
        assert list(r.matches(([1, 2, 4],))) == []

    def test_exact_wrong_length(self):
        r = value.parse_string('[1, 2, 3]')[0]
        assert list(r.matches(([1, 2],))) == []

    def test_wildcard(self):
        r = value.parse_string('[1, *, 3]')[0]
        assert list(r.matches(([1, 99, 3],))) == [[1, 99, 3]]

    def test_wildcard_no_match(self):
        r = value.parse_string('[1, *, 3]')[0]
        assert list(r.matches(([1, 99, 4],))) == []

    def test_glob_any(self):
        r = value.parse_string('[1, ..., 3]')[0]
        assert list(r.matches(([1, 3],))) == [[1, 3]]
        assert list(r.matches(([1, 2, 3],))) == [[1, 2, 3]]
        assert list(r.matches(([1, 2, 2, 3],))) == [[1, 2, 2, 3]]

    def test_glob_no_match(self):
        r = value.parse_string('[1, ..., 3]')[0]
        assert list(r.matches(([1, 2, 4],))) == []

    def test_glob_count_max(self):
        r = value.parse_string('[1, ...2, 3]')[0]
        assert list(r.matches(([1, 3],))) == [[1, 3]]
        assert list(r.matches(([1, 2, 3],))) == [[1, 2, 3]]
        assert list(r.matches(([1, 2, 2, 3],))) == [[1, 2, 2, 3]]
        assert list(r.matches(([1, 2, 2, 2, 3],))) == []

    def test_glob_count_range(self):
        r = value.parse_string('[...2:4]')[0]
        assert list(r.matches(([1],))) == []
        assert list(r.matches(([1, 2],))) == [[1, 2]]
        assert list(r.matches(([1, 2, 3, 4],))) == [[1, 2, 3, 4]]
        assert list(r.matches(([1, 2, 3, 4, 5],))) == []

    def test_glob_regex(self):
        r = value.parse_string('[.../\\d+/]')[0]
        assert list(r.matches(([1, 2, 3],))) == [[1, 2, 3]]
        assert list(r.matches(([],))) == [[]]

    def test_glob_regex_no_match(self):
        r = value.parse_string('[.../\\d+/]')[0]
        assert list(r.matches(([1, 'a', 3],))) == []

    def test_tuple_match_unprefixed(self):
        """
        Unprefixed [] matches both list and tuple.
        """
        r = value.parse_string('[1, 2]')[0]
        assert list(r.matches(((1, 2),))) == [(1, 2)]
        assert list(r.matches(([1, 2],))) == [[1, 2]]

    def test_list_prefix_strict(self):
        """
        l[...] matches list only, not tuple.
        """
        r = value.parse_string('l[1, 2]')[0]
        assert list(r.matches(([1, 2],))) == [[1, 2]]
        assert list(r.matches(((1, 2),))) == []

    def test_tuple_prefix_strict(self):
        """
        t[...] matches tuple only, not list.
        """
        r = value.parse_string('t[1, 2]')[0]
        assert list(r.matches(((1, 2),))) == [(1, 2)]
        assert list(r.matches(([1, 2],))) == []

    def test_empty_list_matches_empty(self):
        """
        [] matches any empty list or tuple.
        """
        r = value.parse_string('[]')[0]
        assert list(r.matches(([],))) == [[]]
        assert list(r.matches(((),))) == [()]

    def test_empty_list_no_match_nonempty(self):
        r = value.parse_string('[]')[0]
        assert list(r.matches(([1],))) == []

    def test_any_list_glob(self):
        """
        [...] matches any list/tuple of any length.
        """
        r = value.parse_string('[...]')[0]
        assert list(r.matches(([],))) == [[]]
        assert list(r.matches(([1, 2, 3],))) == [[1, 2, 3]]

    def test_non_list_rejected(self):
        r = value.parse_string('[1, 2]')[0]
        assert list(r.matches(({1, 2},))) == []
        assert list(r.matches(({'a': 1},))) == []
        assert list(r.matches(('ab',))) == []


# ---------------------------------------------------------------------------
# Dict container matching
# ---------------------------------------------------------------------------

class TestDictMatching:
    """
    ContainerDict.matches() against actual values.
    """

    def test_exact_match(self):
        r = value.parse_string('{"a": 1, "b": 2}')[0]
        assert list(r.matches(({'a': 1, 'b': 2},))) == [{'a': 1, 'b': 2}]

    def test_exact_no_extra_keys(self):
        """
        Without glob, no extra keys allowed.
        """
        r = value.parse_string('{"a": 1}')[0]
        assert list(r.matches(({'a': 1, 'b': 2},))) == []

    def test_exact_missing_key(self):
        r = value.parse_string('{"a": 1, "b": 2}')[0]
        assert list(r.matches(({'a': 1},))) == []

    def test_glob_extra_keys(self):
        """
        ...: * allows extra entries.
        """
        r = value.parse_string('{"a": 1, ...: *}')[0]
        assert list(r.matches(({'a': 1, 'b': 2, 'c': 3},))) == [{'a': 1, 'b': 2, 'c': 3}]

    def test_glob_no_extra(self):
        """
        ...: * with zero extra is fine (min=0).
        """
        r = value.parse_string('{"a": 1, ...: *}')[0]
        assert list(r.matches(({'a': 1},))) == [{'a': 1}]

    def test_glob_regex_keys(self):
        """
        .../regex/: * matches keys by pattern.
        """
        r = value.parse_string('{.../user_.*/: *}')[0]
        assert list(r.matches(({'user_a': 1, 'user_b': 2},))) == [{'user_a': 1, 'user_b': 2}]

    def test_glob_regex_keys_no_match(self):
        r = value.parse_string('{.../user_.*/: *}')[0]
        assert list(r.matches(({'admin': 1},))) == []

    def test_glob_count(self):
        """
        Glob with count constraint on dict keys.
        """
        r = value.parse_string('{.../user_.*/1:3: *}')[0]
        assert list(r.matches(({'user_a': 1},))) == [{'user_a': 1}]
        assert list(r.matches(({'user_a': 1, 'user_b': 2, 'user_c': 3},))) == [{'user_a': 1, 'user_b': 2, 'user_c': 3}]
        assert list(r.matches(({},))) == []

    def test_wildcard_value(self):
        """
        Dict with wildcard value pattern.
        """
        r = value.parse_string('{"a": *}')[0]
        assert list(r.matches(({'a': 1},))) == [{'a': 1}]
        assert list(r.matches(({'a': 'hello'},))) == [{'a': 'hello'}]

    def test_empty_dict_match(self):
        r = value.parse_string('{}')[0]
        assert list(r.matches(({},))) == [{}]

    def test_empty_dict_no_match_nonempty(self):
        r = value.parse_string('{}')[0]
        assert list(r.matches(({'a': 1},))) == []

    def test_any_dict_glob(self):
        """
        {...: *} matches any dict.
        """
        r = value.parse_string('{...: *}')[0]
        assert list(r.matches(({},))) == [{}]
        assert list(r.matches(({'a': 1, 'b': 2},))) == [{'a': 1, 'b': 2}]

    def test_non_dict_rejected(self):
        r = value.parse_string('{"a": 1}')[0]
        assert list(r.matches(([1],))) == []
        assert list(r.matches(({1, 2},))) == []

    def test_dict_prefix_d(self):
        r = value.parse_string('d{"a": 1}')[0]
        assert list(r.matches(({'a': 1},))) == [{'a': 1}]


# ---------------------------------------------------------------------------
# Set container matching
# ---------------------------------------------------------------------------

class TestSetMatching:
    """
    ContainerSet.matches() against actual values.
    """

    def test_exact_match(self):
        r = value.parse_string('{1, 2, 3}')[0]
        assert list(r.matches(({1, 2, 3},))) == [{1, 2, 3}]

    def test_exact_no_extra(self):
        """
        Without glob, exact match required.
        """
        r = value.parse_string('{1, 2}')[0]
        assert list(r.matches(({1, 2, 3},))) == []

    def test_exact_missing(self):
        r = value.parse_string('{1, 2, 3}')[0]
        assert list(r.matches(({1, 2},))) == []

    def test_glob_extra(self):
        """
        {1, 2, ...} allows extra members.
        """
        r = value.parse_string('{1, 2, ...}')[0]
        assert list(r.matches(({1, 2, 3, 4},))) == [{1, 2, 3, 4}]

    def test_glob_no_extra(self):
        r = value.parse_string('{1, 2, ...}')[0]
        assert list(r.matches(({1, 2},))) == [{1, 2}]

    def test_glob_regex(self):
        r = value.parse_string('{1, .../\\d+/}')[0]
        assert list(r.matches(({1, 2, 3},))) == [{1, 2, 3}]

    def test_glob_regex_no_match(self):
        r = value.parse_string('{1, .../\\d+/}')[0]
        assert list(r.matches(({1, 'a'},))) == []

    def test_frozenset_match_unprefixed(self):
        """
        Unprefixed {v, v} matches both set and frozenset.
        """
        r = value.parse_string('{1, 2}')[0]
        assert list(r.matches((frozenset({1, 2}),))) == [frozenset({1, 2})]
        assert list(r.matches(({1, 2},))) == [{1, 2}]

    def test_set_prefix_strict(self):
        """
        s{...} matches set only.
        """
        r = value.parse_string('s{1, 2}')[0]
        assert list(r.matches(({1, 2},))) == [{1, 2}]
        assert list(r.matches((frozenset({1, 2}),))) == []

    def test_frozenset_prefix_strict(self):
        """
        fs{...} matches frozenset only.
        """
        r = value.parse_string('fs{1, 2}')[0]
        assert list(r.matches((frozenset({1, 2}),))) == [frozenset({1, 2})]
        assert list(r.matches(({1, 2},))) == []

    def test_empty_set_s(self):
        r = value.parse_string('s{}')[0]
        assert list(r.matches((set(),))) == [set()]
        assert list(r.matches((frozenset(),))) == []

    def test_empty_set_fs(self):
        r = value.parse_string('fs{}')[0]
        assert list(r.matches((frozenset(),))) == [frozenset()]
        assert list(r.matches((set(),))) == []

    def test_any_set_glob(self):
        """
        s{...} matches any set of any size.
        Note: unprefixed {...} parses as dict (bare glob entry), use s{} prefix for sets.
        """
        r = value.parse_string('s{...}')[0]
        assert list(r.matches((set(),))) == [set()]
        assert list(r.matches(({1, 2, 3},))) == [{1, 2, 3}]

    def test_non_set_rejected(self):
        r = value.parse_string('{1, 2}')[0]
        assert list(r.matches(([1, 2],))) == []
        assert list(r.matches(({'a': 1},))) == []


# ---------------------------------------------------------------------------
# Value guard integration (through dotted.get)
# ---------------------------------------------------------------------------

class TestValueGuardIntegration:
    """
    Container values in value guards via dotted.get().
    """

    def test_guard_list(self):
        d = {'items': [1, 2, 3], 'name': 'test'}
        result = dotted.get(d, '*=[1, 2, 3]')
        assert result == ([1, 2, 3],)

    def test_guard_list_glob(self):
        d = {'items': [1, 2, 3], 'name': 'test'}
        result = dotted.get(d, '*=[1, ..., 3]')
        assert result == ([1, 2, 3],)

    def test_guard_list_any(self):
        """
        *=[...] matches any list-like value.
        """
        d = {'items': [1, 2, 3], 'name': 'test', 'count': 42}
        result = dotted.get(d, '*=[...]')
        assert [1, 2, 3] in result

    def test_guard_dict(self):
        d = {'cfg': {'a': 1, 'b': 2}, 'name': 'test'}
        result = dotted.get(d, '*={"a": 1, "b": 2}')
        assert result == ({'a': 1, 'b': 2},)

    def test_guard_dict_partial(self):
        d = {'cfg': {'a': 1, 'b': 2}, 'name': 'test'}
        result = dotted.get(d, '*={"a": 1, ...: *}')
        assert result == ({'a': 1, 'b': 2},)

    def test_guard_empty_list(self):
        d = {'empty': [], 'full': [1]}
        result = dotted.get(d, '*=[]')
        # [] matches empty list and empty tuple; here only []
        assert [] in result

    def test_guard_set(self):
        d = {'tags': {1, 2, 3}, 'name': 'test'}
        result = dotted.get(d, '*={1, 2, ...}')
        assert result == ({1, 2, 3},)

    def test_guard_prefixed_tuple(self):
        d = {'a': (1, 2), 'b': [1, 2]}
        result = dotted.get(d, '*=t[1, 2]')
        assert result == ((1, 2),)


# ---------------------------------------------------------------------------
# Filter integration (through dotted.get)
# ---------------------------------------------------------------------------

class TestFilterIntegration:
    """
    Container values in filter expressions via dotted.get().
    """

    def test_filter_list(self):
        d = [{'tags': [1, 2, 3]}, {'tags': [4, 5]}]
        result = dotted.get(d, '[*&tags=[1, ...]]')
        assert result == ({'tags': [1, 2, 3]},)

    def test_filter_exact_list(self):
        d = [{'tags': [1, 2, 3]}, {'tags': [1, 2]}]
        result = dotted.get(d, '[*&tags=[1, 2, 3]]')
        assert result == ({'tags': [1, 2, 3]},)

    def test_filter_dict(self):
        d = [{'cfg': {'a': 1, 'b': 2}}, {'cfg': {'a': 1}}]
        result = dotted.get(d, '[*&cfg={"a": 1, ...: *}]')
        assert len(result) == 2  # both match

    def test_filter_exact_dict(self):
        d = [{'cfg': {'a': 1, 'b': 2}}, {'cfg': {'a': 1}}]
        result = dotted.get(d, '[*&cfg={"a": 1}]')
        assert result == ({'cfg': {'a': 1}},)

    def test_filter_set(self):
        d = [{'tags': {1, 2, 3}}, {'tags': {4, 5}}]
        result = dotted.get(d, '[*&tags={1, ...}]')
        assert result == ({'tags': {1, 2, 3}},)


# ---------------------------------------------------------------------------
# Nested container matching
# ---------------------------------------------------------------------------

class TestNestedContainers:
    """
    Nested container patterns and matching.
    """

    def test_dict_with_list_value(self):
        r = value.parse_string('{"a": [1, ...]}')[0]
        assert list(r.matches(({'a': [1, 2, 3]},))) == [{'a': [1, 2, 3]}]
        assert list(r.matches(({'a': [2, 3]},))) == []

    def test_list_with_dict_element(self):
        r = value.parse_string('[{"x": *}, 2]')[0]
        assert list(r.matches(([{'x': 1}, 2],))) == [[{'x': 1}, 2]]

    def test_deep_nesting(self):
        r = value.parse_string('{"a": {"b": [1, ...]}}')[0]
        assert list(r.matches(({'a': {'b': [1, 2, 3]}},))) == [{'a': {'b': [1, 2, 3]}}]
        assert list(r.matches(({'a': {'b': [2, 3]}},))) == []

    def test_list_of_lists(self):
        r = value.parse_string('[[1, 2], [3, 4]]')[0]
        assert list(r.matches(([[1, 2], [3, 4]],))) == [[[1, 2], [3, 4]]]

    def test_set_in_dict(self):
        r = value.parse_string('{"tags": {1, ...}}')[0]
        assert list(r.matches(({'tags': {1, 2, 3}},))) == [{'tags': {1, 2, 3}}]


# ---------------------------------------------------------------------------
# Concrete containers (transform args)
# ---------------------------------------------------------------------------

class TestConcreteContainers:
    """
    Concrete container parsing in transform arg (targ) context.
    """

    def test_concrete_list(self):
        r = targ.parse_string('[1, 2, 3]')[0]
        assert r == [1, 2, 3]
        assert isinstance(r, list)

    def test_concrete_tuple(self):
        r = targ.parse_string('t[1, 2, 3]')[0]
        assert r == (1, 2, 3)
        assert isinstance(r, tuple)

    def test_concrete_list_l(self):
        r = targ.parse_string('l[1, 2]')[0]
        assert r == [1, 2]
        assert isinstance(r, list)

    def test_concrete_dict(self):
        r = targ.parse_string('{"a": 1, "b": 2}')[0]
        assert r == {'a': 1, 'b': 2}
        assert isinstance(r, dict)

    def test_concrete_dict_d(self):
        r = targ.parse_string('d{"a": 1}')[0]
        assert r == {'a': 1}
        assert isinstance(r, dict)

    def test_concrete_set(self):
        r = targ.parse_string('{1, 2, 3}')[0]
        assert r == {1, 2, 3}
        assert isinstance(r, set)

    def test_concrete_set_s(self):
        r = targ.parse_string('s{1, 2}')[0]
        assert r == {1, 2}
        assert isinstance(r, set)

    def test_concrete_frozenset(self):
        r = targ.parse_string('fs{1, 2}')[0]
        assert r == frozenset({1, 2})
        assert isinstance(r, frozenset)

    def test_concrete_empty_list(self):
        r = targ.parse_string('[]')[0]
        assert r == []
        assert isinstance(r, list)

    def test_concrete_empty_tuple(self):
        r = targ.parse_string('t[]')[0]
        assert r == ()
        assert isinstance(r, tuple)

    def test_concrete_empty_dict(self):
        r = targ.parse_string('{}')[0]
        assert r == {}
        assert isinstance(r, dict)

    def test_concrete_empty_set(self):
        r = targ.parse_string('s{}')[0]
        assert r == set()
        assert isinstance(r, set)

    def test_concrete_empty_frozenset(self):
        r = targ.parse_string('fs{}')[0]
        assert r == frozenset()
        assert isinstance(r, frozenset)

    def test_concrete_nested(self):
        r = targ.parse_string('{"a": [1, 2]}')[0]
        assert r == {'a': [1, 2]}

    def test_concrete_nested_tuple(self):
        r = targ.parse_string('[t[1, 2], 3]')[0]
        assert r == [(1, 2), 3]

    def test_concrete_strings(self):
        r = targ.parse_string('["hello", "world"]')[0]
        assert r == ['hello', 'world']


# ---------------------------------------------------------------------------
# Glob element
# ---------------------------------------------------------------------------

class TestGlob:
    """
    Glob element repr and matching.
    """

    def test_bare_glob_repr(self):
        g = Glob()
        assert repr(g) == '...'

    def test_glob_max_repr(self):
        g = Glob(max_count=5)
        assert repr(g) == '...5'

    def test_glob_range_repr(self):
        g = Glob(min_count=2, max_count=5)
        assert repr(g) == '...2:5'

    def test_glob_min_repr(self):
        g = Glob(min_count=2)
        assert repr(g) == '...2:'

    def test_glob_matches_anything(self):
        g = Glob()
        assert g.matches_element(1)
        assert g.matches_element('hello')
        assert g.matches_element(None)

    def test_glob_with_regex_pattern(self):
        from dotted.match import Regex
        r = Regex('\\d+')
        g = Glob(pattern=r)
        assert g.matches_element(42)
        assert not g.matches_element('hello')
