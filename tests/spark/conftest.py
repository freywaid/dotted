"""
Fixtures and collection rules for the Spark tests.

Every test collected under `tests/spark/` is auto-marked as
`integration`: they start a local SparkSession, so they need pyspark
and a JVM. Root `conftest.py` handles the default skip; they run with
`--all` or when `tests/spark` is targeted (`make test.spark`).

A session-scoped local SparkSession serves every test, with one sample
DataFrame of native struct, array and map columns.
"""
import datetime
import os
import sys

import pytest


_HERE = os.path.dirname(os.path.abspath(__file__))

# DDL for the sample DataFrame.
SCHEMA = ', '.join([
    'id INT',
    'status STRING',
    'age INT',
    'deleted_at TIMESTAMP',
    'data STRUCT<'
    'user: STRUCT<age: INT, role: STRING, active: BOOLEAN>, '
    'users: ARRAY<STRUCT<age: INT, active: BOOLEAN>>>',
    'tags MAP<STRING, STRING>',
    'pick STRING',
])


def _rows():
    """
    Sample rows: the sqlize integration seed, plus a map column `tags`
    and a string column `pick` naming one of its keys.
    """
    return [
        (1, 'active', 25, None,
         {'user': {'age': 25, 'role': 'admin', 'active': True},
          'users': [{'age': 25, 'active': True},
                    {'age': 32, 'active': False}]},
         {'color': 'red', 'size': 'L'}, 'color'),
        (2, 'active', 35, None,
         {'user': {'age': 35, 'role': 'user', 'active': True},
          'users': [{'age': 35, 'active': True}]},
         {'color': 'blue', 'size': 'red'}, 'color'),
        (3, 'banned', 40, None,
         {'user': {'age': 40, 'role': 'user', 'active': False},
          'users': [{'age': 40, 'active': False}]},
         {'size': 'red'}, 'size'),
        (4, 'active', 17, None,
         {'user': {'age': 17, 'role': 'user', 'active': True},
          'users': [{'age': 17, 'active': True}]},
         {}, 'color'),
        (5, 'active', 50, None,
         {'user': {'age': 50, 'role': 'admin', 'active': True},
          'users': [{'age': 50, 'active': True},
                    {'age': 18, 'active': True}]},
         {'color': 'green'}, 'size'),
        (6, 'active', 30, datetime.datetime(2024, 1, 1),
         {'user': {'age': 30, 'role': 'user', 'active': False},
          'users': []},
         {'color': 'red'}, 'color'),
        (7, 'pending', None, None,
         {'user': {'age': None, 'role': 'guest', 'active': None},
          'users': []},
         {}, 'color'),
    ]


def pytest_collection_modifyitems(config, items):
    """
    Auto-mark every test collected from this directory as
    `integration`, as tests/integration/conftest.py does.
    """
    for item in items:
        try:
            item_path = str(item.path)
        except AttributeError:
            item_path = str(item.fspath)
        if item_path.startswith(_HERE):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope='session')
def spark():
    """
    A local SparkSession for the whole run. Its Python workers run the
    interpreter running the tests; Spark would otherwise take
    `python3` off PATH, which may be another minor version.
    """
    os.environ.setdefault('PYSPARK_PYTHON', sys.executable)
    from pyspark.sql import SparkSession
    session = (SparkSession.builder
               .master('local[1]')
               .appName('dotted-sparkize-tests')
               .config('spark.ui.enabled', 'false')
               .config('spark.sql.shuffle.partitions', '1')
               .getOrCreate())
    yield session
    session.stop()


@pytest.fixture(scope='session')
def df(spark):
    """
    The sample DataFrame.
    """
    return spark.createDataFrame(_rows(), SCHEMA)
