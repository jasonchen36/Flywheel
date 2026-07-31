---
name: thermo-nuclear-code-quality-review
description: Run an extremely strict maintainability review for abstraction quality, giant files, and spaghetti-condition growth. Use when asked for a thermo-nuclear code quality review, deep code quality audit, or especially harsh maintainability review.
---

# Thermo-Nuclear Code Quality Review

Use this skill for an unusually strict review focused on implementation quality, maintainability, abstraction quality, and codebase health.

Above all, this skill pushes the reviewer to be **ambitious** about code structure. Do not merely identify local cleanup opportunities. Actively search for "code judo" moves: restructurings that preserve behavior while making the implementation dramatically simpler, smaller, more direct, and more elegant.

## Core Principles

1. **Be ambitious about structural simplification ("Code Judo").**
   - Look for opportunities to reframe the change so that whole branches, helpers, modes, conditionals, or layers disappear entirely.
   - Prefer the solution that makes the code feel inevitable in hindsight.
   - If you see a path to delete complexity rather than rearrange it, push hard for that path.

2. **Strict File Size Bounds (<1000 lines).**
   - Do not let a PR push a file from under 1k lines to over 1k lines without a very strong structural reason.
   - Prefer extracting helpers, subcomponents, modules, or local abstractions instead of letting a file sprawl.

3. **Anti-Spaghetti & Special-Case Control.**
   - Be highly suspicious of new ad-hoc conditionals, scattered special cases, or one-off branches inserted into unrelated flows.
   - Pushing feature logic into dedicated helpers or policy objects rather than tangling shared paths.

4. **Direct, Boring, Maintainable Code over AI Fluff.**
   - Reject thin wrappers, pass-through indirection, or generic "magic" handling that hides data shape.
   - Strip AI fluff, over-engineered disclaimers, and unnecessary boilerplate.

5. **Canonical Layer Reuse.**
   - Prefer existing canonical utilities/helpers over bespoke one-offs.
   - Keep logic in the right package/layer instead of normalizing architectural drift.

## Primary Review Questions

- Is there a "code judo" move that would make this dramatically simpler?
- Can this change be reframed so fewer concepts, branches, or helper layers are needed?
- Did the diff add branching complexity where a better abstraction should exist?
- Is this logic living in the canonical layer, or did details leak across a boundary?
- Did this change enlarge a file or component past a healthy size boundary (>1k lines)?

## Approval Bar

Do not approve merely because behavior seems correct. The bar for approval is:
- No structural quality regression.
- No obvious missed opportunity to make the implementation dramatically simpler.
- No unjustified file-size explosion (>1k lines).
- No ad-hoc branching or special-case spaghetti growth.
- No bespoke helper where a canonical utility already exists in the codebase.
