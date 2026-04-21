"""
Root conftest. Adds the `--all` flag and the skip logic for integration
tests. Tests under `tests/integration/` auto-mark themselves via that
directory's conftest; by default they're skipped. They run when either
`--all` is passed or the caller explicitly targets the directory.
"""
import pytest


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
