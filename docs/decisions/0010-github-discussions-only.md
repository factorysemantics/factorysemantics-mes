# 0010 — GitHub Discussions is the only community venue

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
A project going public needs somewhere for questions that are not bugs. The audience — plant engineers, ERP integrators, controls people — is on Discourse forums, r/PLC and LinkedIn, not on any chat this project would run. The project has one maintainer working evenings.

## Options considered
| Option | For | Against |
|---|---|---|
| GitHub Discussions only | indexed by search engines, so an answer stays findable; sits next to the code; converts to an issue in one click; inherits the repository's conduct file and abuse tooling; no new account for the visitor | no real-time feel; announcements reach only people who watch the repository |
| Discord | the most popular developer chat in 2026 | invisible to search, and the growth plan depends on being found; chat with a once-a-day maintainer reads as dead; needs moderation from day one |
| Zulip or Matrix | topic-threaded, public archive that is indexed | the same presence problem, for a smaller audience than Discord |
| A self-hosted forum | full control | a server to patch, for a venue that would be quiet |

## Decision
Discussions on the main repository is the one venue, in six categories: Announcements, Q&A, Ideas, Show and tell, Integrations, and Who is using it. Ideas is the front door for design changes (GOVERNANCE); Q&A is the searchable knowledge base, and a discussion that turns out to be a bug is converted rather than duplicated. Chat is not planned.

## Consequences
Easy: every answer given once stays findable, and moderation is whatever GitHub already provides. Hard: nobody gets an instant reply; the pinned rules say maintainers answer within a few days, not hours. Revisit only if, for two consecutive months, more than five people other than the maintainer are answering in Q&A, more than 30 discussions a month are opened, and someone else volunteers to moderate.

## House rules touched
None. The pinned rules repeat the provenance rule: no real plant data, no tag names that identify a customer, no screenshots with company names — use the simulator.
