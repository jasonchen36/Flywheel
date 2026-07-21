---
name: help
description: "skill router: describe your problem and get routed to the right skill with usage example. Use when working on skill router."
---
# Help Skill

Given a description of what you're trying to do, finds the right skill and shows how to invoke it. Useful when you have 40+ skills and can't remember which one fits.

## How It Works

The user describes their situation or problem in natural language. This skill matches it to the best available skill(s) and provides a ready-to-use invocation example.

## Routing Table

### Starting work / context
| Situation | Skill |
|---|---|
| Starting my day, need context on what's in flight | `/session-start` |
| Switching topics mid-session, don't want to lose context | `/session-start` |
| Sprint just started, need to organize my week | `/sprint-plan` |
| Need to prepare scrum update | `/scrum-update` |

### Tickets and PRs
| Situation | Skill |
|---|---|
| Starting new work, need JIRA ticket + branch + subtasks | `/new-ticket` |
| Need to track a JIRA ticket through the session | `/jira-track` |
| Have PRs in multiple repos for one feature | `/pr-stack` |
| Need to create subtasks and PRs across multiple repos | `/jira-pr` |
| Ready to create a PR | `/pre-pr-workflow` |
| Need to review someone else's PR | `/review` |
| Need to quickly approve a PR | `/approve-pr` |
| Replying to a PR review comment | (use directly — no dedicated skill needed) |

### Deployment
| Situation | Skill |
|---|---|
| Deploying a new internal-lib-airflow version | `/deploy-airflow` |
| Promoting airflow-image to UAT | `/promote-uat` |
| Promoting airflow-image to PRD | `/promote-prd` |
| Creating a GitHub release for a package | `/release-package` |
| Something went wrong, need to roll back | `/rollback` |
| Want to check all gates before promoting | `/promotion-blocker` |
| Need to see what's different between two environments | `/env-diff` |

### Debugging and incidents
| Situation | Skill |
|---|---|
| Airflow DAG is failing | `/debug-airflow` |
| Dataflow/Beam job is failing or stopped | `/debug-dataflow` |
| Drone CI build failed | `/debug-drone` |
| PRD is down or degraded, need to coordinate a response | `/triage-incident` |
| Incident is resolved, need to write the post-mortem | `/post-mortem` |
| Two DAG runs are behaving differently (regression) | `/compare-runs` |
| Handing off to oncall or taking over | `/oncall-handoff` |

### Data and pipelines
| Situation | Skill |
|---|---|
| Need to run a data backfill | `/backfill` |
| Something looks wrong with the data | `/data-quality` |
| Datastream CDC seems slow or stopped | `/datastream-check` |
| Need to trace what feeds or reads a table | `/lineage` |
| DAG is running slower than it used to | `/dag-performance` |
| Two DAG runs behaving differently | `/compare-runs` |
| Need a weekly pipeline health digest | `/pipeline-report` |

### Schema and queries
| Situation | Skill |
|---|---|
| Schema looks different across environments | `/schema-check` |
| A BigQuery query is slow or expensive | `/query-optimizer` |
| A MySQL query is slow | `/mysql-slow-query` |
| Need to add a database index or column | `/database-alter` |
| Need to validate Dataform SQLX before a PR | `/validate-dataform` |
| BigQuery CLI operations (dry-run, schema check) | `/bq` |

### Environment and infrastructure
| Situation | Skill |
|---|---|
| Need to run and document STG testing evidence for a PR | `/test-stg` |
| Need to check if an environment is healthy before testing | `/health-check` |
| Need to browse GCS buckets or verify partitions | `/gcs-browse` |
| Terraform infra changes need validation | `/terraform-check` |
| Need to rotate a secret or API key | `/secret-rotation` |
| Need to check what's different between STG and PRD | `/env-diff` |

### Hygiene and maintenance
| Situation | Skill |
|---|---|
| Check NR alert coverage across all DAGs | `/alert-coverage` |
| Check for outdated package dependencies | `/dependency-update` |
| Check GCP costs | `/cost-check` |
| Clean up stale branches across repos | `/cleanup-branches` |
| Check AGENTS.md / CLAUDE.md are in sync | `/sync-check` |

### Discovery
| Situation | Skill |
|---|---|
| Who owns this table / DAG / pipeline? | `/find-owner` |
| Search docs, Confluence, rtfmcp | `/doc-search` |

### GL-specific
| Situation | Skill |
|---|---|
| GL table partitioning migration | `/gl-partitioning` |

## Multi-Skill Workflows

Common combinations:

**Investigating a data quality issue:**
`/lineage` → `/data-quality` → `/debug-airflow` → `/backfill`

**Deploying a new feature safely:**
`/new-ticket` → build → `/validate-dataform` (if SQLX) → `/pre-pr-workflow` → `/promotion-blocker` → `/promote-uat` → `/promote-prd`

**Responding to a PRD incident:**
`/triage-incident` → `/rollback` (if needed) → `/post-mortem` → `/alert-coverage`

**Starting a sprint:**
`/session-start` → `/sprint-plan` → `/jira-track`

**Pre-promotion checklist:**
`/env-diff` → `/promotion-blocker` → `/promote-uat` or `/promote-prd`

**Monthly hygiene:**
`/alert-coverage` → `/dependency-update` → `/cost-check` → `/cleanup-branches` → `/sync-check`

## If You're Still Unsure

Describe your situation in detail and ask directly — the right skill will be identified from context.

Examples:
- "I need to figure out why this DAG is taking 3x longer than last week" → `/compare-runs` then `/dag-performance`
- "Bronze data looks stale but I'm not sure where it's stuck" → `/lineage` then `/datastream-check`
- "I want to promote to PRD but I'm nervous about what might be different" → `/env-diff` then `/promotion-blocker`
