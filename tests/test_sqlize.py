"""
Tests for dotted.sqlize — dotted path → SQL clause components.

sqlize() returns a paramstyle-neutral Resolver with SQL fragments.
Resolver.build(sql, paramstyle=..., **bindings) renders final SQL.
"""
import pytest
from dotted.sqlize import (
    sqlize, Resolver, SQLFragment, ParamStyle, TranslationError,
)


# All supported paramstyles as fixture values. Used to parametrize
# scenario tests so each runs under every paramstyle.
PARAMSTYLES = ['named', 'pyformat', 'qmark', 'format', 'numeric', 'dollar-numeric']

# Styles that share bindings by back-reference (repeated markers collapse
# to one entry). qmark/format have no back-reference and repeat values.
BACKREF_STYLES = ['named', 'pyformat', 'numeric', 'dollar-numeric']

# Styles that return a dict vs a list.
DICT_STYLES = ['named', 'pyformat']


# ---------------- scalar columns ----------------

def test_bare_column():
    r = sqlize('status')
    sql, params = Resolver.build(r.select)
    assert sql == 'status'
    assert params == {}
    assert r.where is None


def test_column_eq_string():
    r = sqlize('status="active"')
    assert Resolver.build(r.where) == ('status = :_p1', {'_p1': 'active'})
    assert Resolver.build(r.where, paramstyle='dollar-numeric') == (
        'status = $1', ['active'])


def test_column_eq_numeric():
    r = sqlize('age=30')
    assert Resolver.build(r.where) == ('age = :_p1', {'_p1': 30})


def test_column_ordering():
    r = sqlize('age>=30')
    assert Resolver.build(r.where) == ('age >= :_p1', {'_p1': 30})
    r = sqlize('age<18')
    assert Resolver.build(r.where) == ('age < :_p1', {'_p1': 18})


def test_column_ne():
    r = sqlize('status!="banned"')
    assert Resolver.build(r.where) == (
        'status != :_p1', {'_p1': 'banned'})


def test_column_null():
    r = sqlize('deleted_at=None')
    assert Resolver.build(r.where) == ('deleted_at IS NULL', {})


def test_column_not_null():
    r = sqlize('deleted_at!=None')
    assert Resolver.build(r.where) == ('deleted_at IS NOT NULL', {})


def test_column_regex():
    r = sqlize('name=/^alice/')
    assert Resolver.build(r.where) == ('name ~ :_p1', {'_p1': '^alice'})


def test_column_regex_ne():
    r = sqlize('name!=/^alice/')
    assert Resolver.build(r.where) == ('name !~ :_p1', {'_p1': '^alice'})


# ---------------- jsonb traversal ----------------

def test_jsonb_select_only():
    r = sqlize('data.user.name')
    assert Resolver.build(r.select) == ("data #>> '{user,name}'", {})
    assert r.where is None


def test_jsonb_single_segment():
    r = sqlize('data.status')
    assert Resolver.build(r.select) == ("data #>> '{status}'", {})


def test_jsonb_string_guard():
    r = sqlize('data.user.role="admin"')
    assert Resolver.build(r.where) == (
        "(data #>> '{user,role}') = :_p1",
        {'_p1': 'admin'},
    )


def test_jsonb_numeric_guard():
    r = sqlize('data.user.age>=30')
    assert Resolver.build(r.where) == (
        "(data #>> '{user,age}')::numeric >= :_p1",
        {'_p1': 30},
    )


def test_jsonb_boolean_guard():
    r = sqlize('data.user.active=True')
    sql, params = Resolver.build(r.where)
    assert sql == "data #> '{user,active}' = 'true'::jsonb"
    assert params == {}


def test_jsonb_boolean_ne():
    r = sqlize('data.user.active!=True')
    sql, params = Resolver.build(r.where)
    assert sql == "data #> '{user,active}' != 'true'::jsonb"


def test_jsonb_null_guard():
    r = sqlize('data.score=None')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "data #> '{score}' IS NULL "
        "OR jsonb_typeof(data #> '{score}') = 'null'"
    )
    assert params == {}


