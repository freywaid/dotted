"""
Smoke tests: minimum infrastructure check. Confirms the DSN is
reachable, the schema was created, and the seed data is present.
"""


def test_conn_reachable(query):
    rows = query('SELECT 1 AS one')
    assert rows == [{'one': 1}]


def test_seed_row_count(query):
    rows = query('SELECT count(*) AS c FROM dotted_test.items')
    assert rows[0]['c'] == 7
