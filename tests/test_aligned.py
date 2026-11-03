import pytest
import dotted
from dotted.translators import sqlize


# -- Parse / assemble round-trip --

@pytest.mark.parametrize('expr', [
    '[*+]', '[*+].hello', '*+', '*+.a', '/x/+', 'a./x/+.b', '**+', '*a+', '**:-1+', 'a.*+[*+]',
])
def test_round_trip(expr):
    """
    Parse then assemble returns the original expression.
    """
    assert dotted.parse(expr).assemble() == expr


@pytest.mark.parametrize('expr', ['[*+]', '*+', '/x/+', '**+', '*a+'])
def test_is_pattern(expr):
    """
    An aligned pattern is a pattern.
    """
    assert dotted.is_pattern(expr)


# -- get --

def test_get_fills_missing_slot():
    """
    A slot the rest of the path finds nothing under yields the default.
    """
    assert dotted.get([{'hello': 'there'}, {}], '[*+].hello') == ('there', None)


def test_get_plain_pattern_unchanged():
    """
    Without + a missing slot yields nothing, as before.
    """
    assert dotted.get([{'hello': 'there'}, {}], '[*].hello') == ('there',)


def test_get_fill_uses_default():
    """
    The fill is get's default.
    """
    assert dotted.get([{}, {'hello': 1}], '[*+].hello', default='D') == ('D', 1)


def test_get_no_matches_is_empty():
    """
    A pattern with no matches yields nothing; there is no slot to fill.
    """
    assert dotted.get([], '[*+]') == ()
    assert dotted.get([], '[*+]', pattern_default='P') == 'P'


def test_get_at_least_one_per_slot():
    """
    A slot yields all the rest of the path finds, or the default once.
    """
    assert dotted.get([{'tags': [1, 2]}, {}], '[*+].tags[*]') == (1, 2, None)


def test_get_empty_inner_pattern_fills():
    """
    An empty list under a slot is nothing found, so the slot fills.
    """
    assert dotted.get([{'tags': []}, {}], '[*+].tags[*]') == (None, None)


def test_get_non_container_slot_fills():
    """
    Every slot yields, whatever is in it.
    """
    assert dotted.get([{'hello': 1}, 5], '[*+].hello') == (1, None)


def test_get_guard_after_fills():
    """
    A guard after the + decides what a match yields; a match that yields
    nothing, missing or rejected, fills.
    """
    assert dotted.get([{'hello': 1}, {'hello': 2}, {}], '[*+].hello=1') == (1, None, None)


def test_get_filter_on_pattern_selects_matches():
    """
    A filter on the + pattern itself decides which slots are matches.
    """
    d = {'x': [{'n': 1, 'k': 'a'}, {'k': 'a'}, {'n': 3, 'k': 'b'}]}
    assert dotted.get(d, 'x[*+&k="a"].n') == (1, None)


def test_get_transform_not_applied_to_fill():
    """
    The fill is the default as is, not passed through the transforms.
    """
    assert dotted.get([{'a': {'b': 1}}, {'a': {}}, {}], '[*+].a.b|str') == ('1', None, None)


def test_get_dict_wildcard():
    """
    *+ aligns to a dict's keys.
    """
    assert dotted.get({'a': {'hello': 1}, 'b': {}}, '*+.hello') == (1, None)


def test_get_regex():
    """
    /re/+ aligns to the keys the regex matches.
    """
    assert dotted.get({'ab': {'hello': 1}, 'ac': {}, 'x': {}}, '/a./+.hello') == (1, None)


def test_get_recursive():
    """
    **+ aligns to every match of the recursion.
    """
    d = {'a': {'hello': 1, 'x': 2}}
    assert dotted.get(d, '**+.hello') == (1, None, None)
    assert dotted.get(d, '**.hello') == (1,)


def test_get_nested_aligned():
    """
    An inner + aligns within each outer slot.
    """
    assert dotted.get([{'tags': [1]}, {'tags': []}, {}], '[*+].tags[*+]') == (1, None, None)


def test_get_in_group_with_cut():
    """
    A + pattern in a cut branch: the fill counts as the branch matching.
    """
    assert dotted.get([{'h': 1}, {}], '([*+].h#, x)') == (1, None)