def test_jsonb_not_null_guard():
    r = sqlize('data.score!=None')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "data #> '{score}' IS NOT NULL "
        "AND jsonb_typeof(data #> '{score}') != 'null'"
    )


def test_jsonb_regex():
    r = sqlize('data.user.name=/alice/')
    assert Resolver.build(r.where) == (
        "(data #>> '{user,name}') ~ :_p1",
        {'_p1': 'alice'},
    )


# ---------------- guard transforms (casts) ----------------

def test_guard_transform_int():
    r = sqlize('data.user.age|int>=30')
    assert Resolver.build(r.where) == (
        "(data #>> '{user,age}')::int >= :_p1",
        {'_p1': 30},
    )


def test_guard_transform_float():
    r = sqlize('data.user.score|float>=#"0.5"')
    assert Resolver.build(r.where) == (
        "(data #>> '{user,score}')::float >= :_p1",
        {'_p1': 0.5},
    )


def test_guard_transform_str():
    r = sqlize('data.user.id|str="42"')
    assert Resolver.build(r.where) == (
        "(data #>> '{user,id}')::text = :_p1",
        {'_p1': '42'},
    )


def test_guard_transform_unknown():
    with pytest.raises(TranslationError):
        sqlize('data.user.name|uppercase="ALICE"')


# ---------------- boolean groups ----------------

def test_group_and():
    r = sqlize('data.(user.age>=30 & status="active")')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "((data #>> '{user,age}')::numeric >= :_p1) "
        "AND ((data #>> '{status}') = :_p2)"
    )
    assert params == {'_p1': 30, '_p2': 'active'}


def test_group_or():
    r = sqlize('data.(user.age=18, user.age=21)')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "((data #>> '{user,age}')::numeric = :_p1) "
        "OR ((data #>> '{user,age}')::numeric = :_p2)"
    )
    assert params == {'_p1': 18, '_p2': 21}


def test_group_not():
    r = sqlize('data.(!status="banned")')
    sql, params = Resolver.build(r.where)
    assert sql == "NOT ((data #>> '{status}') = :_p1)"
    assert params == {'_p1': 'banned'}


def test_top_level_and_across_columns():
    r = sqlize('(age>=18 & status="active")')
    assert Resolver.build(r.where) == (
        '(age >= :_p1) AND (status = :_p2)',
        {'_p1': 18, '_p2': 'active'},
    )


def test_top_level_or_across_columns():
    r = sqlize('(age=18, age=21, age=25)')
    assert Resolver.build(r.where) == (
        '(age = :_p1) OR (age = :_p2) OR (age = :_p3)',
        {'_p1': 18, '_p2': 21, '_p3': 25},
    )


def test_top_level_mixed_columns_and_jsonb():
    r = sqlize('(data.user.age>=30 & status="active")')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "((data #>> '{user,age}')::numeric >= :_p1) "
        "AND (status = :_p2)"
    )
    assert params == {'_p1': 30, '_p2': 'active'}


def test_group_branch_without_guard_errors():
    with pytest.raises(TranslationError):
        sqlize('data.(x=1, y)')


# ---------------- absolute root references ----------------

def test_ref_select():
    r = sqlize('data.$$(data.config.field)')
    assert Resolver.build(r.select) == (
        "data ->> (data #>> '{config,field}')", {}
    )


def test_ref_with_guard():
    r = sqlize('data.$$(data.config.field)="Alice"')
    sql, params = Resolver.build(r.where)
    assert sql == "(data ->> (data #>> '{config,field}')) = :_p1"
    assert params == {'_p1': 'Alice'}


def test_ref_mid_path():
    r = sqlize('data.users.$$(data.config.key).name')
    assert Resolver.build(r.select) == (
        "data -> 'users' -> (data #>> '{config,key}') ->> 'name'", {}
    )


def test_ref_numeric_guard():
    r = sqlize('data.$$(data.idx)=30')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "(data ->> (data #>> '{idx}'))::numeric = :_p1"
    )
    assert params == {'_p1': 30}


def test_ref_relative_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$$(^field)')


def test_ref_parent_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$$(^^field)')


# ---------------- pattern paths (jsonb_path_exists) ----------------

