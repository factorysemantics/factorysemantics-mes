# 0005 — Plain HTML, CSS and JS for the operator UI, no build step

- **Status:** accepted
- **Date:** 2026-08-28
- **Deciders:** @kalwei

## Context
A plant PC must render the operator screens for years without a Node toolchain, and a plant IT department must be able to read the files it is asked to serve.

## Options considered
| Option | For | Against |
|---|---|---|
| Plain HTML/CSS/JS, served by the API | no build, no toolchain, readable, diffable, works on the PC in the corner | no component framework; discipline needed to keep it consistent (the style guide, the UI checks) |
| React/Vue SPA | ecosystem, components | a build step, a toolchain on plant PCs or a CI artefact to ship, and dependencies that rot |

## Decision
The operator UI is plain HTML, CSS and JavaScript under `src/fsmes/web/`, served by the API, with themes as CSS variables and no build step. `fsmes ui-check` crawls every screen in every theme against baselines so drift is caught without a framework.

## Consequences
Easy: deployment and longevity. Hard: rich components are written by hand. Revisit only if a contributor's plant needs something the approach cannot express.

## House rules touched
Rule 6 (charts get checked by looking at them) is what makes this safe.
