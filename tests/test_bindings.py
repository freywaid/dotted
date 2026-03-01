"""
Tests for template bindings and resolution in parse() and traversal APIs.
"""
import pytest
import dotted


def test_parse_template_partial_true_default():
    """
    parse() with default partial=True allows templates through.
    """
    ops = dotted.parse('a.$0.b')
    assert dotted.is_template(ops)


def test_parse_template_partial_false_raises():
    """
    parse() with partial=False raises on unresolved templates.
    """
    with pytest.raises(TypeError, match='unresolved template'):
        dotted.parse('a.$0.b', partial=False)


def test_parse_bindings_resolves():
    """
    parse() with bindings resolves the template.
    """
    ops = dotted.parse('a.$0.b', bindings=['x'])
    assert not dotted.is_template(ops)


def test_parse_bindings_partial_false_resolved():
    """
    parse() with bindings + partial=False succeeds when fully resolved.
    """
    ops = dotted.parse('a.$0.b', bindings=['x'], partial=False)
    assert not dotted.is_template(ops)


def test_parse_bindings_partial_false_unresolved_raises():
    """
    parse() with partial bindings + partial=False raises on unresolved.
    """
    with pytest.raises(IndexError):
        dotted.parse('$0.$1', bindings=['x'], partial=False)


def test_parse_bindings_partial_true_unresolved_ok():
    """
    parse() with partial bindings + partial=True allows unresolved through.
    """
    ops = dotted.parse('$0.$1', bindings=['x'], partial=True)
    assert dotted.is_template(ops)


def test_parse_non_template_partial_false_ok():
    """
    parse() with partial=False on a non-template path is fine.
    """
    ops = dotted.parse('a.b.c', partial=False)
    assert not dotted.is_template(ops)


# --- get ---

def test_get_with_bindings():
    """
    get() resolves template path via bindings.
    """
    data = {'users': {'alice': 42}}
    assert dotted.get(data, 'users.$(name)', bindings={'name': 'alice'}) == 42


def test_get_template_without_bindings_raises():
    """
    get() raises on unresolved template path.
    """
    with pytest.raises(TypeError, match='unresolved template'):
        dotted.get({}, 'a.$(name)')


def test_get_non_template_no_bindings_ok():
    """
    get() without bindings on a non-template path works normally.
    """
    assert dotted.get({'a': 1}, 'a') == 1


def test_get_positional_bindings():
    """
    get() with positional substitution.
    """
    data = {'hello': 7}
    assert dotted.get(data, '$0', bindings=['hello']) == 7


# --- update ---

def test_update_with_bindings():
    """
    update() resolves template path via bindings.
    """
    data = {'users': {'alice': 42}}
    result = dotted.update(data, 'users.$(name)', 99, bindings={'name': 'alice'})
    assert result == {'users': {'alice': 99}}


def test_update_template_without_bindings_raises():
    """
    update() raises on unresolved template path.
    """
    with pytest.raises(TypeError, match='unresolved template'):
        dotted.update({}, 'a.$(name)', 1)


# --- remove ---

def test_remove_with_bindings():
    """
    remove() resolves template path via bindings.
    """
    data = {'a': 1, 'b': 2}
    result = dotted.remove(data, '$(key)', bindings={'key': 'a'})
    assert result == {'b': 2}


def test_remove_template_without_bindings_raises():
    """
    remove() raises on unresolved template path.
    """
    with pytest.raises(TypeError, match='unresolved template'):
        dotted.remove({}, '$(name)')


# --- has ---

def test_has_with_bindings():
    """
    has() resolves template path via bindings.
    """
    data = {'a': 1}
    assert dotted.has(data, '$(key)', bindings={'key': 'a'})
    assert not dotted.has(data, '$(key)', bindings={'key': 'z'})


def test_has_template_without_bindings_raises():
    """
    has() raises on unresolved template path.
    """
    with pytest.raises(TypeError, match='unresolved template'):
        dotted.has({}, '$(name)')


# --- replace ---

def test_replace_default_raises_on_unresolved():
    """
    replace() with partial=False raises if bindings don't cover all substitutions.
    """
    with pytest.raises((TypeError, IndexError)):
        dotted.replace('$0.$1', ['x'])


def test_replace_partial_allows_unresolved():
    """
    replace() with partial=True leaves unresolved substitutions as-is.
    """
    result = dotted.replace('$0.$1', ['x'], partial=True)
    assert 'x' in result


def test_replace_full_resolution():
    """
    replace() fully resolves when all bindings provided.
    """
    assert dotted.replace('$0.$1', ['a', 'b']) == 'a.b'


# --- walk ---

def test_walk_with_bindings():
    """
    walk() resolves template path via bindings.
    """
    data = {'a': {'b': 1, 'c': 2}}
    result = list(dotted.walk(data, '$(key).*', bindings={'key': 'a'}))
    assert result == [('a.b', 1), ('a.c', 2)]


def test_walk_template_without_bindings_raises():
    """
    walk() raises on unresolved template path.
    """
    with pytest.raises(TypeError, match='unresolved template'):
        list(dotted.walk({}, '$(name).*'))


# --- setdefault ---

def test_setdefault_with_bindings():
    """
    setdefault() resolves template path via bindings.
    """
    data = {'a': 1}
    result = dotted.setdefault(data, '$(key)', 99, bindings={'key': 'a'})
    assert result == 1


# --- concat + bindings ---

def test_get_concat_with_bindings():
    """
    get() resolves concat template via bindings.
    """
    data = {'user_alice': 42}
    assert dotted.get(data, 'user_+$(name)', bindings={'name': 'alice'}) == 42


def test_update_concat_with_bindings():
    """
    update() resolves concat template via bindings.
    """
    data = {'prefix_x': 1}
    result = dotted.update(data, 'prefix_+$(key)', 99, bindings={'key': 'x'})
    assert result == {'prefix_x': 99}
