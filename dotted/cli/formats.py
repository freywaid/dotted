"""
Format readers and writers for the dq CLI.
"""
import ast
import csv
import io
import json
import sys

from ._compat import require_yaml, require_toml_reader, require_toml_writer


# ---------------------------------------------------------------------------
# Readers — each yields one document at a time from a stream
# ---------------------------------------------------------------------------

def read_json(stream):
    """
    Read a single JSON document. If it's an array, yield elements individually.
    """
    data = json.load(stream)
    if isinstance(data, list):
        yield from data
    else:
        yield data


def read_jsonl(stream):
    """
    Read one JSON document per line.
    """
    for line in stream:
        line = line.strip()
        if line:
            yield json.loads(line)


def read_yaml(stream):
    """
    Read YAML documents (multi-doc with --- supported).
    """
    yaml = require_yaml()
    for doc in yaml.safe_load_all(stream):
        if doc is not None:
            yield doc


def read_toml(stream):
    """
    Read a single TOML document.
    """
    tomllib = require_toml_reader()
    yield tomllib.loads(stream.read())


def read_python(stream):
    """
    Read a single Python literal. If it's a list, yield elements individually.
    """
    data = ast.literal_eval(stream.read())
    if isinstance(data, list):
        yield from data
    else:
        yield data


def read_pythonl(stream):
    """
    Read one Python literal per line.
    """
    for line in stream:
        line = line.strip()
        if line:
            yield ast.literal_eval(line)


def read_csv_format(stream):
    """
    Read CSV rows as dicts (headers from first row).
    """
    reader = csv.DictReader(stream)
    yield from reader


READERS = {
    'json': read_json,
    'jsonl': read_jsonl,
    'py': read_python,
    'pyl': read_pythonl,
    'yaml': read_yaml,
    'toml': read_toml,
    'csv': read_csv_format,
}


_READER_VALIDATORS = {
    'yaml': require_yaml,
    'toml': require_toml_reader,
}

_WRITER_VALIDATORS = {
    'yaml': require_yaml,
    'toml': require_toml_writer,
}


def validate_reader(fmt):
    """
    Eagerly check that optional dependencies for reading a format are available.
    Exits with a clear message before blocking on stdin.
    """
    check = _READER_VALIDATORS.get(fmt)
    if check is not None:
        check()


def validate_writer(fmt):
    """
    Eagerly check that optional dependencies for writing a format are available.
    Exits with a clear message before blocking on stdin.
    """
    check = _WRITER_VALIDATORS.get(fmt)
    if check is not None:
        check()


# ---------------------------------------------------------------------------
# Writers — each emits one result at a time to a stream
# ---------------------------------------------------------------------------

class JsonWriter:
    """
    Write JSON output. Multiple values are wrapped in an array.
    """

    def __init__(self, stream):
        self._stream = stream
        self._count = 0
        self._pending = None

    def _dump(self, value):
        """
        Serialize a single value to the stream.
        """
        json.dump(value, self._stream, ensure_ascii=False)

    def write(self, value):
        """
        Write a single value.
        """
        if self._count == 0:
            self._pending = value
            self._count = 1
            return
        if self._count == 1:
            self._stream.write('[\n')
            self._dump(self._pending)
            self._pending = None
        self._stream.write(',\n')
        self._dump(value)
        self._count += 1

    def finish(self):
        """
        Close the output.
        """
        if self._count == 0:
            return
        if self._count == 1:
            self._dump(self._pending)
            self._stream.write('\n')
        else:
            self._stream.write('\n]\n')
        self._stream.flush()


class JsonlWriter:
    """
    Write one JSON document per line.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, value):
        """
        Write a single value as a JSON line.
        """
        json.dump(value, self._stream, ensure_ascii=False)
        self._stream.write('\n')
        self._stream.flush()

    def finish(self):
        """
        No-op for JSONL.
        """
        pass


class YamlWriter:
    """
    Write YAML multi-doc with --- separators.
    """

    def __init__(self, stream):
        self._stream = stream
        self._yaml = require_yaml()
        self._count = 0

    def write(self, value):
        """
        Write a single YAML document.
        """
        if self._count > 0:
            self._stream.write('---\n')
        self._yaml.dump(
            value, self._stream,
            default_flow_style=False,
            allow_unicode=True,
        )
        self._stream.flush()
        self._count += 1

    def finish(self):
        """
        No-op for YAML.
        """
        pass


class TomlWriter:
    """
    Write TOML output. Scalars are wrapped as {value: x}.
    """

    def __init__(self, stream):
        self._stream = stream
        self._tomli_w = None

    def write(self, value):
        """
        Write a single TOML document.
        """
        if self._tomli_w is None:
            self._tomli_w = require_toml_writer()
        if not isinstance(value, dict):
            value = {'value': value}
        self._stream.write(self._tomli_w.dumps(value))
        self._stream.flush()

    def finish(self):
        """
        No-op for TOML.
        """
        pass


class PythonWriter:
    """
    Write Python literal output. Multiple values are wrapped in a list.
    """

    def __init__(self, stream):
        self._stream = stream
        self._count = 0
        self._pending = None

    def write(self, value):
        """
        Write a single value.
        """
        if self._count == 0:
            self._pending = value
            self._count = 1
            return
        if self._count == 1:
            self._stream.write('[\n')
            self._stream.write(repr(self._pending))
            self._pending = None
        self._stream.write(',\n')
        self._stream.write(repr(value))
        self._count += 1

    def finish(self):
        """
        Close the output.
        """
        if self._count == 0:
            return
        if self._count == 1:
            self._stream.write(repr(self._pending))
            self._stream.write('\n')
        else:
            self._stream.write('\n]\n')
        self._stream.flush()


class PythonlWriter:
    """
    Write one Python literal per line.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, value):
        """
        Write a single value as a Python repr line.
        """
        self._stream.write(repr(value))
        self._stream.write('\n')
        self._stream.flush()

    def finish(self):
        """
        No-op for pyl.
        """
        pass


class CsvWriter:
    """
    Write CSV output. Headers determined from first document.
    """

    def __init__(self, stream):
        self._stream = stream
        self._writer = None
        self._fieldnames = None

    def _ensure_writer(self, value):
        """
        Initialize the CSV writer from the first value's keys.
        """
        if isinstance(value, dict):
            self._fieldnames = list(value.keys())
        else:
            self._fieldnames = ['value']
        self._writer = csv.DictWriter(
            self._stream, fieldnames=self._fieldnames,
        )
        self._writer.writeheader()

    def _to_row(self, value):
        """
        Convert a value to a CSV row dict, JSON-encoding nested values.
        """
        if not isinstance(value, dict):
            return {'value': value}
        row = {}
        for k, v in value.items():
            if isinstance(v, (dict, list, tuple)):
                row[k] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                row[k] = ''
            else:
                row[k] = v
        return row

    def write(self, value):
        """
        Write a single row.
        """
        if self._writer is None:
            self._ensure_writer(value)
        self._writer.writerow(self._to_row(value))
        self._stream.flush()

    def finish(self):
        """
        No-op for CSV.
        """
        pass


WRITERS = {
    'json': JsonWriter,
    'jsonl': JsonlWriter,
    'py': PythonWriter,
    'pyl': PythonlWriter,
    'yaml': YamlWriter,
    'toml': TomlWriter,
    'csv': CsvWriter,
}
