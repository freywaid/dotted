"""
Tests for the + concatenation operator in key construction.
"""
import pytest
import dotted
from dotted.api import parse, get, update, remove, replace, is_template
from dotted.matchers import Concat, ConcatPart, Word, Numeric, Subst, Reference
from dotted.results import assemble


# ---- parsing: key context ----

def test_parse_word_plus_subst():
    """
    prefix_+$(name) parses as Key with Concat.
    """
    ops = parse('prefix_+$(name)')
    assert isinstance(ops[0].op, Concat)
    assert len(ops[0].op.parts) == 2
    assert isinstance(ops[0].op.parts[0].op, Word)
    assert isinstance(ops[0].op.parts[1].op, Subst)


def test_parse_subst_plus_word():
    """
    $(name)+_suffix parses as Key with Concat.
    """
    ops = parse('$(name)+_suffix')
    assert isinstance(ops[0].op, Concat)
    assert isinstance(ops[0].op.parts[0].op, Subst)
    assert isinstance(ops[0].op.parts[1].op, Word)


def test_parse_three_parts():
    """
    prefix_+$(name)+_end parses as 3-part Concat.
    """
    ops = parse('prefix_+$(name)+_end')
    assert isinstance(ops[0].op, Concat)
    assert len(ops[0].op.parts) == 3


def test_parse_all_literal_collapses():
    """
    hello+world collapses to a single Word at parse time.
    """
    ops = parse('hello+world')
    assert isinstance(ops[0].op, Word)
    assert ops[0].op.value == 'helloworld'


def test_parse_reference_plus_word():
    """
    $$(config.key)+_v2 parses as Key with Concat containing Reference.
    """
    ops = parse('$$(config.key)+_v2')
    assert isinstance(ops[0].op, Concat)
    assert isinstance(ops[0].op.parts[0].op, Reference)
    assert isinstance(ops[0].op.parts[1].op, Word)


def test_parse_multi_segment():
    """
    a.prefix_+$(name).b parses as 3 ops.
    """
    ops = parse('a.prefix_+$(name).b')
    assert len(ops) == 3
    assert isinstance(ops[1].op, Concat)


def test_parse_positional_plus_word():
    """
    $0+_suffix parses as Concat.
    """
    ops = parse('$0+_suffix')
    assert isinstance(ops[0].op, Concat)
    assert isinstance(ops[0].op.parts[0].op, Subst)


# ---- parsing: attr context ----

def test_parse_attr_concat():
    """
    @prefix_+$(name) parses as Attr with Concat.
    """
    ops = parse('x@prefix_+$(name)')
    assert isinstance(ops[1].op, Concat)
    assert isinstance(ops[1].op.parts[0].op, Word)
    assert isinstance(ops[1].op.parts[1].op, Subst)


def test_parse_attr_reference_concat():
    """
    @$$(config.key)+_v2 parses as Attr with Concat containing Reference.
    """
    ops = parse('x@$$(config.key)+_v2')
    assert isinstance(ops[1].op, Concat)
    assert isinstance(ops[1].op.parts[0].op, Reference)


def test_roundtrip_attr_concat():
    """
    assemble(parse('x@prefix_+$(name)')) round-trips.
    """
    assert assemble(parse('x@prefix_+$(name)')) == 'x@prefix_+$(name)'


def test_get_attr_reference_concat():
    """
    get with @$$(config.key)+_v2.
    """
    class Obj:
        pass
    o = Obj()
    o.name_v2 = 42
    data = {'config': {'key': 'name'}, 'obj': o}
    assert get(data, 'obj@$$(config.key)+_v2') == 42


# ---- parsing: slot context ----

def test_parse_slot_reference_concat():
    """
    [$$(meta.idx)+_key] parses as Slot with Concat.
    """
    ops = parse('[$$(meta.idx)+_key]')
    assert isinstance(ops[0].op, Concat)


def test_parse_slot_int_collapse():
    """
    [1+2] collapses to Numeric(3) at parse time.
    """
    ops = parse('[1+2]')
    assert isinstance(ops[0].op, Numeric)
    assert ops[0].op.value == 3


def test_parse_slot_string_collapse():
    """
    ['a'+'b'] collapses to Word("ab") at parse time.
    """
    ops = parse("['a'+'b']")
    assert isinstance(ops[0].op, Word)
    assert ops[0].op.value == 'ab'


# ---- appender/slice regression ----

def test_appender_still_works():
    """
    [+] still parses as SlotSpecial (appender).
    """
    ops = parse('[+]')
    assert assemble(ops) == '[+]'