def test_getter():
    """
    getter fills with its default.
    """
    assert dotted.getter('[*+].h', default=0)([{}, {'h': 2}]) == (0, 2)


# -- pluck / expand --

def test_pluck_fill_path_is_rest_as_written():
    """
    A fill's path is the match followed by the rest of the path as written.
    """
    assert dotted.pluck([{'hello': 'there'}, {}], '[*+].hello') == (('[0].hello', 'there'), ('[1].hello', None))
    assert dotted.pluck([{'tags': [1]}, {}], '[*+].tags[*]') == (('[0].tags[0]', 1), ('[1].tags[*]', None))


def test_pluck_fill_keeps_guard():
    """
    A guard in the rest of the path stays in the fill's path.
    """
    assert dotted.pluck([{'hello': 1}, {'hello': 2}], '[*+].hello=1') == (('[0].hello', 1), ('[1].hello=1', None))


def test_pluck_fill_uses_default():
    """
    pluck fills with its default.
    """
    assert dotted.pluck([{}], '[*+].hello', default='D') == (('[0].hello', 'D'),)


def test_expand_includes_fill_paths():
    """
    expand reports a fill's path too.
    """
    assert dotted.expand([{'hello': 1}, {}], '[*+].hello') == ('[0].hello', '[1].hello')


# -- writes treat + as the plain pattern --

def test_update_as_plain_pattern():
    """
    update writes every slot, as with [*].
    """
    assert dotted.update([{'hello': 1}, {}], '[*+].hello', 5) == [{'hello': 5}, {'hello': 5}]


def test_remove_as_plain_pattern():
    """
    remove removes what is there, as with [*].
    """
    assert dotted.remove([{'hello': 1}, {}], '[*+].hello') == [{}, {}]


# -- match --

def test_match_concrete_path():
    """
    An aligned pattern matches a concrete path as the plain pattern does.
    """
    assert dotted.match('[*+].hello', '[1].hello') == '[1].hello'
    assert dotted.match('/a./+', 'ab') == 'ab'


# -- translators --

def test_sqlize_left_joins_rest():
    """
    The rest of the path runs under each match in a LEFT JOIN LATERAL,
    so a match it finds nothing under keeps a NULL row; the rows are
    those with a match.
    """
    r = sqlize('c.users[*+].tags[*]', driver='asyncpg')
    assert str(r.select) == '_pat2.value'
    assert str(r.where) == "jsonb_path_exists(c, '$.users[*]')"
    assert str(r.lateral) == (
        "LATERAL jsonb_path_query(c, '$.users[*]') AS _pat1(value) "
        "LEFT JOIN LATERAL jsonb_path_query(_pat1.value, '$.tags[*]') AS _pat2(value) ON TRUE"
    )


def test_sqlize_guard_after_runs_in_rest():
    """
    A guard after the + is in the joined jsonpath, so a rejected match
    yields NULL.
    """
    r = sqlize('c[*+].n = 1', driver='asyncpg')
    assert str(r.where) == "jsonb_path_exists(c, '$[*]')"
    assert str(r.lateral) == (
        "LATERAL jsonb_path_query(c, '$[*]') AS _pat1(value) "
        "LEFT JOIN LATERAL jsonb_path_query(_pat1.value, '$.n ? (@ == 1)') AS _pat2(value) ON TRUE"
    )


def test_sqlize_each_aligned_op_joins():
    """
    Each + splits the path once more.
    """
    r = sqlize('c[*+].a[*+].b', driver='asyncpg')
    assert str(r.select) == '_pat3.value'
    assert str(r.lateral) == (
        "LATERAL jsonb_path_query(c, '$[*]') AS _pat1(value) "
        "LEFT JOIN LATERAL jsonb_path_query(_pat1.value, '$.a[*]') AS _pat2(value) ON TRUE "
        "LEFT JOIN LATERAL jsonb_path_query(_pat2.value, '$.b') AS _pat3(value) ON TRUE"
    )


def test_sqlize_trailing_aligned_is_plain():
    """
    With nothing after the +, every match yields itself.
    """
    r = sqlize('c[*+]', driver='asyncpg')
    assert str(r.lateral) == "LATERAL jsonb_path_query(c, '$[*]') AS _pat1(value)"
