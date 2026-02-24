"""
Tests for guard transforms: field|transform=value, [slot]|transform=value,
**|transform=value, filter transforms [*&field|transform=value], and
template-level guards.

Guard transforms are matching-only: yielded values are originals (untransformed).
"""
import dotted


# --- Parse / Assemble round-trips ---


def test_parse_assemble_key_guard_transform():
    p = dotted.parse('field|int=7')
    assert dotted.assemble(p) == 'field|int=7'


def test_parse_assemble_key_guard_transform_neq():
    p = dotted.parse('field|int!=7')
    assert dotted.assemble(p) == 'field|int!=7'


def test_parse_assemble_wildcard_guard_transform():
    p = dotted.parse('*|int=7')
    assert dotted.assemble(p) == '*|int=7'


def test_parse_assemble_wildcard_guard_transform_neq():
    p = dotted.parse('*|int!=7')
    assert dotted.assemble(p) == '*|int!=7'


def test_parse_assemble_slot_guard_transform():
    p = dotted.parse('[*]|int=7')
    assert dotted.assemble(p) == '[*]|int=7'


def test_parse_assemble_slot_guard_transform_neq():
    p = dotted.parse('[*]|int!=7')
    assert dotted.assemble(p) == '[*]|int!=7'


def test_parse_assemble_multiseg_guard_transform():
    p = dotted.parse('a.b.c|int=7')
    assert dotted.assemble(p) == 'a.b.c|int=7'


def test_parse_assemble_chained_transforms():
    p = dotted.parse('field|float|int=7')
    assert dotted.assemble(p) == 'field|float|int=7'


def test_parse_assemble_recursive_guard_transform():
    p = dotted.parse('**|int=7')
    assert dotted.assemble(p) == '**|int=7'


def test_parse_assemble_recursive_guard_transform_neq():
    p = dotted.parse('**|int!=7')
    assert dotted.assemble(p) == '**|int!=7'


def test_parse_assemble_recursive_pattern_guard_transform():
    p = dotted.parse('*val|int=7')
    assert dotted.assemble(p) == '*val|int=7'


def test_parse_assemble_recursive_pattern_guard_transform_neq():
    p = dotted.parse('*val|int!=7')
    assert dotted.assemble(p) == '*val|int!=7'


def test_parse_assemble_filter_transform():
    """
    Filter transforms parse correctly (& assembles as . — pre-existing behavior).
    """
    p = dotted.parse('[*&val|int=7]')
    a = dotted.assemble(p)
    assert '|int=7' in a


def test_parse_assemble_filter_transform_neq():
    p = dotted.parse('[*&val|int!=7]')
    a = dotted.assemble(p)
    assert '|int!=7' in a


# --- Get with guard transforms (values are originals, not transformed) ---


def test_get_key_guard_transform_match():
    """
    Guard transforms filter by transformed value but yield the original.
    """
    assert dotted.get({'val': '7'}, 'val|int=7') == '7'


def test_get_key_guard_transform_no_match():
    assert dotted.get({'val': '3'}, 'val|int=7') is None


def test_get_key_guard_transform_neq():
    assert dotted.get({'val': '3'}, 'val|int!=7') == '3'


def test_get_key_guard_transform_neq_no_match():
    assert dotted.get({'val': '7'}, 'val|int!=7') is None


def test_get_wildcard_guard_transform():
    d = {'a': '7', 'b': '3', 'c': '7'}
    assert dotted.get(d, '*|int=7') == ('7', '7')


def test_get_wildcard_guard_transform_neq():
    assert dotted.get({'a': '7', 'b': '3'}, '*|int!=7') == ('3',)


def test_get_slot_guard_transform():
    assert dotted.get(['3', '7', '7'], '[*]|int=7') == ('7', '7')


def test_get_slot_guard_transform_neq():
    assert dotted.get(['3', '7', '7'], '[*]|int!=7') == ('3',)


def test_get_multiseg_guard_transform():
    """
    Guard with transform on the last segment of a multi-segment path.
    """
    data = {'a': {'b': {'c': '7'}}}
    assert dotted.get(data, 'a.b.c|int=7') == '7'


