.PHONY: install-dev test lint hooks security shellcheck check

install-dev:
	python3 -m pip install -r requirements-dev.txt
	bun install --frozen-lockfile

test:
	python3 -m pytest -q

lint:
	ruff check learning tests
	python3 -m compileall -q learning tests

hooks:
	bun run check:hooks

security:
	bandit -q -r learning -ll
	pip-audit -r requirements.txt
	bun audit

shellcheck:
	shellcheck -x install.sh templates/session-end.sh graphiti/scripts/*.sh hooks/*.sh

check: lint hooks test security shellcheck
