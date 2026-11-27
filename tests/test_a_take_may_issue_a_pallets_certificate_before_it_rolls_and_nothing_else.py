"""A take may issue a pallet's certificate before it rolls, and nothing else.

A pallet's first certificate of analysis is issued only when somebody asks,
and no screen asks; class 4.5 needs two to open. `[plant]
pallet_certificates_before_roll` lists the pallets whose certificates the
recorder issues off camera, on the take's own scratch plant, by the plant's
own `POST /coa/pallet/{serial}`, signed in as the scene's user, so the
plant's permission check decides. The folder's README says so. These tests
hold that: what the key accepts, what it refuses, the one request it sends,
and what happens when the plant says no. A tiny local HTTP server stands in
for the plant; no plant and no browser.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "05-05-the-certificate-of-analysis.toml"


class Plant(BaseHTTPRequestHandler):
    """Signs anyone in, and issues a certificate for every pallet but one."""

    asked: ClassVar[list[tuple[str, str, str | None]]] = []

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/auth/login":
            self.asked.append(("POST", self.path, json.loads(body)["code"]))
            self._answer(200, {"ok": True}, cookie="session=signed-in")
            return
        self.asked.append(("POST", self.path, self.headers.get("Cookie")))
        if self.path == "/coa/pallet/PL1-000000999":
            self._answer(404, {"detail": "no pallet PL1-000000999"})
            return
        serial = self.path.rsplit("/", 1)[-1]
        self._answer(200, {"document": f"COA-PALLET-{serial}", "issued_revision": 1})

    def _answer(self, code: int, body: dict, cookie: str | None = None) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", f"{cookie}; Path=/")
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def plant():
    Plant.asked = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Plant)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_plant_with_no_pallets_named_issues_no_certificate(runner):
    spec = runner.plant_defaults({"pack": "labs/cutlery"})

    assert spec["pallet_certificates_before_roll"] == []


@pytest.mark.parametrize(
    "named",
    ["PL1-000000001", ["PL1-000000001", ""], ["PL1/../users"], ["PL1-000000001?x=1"], ["PL1#1"], [7]],
)
def test_anything_but_a_list_of_plain_pallet_serials_is_refused(runner, named):
    with pytest.raises(SystemExit, match="pallet_certificates_before_roll"):
        runner.plant_defaults({"pack": "labs/cutlery", "pallet_certificates_before_roll": named})


def test_it_signs_in_as_the_scenes_user_and_asks_the_plant_to_issue_each_named_pallet_once(runner, plant):
    issued = runner.issue_pallet_certificates(plant, ["PL1-000000001", "PL1-000000013"], "WALK-SUP", "pw")

    assert [i["document"] for i in issued] == ["COA-PALLET-PL1-000000001", "COA-PALLET-PL1-000000013"]
    assert Plant.asked == [
        ("POST", "/auth/login", "WALK-SUP"),
        ("POST", "/coa/pallet/PL1-000000001", "session=signed-in"),
        ("POST", "/coa/pallet/PL1-000000013", "session=signed-in"),
    ]


def test_a_pallet_the_plant_refuses_stops_the_take_with_the_plants_own_answer(runner, plant):
    with pytest.raises(SystemExit, match=r"refused.*PL1-000000999.*404.*no pallet PL1-000000999"):
        runner.issue_pallet_certificates(plant, ["PL1-000000999"], "WALK-SUP", "pw")


def test_the_folders_readme_says_which_certificates_were_issued_off_camera_and_by_whom(runner):
    scene = runner.load_scene(SCENE)
    page = runner.readme(
        scene,
        {
            "said": runner.script_lines(scene),
            "plant": "cutlery",
            "version": "0.4.2",
            "badge": "pre-alpha · simulated plant · v0.4.2",
            "base": scene["base"],
            "commit": "abc1234",
            "scene_file": SCENE.relative_to(REPO).as_posix(),
            "command": "python scripts/record-scene.py …",
            "when": "2026-11-27 10:00 CST",
            "gif_seconds": 30.0,
            "gif_bytes": 4_000_000,
            "gif_setting": "10 fps, 1100 wide",
        },
    )

    issued = scene["plant"]["pallet_certificates_before_roll"]
    assert issued
    for serial in issued:
        assert f"the certificate of pallet `{serial}` was issued, off camera, through the plant's own API" in page
    assert "as `WALK-SUP`" in page
