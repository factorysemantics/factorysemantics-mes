"""The image's build context carries every path the wheel is told to carry.

`pyproject.toml` force-includes sixteen paths - six config files and the ten
that are the two lab packs - so that the wheel works on a machine with no
checkout. Hatchling copies whatever is on disk when the wheel is built, and
inside the image the wheel is built by `pip install .` at `/app`. So a forced
path that `docker/Dockerfile` does not copy is not a missing file in the image:
it is an image that cannot be built at all.

That is exactly what happened. #163 added the ten pack paths to the table on
2026-10-12; the Dockerfile copied `src/` and `config/` and nothing else; and
the first build after it - the release rehearsal at 12:30 that afternoon, which
would otherwise have been a tag - stopped at

    FileNotFoundError: Forced include not found:
    /app/labs/multiplant/bottling/floor.json

after PyPI would already have had the release. Nothing was red before then
because no job in `ci.yml` built the image. One now does, and this test is the
cheap half of the same check: it reads the table out of `pyproject.toml` and
the `COPY` lines out of the Dockerfile and compares two lists of paths, in a
second, from a checkout, with no Docker anywhere.

It is a string check and it says so: it proves the two lists agree, not that
the image builds. The `image` job in `ci.yml` proves that, on every pull
request, and takes minutes.
"""

from __future__ import annotations

import shlex
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO / "docker" / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"


@pytest.fixture(autouse=True)
def only_from_a_checkout():
    if not (REPO / "pyproject.toml").is_file():
        pytest.skip("installed package: pyproject.toml and docker/ only exist in a checkout")


# --------------------------------------------------------------- the two lists


