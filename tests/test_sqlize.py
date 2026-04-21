"""
Tests for dotted.sqlize — dotted path → SQL clause components.
"""
import pytest
from dotted.sqlize import sqlize, TranslationError


# ---------------- scalar columns ----------------

def test_bare_column():
    assert sqlize('status') == {'select': 'status', 'params': {}}


def test_column_eq_string():
    assert sqlize('status="active"') == {
        'select': 'status',
        'where': 'status = :_p1',
        'params': {'_p1': 'active'},
    }


def test_column_eq_numeric():
    assert sqlize('age=30') == {
        'select': 'age',
        'where': 'age = :_p1',
        'params': {'_p1': 30},
    }


def test_column_ordering():
    assert sqlize('age>=30') == {
        'select': 'age',
        'where': 'age >= :_p1',
        'params': {'_p1': 30},
    }
    assert sqlize('age<18') == {
        'select': 'age',
        'where': 'age < :_p1',
        'params': {'_p1': 18},
    }


def test_column_ne():
    assert sqlize('status!="banned"') == {
        'select': 'status',
        'where': 'status != :_p1',
        'params': {'_p1': 'banned'},
    }


def test_column_null():
    assert sqlize('deleted_at=None') == {
        'select': 'deleted_at',
        'where': 'deleted_at IS NULL',
        'params': {},
    }


def test_column_not_null():
    assert sqlize('deleted_at!=None') == {
        'select': 'deleted_at',
        'where': 'deleted_at IS NOT NULL',
        'params': {},
    }


def test_column_regex():
    assert sqlize('name=/^alice/') == {
        'select': 'name',
        'where': 'name ~ :_p1',
        'params': {'_p1': '^alice'},
    }


def test_column_regex_ne():
    assert sqlize('name!=/^alice/') == {
        'select': 'name',
        'where': 'name !~ :_p1',
        'params': {'_p1': '^alice'},
    }


# ---------------- jsonb column traversal ----------------

def test_jsonb_select_only():
    assert sqlize('data.user.name') == {
        'select': "data #>> '{user,name}'",
        'params': {},
    }


def test_jsonb_single_segment():
    assert sqlize('data.status') == {
        'select': "data #>> '{status}'",
        'params': {},
    }


def test_jsonb_string_guard():
    assert sqlize('data.user.role="admin"') == {
        'select': "data #>> '{user,role}'",
        'where': "(data #>> '{user,role}') = :_p1",
        'params': {'_p1': 'admin'},
    }


def test_jsonb_numeric_guard():
    assert sqlize('data.user.age>=30') == {
        'select': "data #>> '{user,age}'",
        'where': "(data #>> '{user,age}')::numeric >= :_p1",
        'params': {'_p1': 30},
    }


def test_jsonb_boolean_guard():
    assert sqlize('data.user.active=True') == {
        'select': "data #>> '{user,active}'",
        'where': "data #> '{user,active}' = 'true'::jsonb",
        'params': {},
    }


def test_jsonb_boolean_ne():
    assert sqlize('data.user.active!=True') == {
        'select': "data #>> '{user,active}'",
        'where': "data #> '{user,active}' != 'true'::jsonb",
        'params': {},
    }


def test_jsonb_null_guard():
    result = sqlize('data.score=None')
    assert result['select'] == "data #>> '{score}'"
    assert result['where'] == (
        "data #> '{score}' IS NULL OR jsonb_typeof(data #> '{score}') = 'null'"
    )
    assert result['params'] == {}


def test_jsonb_not_null_guard():
    result = sqlize('data.score!=None')
    assert result['where'] == (
        "data #> '{score}' IS NOT NULL AND jsonb_typeof(data #> '{score}') != 'null'"
    )


def test_jsonb_regex():
    assert sqlize('data.user.name=/alice/') == {
        'select': "data #>> '{user,name}'",
        'where': "(data #>> '{user,name}') ~ :_p1",
        'params': {'_p1': 'alice'},
    }


# ---------------- guard transforms (casts) ----------------

def test_guard_transform_int():
    assert sqlize('data.user.age|int>=30') == {
        'select': "data #>> '{user,age}'",
        'where': "(data #>> '{user,age}')::int >= :_p1",
        'params': {'_p1': 30},
    }


