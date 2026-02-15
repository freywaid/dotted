import pytest
import dotted


# --- Parse / Assemble round-trip ---

class TestValueGuardParseAssemble:
    def test_key_guard(self):
        p = dotted.parse('first=7')
        assert dotted.assemble(p) == 'first=7'

    def test_wildcard_guard(self):
        p = dotted.parse('*=7')
        assert dotted.assemble(p) == '*=7'

    def test_slot_guard(self):
        p = dotted.parse('[*]=7')
        assert dotted.assemble(p) == '[*]=7'

    def test_slot_index_guard(self):
        p = dotted.parse('[0]=1')
        assert dotted.assemble(p) == '[0]=1'

    def test_guard_none(self):
        p = dotted.parse('*=None')
        assert dotted.assemble(p) == '*=None'

    def test_guard_string(self):
        p = dotted.parse('first="hello"')
        assert dotted.assemble(p) == "first='hello'"

    def test_guard_regex(self):
        p = dotted.parse('*=/pattern/')
        assert dotted.assemble(p) == '*=/pattern/'

    def test_guard_true(self):
        p = dotted.parse('active=True')
        assert dotted.assemble(p) == 'active=True'

    def test_guard_false(self):
        p = dotted.parse('enabled=False')
        assert dotted.assemble(p) == 'enabled=False'

    def test_guard_wildcard_value(self):
        p = dotted.parse('*=*')
        assert dotted.assemble(p) == '*=*'

    def test_neq_key_guard(self):
        p = dotted.parse('first!=7')
        assert dotted.assemble(p) == 'first!=7'

    def test_neq_wildcard_guard(self):
        p = dotted.parse('*!=7')
        assert dotted.assemble(p) == '*!=7'

    def test_neq_slot_guard(self):
        p = dotted.parse('[*]!=7')
        assert dotted.assemble(p) == '[*]!=7'

    def test_continuation(self):
        p = dotted.parse('a.first=7')
        assert dotted.assemble(p) == 'a.first=7'

    def test_continuation_wildcard(self):
        p = dotted.parse('a.*=7')
        assert dotted.assemble(p) == 'a.*=7'

    def test_slot_continuation(self):
        p = dotted.parse('a[0]=1')
        assert dotted.assemble(p) == 'a[0]=1'


# --- Get: key guard ---

class TestValueGuardGet:
    def test_key_guard_match(self):
        assert dotted.get({'first': 7, 'last': 3}, 'first=7') == 7

    def test_key_guard_no_match(self):
        assert dotted.get({'first': 3}, 'first=7') is None

    def test_key_guard_missing_key(self):
        assert dotted.get({'other': 7}, 'first=7') is None

    def test_wildcard_guard(self):
        r = dotted.get({'a': 7, 'b': 7, 'c': 3}, '*=7')
        assert r == (7, 7)

    def test_wildcard_guard_no_match(self):
        r = dotted.get({'a': 1, 'b': 2}, '*=7')
        assert r == ()

    def test_neq_guard(self):
        r = dotted.get({'a': 7, 'b': 3, 'c': 7}, '*!=7')
        assert r == (3,)

    def test_neq_key_guard(self):
        assert dotted.get({'first': 7}, 'first!=7') is None

    def test_neq_key_guard_match(self):
        assert dotted.get({'first': 3}, 'first!=7') == 3


# --- Get: slot guard ---

class TestValueGuardSlotGet:
    def test_slot_wildcard_guard(self):
        r = dotted.get([1, 7, 3, 7], '[*]=7')
        assert r == (7, 7)

    def test_slot_index_guard_match(self):
        assert dotted.get([1, 7, 3], '[0]=1') == 1

    def test_slot_index_guard_no_match(self):
        assert dotted.get([1, 7, 3], '[0]=7') is None

    def test_slot_neq_guard(self):
        r = dotted.get([1, 7, 3, 7], '[*]!=7')
        assert r == (1, 3)

    def test_slot_guard_none(self):
        r = dotted.get([None, 1, None], '[*]=None')
        assert r == (None, None)

    def test_slot_guard_string(self):
        r = dotted.get(['hello', 'world', 'hello'], '[*]="hello"')
        assert r == ('hello', 'hello')

    def test_slot_guard_bool(self):
        r = dotted.get([True, False, True], '[*]=True')
        assert r == (True, True)

    def test_slot_guard_regex(self):
        r = dotted.get(['hello', 'help', 'world'], '[*]=/hel.*/')
        assert r == ('hello', 'help')


# --- Get: continuation ---

class TestValueGuardContinuation:
    def test_dot_continuation(self):
        assert dotted.get({'a': {'first': 7}}, 'a.first=7') == 7

    def test_dot_continuation_no_match(self):
        assert dotted.get({'a': {'first': 3}}, 'a.first=7') is None

    def test_wildcard_continuation(self):
        d = {'a': {'val': 7}, 'b': {'val': 3}, 'c': {'val': 7}}
        r = dotted.get(d, 'a.*=7')
        assert r == (7,)

    def test_slot_continuation(self):
        d = {'items': [10, 20, 30]}
        assert dotted.get(d, 'items[0]=10') == 10
        assert dotted.get(d, 'items[0]=99') is None


