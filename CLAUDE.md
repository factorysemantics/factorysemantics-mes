# Where code goes

One page for anyone, person or agent, about to add code here. It says what is
true of the code today; where today falls short of the rule it says so.

## The ladder

Lowest first; a layer imports only from itself and the ones below it:

    foundations (core, kernel, db, config, ...) <- domain <- services
        <- edges (api, mcp, integrations, ...) <- cli

`tests/test_core_purity.py` holds the full ladder and fails an import that
goes up. `docs/ARCHITECTURE.md` is the design; decisions
[0002](docs/decisions/0002-kernel-and-modules.md) (kernel and modules),
[0005](docs/decisions/0005-plain-html-no-build-step.md) (plain HTML, no build),
[0022](docs/decisions/0022-what-a-plant-pack-may-contain.md) (a pack is data) and
[0035](docs/decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
(configuration is authored by roles) are why.

- **Services decide.** Every rule and every audit write lives in
  `src/fsmes/services/` (its `__init__.py` says so and holds the error types).
- **Routers parse and call.** A router in `src/fsmes/api/routers/` reads the
  request, calls a service, and shapes the answer. Today 14 routers still
  import `fsmes.domain` or `fsmes.db` directly; new code does not.
- **Screens draw what the API measured.** `src/fsmes/web/` computes no number.
- **A plant's differences are data**, in its pack or its settings, not an
  `if` on its name.

## Before you write a helper, grep for it

If it exists, call it. These are the ones that keep being written again:

| You want | The one copy |
|---|---|
| the line a machine is on | `services/masterdata.work_center_of` |
| a machine's cost center | `services/masterdata.cost_center` |
| a machine by code, or a 404 | `services/masterdata.get_equipment` |
| an xlsx download of a list | `api/export.spreadsheet` + `FS.exportXlsx` in `web/common.js` |
| a chart on a screen | `FS.kit.chart` in `web/kit.js` |
| a call to the API from a page | `FS.api` in `web/common.js` |
| a message after a save | `FS.toast` in `web/common.js` |
| a `<select>` filled from a list | `fillOptions` in `web/app.js` |
| a list the plant authors (reasons, severities) | `services/vocabulary` |
| a setting the plant owns | `services/plant_settings` |
| machines ranked worst OEE first | `services/analysis.oee_breakdown` |

`tests/test_web.py::test_no_new_page_grows_its_own_api_helper` already holds
the browser half of this.

## Fences that fail a pull request

- `tests/test_routers_do_not_grow.py`: each router has a ceiling (its length
  on 2026-11-14 + 5 %; a new router, 150 lines). Over it, move the logic to a
  service, or raise the ceiling in the table with a reason a reviewer reads.
- `tests/test_browser_tests_share_one_server.py`: a new browser test does not
  start its own server. It uses `live_server` (the demo plant, shared, for a
  test that only reads or writes under codes of its own) or `serve_plant`
  (a plant with rows of the test's own), both in `tests/conftest.py`.
- `tests/test_core_purity.py`: no import goes up the ladder.

## One change, every door

A thing one screen records is often recorded from a sibling too: the Floor
form, the station screen, an MCP tool in `src/fsmes/mcp/`. A change one of
them wants changes all of them, or the pull request says why not.

## House rules

Never invent production: unknown is not zero. Every list states its total.
Config, not code, at plant boundaries. Tests are named as prose. Commits are
signed off (`git commit -s`). Set up and run the suite as `CONTRIBUTING.md`
says; browser tests run with `pytest -m browser`, not by file name alone.
