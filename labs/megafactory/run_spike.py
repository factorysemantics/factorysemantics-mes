"""Run the mega-factory scale spike as one ephemeral scored run.

    python3 labs/megafactory/run_spike.py [--speed 60] [--out scorecard.json]

Regenerates line.json's CSVs and tag_map.json fresh each time (both are
derived, not hand-edited), then calls the same ephemeral-run machinery every
scored run uses: fresh database, ports from the 8100-8199/4900-4999 reserved
ranges, torn down after scoring. Never registered in plants.toml - this is a
one-off measurement, not a new plant.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from fsmes.sim.generate import generate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    import make_tag_map

    tag_map = make_tag_map.build(HERE / "line.json")
    (HERE / "tag_map.json").write_text(json.dumps(tag_map, indent=2) + "\n", encoding="utf-8")
    print(f"tag_map.json: {len(tag_map['machines'])} machines")

    out_dir = HERE / "out"
    result = generate(HERE / "line.json", out_dir)
    print(f"generated: {len(result.get('stations', []))} stations -> {out_dir}")

    from seed_breadth import seed_breadth

    from fsmes.sim.runner import scored_run

    cfg = {
        "replay_dir": str(out_dir),
        "tag_map": str(HERE / "tag_map.json"),
        "init": str(HERE / "init.py"),
        # scored_run overwrites these via env after plant_env() reads them;
        # plant_env indexes cfg["api_port"]/cfg["opc_port"] unconditionally,
        # so placeholders are required even though the ephemeral run ignores
        # them in favour of its own freshly-allocated ports.
        "api_port": 0,
        "opc_port": 0,
    }
    card = scored_run("megafactory", cfg, REPO, speed=args.speed,
                      line_json=HERE / "line.json", post_boot=seed_breadth,
                      keep_evidence=True, echo=print)

    print(json.dumps(card, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        print(f"scorecard written to {args.out}")


if __name__ == "__main__":
    main()