# --- Update ---

class TestValueGuardUpdate:
    def test_update_key_guard_match(self):
        d = {'a': 7, 'b': 3}
        r = dotted.update(d, 'a=7', 99)
        assert r == {'a': 99, 'b': 3}

    def test_update_key_guard_no_match(self):
        d = {'a': 3, 'b': 7}
        r = dotted.update(d, 'a=7', 99)
        assert r == {'a': 3, 'b': 7}

    def test_update_wildcard_guard(self):
        d = {'a': 7, 'b': 3, 'c': 7}
        r = dotted.update(d, '*=7', 99)
        assert r == {'a': 99, 'b': 3, 'c': 99}


# --- Remove ---

class TestValueGuardRemove:
    def test_remove_key_guard_match(self):
        d = {'a': 7, 'b': 3}
        r = dotted.remove(d, 'a=7')
        assert r == {'b': 3}

    def test_remove_key_guard_no_match(self):
        d = {'a': 3, 'b': 7}
        r = dotted.remove(d, 'a=7')
        assert r == {'a': 3, 'b': 7}

    def test_remove_wildcard_guard(self):
        d = {'a': 7, 'b': 3, 'c': 7}
        r = dotted.remove(d, '*=7')
        assert r == {'b': 3}


# --- Pluck ---

class TestValueGuardPluck:
    def test_pluck_key_guard(self):
        d = {'a': 7, 'b': 7, 'c': 3}
        r = dotted.pluck(d, '*=7')
        assert r == (('a', 7), ('b', 7))

    def test_pluck_slot_guard(self):
        r = dotted.pluck([10, 20, 30], '[*]=20')
        assert r == (('[1]', 20),)


# --- NOP composition ---

class TestValueGuardNop:
    def test_nop_key_guard(self):
        p = dotted.parse('~first=7')
        assert dotted.assemble(p) == '~first=7'
        # NopWrap means match but don't update
        assert dotted.get({'first': 7}, '~first=7') == 7

    def test_nop_slot_guard(self):
        p = dotted.parse('[~*]=7')
        assert dotted.assemble(p) == '[~*]=7'
        assert dotted.get([1, 7, 3], '[~*]=7') == (7,)


# --- Existing filter operators still work after special case removal ---

class TestFilterStillWorks:
    def test_slicefilter_on_dicts(self):
        """[*=7] on dicts still works via SliceFilter."""
        data = [{'a': 7, 'b': 1}, {'a': 3}]
        r = dotted.get(data, '[*=7]')
        assert r == [{'a': 7, 'b': 1}]

    def test_slicefilter_on_primitives_empty(self):
        """[*=7] on primitives now returns [] (no keys to test)."""
        r = dotted.get([1, 7, 3], '[*=7]')
        assert r == []

    def test_filter_conjunction(self):
        d = {'a': {'id': 1, 'x': True}, 'b': {'id': 2, 'x': True}}
        r = dotted.get(d, '*&id=1&x=True')
        assert r == ({'id': 1, 'x': True},)

    def test_filter_disjunction(self):
        d = {'a': {'id': 1}, 'b': {'id': 2}, 'c': {'id': 3}}
        r = dotted.get(d, '*&id=1,id=2')
        assert len(r) == 2

    def test_filter_grouping(self):
        data = [{'id': 1, 'active': True}, {'id': 2, 'active': False}]
        r = dotted.get(data, '[(id=1,id=2)&active=True]')
        assert len(r) == 1

    def test_filter_negation(self):
        data = [{'id': 1}, {'id': 2}, {'id': 3}]
        r = dotted.get(data, '[!(id=1)]')
        assert len(r) == 2

    def test_special_case_removed_consistency(self):
        """*&*=7 no longer matches primitives — consistent with *&first=7."""
        d = {'first': 7}
        r1 = dotted.get(d, '*&*=7')
        r2 = dotted.get(d, '*&first=7')
        # Both fail: filter tests the dict-value (7, a primitive) for keys — 7 has no keys
        assert r1 == r2 == ()


# --- Has ---

class TestValueGuardHas:
    def test_has_key_guard(self):
        assert dotted.has({'a': 7}, 'a=7') is True
        assert dotted.has({'a': 3}, 'a=7') is False

    def test_has_slot_guard(self):
        assert dotted.has([1, 7, 3], '[*]=7') is True
        assert dotted.has([1, 2, 3], '[*]=7') is False


# --- Expand ---

class TestValueGuardExpand:
    def test_expand_wildcard_guard(self):
        d = {'a': 7, 'b': 3, 'c': 7}
        r = dotted.expand(d, '*=7')
        assert set(r) == {'a', 'c'}