def test_appender_unique_still_works():
    """
    [+?] still parses as SlotSpecial (appender-unique).
    """
    ops = parse('[+?]')
    assert assemble(ops) == '[+?]'


def test_slice_plus_start():
    """
    [+:] still parses as a slice.
    """
    ops = parse('[+:]')
    assert assemble(ops) == '[+:]'


def test_slice_plus_end():
    """
    [:+] still parses as a slice.
    """
    ops = parse('[:+]')
    assert assemble(ops) == '[:+]'


# ---- Python + semantics ----

def test_int_plus_int():
    """
    [1+2] → 3 (int + int = addition).
    """
    assert get({'x': {3: 'found'}}, 'x[1+2]') == 'found'


def test_str_plus_str():
    """
    'a'+'b' → 'ab' (str + str = concat).
    """
    assert get({'ab': 42}, "'a'+'b'") == 42


def test_per_part_transform_str():
    """
    ['0'+0|str] → '00' (int 0 coerced to str via transform).
    """
    ops = parse("['0'+0|str]")
    assert isinstance(ops[0].op, Concat)
    assert ops[0].op.value == '00'


def test_per_part_transform_int():
    """
    [1+$(var)|int] with var='5' → 6 (string coerced to int via transform).
    """
    result = replace('[1+$(var)|int]', {'var': '5'})
    assert result == '[6]'


# ---- round-trip ----

def test_roundtrip_prefix_subst():
    """
    assemble(parse('prefix_+$(name)')) round-trips.
    """
    assert assemble(parse('prefix_+$(name)')) == 'prefix_+$(name)'


def test_roundtrip_positional_suffix():
    """
    assemble(parse('$0+_suffix')) round-trips.
    """
    assert assemble(parse('$0+_suffix')) == '$0+_suffix'


def test_roundtrip_reference_suffix():
    """
    assemble(parse('$$(config.key)+_v2')) round-trips.
    """
    assert assemble(parse('$$(config.key)+_v2')) == '$$(config.key)+_v2'


def test_roundtrip_multi_segment():
    """
    assemble(parse('a.prefix_+$(name).b')) round-trips.
    """
    assert assemble(parse('a.prefix_+$(name).b')) == 'a.prefix_+$(name).b'


# ---- replace (substitution) ----

def test_replace_prefix_subst():
    """
    replace('prefix_+$(name)', {'name': 'alice'}) → 'prefix_alice'.
    """
    assert replace('prefix_+$(name)', {'name': 'alice'}) == 'prefix_alice'


def test_replace_positional_suffix():
    """
    replace('$0+_suffix', ['hello']) → 'hello_suffix'.
    """
    assert replace('$0+_suffix', ['hello']) == 'hello_suffix'


def test_replace_partial():
    """
    Partial replace leaves unresolved concat as-is.
    """
    result = replace('prefix_+$(name)', {}, partial=True)
    assert result == 'prefix_+$(name)'


# ---- get (reference) ----

def test_get_reference_concat():
    """
    $$(config.key)+_v2 resolves reference and concatenates.
    """
    data = {'config': {'key': 'name'}, 'name_v2': 42}
    assert get(data, '$$(config.key)+_v2') == 42


def test_get_relative_reference_concat():
    """
    Relative reference + concat: $$(^key)+_suffix.
    """
    data = {'a': {'key': 'item', 'item_data': 99}}
    assert get(data, 'a.$$(^key)+_data') == 99


# ---- update ----

def test_update_reference_concat():
    """
    update with $$(config.key)+_v2.
    """
    data = {'config': {'key': 'name'}, 'name_v2': 42}
    result = update(data, '$$(config.key)+_v2', 99)
    assert result == {'config': {'key': 'name'}, 'name_v2': 99}


# ---- remove ----

def test_remove_reference_concat():
    """
    remove with $$(config.key)+_v2.
    """
    data = {'config': {'key': 'name'}, 'name_v2': 42, 'other': 1}
    result = remove(data, '$$(config.key)+_v2')
    assert result == {'config': {'key': 'name'}, 'other': 1}


# ---- is_template ----

def test_is_template_concat_with_subst():
    """
    prefix_+$(name) is a template.
    """
    assert is_template('prefix_+$(name)')


def test_is_template_concat_literal():
    """
    hello+world is NOT a template (collapses to literal).
    """
    assert not is_template('hello+world')


def test_is_template_concat_positional():
    """
    $0+_suffix is a template.
    """
    assert is_template('$0+_suffix')
