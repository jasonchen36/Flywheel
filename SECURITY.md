# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch. Users should update to the latest release or commit before reporting an issue that may already have been corrected.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub’s security-advisory feature for this repository. Do not open a public issue when a report includes an exploit, secret, personal data, or a path to arbitrary command execution. Include the affected component, reproduction conditions, impact, and any proposed mitigation. Avoid including real credentials or private transcripts.

## Security boundaries

Flywheel processes model-authored text, hook payloads, transcripts, repository paths, and optional network endpoints. These inputs are untrusted. Code changes must not pass model-authored validation commands through a shell, interpolate untrusted paths into shell command strings, follow unsupported URL schemes, or write outside configured mutation surfaces.

The installer and tests must not overwrite user-owned state silently. Tests should always set a temporary `HARNESS_HOME` and must not connect to a live Graphiti service or invoke a paid model. Secrets belong in local environment files or host configuration and must never be committed.

## Operational guidance

Review `editable_surfaces.json` before enabling automatic mutation. Keep the global enforcement kill switch available, inspect the SessionEnd status file under `MEMORY/LEARNING/DIAGNOSTICS/session-end/latest.tsv`, and retain repository or state backups before enabling unattended automation.
