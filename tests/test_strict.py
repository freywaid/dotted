"""
Tests for strict=True mode.

In strict mode:
- Slot accessors ([0], [*], etc.) only match list-like nodes, never dict keys
- Numeric Key accessors (.0, etc.) only match dict keys, never list indices
- Mismatches silently return empty (consistent with optional chaining)
"""
import dotted


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

class TestStrictGet:
    def test_slot_on_dict_nonstrict_returns_value(self):
        """
        [0] on {0: 'x'} — non-strict falls through to dict key
        """
        assert dotted.get({0: 'x'}, '[0]') == 'x'

    def test_slot_on_dict_strict_returns_default(self):
        """
        [0] on {0: 'x'} — strict returns default (no fallthrough)
        """
        assert dotted.get({0: 'x'}, '[0]', strict=True) is None

    def test_slot_wildcard_on_dict_nonstrict(self):
        """
        [*] on dict — non-strict matches dict keys
        """
        assert dotted.get({'a': 1, 'b': 2}, '[*]') == (1, 2)

    def test_slot_wildcard_on_dict_strict(self):
        """
        [*] on dict — strict returns empty
        """
        assert dotted.get({'a': 1, 'b': 2}, '[*]', strict=True) == ()

    def test_key_on_list_nonstrict_returns_value(self):
        """
        .0 on ['a', 'b'] — non-strict coerces to list index
        """
        assert dotted.get(['a', 'b'], '0') == 'a'

    def test_key_on_list_strict_returns_default(self):
        """
        .0 on ['a', 'b'] — strict returns default (no coercion)
        """
        assert dotted.get(['a', 'b'], '0', strict=True) is None

    def test_slot_on_list_strict_works(self):
        """
        [0] on list — strict still works (slot matches list)
        """
        assert dotted.get(['a', 'b'], '[0]', strict=True) == 'a'

    def test_key_on_dict_strict_works(self):
        """
        .x on dict — strict still works (key matches dict)
        """
        assert dotted.get({'x': 7}, 'x', strict=True) == 7

    def test_nested_slot_on_dict_strict(self):
        """
        path.to[0] where [0] hits a dict — strict skips
        """
        d = {'path': {'to': {0: 'nope'}}}
        assert dotted.get(d, 'path.to[0]') == 'nope'
        assert dotted.get(d, 'path.to[0]', strict=True) is None

    def test_nested_key_on_list_strict(self):
        """
        path.0 where .0 hits a list — strict skips
        """
        d = {'path': ['a', 'b']}
        assert dotted.get(d, 'path.0') == 'a'
        assert dotted.get(d, 'path.0', strict=True) is None

    def test_strict_default_is_false(self):
        """
        Default behavior is non-strict
        """
        assert dotted.get({0: 'x'}, '[0]') == 'x'
        assert dotted.get(['a'], '0') == 'a'


# ---------------------------------------------------------------------------
# HAS
# ---------------------------------------------------------------------------

class TestStrictHas:
    def test_slot_on_dict_strict(self):
        assert dotted.has({0: 'x'}, '[0]') is True
        assert dotted.has({0: 'x'}, '[0]', strict=True) is False

    def test_key_on_list_strict(self):
        assert dotted.has(['a'], '0') is True
        assert dotted.has(['a'], '0', strict=True) is False


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------

class TestStrictUpdate:
    def test_slot_update_on_dict_nonstrict(self):
        """
        [0] update on dict — non-strict updates dict key
        """
        d = {0: 'old'}
        result = dotted.update(d, '[0]', 'new')
        assert result == {0: 'new'}

    def test_slot_update_on_dict_strict_noop(self):
        """
        [0] update on dict — strict is a no-op
        """
        d = {0: 'old'}
        result = dotted.update(d, '[0]', 'new', strict=True)
        assert result == {0: 'old'}

    def test_key_update_on_list_nonstrict(self):
        """
        .0 update on list — non-strict updates by index
        """
        result = dotted.update(['a', 'b'], '0', 'z')
        assert result == ['z', 'b']

    def test_key_update_on_list_strict_noop(self):
        """
        .0 update on list — strict is a no-op
        """
        result = dotted.update(['a', 'b'], '0', 'z', strict=True)
        assert result == ['a', 'b']

    def test_wildcard_slot_update_on_dict_strict_noop(self):
        """
        [*] update on dict — strict is a no-op
        """
        d = {'a': 1, 'b': 2}
        result = dotted.update(d, '[*]', 99, strict=True)
        assert result == {'a': 1, 'b': 2}

    def test_slot_update_on_list_strict_works(self):
        """
        [0] update on list — strict still works
        """
        result = dotted.update(['a', 'b'], '[0]', 'z', strict=True)
        assert result == ['z', 'b']

    def test_key_update_on_dict_strict_works(self):
        """
        .x update on dict — strict still works
        """
        result = dotted.update({'x': 1}, 'x', 2, strict=True)
        assert result == {'x': 2}


# ---------------------------------------------------------------------------
# REMOVE
# ---------------------------------------------------------------------------