def test_pattern_slot_wildcard_guard_numeric():
    r = sqlize('data.users[*].age >= 30')
    sql, params = Resolver.build(r.where)
    assert sql == "jsonb_path_exists(data, '$.users[*].age ? (@ >= 30)')"
    assert params == {}


def test_pattern_slot_wildcard_guard_boolean():
    r = sqlize('data.users[*].active = True')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "jsonb_path_exists(data, '$.users[*].active ? (@ == true)')"
    )


def test_pattern_key_wildcard_guard_string():
    r = sqlize('data.*.status = "on"')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "jsonb_path_exists(data, '$.*.status ? (@ == $_p1)', "
        "jsonb_build_object('_p1', :_p1))"
    )
    assert params == {'_p1': 'on'}


def test_pattern_recursive_guard():
    r = sqlize('data.**.flag = True')
    sql, params = Resolver.build(r.where)
    assert sql == "jsonb_path_exists(data, '$.**.flag ? (@ == true)')"


def test_pattern_slice_filter():
    r = sqlize('data.users[age>=30]')
    sql, params = Resolver.build(r.where)
    assert sql == "jsonb_path_exists(data, '$.users[*] ? (@.age >= 30)')"


def test_pattern_filter_wrap():
    r = sqlize('data.users[*&age>=30]')
    sql, params = Resolver.build(r.where)
    assert sql == "jsonb_path_exists(data, '$.users[*] ? (@.age >= 30)')"


def test_pattern_filter_and_trailing_guard():
    r = sqlize('data.users[*&age>=30].name = "alice"')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "jsonb_path_exists(data, "
        "'$.users[*] ? (@.age >= 30).name ? (@ == $_p1)', "
        "jsonb_build_object('_p1', :_p1))"
    )
    assert params == {'_p1': 'alice'}


def test_pattern_with_substitution():
    r = sqlize('data.users[*].age >= $(min_age)')
    sql, params = Resolver.build(r.where, min_age=30)
    assert sql == (
        "jsonb_path_exists(data, '$.users[*].age ? (@ >= $min_age)', "
        "jsonb_build_object('min_age', :min_age))"
    )
    assert params == {'min_age': 30}


def test_pattern_with_substitution_dollar():
    # Dollar-numeric: jsonb_build_object still takes value by position
    r = sqlize('data.users[*].age >= $(min_age)')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric', min_age=30)
    assert sql == (
        "jsonb_path_exists(data, '$.users[*].age ? (@ >= $min_age)', "
        "jsonb_build_object('min_age', $1))"
    )
    assert args == [30]


def test_pattern_mixed_in_group():
    r = sqlize('(data.users[*].age >= 30 & status = "active")')
    sql, params = Resolver.build(r.where)
    assert sql == (
        "(jsonb_path_exists(data, '$.users[*].age ? (@ >= 30)')) "
        "AND (status = :_p1)"
    )
    assert params == {'_p1': 'active'}


# ---------------- substitutions ----------------

def test_value_sub_with_binding():
    # Bound substitution resolves at sqlize time and becomes a generated
    # literal name.
    r = sqlize('age>=$(min_age)', bindings={'min_age': 30})
    sql, params = Resolver.build(r.where)
    assert sql == 'age >= :_p1'
    assert params == {'_p1': 30}


def test_value_sub_without_binding():
    # Unbound — build() requires binding
    r = sqlize('age>=$(min_age)')
    assert 'min_age' in r.where.unbound.values()
    sql, params = Resolver.build(r.where, min_age=30)
    assert sql == 'age >= :min_age'
    assert params == {'min_age': 30}


def test_value_sub_missing_binding_errors():
    r = sqlize('age>=$(min_age)')
    with pytest.raises(TranslationError, match='missing binding'):
        Resolver.build(r.where)


def test_value_sub_unknown_binding_errors():
    r = sqlize('age>=$(min_age)')
    with pytest.raises(TranslationError, match='unknown binding'):
        Resolver.build(r.where, min_age=30, max_age=65)


