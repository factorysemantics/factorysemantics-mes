"""The line view draws a machine from a model file in the plant's own pack.

A pack may carry `models/` beside `line_layout.json`: `<kind>.glb` for every
machine of a kind, `<CODE>.glb` for one machine, or a file the layout names
for a station. The layout answer gives each station the URL of its model, or
`null` for the built-in shape it has always had. The route serves only that
folder, only lowercase `.glb` names, only up to 8 MB.

The model in these tests is the 1 m cube `tests/make_cube_glb.py` writes: the
only model in the repository, because no artwork of anyone else's belongs
here.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from fsmes.config import get_settings
from fsmes.seed_kepsim import seed_kepsim_line
from fsmes.services import line as line_service

REPO = Path(__file__).resolve().parents[1]
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"

_spec = importlib.util.spec_from_file_location("make_cube_glb", Path(__file__).parent / "make_cube_glb.py")
make_cube_glb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_cube_glb)


@pytest.fixture()
def pack(session, monkeypatch, tmp_path):
    """The six-station line, with a pack folder whose layout file is not drawn."""
    seed_kepsim_line(session, tag_map=KEPSIM_MAP)
    session.flush()
    folder = tmp_path / "pack"
    folder.mkdir()
    monkeypatch.setattr(get_settings(), "line_layout_file", folder / "line_layout.json")
    return folder


def _models(pack: Path, *names: str) -> None:
    for name in names:
        make_cube_glb.write_cube(pack / "models" / name)


def _stations(client) -> dict[str, dict]:
    return {s["code"]: s for s in client.get("/line/layout", params={"line": "SIMLINE"}).json()["stations"]}


# ------------------------------------------------------------------ the layout


def test_a_pack_with_a_filler_model_draws_the_filler_from_it_and_the_washer_as_before(client, pack):
    _models(pack, "filler.glb")
    stations = _stations(client)
    assert stations["FILL01"]["model"] == "/line/models/filler.glb"
    assert stations["WASH01"]["model"] is None


def test_a_machine_s_own_model_beats_the_model_for_its_kind(client, pack):
    _models(pack, "filler.glb", "FILL01.glb")
    assert _stations(client)["FILL01"]["model"] == "/line/models/fill01.glb"


def test_a_model_the_layout_names_beats_the_machine_s_own_and_its_kind_s(client, pack):
    _models(pack, "filler.glb", "FILL01.glb", "big-filler.glb")
    (pack / "line_layout.json").write_text(json.dumps(
        {"lines": {"SIMLINE": {"stations": {"FILL01": {"model": "big-filler.glb"}}}}}), encoding="utf-8")
    assert _stations(client)["FILL01"]["model"] == "/line/models/big-filler.glb"


def test_a_model_the_layout_names_that_is_not_there_falls_through_to_the_kind_s(client, pack):
    _models(pack, "filler.glb")
    (pack / "line_layout.json").write_text(json.dumps(
        {"lines": {"SIMLINE": {"stations": {"FILL01": {"model": "missing.glb"}}}}}), encoding="utf-8")
    assert _stations(client)["FILL01"]["model"] == "/line/models/filler.glb"


def test_a_pack_without_a_models_folder_gives_every_station_the_built_in_shape(client, pack):
    stations = _stations(client)
    assert len(stations) == 6
    assert [s["model"] for s in stations.values()] == [None] * 6


def test_a_model_over_the_size_cap_is_not_offered_to_the_view(client, pack):
    _models(pack, "filler.glb")
    with (pack / "models" / "filler.glb").open("ab") as fh:
        fh.truncate(9 * 1024 * 1024)
    assert _stations(client)["FILL01"]["model"] is None


# ------------------------------------------------------------------ the route


def test_the_route_serves_the_cube_as_a_binary_gltf_cached_for_an_hour(client, pack):
    _models(pack, "filler.glb")
    reply = client.get("/line/models/filler.glb")
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "model/gltf-binary"
    assert "max-age=3600" in reply.headers["cache-control"]
    assert reply.content == make_cube_glb.cube_glb()
    assert reply.content[:4] == b"glTF"


def test_the_route_serves_a_machine_s_own_model_by_its_lowercase_name(client, pack):
    _models(pack, "FILL01.glb")
    assert client.get("/line/models/fill01.glb").status_code == 200


@pytest.mark.parametrize("name", [
    "..%2Fx.glb",           # out of the folder, encoded so the client keeps it
    "%2E%2E%2Fsecret.glb",
    "x.gltf",               # another extension
    "line_layout.json",     # another file in the pack
    "X.glb",                # outside the lowercase pattern
    "fill er.glb",
    "missing.glb",          # inside the pattern, not on disk
])
def test_the_route_refuses_anything_but_a_lowercase_glb_in_the_models_folder(client, pack, name):
    _models(pack, "x.glb", "secret.glb")
    (pack / "line_layout.json").write_text("{}", encoding="utf-8")
    (pack.parent / "secret.glb").write_bytes(make_cube_glb.cube_glb())
    (pack / "models" / "x.gltf").write_text("{}", encoding="utf-8")
    assert client.get(f"/line/models/{name}").status_code == 404


def test_the_route_refuses_an_absolute_path_and_a_name_with_a_path_in_it(pack):
    _models(pack, "x.glb")
    assert line_service.model_file(str(pack / "models" / "x.glb")) is None
    assert line_service.model_file("../x.glb") is None
    assert line_service.model_file("models/x.glb") is None
    assert line_service.model_file("x.glb") is not None


def test_the_route_refuses_a_nine_megabyte_model(client, pack):
    _models(pack, "filler.glb")
    with (pack / "models" / "filler.glb").open("ab") as fh:
        fh.truncate(9 * 1024 * 1024)
    assert client.get("/line/models/filler.glb").status_code == 404


def test_the_route_is_not_public(anon, pack):
    _models(pack, "filler.glb")
    assert anon.get("/line/models/filler.glb").status_code == 401


# ------------------------------------------------------------------ the fixture


def test_the_cube_is_a_well_formed_glb_whose_lengths_add_up():
    import struct

    data = make_cube_glb.cube_glb()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert (magic, version, length) == (0x46546C67, 2, len(data))
    json_length, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A and json_length % 4 == 0
    gltf = json.loads(data[20:20 + json_length])
    bin_length, bin_type = struct.unpack_from("<II", data, 20 + json_length)
    assert bin_type == 0x004E4942
    assert gltf["buffers"][0]["byteLength"] == bin_length
    assert 28 + json_length + bin_length == len(data)
