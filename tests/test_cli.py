import io
import json
import subprocess
import sys
import tempfile
import os

import pytest

from dotted.cli.main import main as _dq_main


def dq(*args, input_text='', expect_fail=False):
    """
    Run dq in-process by calling main(argv) with captured I/O.
    """
    argv = list(args)
    stdin_backup = sys.stdin
    stdout_backup = sys.stdout
    stderr_backup = sys.stderr
    sys.stdin = io.StringIO(input_text)
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        _dq_main(argv)
        returncode = 0
    except SystemExit as e:
        returncode = int(e.code) if isinstance(e.code, int) else 1
        if isinstance(e.code, str):
            sys.stderr.write(e.code + '\n')
    finally:
        stdout_val = sys.stdout.getvalue()
        stderr_val = sys.stderr.getvalue()
        sys.stdin = stdin_backup
        sys.stdout = stdout_backup
        sys.stderr = stderr_backup

    if expect_fail:
        assert returncode != 0, f"Expected failure but got: {stdout_val}"
        return stderr_val
    assert returncode == 0, f"dq failed: {stderr_val}"
    return stdout_val.replace('\r\n', '\n')


def dq_subprocess(*args, **kwargs):
    """
    Run dq via subprocess. Use for tests that need a real process (e.g. --version).
    """
    return subprocess.run(
        [sys.executable, '-m', 'dotted'] + list(args),
        capture_output=True, text=True, **kwargs,
    )


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------

class TestGet:

    def test_single_path_positional(self):
        out = dq('a.b', input_text='{"a": {"b": 1}}')
        assert out.strip() == '1'

    def test_single_path_flag(self):
        out = dq('-p', 'a.b', input_text='{"a": {"b": 1}}')
        assert out.strip() == '1'

    def test_explicit_get(self):
        out = dq('get', '-p', 'a.b', input_text='{"a": {"b": 1}}')
        assert out.strip() == '1'

    def test_multi_path_projection(self):
        out = dq('-p', 'a', '-p', 'b', input_text='{"a": 1, "b": 2, "c": 3}')
        assert json.loads(out) == {"a": 1, "b": 2}

    def test_nested_projection(self):
        out = dq('-p', 'a.x', '-p', 'b', input_text='{"a": {"x": 1, "y": 2}, "b": 3}')
        assert json.loads(out) == {"a": {"x": 1}, "b": 3}

    def test_unpack_projection(self):
        out = dq('--unpack', '-p', 'a.x', '-p', 'b',
                 input_text='{"a": {"x": 1, "y": 2}, "b": 3}')
        assert json.loads(out) == {"a.x": 1, "b": 3}

    def test_get_string_value(self):
        out = dq('name', input_text='{"name": "alice"}')
        assert out.strip() == '"alice"'

    def test_get_nested_object(self):
        out = dq('a', input_text='{"a": {"b": 1, "c": 2}}')
        assert json.loads(out) == {"b": 1, "c": 2}

    def test_get_missing_key(self):
        out = dq('missing', input_text='{"a": 1}')
        assert out.strip() == 'null'


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class TestUpdate:

    def test_single_update(self):
        out = dq('update', '-p', 'a', '42', input_text='{"a": 1, "b": 2}')
        assert json.loads(out) == {"a": 42, "b": 2}

    def test_multi_update(self):
        out = dq('update', '-p', 'a', '42', '-p', 'b', '43',
                 input_text='{"a": 1, "b": 2, "c": 3}')
        assert json.loads(out) == {"a": 42, "b": 43, "c": 3}

    def test_update_string_value(self):
        out = dq('update', '-p', 'name', '"bob"', input_text='{"name": "alice"}')
        assert json.loads(out) == {"name": "bob"}

    def test_update_nested(self):
        out = dq('update', '-p', 'a.b', '99', input_text='{"a": {"b": 1}, "c": 2}')
        assert json.loads(out) == {"a": {"b": 99}, "c": 2}


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------