def test_value_sub_repeated_dedupes():
    r = sqlize('(age>=$(x) & weight=$(x))')
    # Same name in two positions → single marker shared
    sql, params = Resolver.build(r.where, x=42)
    assert sql == '(age >= :x) AND (weight = :x)'
    assert params == {'x': 42}


def test_value_sub_repeated_dollar_dedupes():
    r = sqlize('(age>=$(x) & weight=$(x))')
    # Dollar-numeric also shares one $N slot for repeated name
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric', x=42)
    assert sql == '(age >= $1) AND (weight = $1)'
    assert args == [42]


def test_mixed_subst_and_literal():
    r = sqlize('age>=$(min_age) & weight>200')
    sql, params = Resolver.build(r.where, min_age=30)
    assert sql == '(age >= :min_age) AND (weight > :_p1)'
    assert params == {'min_age': 30, '_p1': 200}


def test_value_sub_dotted_name_with_bindings():
    # bindings= resolves via nested lookup
    r = sqlize('age>=$(user.min_age)',
               bindings={'user': {'min_age': 30}})
    sql, params = Resolver.build(r.where)
    assert sql == 'age >= :_p1'
    assert params == {'_p1': 30}


def test_value_sub_dotted_name_hashed():
    # Deferred dotted name hashes to _s_<hex> marker; original name
    # recoverable via unbound mapping.
    r = sqlize('age>=$(user.min_age)')
    [(marker, orig)] = r.where.unbound.items()
    assert marker.startswith('_s_')
    assert orig == 'user.min_age'


def test_value_sub_hash_is_deterministic():
    a = next(iter(sqlize('age>=$(user.min_age)').where.unbound))
    b = next(iter(sqlize('weight>=$(user.min_age)').where.unbound))
    assert a == b


def test_value_sub_hash_names_are_distinct():
    a = next(iter(sqlize('age>=$(user.min_age)').where.unbound))
    b = next(iter(sqlize('age>=$(user.max_age)').where.unbound))
    assert a != b


def test_value_sub_special_char_hashed():
    r = sqlize("age>=$('dr pepper')")
    [(marker, orig)] = r.where.unbound.items()
    assert marker.startswith('_s_')
    assert orig == "'dr pepper'"


def test_path_sub_with_binding():
    r = sqlize('data.$(field)', bindings={'field': 'name'})
    assert Resolver.build(r.select) == ("data #>> '{name}'", {})


def test_path_sub_dotted_name_with_bindings():
    r = sqlize('data.$(cfg.field)',
               bindings={'cfg': {'field': 'name'}})
    assert Resolver.build(r.select) == ("data #>> '{name}'", {})


def test_path_sub_without_binding_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$(field)')


def test_positional_sub():
    r = sqlize('data.$0', bindings=['name'])
    assert Resolver.build(r.select) == ("data #>> '{name}'", {})


# ---------------- SQL composition / concatenation ----------------

def test_sql_concat_preserves_metadata():
    r = sqlize('age >= $(min_age)')
    combined = "WHERE " + r.where
    assert isinstance(combined, SQLFragment)
    sql, params = Resolver.build(combined, min_age=30)
    assert sql == 'WHERE age >= :min_age'
    assert params == {'min_age': 30}


def test_sql_concat_two_fragments_merges_metadata():
    r = sqlize('(age>=$(min_age) & weight<200)')
    # select is None here (top-level group); use where + where-like combo
    combined = r.where + ' -- comment'
    sql, params = Resolver.build(combined, min_age=30)
    assert sql.startswith('(age >= :min_age) AND (weight < :_p1) -- comment')
    assert params == {'min_age': 30, '_p1': 200}


def test_sql_concat_both_directions():
    r = sqlize('age >= $(min_age)')
    s1 = "LEFT " + r.where
    s2 = r.where + " RIGHT"
    assert isinstance(s1, SQLFragment)
    assert isinstance(s2, SQLFragment)
    assert s1.text == "LEFT age >= {min_age}"
    assert s2.text == "age >= {min_age} RIGHT"


# ---------------- error cases ----------------

def test_empty_path_errors():
    with pytest.raises(TranslationError):
        sqlize('')


def test_wildcard_first_segment_still_errors():
    with pytest.raises(TranslationError):
        sqlize('*.age=30')


