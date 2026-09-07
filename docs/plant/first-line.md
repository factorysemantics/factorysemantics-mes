# Your first line

*Tutorial. Ten minutes, a laptop, no hardware. Everything here is simulated.*

## 1. Install and check

```bash
pipx install factorysemantics-mes
fsmes info
```

`fsmes info` prints the version, where the package is installed, and which
modules are registered — `erpnext` on a fresh install.

## 2. Run the story

```bash
fsmes demo
```

One process starts five things: an OPC UA server playing a simulated
six-station line, the MES API, a mock ERP, the OPC agent that reads the
machines into the MES, and the ERP sync worker. Then it tells a story in five
steps:

1. A planner creates an order in the ERP: 15 units of a finished good.
2. ERP sync imports the order into the MES and acknowledges it back.
3. The seeded operator account releases the order and issues two raw-material
   lots against the bill of materials.
4. The agent loads the order code into the machines; the line runs at four
   times real time; every few seconds the console shows good and scrap
   quantities per operation, straight from machine counters.
5. The order completes; the MES books the finished lot with its genealogy and
   confirms quantities to the ERP. The console ends with the OEE over the
   actual production window.

While it runs, open <http://127.0.0.1:8000/dashboard> and sign in as `ADMIN`
/ `admin` (a lab default; the log tells you so). The screens are the same
ones a plant would use.

## 3. What you just saw, and what you did not

- Production was booked from **counter deltas** read over OPC UA, not from
  the order quantity. If the simulated counter had reset, the MES would have
  re-baselined and booked nothing — see [never invent production](never-invent-production.md).
- OEE was computed over the window the MES was actually watching. Ask for a
  window before the demo started and the answer is *unknown*, not zero.
- Nothing was written to a machine except the order code the routing says
  the machine displays. That is the whole write surface of a default install.

## 4. Next

- Run a whole plant from a registry, with its own database, OPC UA server
  and dashboard: [plants from a registry](../operate/registry.md).
- Point the same agent at a real Kepware server with a worksheet:
  [IT guide](../onboarding/GUIDE-IT.md) and [Engineering guide](../onboarding/GUIDE-ENGINEERING.md).
- Let an agent operate it: [connect an MCP client](../agents/connect-mcp-client.md).

## See also

- [Reading OEE](reading-oee.md)
- [Settings reference](../reference/settings.md) — `MES_SIM_SPEED`, `MES_API_PORT`
- [CLI reference](../reference/cli.md) — `demo`, `plant`, `score`
