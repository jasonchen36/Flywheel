.PHONY: install-dev test coverage-foundations lint hooks security shellcheck check

install-dev:
	python3 -m pip install -r requirements-dev.txt
	bun install --frozen-lockfile

test:
	python3 -m pytest --cov=learning --cov-branch --cov-fail-under=49 -q

coverage-foundations:
	python3 -m pytest tests/test_state_io.py tests/test_review_store.py tests/test_harness_config.py \
		tests/test_surface_gate.py tests/test_ratings_hygiene.py \
		tests/test_enforcement_promotion.py tests/test_summary_ingest.py \
		tests/test_lesson_dedup.py tests/test_pattern_promotion.py \
		--cov=state_io --cov=review_store --cov=harness_config --cov=surface_gate \
		--cov=ratings_hygiene --cov=enforcement_promotion --cov=summary_ingest \
		--cov=lesson_dedup --cov=pattern_promotion \
		--cov-branch --cov-fail-under=100 -q

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
