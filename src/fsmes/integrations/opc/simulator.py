"""The simulated plant: an OPC UA server that behaves like real line PLCs.

Each machine from the tag map gets an object with:
    State (running|idle|down)   GoodCount / ScrapCount (monotonic counters)
    Temperature (analog)        OrderCode (writable — the MES tells the
                                machine what to run; empty means stop)

Machines only produce while an order code is loaded, occasionally break down,
and scrap ~3% — enough realism for meaningful OEE numbers downstream.
"""

import asyncio
import random

import structlog
from asyncua import Server, ua

from fsmes.config import Settings
from fsmes.integrations.opc.tag_map import MachineMap, load_tag_map

log = structlog.get_logger("opc.simulator")

_DOWNTIME_CHANCE_PER_SEC = 0.008
_SCRAP_RATE = 0.03


class SimMachine:
    def __init__(self, spec: MachineMap, state_node, good_node, scrap_node, temp_node, order_node):
        self.spec = spec
        self.nodes = {"state": state_node, "good": good_node, "scrap": scrap_node, "temp": temp_node}
        self.order_node = order_node
        self.state = "idle"
        self.good = 0
        self.scrap = 0
        self.temperature = 25.0
        self.progress = 0.0
        self.down_left = 0.0

    async def step(self, dt: float) -> None:
        order = (await self.order_node.read_value()) or ""

        if self.down_left > 0:
            self.down_left = max(0.0, self.down_left - dt)
            new_state = "down"
        elif order:
            if random.random() < _DOWNTIME_CHANCE_PER_SEC * dt:
                self.down_left = random.uniform(5, 20)
                new_state = "down"
            else:
                new_state = "running"
        else:
            new_state = "idle"

        if new_state == "running":
            self.progress += dt / self.spec.cycle_seconds
            while self.progress >= 1.0:
                self.progress -= 1.0
                if random.random() < _SCRAP_RATE:
                    self.scrap += 1
                    await self.nodes["scrap"].write_value(ua.Variant(self.scrap, ua.VariantType.Int64))
                else:
                    self.good += 1
                    await self.nodes["good"].write_value(ua.Variant(self.good, ua.VariantType.Int64))

        target_temp = 60.0 if new_state == "running" else 25.0
        self.temperature += (target_temp - self.temperature) * 0.1 + random.uniform(-0.5, 0.5)
        await self.nodes["temp"].write_value(round(self.temperature, 2))

        if new_state != self.state:
            self.state = new_state
            await self.nodes["state"].write_value(new_state)
            log.info("machine state", machine=self.spec.equipment, state=new_state)


async def run(settings: Settings, speed: float | None = None) -> None:
    speed = speed or settings.sim_speed
    machines = load_tag_map(settings.tag_map_file)

    server = Server()
    await server.init()
    server.set_endpoint(settings.opc_endpoint)
    server.set_server_name("MES-TWIN Plant Simulator")
    idx = await server.register_namespace(settings.opc_namespace)

    sims: list[SimMachine] = []
    for spec in machines:
        obj = await server.nodes.objects.add_object(idx, spec.object)
        state = await obj.add_variable(idx, "State", "idle")
        good = await obj.add_variable(idx, "GoodCount", ua.Variant(0, ua.VariantType.Int64))
        scrap = await obj.add_variable(idx, "ScrapCount", ua.Variant(0, ua.VariantType.Int64))
        temp = await obj.add_variable(idx, spec.analog, 25.0)
        order = await obj.add_variable(idx, spec.order_tag or "OrderCode", "")
        await order.set_writable()
        sims.append(SimMachine(spec, state, good, scrap, temp, order))

    log.info("simulator online", endpoint=settings.opc_endpoint, machines=[m.equipment for m in machines], speed=speed)
    async with server:
        while True:
            await asyncio.sleep(1.0)
            for sim in sims:
                await sim.step(1.0 * speed)