def test_unsupported_flavor_errors():
    with pytest.raises(TranslationError):
        sqlize('age=30', flavor='mysql')


def test_unsupported_paramstyle_errors():
    r = sqlize('age=30')
    with pytest.raises(TranslationError, match='unsupported paramstyle'):
        Resolver.build(r.where, paramstyle='bogus')


# ---------------- identifier quoting ----------------

def test_quoted_column_with_special_chars():
    r = sqlize("'user data'.name")
    assert Resolver.build(r.select) == ('"user data" #>> \'{name}\'', {})


# ---------------- dollar-numeric output shape ----------------

def test_dollar_numeric_simple():
    r = sqlize('age >= 30')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric')
    assert sql == 'age >= $1'
    assert args == [30]


def test_dollar_numeric_multiple_params():
    r = sqlize('(age >= 18 & status = "active")')
    sql, args = Resolver.build(r.where, paramstyle='dollar-numeric')
    assert sql == '(age >= $1) AND (status = $2)'
    assert args == [18, 'active']


def test_dollar_numeric_with_subst():
    r = sqlize('(age >= $(min_age) & weight < 200)')
    sql, args = Resolver.build(r.where,
                               paramstyle='dollar-numeric',
                               min_age=30)
    assert sql == '(age >= $1) AND (weight < $2)'
    assert args == [30, 200]


# ---------------- paramstyle matrix ----------------
#
# Core scenarios exercised against each supported paramstyle. Each
# parametrize invokes both ('named' → `:p1`-style + params dict) and
# ('dollar-numeric' → `$N`-style + args list) to catch asymmetries.


def _placeholder(paramstyle, name, position):
    """
    Return the expected placeholder string for a given paramstyle.
    name: the bind name (for name-keyed styles) — ignored for positional.
    position: 1-based position in the final SQL.
    """
    if paramstyle == 'named':
        return f':{name}'
    if paramstyle == 'pyformat':
        return f'%({name})s'
    if paramstyle == 'numeric':
        return f':{position}'
    if paramstyle == 'dollar-numeric':
        return f'${position}'
    if paramstyle == 'qmark':
        return '?'
    if paramstyle == 'format':
        return '%s'
    raise ValueError(paramstyle)


