# 0018 — Public on 2026-09-08, and a release check that runs the built wheel

- **Status:** accepted
- **Date:** 2026-09-08
- **Deciders:** @kalwei

## Context
The readiness checklist (0014) went green over 2026-09-07. The maintainer's go-ahead came the following evening. Publication meant four things happening at once: the repository becoming visible, the first tag firing a release workflow that had never run for real, a package index entry, and a documentation site on its own domain.

## Options considered
| Option | For | Against |
|---|---|---|
| Publish and tag the same evening | one event; the release workflow is exercised against real registries immediately, which is the only way it gets exercised | anything the workflow gets wrong is public and permanent, because a tag is never moved |
| Publish, then tag after a quiet week | defects found without an audience | nothing would have found them: the failures were in steps that only run on a tag |
| Tag privately first | a rehearsal | the free plan's private repository cannot exercise the environments and protections the real release uses |

## Decision
The repository went public on 2026-09-08 with branch protection on `main` (a pull request, the CI checks and the sign-off check required, no force-push, the maintainer included), a reviewer-gated publishing environment that deploys only from a version tag, dependency alerts, private vulnerability reporting, secret scanning with push protection, and code scanning. The private repository becomes the archive from that day and receives nothing further. Three tags were cut that evening — the release workflow's container step named a file it had not copied, a later step asked for an artefact under a tag that did not exist, and the built wheel shipped without the four configuration files the demo command reads, so a fresh install ran the loop without booking anything. Each was fixed forward as a new patch release; no tag was moved.

Every one of those was invisible to the test suite and visible in ten seconds to anyone who installed the package. So the release procedure gains a step that CI performs rather than a person remembers: build the wheel, install it into an empty environment, and run the demo end to end before a tag may be cut. The release build also refuses a wheel that is missing its packaged configuration.

## Consequences
Easy: the first version anyone found by searching, `0.1.2`, works from a clean install, and the archive question is settled — one repository is the project, the other is history. Hard: three tags on day one are permanent, and the changelog says why. The lesson generalises past this project: a test suite run from the source tree never tests what was packaged.

## House rules touched
Rule 5 in spirit — the check is named for the behaviour it pins, which is that a fresh install books production, not that a build step exits zero.