def test_guard_transform_float():
    assert sqlize('data.user.score|float>=#"0.5"') == {
        'select': "data #>> '{user,score}'",
        'where': "(data #>> '{user,score}')::float >= :_p1",
        'params': {'_p1': 0.5},
    }


def test_guard_transform_str():
    assert sqlize('data.user.id|str="42"') == {
        'select': "data #>> '{user,id}'",
        'where': "(data #>> '{user,id}')::text = :_p1",
        'params': {'_p1': '42'},
    }


def test_guard_transform_unknown():
    with pytest.raises(TranslationError):
        sqlize('data.user.name|uppercase="ALICE"')


# ---------------- boolean groups ----------------

def test_group_and():
    assert sqlize('data.(user.age>=30 & status="active")') == {
        'select': 'data',
        'where': (
            "((data #>> '{user,age}')::numeric >= :_p1) "
            "AND ((data #>> '{status}') = :_p2)"
        ),
        'params': {'_p1': 30, '_p2': 'active'},
    }


def test_group_or():
    assert sqlize('data.(user.age=18, user.age=21)') == {
        'select': 'data',
        'where': (
            "((data #>> '{user,age}')::numeric = :_p1) "
            "OR ((data #>> '{user,age}')::numeric = :_p2)"
        ),
        'params': {'_p1': 18, '_p2': 21},
    }


def test_group_not():
    assert sqlize('data.(!status="banned")') == {
        'select': 'data',
        'where': "NOT ((data #>> '{status}') = :_p1)",
        'params': {'_p1': 'banned'},
    }


def test_top_level_and_across_columns():
    assert sqlize('(age>=18 & status="active")') == {
        'where': '(age >= :_p1) AND (status = :_p2)',
        'params': {'_p1': 18, '_p2': 'active'},
    }


def test_top_level_or_across_columns():
    assert sqlize('(age=18, age=21, age=25)') == {
        'where': '(age = :_p1) OR (age = :_p2) OR (age = :_p3)',
        'params': {'_p1': 18, '_p2': 21, '_p3': 25},
    }


def test_top_level_mixed_columns_and_jsonb():
    assert sqlize('(data.user.age>=30 & status="active")') == {
        'where': "((data #>> '{user,age}')::numeric >= :_p1) AND (status = :_p2)",
        'params': {'_p1': 30, '_p2': 'active'},
    }


def test_group_branch_without_guard_errors():
    with pytest.raises(TranslationError):
        sqlize('data.(x=1, y)')


# ---------------- absolute root references ----------------

def test_ref_select():
    assert sqlize('data.$$(data.config.field)') == {
        'select': "data ->> (data #>> '{config,field}')",
        'params': {},
    }


def test_ref_with_guard():
    assert sqlize('data.$$(data.config.field)="Alice"') == {
        'select': "data ->> (data #>> '{config,field}')",
        'where': "(data ->> (data #>> '{config,field}')) = :_p1",
        'params': {'_p1': 'Alice'},
    }


def test_ref_mid_path():
    assert sqlize('data.users.$$(data.config.key).name') == {
        'select': "data -> 'users' -> (data #>> '{config,key}') ->> 'name'",
        'params': {},
    }


def test_ref_numeric_guard():
    assert sqlize('data.$$(data.idx)=30') == {
        'select': "data ->> (data #>> '{idx}')",
        'where': "(data ->> (data #>> '{idx}'))::numeric = :_p1",
        'params': {'_p1': 30},
    }


def test_ref_relative_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$$(^field)')


def test_ref_parent_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$$(^^field)')


# ---------------- substitutions ----------------

def test_value_sub_with_binding():
    # Bound substitution resolves to a concrete value; hoisted under a
    # generated name like any other literal
    result = sqlize('age>=$(min_age)', bindings={'min_age': 30})
    assert result == {
        'select': 'age',
        'where': 'age >= :_p1',
        'params': {'_p1': 30},
    }


def test_value_sub_without_binding():
    # Unbound substitution: name is already a valid identifier, so it's
    # used directly as the bind name. unbound maps bind → orig.
    result = sqlize('age>=$(min_age)')
    assert result == {
        'select': 'age',
        'where': 'age >= :min_age',
        'params': {},
        'unbound': {'min_age': 'min_age'},
    }