class TestStrictRemove:
    def test_slot_remove_on_dict_nonstrict(self):
        """
        [0] remove on dict — non-strict removes dict key
        """
        d = {0: 'x', 1: 'y'}
        result = dotted.remove(d, '[0]')
        assert result == {1: 'y'}

    def test_slot_remove_on_dict_strict_noop(self):
        """
        [0] remove on dict — strict is a no-op
        """
        d = {0: 'x', 1: 'y'}
        result = dotted.remove(d, '[0]', strict=True)
        assert result == {0: 'x', 1: 'y'}

    def test_key_remove_on_list_strict_noop(self):
        """
        .0 remove on list — strict is a no-op (can't remove by key from list)
        """
        result = dotted.remove(['a', 'b'], '0', strict=True)
        assert result == ['a', 'b']

    def test_slot_remove_on_list_strict_works(self):
        """
        [0] remove on list — strict still works
        """
        result = dotted.remove(['a', 'b'], '[0]')
        assert result == ['b']

    def test_key_remove_on_dict_strict_works(self):
        """
        .x remove on dict — strict still works
        """
        result = dotted.remove({'x': 1, 'y': 2}, 'x', strict=True)
        assert result == {'y': 2}


# ---------------------------------------------------------------------------
# EXPAND / PLUCK
# ---------------------------------------------------------------------------

class TestStrictExpandPluck:
    def test_slot_wildcard_expand_on_dict_strict(self):
        """
        [*] expand on dict — strict returns empty
        """
        d = {'a': 1, 'b': 2}
        assert dotted.expand(d, '[*]', strict=True) == ()

    def test_key_wildcard_expand_on_list_strict(self):
        """
        * expand on list — strict returns empty (wildcard key on list)
        """
        # * matches dict keys; on a list in non-strict it may coerce
        # but in strict mode it should not
        result = dotted.expand(['a', 'b'], '*', strict=True)
        assert result == ()

    def test_pluck_slot_on_dict_strict(self):
        """
        pluck [*] on dict — strict returns empty
        """
        d = {'a': 1, 'b': 2}
        result = dotted.pluck(d, '[*]', strict=True)
        assert result == ()

    def test_pluck_key_on_dict_strict_works(self):
        """
        pluck * on dict — strict still works for keys on dicts
        """
        d = {'a': 1, 'b': 2}
        result = dotted.pluck(d, '*', strict=True)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# RECURSIVE PATTERNS
# ---------------------------------------------------------------------------

class TestStrictRecursive:
    def test_recursive_key_on_nested_strict(self):
        """
        ** recursively matches keys — strict shouldn't affect this
        (** matches dict keys, which is always valid on dicts)
        """
        d = {'a': {'b': {'c': 1}}, 'x': {'y': 2}}
        result = dotted.get(d, '**', strict=True)
        assert len(result) > 0

    def test_recursive_with_slot_mismatch_strict(self):
        """
        Recursive into nested dicts; slot access at leaf — strict blocks
        """
        d = {'a': {0: 'found'}}
        # non-strict: [0] falls through to dict key
        assert dotted.get(d, 'a[0]') == 'found'
        # strict: [0] on dict is blocked
        assert dotted.get(d, 'a[0]', strict=True) is None


# ---------------------------------------------------------------------------
# MULTI VARIANTS
# ---------------------------------------------------------------------------

class TestStrictMulti:
    def test_get_multi_strict(self):
        d = {0: 'x', 'a': 1}
        result = list(dotted.get_multi(d, ['[0]', 'a'], strict=True))
        # [0] on dict blocked, 'a' works
        assert result == [1]

    def test_update_multi_strict(self):
        d = {0: 'old', 'a': 1}
        result = dotted.update_multi(d, [('[0]', 'new'), ('a', 2)], strict=True)
        assert result[0] == 'old'  # [0] update blocked
        assert result['a'] == 2    # 'a' update works

    def test_remove_multi_strict(self):
        d = {0: 'x', 'a': 1}
        result = dotted.remove_multi(d, ['[0]', 'a'], strict=True)
        assert 0 in result      # [0] remove blocked
        assert 'a' not in result  # 'a' remove works


# ---------------------------------------------------------------------------
# SETDEFAULT
# ---------------------------------------------------------------------------

class TestStrictSetdefault:
    def test_setdefault_slot_on_dict_strict(self):
        """
        setdefault with [0] on dict — strict: key not found, sets nothing
        """
        d = {0: 'existing'}
        # non-strict: key exists
        assert dotted.setdefault(d, '[0]', 'default') == 'existing'
        # strict: key not found via slot, but setdefault tries to update...
        # with strict the has() returns False and update is also a no-op
        d2 = {0: 'existing'}
        result = dotted.setdefault(d2, '[0]', 'default', strict=True)
        # The value set should be 'default' but update is no-op in strict...
        # has returns False, tries update (no-op), then get returns None
        assert result is None


# ---------------------------------------------------------------------------
# APPLY
# ---------------------------------------------------------------------------

class TestStrictApply:
    def test_apply_slot_on_dict_strict(self):
        """
        apply [*]|str on dict — strict blocks
        """
        d = {'a': 1, 'b': 2}
        result = dotted.apply(d, '[*]|str', strict=True)
        assert result == {'a': 1, 'b': 2}  # unchanged