def _values(paramstyle, items):
    """
    Build the expected params (dict) or args (list) from an ordered
    list of (bind_name, value) tuples. Each test passes items in the
    canonical named-paramstyle shape; this helper converts for
    positional styles.
    """
    if paramstyle in DICT_STYLES:
        return dict(items)
    return [v for _, v in items]


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_null(paramstyle):
    r = sqlize('deleted_at=None')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    assert sql == 'deleted_at IS NULL'
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_not_null(paramstyle):
    r = sqlize('deleted_at!=None')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    assert sql == 'deleted_at IS NOT NULL'
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_boolean_guard(paramstyle):
    r = sqlize('data.user.active=True')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    assert sql == "data #> '{user,active}' = 'true'::jsonb"
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_jsonb_null_guard(paramstyle):
    r = sqlize('data.score=None')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    assert sql == (
        "data #> '{score}' IS NULL "
        "OR jsonb_typeof(data #> '{score}') = 'null'"
    )
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_regex(paramstyle):
    r = sqlize('name=/^alice/')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    assert sql == f'name ~ {ph}'
    assert values == _values(paramstyle, [('_p1', '^alice')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_ref_with_guard(paramstyle):
    r = sqlize('data.$$(data.config.field)="Alice"')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    assert sql == f"(data ->> (data #>> '{{config,field}}')) = {ph}"
    assert values == _values(paramstyle, [('_p1', 'Alice')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_jsonb_numeric_guard(paramstyle):
    r = sqlize('data.user.age>=30')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    assert sql == f"(data #>> '{{user,age}}')::numeric >= {ph}"
    assert values == _values(paramstyle, [('_p1', 30)])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_guard_transform(paramstyle):
    r = sqlize('data.user.age|int>=30')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    assert sql == f"(data #>> '{{user,age}}')::int >= {ph}"
    assert values == _values(paramstyle, [('_p1', 30)])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_group_and(paramstyle):
    r = sqlize('(age >= 18 & status = "active")')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    p1 = _placeholder(paramstyle, '_p1', 1)
    p2 = _placeholder(paramstyle, '_p2', 2)
    assert sql == f'(age >= {p1}) AND (status = {p2})'
    assert values == _values(paramstyle,
                             [('_p1', 18), ('_p2', 'active')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_group_not(paramstyle):
    r = sqlize('data.(!status="banned")')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    assert sql == f"NOT ((data #>> '{{status}}') = {ph})"
    assert values == _values(paramstyle, [('_p1', 'banned')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_subst(paramstyle):
    r = sqlize('age >= $(min_age)')
    sql, values = Resolver.build(r.where,
                                 paramstyle=paramstyle,
                                 min_age=30)
    ph = _placeholder(paramstyle, 'min_age', 1)
    assert sql == f'age >= {ph}'
    assert values == _values(paramstyle, [('min_age', 30)])


@pytest.mark.parametrize('paramstyle', BACKREF_STYLES)
def test_ps_subst_repeated_backref(paramstyle):
    """Styles with back-reference share one slot across occurrences."""
    r = sqlize('(age >= $(x) & weight = $(x))')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle, x=42)
    ph = _placeholder(paramstyle, 'x', 1)
    assert sql == f'(age >= {ph}) AND (weight = {ph})'
    assert values == _values(paramstyle, [('x', 42)])


@pytest.mark.parametrize('paramstyle', ['qmark', 'format'])
def test_ps_subst_repeated_no_backref(paramstyle):
    """qmark/format repeat the value (no back-reference)."""
    r = sqlize('(age >= $(x) & weight = $(x))')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle, x=42)
    ph = _placeholder(paramstyle, 'x', 1)
    assert sql == f'(age >= {ph}) AND (weight = {ph})'
    assert values == [42, 42]


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_pattern_with_literal_inline(paramstyle):
    r = sqlize('data.users[*].age >= 30')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    # Numeric literals inline inside JSONPath — no params hoisted.
    assert sql == "jsonb_path_exists(data, '$.users[*].age ? (@ >= 30)')"
    assert values == _values(paramstyle, [])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_pattern_with_string_hoisted(paramstyle):
    r = sqlize('data.*.status = "on"')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    # String values flow through jsonb_build_object vars
    assert sql == (
        f"jsonb_path_exists(data, '$.*.status ? (@ == $_p1)', "
        f"jsonb_build_object('_p1', {ph}))"
    )
    assert values == _values(paramstyle, [('_p1', 'on')])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_pattern_with_substitution(paramstyle):
    r = sqlize('data.users[*].age >= $(min_age)')
    sql, values = Resolver.build(r.where,
                                 paramstyle=paramstyle,
                                 min_age=30)
    ph = _placeholder(paramstyle, 'min_age', 1)
    assert sql == (
        f"jsonb_path_exists(data, '$.users[*].age ? (@ >= $min_age)', "
        f"jsonb_build_object('min_age', {ph}))"
    )
    assert values == _values(paramstyle, [('min_age', 30)])


@pytest.mark.parametrize('paramstyle', PARAMSTYLES)
def test_ps_mixed_pattern_and_scalar(paramstyle):
    r = sqlize('(data.users[*].age >= 30 & status = "active")')
    sql, values = Resolver.build(r.where, paramstyle=paramstyle)
    ph = _placeholder(paramstyle, '_p1', 1)
    assert sql == (
        f"(jsonb_path_exists(data, '$.users[*].age ? (@ >= 30)')) "
        f"AND (status = {ph})"
    )
    assert values == _values(paramstyle, [('_p1', 'active')])


# ---------------- ParamStyle enum accepted ----------------

@pytest.mark.parametrize('style', list(ParamStyle))
def test_paramstyle_enum_member_accepted(style):
    """Enum members work interchangeably with their string values."""
    r = sqlize('age = 30')
    sql_enum, args_enum = Resolver.build(r.where, paramstyle=style)
    sql_str, args_str = Resolver.build(r.where, paramstyle=str(style))
    assert sql_enum == sql_str
    assert args_enum == args_str