def test_value_sub_repeated_dedupes():
    # Same name used twice → single placeholder, one unbound entry.
    result = sqlize('(age>=$(x) & weight=$(x))')
    assert result == {
        'where': '(age >= :x) AND (weight = :x)',
        'params': {},
        'unbound': {'x': 'x'},
    }


def test_mixed_subst_and_literal():
    result = sqlize('age>=$(min_age) & weight>200')
    assert result == {
        'where': '(age >= :min_age) AND (weight > :_p1)',
        'params': {'_p1': 200},
        'unbound': {'min_age': 'min_age'},
    }


def test_value_sub_dotted_name_with_bindings():
    # Dotted subst name resolves via nested lookup in bindings
    result = sqlize('age>=$(user.min_age)',
                    bindings={'user': {'min_age': 30}})
    assert result == {
        'select': 'age',
        'where': 'age >= :_p1',
        'params': {'_p1': 30},
    }


def test_value_sub_dotted_name_hashed():
    # Dotted subst name hashes to an _s_<hex> bind name; unbound maps
    # bind → orig so callers can recover the source name.
    result = sqlize('age>=$(user.min_age)')
    assert result['select'] == 'age'
    assert set(result['unbound'].values()) == {'user.min_age'}
    [(bind, orig)] = result['unbound'].items()
    assert bind.startswith('_s_')
    assert orig == 'user.min_age'
    assert result['where'] == f'age >= :{bind}'
    assert result['params'] == {}


def test_value_sub_hash_is_deterministic():
    # Same subst name → same hash across calls (still keyed by bind)
    a = list(sqlize('age>=$(user.min_age)')['unbound'])[0]
    b = list(sqlize('weight>=$(user.min_age)')['unbound'])[0]
    assert a == b


def test_value_sub_hash_names_are_distinct():
    # Different subst names produce different hashes
    a = list(sqlize('age>=$(user.min_age)')['unbound'])[0]
    b = list(sqlize('age>=$(user.max_age)')['unbound'])[0]
    assert a != b


def test_value_sub_special_char_hashed():
    # Names with spaces / quotes / punctuation hash without erroring
    result = sqlize("age>=$('dr pepper')")
    assert set(result['unbound'].values()) == {"'dr pepper'"}
    [(bind, orig)] = result['unbound'].items()
    assert bind.startswith('_s_')
    assert orig == "'dr pepper'"


def test_path_sub_with_binding():
    result = sqlize('data.$(field)', bindings={'field': 'name'})
    assert result == {'select': "data #>> '{name}'", 'params': {}}


def test_path_sub_dotted_name_with_bindings():
    # Dotted subst name in path position resolves via nested lookup
    result = sqlize('data.$(cfg.field)',
                    bindings={'cfg': {'field': 'name'}})
    assert result == {'select': "data #>> '{name}'", 'params': {}}


def test_path_sub_without_binding_errors():
    with pytest.raises(TranslationError):
        sqlize('data.$(field)')


def test_positional_sub():
    result = sqlize('data.$0', bindings=['name'])
    assert result == {'select': "data #>> '{name}'", 'params': {}}


# ---------------- error cases ----------------

def test_empty_path_errors():
    with pytest.raises(TranslationError):
        sqlize('')


def test_wildcard_first_segment_errors():
    with pytest.raises(TranslationError):
        sqlize('*.age=30')


def test_wildcard_mid_segment_errors():
    with pytest.raises(TranslationError):
        sqlize('data.*.age=30')


def test_slot_wildcard_errors():
    with pytest.raises(TranslationError):
        sqlize('data.users[*].age=30')


def test_unsupported_flavor_errors():
    with pytest.raises(TranslationError):
        sqlize('age=30', flavor='mysql')


def test_unsupported_format_errors():
    with pytest.raises(TranslationError):
        sqlize('age=30', format='qmark')


# ---------------- identifier quoting ----------------

def test_quoted_column_with_special_chars():
    assert sqlize("'user data'.name") == {
        'select': '"user data" #>> \'{name}\'',
        'params': {},
    }
