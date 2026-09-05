.PHONY: install-dev test coverage-foundations lint hooks security shellcheck check

install-dev:
	python3 -m pip install -r requirements-dev.txt
	bun install --frozen-lockfile

test:
	python3 -m pytest --cov=learning --cov-branch --cov-fail-under=18 -q

coverage-foundations:
	python3 -m pytest tests/test_state_io.py tests/test_review_store.py tests/test_harness_config.py \
		--cov=state_io --cov=review_store --cov=harness_config --cov-branch \
		--cov-fail-under=100 -q

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