class TestRemove:

    def test_single_remove(self):
        out = dq('remove', '-p', 'a', input_text='{"a": 1, "b": 2}')
        assert json.loads(out) == {"b": 2}

    def test_multi_remove(self):
        out = dq('remove', '-p', 'a', '-p', 'b',
                 input_text='{"a": 1, "b": 2, "c": 3}')
        assert json.loads(out) == {"c": 3}

    def test_remove_nested(self):
        out = dq('remove', '-p', 'a.b', input_text='{"a": {"b": 1, "c": 2}}')
        assert json.loads(out) == {"a": {"c": 2}}

    def test_remove_conditional_match(self):
        out = dq('remove', '-p', 'a', '1', input_text='{"a": 1, "b": 2}')
        assert json.loads(out) == {"b": 2}

    def test_remove_conditional_no_match(self):
        out = dq('remove', '-p', 'a', '99', input_text='{"a": 1, "b": 2}')
        assert json.loads(out) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------

class TestJsonl:

    def test_jsonl_get(self):
        inp = '{"a":1,"b":2}\n{"a":3,"b":4}\n'
        out = dq('-i', 'jsonl', '-p', 'a', input_text=inp)
        lines = [l for l in out.strip().split('\n') if l]
        assert lines == ['1', '3']

    def test_jsonl_update(self):
        inp = '{"a":1}\n{"a":2}\n'
        out = dq('-i', 'jsonl', 'update', '-p', 'a', '99', input_text=inp)
        lines = [json.loads(l) for l in out.strip().split('\n') if l]
        assert lines == [{"a": 99}, {"a": 99}]

    def test_jsonl_remove(self):
        inp = '{"a":1,"b":2}\n{"a":3,"b":4}\n'
        out = dq('-i', 'jsonl', 'remove', '-p', 'a', input_text=inp)
        lines = [json.loads(l) for l in out.strip().split('\n') if l]
        assert lines == [{"b": 2}, {"b": 4}]


# ---------------------------------------------------------------------------
# JSON array streaming
# ---------------------------------------------------------------------------

class TestJsonArray:

    def test_json_array_streams_elements(self):
        inp = '[{"a":1},{"a":2},{"a":3}]'
        out = dq('-i', 'json', '-o', 'jsonl', '-p', 'a', input_text=inp)
        lines = [l for l in out.strip().split('\n') if l]
        assert lines == ['1', '2', '3']


# ---------------------------------------------------------------------------
# Format conversion
# ---------------------------------------------------------------------------

class TestFormatConversion:

    def test_json_to_jsonl(self):
        inp = '[{"a":1},{"a":2}]'
        out = dq('-i', 'json', '-o', 'jsonl', '-p', 'a', input_text=inp)
        lines = out.strip().split('\n')
        assert lines == ['1', '2']

    def test_jsonl_to_json_single(self):
        out = dq('-i', 'jsonl', '-o', 'json', '-p', 'a', input_text='{"a":1}\n')
        assert json.loads(out) == 1

    def test_jsonl_to_json_multi(self):
        out = dq('-i', 'jsonl', '-o', 'json', '-p', 'a',
                 input_text='{"a":1}\n{"a":2}\n')
        assert json.loads(out) == [1, 2]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

class TestCsv:

    def test_csv_get(self):
        inp = 'name,age\nalice,30\nbob,25\n'
        out = dq('-i', 'csv', '-o', 'jsonl', '-p', 'name', input_text=inp)
        lines = [json.loads(l) for l in out.strip().split('\n') if l]
        assert lines == ['alice', 'bob']

    def test_csv_projection(self):
        inp = 'name,age,city\nalice,30,nyc\n'
        out = dq('-i', 'csv', '-o', 'jsonl', '-p', 'name', '-p', 'age', input_text=inp)
        lines = [json.loads(l) for l in out.strip().split('\n') if l]
        assert lines == [{"name": "alice", "age": "30"}]

    def test_csv_output(self):
        inp = '[{"name":"alice","age":30},{"name":"bob","age":25}]'
        out = dq('-i', 'json', '-o', 'csv', input_text=inp)
        lines = out.strip().split('\n')
        assert lines[0] == 'name,age'
        assert 'alice' in lines[1]
        assert 'bob' in lines[2]


# ---------------------------------------------------------------------------
# Path files
# ---------------------------------------------------------------------------

