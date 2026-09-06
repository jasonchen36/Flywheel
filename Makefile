.PHONY: install-dev test coverage-foundations lint hooks security shellcheck check

install-dev:
	python3 -m pip install -r requirements-dev.txt
	bun install --frozen-lockfile

test:
	python3 -m pytest --cov=learning --cov-branch --cov-fail-under=93 -q

coverage-foundations:
	python3 -m pytest tests/test_state_io.py tests/test_review_store.py tests/test_harness_config.py \
		tests/test_surface_gate.py tests/test_ratings_hygiene.py \
		tests/test_enforcement_promotion.py tests/test_summary_ingest.py \
		tests/test_lesson_dedup.py tests/test_pattern_promotion.py \
		tests/test_judge_outcomes.py tests/test_pipeline_state.py \
		tests/test_measure_effectiveness.py tests/test_feedback_loop.py \
		tests/test_held_out_regression.py tests/test_harness_changelog.py \
		tests/test_self_improve_engine.py tests/test_self_improve_lifecycle.py \
		tests/test_evals_registry.py tests/test_held_out_suite.py \
		tests/test_lesson_evolve.py tests/test_review_workflow.py \
		tests/test_skill_autofix_lifecycle.py tests/test_skill_burnin.py \
		tests/test_autofix_end_to_end.py tests/test_hardening.py \
		tests/test_ace_lifecycle.py tests/test_agent_rollouts.py \
		tests/test_agent_rollouts_lifecycle.py tests/test_self_harness.py \
		tests/test_consolidate_memory.py tests/test_chronic_failures.py \
		tests/test_intent_how_audit.py \
		--cov=state_io --cov=review_store --cov=harness_config --cov=surface_gate \
		--cov=ratings_hygiene --cov=enforcement_promotion --cov=summary_ingest \
		--cov=lesson_dedup --cov=pattern_promotion --cov=judge_outcomes \
		--cov=measure_effectiveness --cov=held_out_regression --cov=harness_changelog \
		--cov=self_improve --cov=evals --cov=lesson_evolve \
		--cov=skill_autofix --cov=skill_burnin \
		--cov=ace_reflector --cov=ace_playbook --cov=agent_rollouts \
		--cov=self_harness --cov=consolidate_memory --cov=chronic_failures \
		--cov=intent_how_audit \
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
