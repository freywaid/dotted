PYTHON ?= python3
VENV := venv
VENV_BIN := $(VENV)/bin

# Extra args passed to pytest. e.g.:  make test PYTEST_ARGS=-x
PYTEST_ARGS ?=

.PHONY: install test test.integration test.all clean

# Create the local venv and install dotted (with all optional extras),
# pytest, and integration-test deps. Other targets depend on this.
install: $(VENV_BIN)/pytest

$(VENV_BIN)/pytest: requirements-integration.txt setup.py
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -e '.[all]'
	$(VENV_BIN)/pip install pytest
	$(VENV_BIN)/pip install -r requirements-integration.txt
	@touch $(VENV_BIN)/pytest

# Unit tests only (integration tests skipped).
test: install
	$(VENV_BIN)/pytest $(PYTEST_ARGS)

# Integration tests only (requires a live Postgres reachable via
# DOTTED_TEST_DSN, defaults to postgres://postgres:postgres@localhost:5432/postgres).
test.integration: install
	$(VENV_BIN)/pytest --integration tests/integration/ $(PYTEST_ARGS)

# Everything: unit + integration.
test.all: install
	$(VENV_BIN)/pytest --integration $(PYTEST_ARGS)

clean:
	rm -rf $(VENV) .pytest_cache *.egg-info build dist
