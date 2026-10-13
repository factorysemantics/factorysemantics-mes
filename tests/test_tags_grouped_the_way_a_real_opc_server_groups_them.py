"""Several machines under one node, which is how brownfield servers look.

The twin's own simulator puts every machine straight under Objects/, one node
per machine. Kepware and Ignition do not: tags are organised by channel,
device and folder, and two machines on the same PLC share one device node. A
plant whose shape says so has to be served that way, or the look-alike plant
teaches the wrong address space — and the address space is the part a site
integrator checks first.

What is proven here is the address space the replay server builds and the
browse path the agent walks to read it, against the product's own generator.
What is not proven is a live KEPServerEX, which nobody on this project has.
"""

import asyncio
import json
from pathlib import Path

import pytest

from fsmes.config import Settings
from fsmes.integrations.opc import csv_replay
from fsmes.integrations.opc.tag_map import load_manifest, load_tag_map

REPO = Path(__file__).resolve().parents[1]
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"

# Who sits where. Two machines share PLC_02, which is the case that a map with
# one node per machine cannot describe at all.
GROUPS = {
    "LD01": ["Fillers", "PLC_01"],
    "RD01": ["Fillers", "PLC_02"],
    "WASH01": ["Fillers", "PLC_02"],
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    from fsmes.sim.generate import generate

    out = tmp_path_factory.mktemp("grouped_out")
    generate(REPO / "labs" / "kepsim" / "line.json", out, write_docs=False)
    return out


@pytest.fixture()
def grouped_map(tmp_path) -> Path:
    data = json.loads(KEPSIM_MAP.read_text(encoding="utf-8"))
    for machine in data["machines"]:
        group = GROUPS.get(machine["equipment"])
        if group:
            machine["group"] = group
    path = tmp_path / "tag_map_grouped.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def test_a_map_that_says_nothing_about_grouping_still_means_one_node_per_machine():
    for spec in load_tag_map(KEPSIM_MAP):
        assert spec.group == ()
        assert spec.browse_path == (spec.object,), "a direct child of Objects/"


def test_a_machine_in_a_folder_is_browsed_through_that_folder(grouped_map):
    machines = {m.equipment: m for m in load_tag_map(grouped_map)}
    assert machines["RD01"].group == ("Fillers", "PLC_02")
    assert machines["RD01"].browse_path == ("Fillers", "PLC_02", "RD")
    # The manifest and the replay CSVs are keyed by `object`, not by the path:
    # which folder a server happens to use says nothing about which machine a
    # row belongs to.
    assert machines["RD01"].object == "RD"
    assert machines["PAL01"].browse_path == ("Palletiser",), "ungrouped machines unmoved"


def test_the_replay_server_serves_two_machines_under_one_device_node(grouped_map, generated, tmp_path):
    """The whole point: RD and WASH hang off the same PLC_02 node, and every
    tag is reachable by walking the path the agent walks."""
    settings = Settings(tag_map_file=grouped_map, replay_dir=generated,
                        opc_endpoint="opc.tcp://127.0.0.1:48401/freeopcua/server/")
    machines = load_tag_map(grouped_map, load_manifest(generated))

    async def browse() -> dict:
        server, *_ = await csv_replay.build_server(settings, machines, generated)
        idx = await server.get_namespace_index(settings.opc_namespace)
        objects = server.nodes.objects
        seen = {}
        for spec in machines:
            # Exactly the expression the agent uses to find a machine.
            obj = await objects.get_child([f"{idx}:{part}" for part in spec.browse_path])
            tags = {}
            for tag in spec.tags:
                tags[tag] = await obj.get_child(f"{idx}:{tag}")
            seen[spec.equipment] = (obj, tags)
        # Two machines, one parent node, and the parent was created once.
        rd_parent = await seen["RD01"][0].get_parent()
        wash_parent = await seen["WASH01"][0].get_parent()
        assert rd_parent == wash_parent
        fillers = await objects.get_child(f"{idx}:Fillers")
        children = {(await c.read_browse_name()).Name for c in await fillers.get_children()}
        assert children == {"PLC_01", "PLC_02"}
        plc2 = {(await c.read_browse_name()).Name for c in await rd_parent.get_children()}
        assert plc2 == {"RD", "Washer"}
        # And nothing grouped is left lying directly under Objects/ as well.
        top = {(await c.read_browse_name()).Name for c in await objects.get_children()}
        assert "RD" not in top and "Washer" not in top and "LD" not in top
        assert {"Fillers", "QI", "Refill", "Palletiser"} <= top
        return seen

    seen = asyncio.run(browse())
    assert set(seen) == {m.equipment for m in machines}
    assert "State" in seen["WASH01"][1]
