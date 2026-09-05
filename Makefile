.PHONY: install-dev test coverage-foundations lint hooks security shellcheck check

install-dev:
	python3 -m pip install -r requirements-dev.txt
	bun install --frozen-lockfile

test:
	python3 -m pytest -q

coverage-foundations:
	python3 -m pytest tests/test_state_io.py --cov=state_io --cov-branch --cov-fail-under=100 -q

lint:
	ruff check learning tests
	mypy learning
	python3 -m compileall -q learning tests

hooks:
	bun run check:hooks

security:
	bandit -q -r learning -ll
	pip-audit -r requirements-dev.txt
	bun audit

shellcheck:
	shellcheck -x install.sh templates/session-end.sh graphiti/scripts/*.sh hooks/*.sh

check: lint hooks test coverage-foundations security shellcheck
