PYTHON ?= python3
VENV := venv
VENV_BIN := $(VENV)/bin

# Extra args passed to pytest. e.g.:  make test PYTEST_ARGS=-x
PYTEST_ARGS ?=

.PHONY: install test test.python test.native test.integration bench bench.python bench.native native native.clean clean

# Create the local venv and install dotted (with all optional extras),
# pytest, and integration-test deps. Other targets depend on this.
install: $(VENV_BIN)/pytest

$(VENV_BIN)/pytest: requirements-integration.txt pyproject.toml
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -e '.[formats,copium]'
	$(VENV_BIN)/pip install pytest
	$(VENV_BIN)/pip install -r requirements-integration.txt
	@touch $(VENV_BIN)/pytest

# Unit tests (integration tests skipped) on both engines: the engine's .py
# source, then the compiled engine.
test: test.python test.native

# On the engine's .py source, whether or not a compiled engine is present.
test.python: install
	DOTTED_NATIVE=0 $(VENV_BIN)/pytest $(PYTEST_ARGS)

# On the compiled engine, building it first.
test.native: native
	DOTTED_NATIVE=1 $(VENV_BIN)/pytest $(PYTEST_ARGS)

# Integration tests only (requires a live Postgres reachable via
# DOTTED_TEST_DSN, defaults to postgres://postgres:postgres@localhost:5432/postgres).
test.integration: install
	$(VENV_BIN)/pytest tests/integration/ $(PYTEST_ARGS)

# Benchmarks on both engines, side by side, building the compiled engine
# first. e.g.:  make bench BENCH_ARGS='update remove'
bench: native
	$(VENV_BIN)/python -m benchmarks --engines $(BENCH_ARGS)

# On one engine.
bench.python: install
	DOTTED_NATIVE=0 $(VENV_BIN)/python -m benchmarks $(BENCH_ARGS)

bench.native: native
	DOTTED_NATIVE=1 $(VENV_BIN)/python -m benchmarks $(BENCH_ARGS)

# Compile the engine to C, in place. Python then imports the compiled
# modules in preference to the .py files beside them, so after editing an
# engine module run this again, or `make native.clean` to go back to the
# source.
native: install
	$(VENV_BIN)/pip install cython setuptools
	$(VENV_BIN)/python setup.py build_ext --inplace

# Remove what `make native` built: dotted runs from the .py files again.
native.clean:
	rm -rf build dotted/*.so dotted/*.pyd dotted/*.c

clean: native.clean
	rm -rf $(VENV) .pytest_cache *.egg-info build dist
