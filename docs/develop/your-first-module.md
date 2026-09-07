# Your first module

*Tutorial. How a module registers itself, using the ERPNext connector as the worked example. Honest scope: the entry-point mechanism is small, and the "module" boundary is still a target the code is moving toward (see the architecture page).*

## What "module" means here, today

`fsmes info` lists modules by reading the `fsmes.modules` entry-point group.
As of 2026-09-07 one thing registers there: the ERPNext connector. The ERP
factory resolves any `MES_ERP_MODE` it does not know built in (`rest`,
`file`, `off`) by looking up an entry point of that name, so a connector in
a separate package plugs in without a change here.

That is the whole mechanism. Quality, maintenance and scheduling are not yet
separately installable packages; they ship in the kernel wheel. Turning them
into entry-point modules is on the roadmap, not done.

## The worked example: ERPNext

`pyproject.toml`:

```toml
[project.entry-points."fsmes.modules"]
erpnext = "fsmes.integrations.erp.erpnext_adapter:from_settings"
```

`from_settings(settings)` builds an adapter that satisfies the `ErpAdapter`
protocol — three methods:

```python
class ErpAdapter(Protocol):
    def fetch_orders(self) -> list[ProductionRequest]: ...
    def acknowledge(self, order_code: str) -> None: ...
    def send_confirmation(self, confirmation: Confirmation) -> None: ...
```

The models are typed (`contract.py`); every MES-side rule lives in
`services.erp`, so an adapter is transport only.

## Write your own connector, in its own package

1. New package, say `fsmes-sap`, depending on `factorysemantics-mes`.
2. Implement the three methods against `fsmes.integrations.erp.contract`.
3. Register:

    ```toml
    [project.entry-points."fsmes.modules"]
    sap = "fsmes_sap.adapter:from_settings"
    ```

4. `pip install fsmes-sap`; `fsmes info` now lists `erpnext, sap`;
   `MES_ERP_MODE=sap` selects it.

Settings your connector needs come from the same `MES_` environment; read
them off the `Settings` object you are handed, or from `os.environ` if they
are yours alone.

## What to test

The ERPNext connector's tests (`tests/test_erpnext.py`) run against a fake
ERPNext in-process: the shape to copy. Name tests after the promise they
pin, not the function they call — house rule 5.

## See also

- Decision records [0002](../decisions/0002-kernel-and-modules.md) and [0008](../decisions/0008-erp-connectors-are-modules.md)
- [The ERPNext connector](../operate/erpnext.md)