def test_get_multiseg_guard_transform_no_match():
    data = {'a': {'b': {'c': '3'}}}
    assert dotted.get(data, 'a.b.c|int=7') is None


def test_get_chained_transforms():
    """
    Multiple transforms chained before guard: str -> float -> int.
    """
    assert dotted.get({'val': '7.9'}, 'val|float|int=7') == '7.9'


def test_get_recursive_guard_transform():
    deep = {'x': {'val': '10'}, 'y': {'val': '5'}}
    assert dotted.get(deep, '**.val|int=10') == ('10',)


def test_get_recursive_guard_transform_neq():
    deep = {'x': {'val': '10'}, 'y': {'val': '5'}}
    assert dotted.get(deep, '**.val|int!=10') == ('5',)


def test_get_recursive_dstar_guard_transform():
    deep = {'x': {'a': '7'}, 'y': {'a': '3'}}
    assert dotted.get(deep, '**|int=7') == ('7',)


# --- Filter with transforms ---


def test_filter_slicefilter_transform():
    """
    SliceFilter [field|int=value] returns filtered list.
    """
    items = [{'val': '7', 'name': 'a'}, {'val': '3', 'name': 'b'}]
    result = dotted.get(items, '[val|int=7]')
    assert result == [{'val': '7', 'name': 'a'}]


def test_filter_slicefilter_transform_neq():
    items = [{'val': '7', 'name': 'a'}, {'val': '3', 'name': 'b'}]
    result = dotted.get(items, '[val|int!=7]')
    assert result == [{'val': '3', 'name': 'b'}]


def test_filter_slot_amp_transform():
    """
    Slot with amp filter: [*&field|transform=value] iterates and filters.
    """
    items = [{'val': '7', 'name': 'a'}, {'val': '3', 'name': 'b'}]
    assert dotted.get(items, '[*&val|int=7].name') == ('a',)


def test_filter_slot_amp_transform_neq():
    items = [{'val': '7', 'name': 'a'}, {'val': '3', 'name': 'b'}]
    assert dotted.get(items, '[*&val|int!=7].name') == ('b',)


# --- Update with guard transforms ---


def test_update_key_guard_transform():
    assert dotted.update({'a': '7', 'b': '3'}, '*|int=7', 'X') == {'a': 'X', 'b': '3'}


def test_update_key_guard_transform_neq():
    assert dotted.update({'a': '7', 'b': '3'}, '*|int!=7', 'X') == {'a': '7', 'b': 'X'}


def test_update_slot_guard_transform():
    assert dotted.update(['3', '7'], '[*]|int=7', 'X') == ['3', 'X']


# --- Remove with guard transforms ---


def test_remove_key_guard_transform():
    assert dotted.remove({'a': '7', 'b': '3'}, '*|int=7') == {'b': '3'}


def test_remove_key_guard_transform_neq():
    assert dotted.remove({'a': '7', 'b': '3'}, '*|int!=7') == {'a': '7'}


# --- Has with guard transforms ---


def test_has_key_guard_transform():
    assert dotted.has({'val': '7'}, 'val|int=7') is True


def test_has_key_guard_transform_no_match():
    assert dotted.has({'val': '7'}, 'val|int=99') is False


def test_has_wildcard_guard_transform_neq():
    assert dotted.has({'a': '7', 'b': '3'}, '*|int!=7') is True


# --- Existing behavior preserved ---


def test_plain_transform_still_works():
    """
    Plain transforms without guard still work.
    """
    assert dotted.get({'val': '7'}, 'val|int') == 7


def test_plain_guard_still_works():
    """
    Guards without transforms still work.
    """
    assert dotted.get({'a': 7, 'b': 3}, '*=7') == (7,)


def test_plain_guard_neq_still_works():
    assert dotted.get({'a': 7, 'b': 3}, '*!=7') == (3,)


def test_plain_wildcard_no_guard():
    assert dotted.get({'a': 1, 'b': 2}, '*') == (1, 2)
