.PHONY: check lint test format

PYTHON ?= .venv/bin/python

check: lint test

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

test:
	$(PYTHON) -m pytest

format:
	$(PYTHON) -m ruff check . --fix
	$(PYTHON) -m ruff format .
