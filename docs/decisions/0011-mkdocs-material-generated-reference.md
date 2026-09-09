# 0011 — MkDocs Material, with the developer reference generated from the code

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The documentation was already markdown files in `docs/`, readable on GitHub and nowhere else. A CLI, a REST API, an MCP tool list and a settings table all existed in the code and were also described by hand, which is the shape that drifts. Whatever builds the site has to be maintainable in evenings by one person.

## Options considered
| Option | For | Against |
|---|---|---|
| MkDocs with the Material theme | reads the markdown that already exists; Python tool for a Python project, no Node toolchain; tabs, admonitions, Mermaid, search, versioning via `mike`; API pages from docstrings | one more optional dependency group to keep current |
| Docusaurus | the best-looking default, and familiar to a React contributor | a Node build chain next to a project whose own UI decision (0005) is "no build step" |
| Sphinx | the strongest API reference in Python | the reference is a minority of these pages; the narrative books would fight the tooling |
| Leave the markdown on GitHub | nothing to build | no search, no navigation, no versions, and no generated reference |

## Decision
The documentation site is MkDocs Material, built from `docs/` in this repository with `mkdocs.yml` at the root and a `docs` extra in `pyproject.toml`. The developer reference — CLI, REST API, MCP tools and settings — is generated at build time by a hook, so it cannot drift from the code; if generation fails, the build fails. CI runs `mkdocs build --strict` on every pull request, so a broken link is a red check. `mike` publishes `dev` from `main` and `X.Y` plus `latest` from a release tag. Writing a page is writing a markdown file; publishing it is merging to `main`.

## Consequences
Easy: the reference is always true, and a typo fix is a one-click pull request from the site. Hard: `--strict` means a moved page breaks the build until its redirect is added, which is the point.

## House rules touched
None directly. Generating the reference is the documentation form of rule 4: the description lives with the thing, not in a second copy someone has to remember.