class TestPathFile:

    def test_get_path_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('a\nb\n')
            f.flush()
            try:
                out = dq('-pf', f.name, input_text='{"a": 1, "b": 2, "c": 3}')
                assert json.loads(out) == {"a": 1, "b": 2}
            finally:
                os.unlink(f.name)

    def test_update_path_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('a 42\nb 43\n')
            f.flush()
            try:
                out = dq('update', '-pf', f.name,
                         input_text='{"a": 1, "b": 2, "c": 3}')
                assert json.loads(out) == {"a": 42, "b": 43, "c": 3}
            finally:
                os.unlink(f.name)

    def test_path_file_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('# comment\na\n\nb\n# another comment\n')
            f.flush()
            try:
                out = dq('-pf', f.name, input_text='{"a": 1, "b": 2, "c": 3}')
                assert json.loads(out) == {"a": 1, "b": 2}
            finally:
                os.unlink(f.name)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:

    def test_no_path_passthrough(self):
        out = dq(input_text='{"a": 1}')
        assert json.loads(out) == {"a": 1}

    def test_update_no_path(self):
        stderr = dq('update', input_text='{"a": 1}', expect_fail=True)
        assert 'path' in stderr.lower() or 'requires' in stderr.lower()

    def test_remove_no_path(self):
        stderr = dq('remove', input_text='{"a": 1}', expect_fail=True)
        assert 'path' in stderr.lower() or 'requires' in stderr.lower()

    def test_update_missing_value(self):
        stderr = dq('update', '-p', 'a', input_text='{"a": 1}', expect_fail=True)
        assert stderr  # should have an error message

    def test_invalid_path(self):
        stderr = dq('-p', '[[[invalid', input_text='{"a": 1}', expect_fail=True)
        assert stderr


# ---------------------------------------------------------------------------
# YAML (optional)
# ---------------------------------------------------------------------------

class TestYaml:

    @pytest.fixture(autouse=True)
    def _require_yaml(self):
        pytest.importorskip('yaml')

    def test_json_to_yaml(self):
        out = dq('-i', 'json', '-o', 'yaml', input_text='{"a": 1, "b": 2}')
        import yaml
        assert yaml.safe_load(out) == {"a": 1, "b": 2}

    def test_yaml_to_json(self):
        out = dq('-i', 'yaml', '-o', 'json', input_text='a: 1\nb: 2\n')
        assert json.loads(out) == {"a": 1, "b": 2}

    def test_yaml_multidoc(self):
        inp = 'a: 1\n---\na: 2\n'
        out = dq('-i', 'yaml', '-o', 'jsonl', '-p', 'a', input_text=inp)
        lines = [l for l in out.strip().split('\n') if l]
        assert lines == ['1', '2']


# ---------------------------------------------------------------------------
# TOML (optional)
# ---------------------------------------------------------------------------

class TestToml:

    @pytest.fixture(autouse=True)
    def _require_toml(self):
        pytest.importorskip('tomllib')

    def test_toml_get(self):
        inp = 'a = 1\nb = 2\n'
        out = dq('-i', 'toml', '-o', 'json', '-p', 'a', input_text=inp)
        assert out.strip() == '1'

    def test_toml_projection(self):
        inp = 'a = 1\nb = 2\nc = 3\n'
        out = dq('-i', 'toml', '-o', 'json', '-p', 'a', '-p', 'b', input_text=inp)
        assert json.loads(out) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Python literals
# ---------------------------------------------------------------------------

def test_py_read_dict():
    out = dq('-i', 'py', '-o', 'json', '-p', 'a',
             input_text="{'a': 1, 'b': 2}")
    assert out.strip() == '1'


def test_py_read_none():
    out = dq('-i', 'py', '-o', 'json', input_text="{'a': None}")
    assert json.loads(out) == {"a": None}


def test_py_read_list_streams():
    out = dq('-i', 'py', '-o', 'jsonl', '-p', 'a',
             input_text="[{'a': 1}, {'a': 2}]")
    lines = [l for l in out.strip().split('\n') if l]
    assert lines == ['1', '2']


def test_py_write_single():
    out = dq('-i', 'json', '-o', 'py', input_text='{"a": null, "b": true}')
    import ast
    assert ast.literal_eval(out.strip()) == {"a": None, "b": True}


def test_py_write_multi():
    out = dq('-i', 'json', '-o', 'py', input_text='[{"a": 1}, {"a": 2}]')
    import ast
    assert ast.literal_eval(out.strip()) == [{"a": 1}, {"a": 2}]


def test_pyl_read():
    inp = "{'a': 1}\n{'a': 2}\n"
    out = dq('-i', 'pyl', '-o', 'jsonl', '-p', 'a', input_text=inp)
    lines = [l for l in out.strip().split('\n') if l]
    assert lines == ['1', '2']


