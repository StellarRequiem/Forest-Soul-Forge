# Audits

Retrospective reviews of the codebase, architecture, dependencies, and security posture.

## What goes here

- **Phase audits** — at the end of each phase, a short review of what was built vs. what was planned, what's stable, what's known-broken.
- **Security reviews** — before any release, before onboarding new dependencies, or after any change touching auth/audit/trait-grading.
- **Dependency audits** — output of `pip-audit`, `safety`, or equivalent, with any accepted findings annotated.
- **Compliance notes** — anything relevant to SOC 2, TOS enforcement, or future certification requirements.

## What does NOT go here

- Ongoing test results — those go in CI artifacts.
- Decisions about the future — those go in `docs/decisions/` as ADRs.
- Incident response write-ups — those need their own folder if incidents ever happen. We don't have one yet because we don't have users yet.

## Conventions

- Filename: `YYYY-MM-DD-short-slug.md`.
- Every audit file must state: who ran it, what scope, what tool/method, what was found, and what was done about each finding (fixed / accepted / deferred with ticket link).

## Index

_No audits yet. First one will be the Phase 0 scaffolding review after the initial commit._
