"""
Root conftest. Adds the `--all` flag and the skip logic for integration
tests. Tests under `tests/integration/` auto-mark themselves via that
directory's conftest; by default they're skipped. They run when either
`--all` is passed or the caller explicitly targets the directory.

When copium is installed every test function runs twice, once per
deepcopy implementation (copium and the stdlib `copy.deepcopy` fallback).
Doctests can't be parametrized, so they run once on the default.
"""
import copy

import pytest

from dotted import utils


def pytest_addoption(parser):
    parser.addoption(
        '--all',
        action='store_true',
        default=False,
        help='Run all tests including integration tests that require a '
             'live Postgres (see DOTTED_TEST_DSN).',
    )


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'integration: test requires a live Postgres connection',
    )


def _copiers():
    """
    Deepcopy implementations to test: the stdlib always, copium when installed.
    """
    if utils.deepcopy is copy.deepcopy:
        return ['copy']
    return ['copium', 'copy']


def pytest_generate_tests(metafunc):
    metafunc.parametrize('copier', _copiers(), indirect=True)


@pytest.fixture(autouse=True)
def copier(request, monkeypatch):
    """
    Run each test under each deepcopy implementation.
    """
    name = getattr(request, 'param', None)
    if name == 'copy':
        monkeypatch.setattr(utils, 'deepcopy', copy.deepcopy)
    return name


def pytest_collection_modifyitems(config, items):
    # `--all` opts into everything.
    if config.getoption('--all'):
        return
    # Explicit targeting of tests/integration also opts in.
    if any('tests/integration' in str(arg) for arg in config.args):
        return
    # Otherwise skip anything carrying the integration marker.
    skip = pytest.mark.skip(
        reason='integration tests skipped (use --all or target tests/integration/)')
    for item in items:
        if 'integration' in item.keywords:
            item.add_marker(skip)