def test_pyl_write():
    inp = '{"a":1}\n{"a":2}\n'
    out = dq('-i', 'jsonl', '-o', 'pyl', input_text=inp)
    import ast
    lines = [ast.literal_eval(l) for l in out.strip().split('\n') if l]
    assert lines == [{"a": 1}, {"a": 2}]


def test_py_roundtrip():
    inp = "{'x': None, 'y': (1, 2), 'z': True}"
    out = dq('-i', 'py', '-o', 'py', input_text=inp)
    import ast
    assert ast.literal_eval(out.strip()) == {'x': None, 'y': (1, 2), 'z': True}


# ---------------------------------------------------------------------------
# Pack / Unpack
# ---------------------------------------------------------------------------

class TestPackUnpack:

    def test_unpack_passthrough(self):
        out = dq('--unpack', input_text='{"a": {"b": 1}, "c": 2}')
        assert json.loads(out) == {"a.b": 1, "c": 2}

    def test_unpack_update(self):
        out = dq('--unpack', 'update', '-p', 'a.b', '99',
                 input_text='{"a": {"b": 1}, "c": 2}')
        assert json.loads(out) == {"a.b": 99, "c": 2}

    def test_unpack_remove(self):
        out = dq('--unpack', 'remove', '-p', 'a',
                 input_text='{"a": 1, "b": {"c": 2}}')
        assert json.loads(out) == {"b.c": 2}

    def test_pack_passthrough(self):
        out = dq('--pack', input_text='{"a.b": 1, "c": 2}')
        assert json.loads(out) == {"a": {"b": 1}, "c": 2}

    def test_pack_then_get(self):
        out = dq('--pack', '-p', 'a.b',
                 input_text='{"a.b": 1, "c": 2}')
        assert out.strip() == '1'

    def test_pack_unpack_roundtrip(self):
        inp = '{"a": {"b": [1, 2]}, "c": 3}'
        unpacked = dq('--unpack', input_text=inp)
        repacked = dq('--pack', input_text=unpacked)
        assert json.loads(repacked) == json.loads(inp)


# ---------------------------------------------------------------------------
# File input
# ---------------------------------------------------------------------------

class TestFileInput:

    def test_file_input(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"a": 1, "b": 2}')
            f.flush()
            try:
                out = dq('-f', f.name, '-p', 'a')
                assert out.strip() == '1'
            finally:
                os.unlink(f.name)

    def test_file_auto_detect_yaml(self):
        pytest.importorskip('yaml')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write('a: 1\nb: 2\n')
            f.flush()
            try:
                out = dq('-f', f.name, '-o', 'json', '-p', 'a')
                assert out.strip() == '1'
            finally:
                os.unlink(f.name)

    def test_file_auto_detect_csv(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('name,age\nalice,30\n')
            f.flush()
            try:
                out = dq('-f', f.name, '-o', 'jsonl', '-p', 'name')
                assert json.loads(out.strip()) == 'alice'
            finally:
                os.unlink(f.name)

    def test_file_explicit_format_overrides(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('{"a": 1}')
            f.flush()
            try:
                out = dq('-f', f.name, '-i', 'json', '-p', 'a')
                assert out.strip() == '1'
            finally:
                os.unlink(f.name)

    def test_file_not_found(self):
        stderr = dq('-f', '/tmp/nonexistent_dq_test.json', '-p', 'a',
                    input_text='', expect_fail=True)
        assert stderr


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

class TestVersion:

    def test_version_flag(self):
        result = dq_subprocess('--version')
        assert result.returncode == 0
        assert 'dq' in result.stdout


# ---------------------------------------------------------------------------
# Subprocess smoke tests
# ---------------------------------------------------------------------------

class TestSubprocess:
    """
    Run a few tests via real subprocess to verify end-to-end behavior.
    """

    def test_get(self):
        result = dq_subprocess('-p', 'a', input='{"a": 1, "b": 2}')
        assert result.returncode == 0
        assert result.stdout.strip() == '1'

    def test_update(self):
        result = dq_subprocess('update', '-p', 'a', '42', input='{"a": 1}')
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"a": 42}

    def test_error(self):
        result = dq_subprocess('update', input='{"a": 1}')
        assert result.returncode != 0
        assert result.stderr