def forced_includes() -> dict[str, str]:
    """`[tool.hatch.build.targets.wheel.force-include]`, source -> wheel path.

    The keys are paths relative to the directory holding `pyproject.toml`,
    which inside the image is the working directory `pip install .` runs in.
    """
    table = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return table["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def instructions() -> list[tuple[str, list[str]]]:
    """The Dockerfile, as (instruction, arguments), continuations joined.

    Deliberately small: this repository's Dockerfile is a dozen lines of plain
    instructions with no build arguments and no stages, and a parser that
    understood more would be a second thing to trust.
    """
    joined: list[str] = []
    buffer = ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        joined.append(buffer + line)
        buffer = ""
    assert not buffer, "docker/Dockerfile ends on a line continuation"
    out = []
    for line in joined:
        word, _, rest = line.partition(" ")
        out.append((word.upper(), shlex.split(rest)))
    return out


def copies_into_the_image() -> dict[str, str]:
    """Which repository path lands where in the image, for every `COPY`.

    A key is a path in the build context; its value is the absolute path in the
    image that path's tree is rooted at. Docker's rules, the two that this
    Dockerfile uses: a directory's *contents* are copied into the destination,
    and a file goes to `destination/its-name` when the destination is a
    directory (which is every destination ending in `/`, and any destination at
    all when there is more than one source).
    """
    workdir = PurePosixPath("/")
    landing: dict[str, str] = {}
    for word, args in instructions():
        if word == "WORKDIR":
            workdir = PurePosixPath(args[0])
            continue
        if word != "COPY":
            continue
        args = [a for a in args if not a.startswith("--")]
        *sources, destination = args
        into_a_directory = destination.endswith("/") or len(sources) > 1
        at = workdir / destination.rstrip("/")
        for source in sources:
            if (REPO / source).is_dir():
                landing[source] = str(at)
            elif into_a_directory:
                landing[source] = str(at / PurePosixPath(source).name)
            else:
                landing[source] = str(at)
    return landing


def where_the_image_puts(path: str, landing: dict[str, str]) -> str | None:
    """Where `path` ends up in the image, named or beneath a copied directory."""
    if path in landing:
        return landing[path]
    for source, at in landing.items():
        if path.startswith(f"{source}/"):
            return f"{at}/{path[len(source) + 1:]}"
    return None


# ----------------------------------------------------------------- the .dockerignore


def exclusions() -> list[str]:
    """The `.dockerignore`'s patterns, in order, exceptions included."""
    return [line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def left_out_of_the_context(path: str) -> str | None:
    """The pattern that excludes `path`, if one does - else None.

    Docker matches a pattern against the path and against every directory above
    it, so `**/out/` drops a file three levels inside an `out`. Understood here:
    a leading `**/` (any depth), a trailing `/` (a directory), `!` (an
    exception, last one wins), and the shell wildcards in a single name. That
    is every form this repository's `.dockerignore` uses; a pattern using more
    than that would need this function to grow with it.
    """
    answer = None
    ancestors = []
    parts = path.split("/")
    for i in range(len(parts)):
        ancestors.append("/".join(parts[: i + 1]))
    for pattern in exclusions():
        negated = pattern.startswith("!")
        candidate = pattern.lstrip("!").strip("/")
        if candidate.startswith("**/"):
            tail = candidate[3:]
            hit = any(fnmatchcase(a.split("/")[-1], tail) or fnmatchcase(a, tail)
                      for a in ancestors)
        else:
            hit = any(fnmatchcase(a, candidate) for a in ancestors)
        if hit:
            answer = None if negated else pattern
    return answer


# ----------------------------------------------------------------------- tests


def test_every_path_the_wheel_force_includes_is_copied_into_the_image():
    landing = copies_into_the_image()
    forced = forced_includes()
    missing = [path for path in forced if where_the_image_puts(path, landing) is None]
    assert not missing, (
        f"{len(missing)} of {len(forced)} paths in "
        f"[tool.hatch.build.targets.wheel.force-include] are not copied by "
        f"docker/Dockerfile: {missing}. `pip install .` inside the image reads that "
        f"table and stops at the first one it cannot find, so the image cannot be "
        f"built until each is COPYd. Add a COPY line; do not copy labs/ whole - "
        f"labs/multiplant/*/out/ is generated replay.")


def test_a_forced_path_lands_where_the_wheel_build_will_look_for_it():
    """`/app/<the path as pyproject writes it>`, or hatchling will not see it."""
    landing = copies_into_the_image()
    workdir = next(args[0] for word, args in instructions() if word == "WORKDIR")
    wrong = {}
    for path in forced_includes():
        at = where_the_image_puts(path, landing)
        wanted = str(PurePosixPath(workdir) / path)
        if at != wanted:
            wrong[path] = (at, wanted)
    assert not wrong, (
        f"docker/Dockerfile copies these to the wrong place for the wheel build, "
        f"which resolves a forced include against {workdir}: "
        + "; ".join(f"{p} lands at {got}, wanted {wanted}"
                    for p, (got, wanted) in wrong.items()))


def test_nothing_the_dockerfile_copies_is_missing_from_the_repository():
    absent = [source for source in copies_into_the_image() if not (REPO / source).exists()]
    assert not absent, (
        f"docker/Dockerfile copies {absent}, which is not in the repository. "
        f"A COPY of a path that is not there fails the build.")


def test_nothing_the_dockerfile_copies_is_excluded_from_the_build_context():
    """A `.dockerignore` rule that drops a copied path breaks the build."""
    dropped = {source: left_out_of_the_context(source)
               for source in copies_into_the_image()
               if left_out_of_the_context(source)}
    assert not dropped, (
        ".dockerignore excludes paths docker/Dockerfile copies: "
        + "; ".join(f"{p} by `{pattern}`" for p, pattern in dropped.items()))


def test_the_generated_replay_stays_out_of_the_build_context():
    """The reason the packs are listed file by file instead of `COPY labs`.

    `labs/multiplant/bottling/out/` is 9.7 MB of CSV generated from the seed in
    `line.json` on first start. It must not be sent to the builder and it must
    not be in the image.
    """
    for path in ("labs/multiplant/bottling/out/line_data.csv",
                 "labs/multiplant/machining/out/line_data.csv",
                 "labs/multiplant/.data/bottling/fsmes.db"):
        assert left_out_of_the_context(path), (
            f".dockerignore lets {path} into the build context. Generated line "
            f"data and plant state are regenerated from a seed and never shipped.")
    landing = copies_into_the_image()
    copied_whole = [source for source in landing
                    if source in ("labs", "labs/multiplant") or source.endswith("/out")]
    assert not copied_whole, (
        f"docker/Dockerfile copies {copied_whole}, which carries the generated "
        f"replay into the image. Copy the pack files by name.")


def test_the_image_is_built_on_every_pull_request():
    """The job whose absence is why the Dockerfile went five hours unbuilt.

    #163 was green on every check and left the image unbuildable, because the
    only job that built it ran on a tag. If this job is taken out of `ci.yml`,
    the string check above is all that is left and the next thing to break the
    image will be found on release night again.
    """
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "\n  image:\n" in ci, (
        "ci.yml has no `image` job. The Dockerfile is then built by nothing "
        "until a tag or a release rehearsal, which is how 2026-10-12 happened.")
    assert "docker/Dockerfile" in ci, "ci.yml's image job does not build docker/Dockerfile"
    assert "push: false" in ci, "ci.yml's image job must push nothing"
