.PHONY: install-dev test coverage-foundations lint hooks security shellcheck check

install-dev:
	python3 -m pip install -r requirements-dev.txt
	bun install --frozen-lockfile

test:
	python3 -m pytest --cov=learning --cov-branch --cov-fail-under=71 -q

coverage-foundations:
	python3 -m pytest tests/test_state_io.py tests/test_review_store.py tests/test_harness_config.py \
		tests/test_surface_gate.py tests/test_ratings_hygiene.py \
		tests/test_enforcement_promotion.py tests/test_summary_ingest.py \
		tests/test_lesson_dedup.py tests/test_pattern_promotion.py \
		tests/test_judge_outcomes.py tests/test_pipeline_state.py \
		tests/test_measure_effectiveness.py tests/test_feedback_loop.py \
		tests/test_held_out_regression.py tests/test_harness_changelog.py \
		tests/test_self_improve_engine.py tests/test_self_improve_lifecycle.py \
		--cov=state_io --cov=review_store --cov=harness_config --cov=surface_gate \
		--cov=ratings_hygiene --cov=enforcement_promotion --cov=summary_ingest \
		--cov=lesson_dedup --cov=pattern_promotion --cov=judge_outcomes \
		--cov=measure_effectiveness --cov=held_out_regression --cov=harness_changelog \
		--cov=self_improve \
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
