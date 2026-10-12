# One image, five roles: api, opc-sim, opc-agent, erp-sync, mock-erp.
# The compose file picks the role via the container command.
FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY config ./config

# THE TWO PLANT PACKS, BECAUSE `pip install .` BELOW CANNOT BUILD A WHEEL
# WITHOUT THEM.
#
# `pyproject.toml` force-includes ten paths under `labs/multiplant/` so that the
# wheel carries the bottling and machining packs (#163, 2026-10-12), and
# hatchling copies whatever is on disk at build time. With those paths outside
# the image's build context the install stopped dead:
#
#   FileNotFoundError: Forced include not found:
#   /app/labs/multiplant/bottling/floor.json
#
# which is the red `image` job in the release rehearsal of 2026-10-12 12:30
# (run 38278749824) - the first run to build this file since #163, because
# until the `image` job was added to `ci.yml` nothing but a tag or a dispatch
# ever built the image.
#
# The same ten paths, written out, and not `COPY labs ./labs`: `labs/multiplant/
# */out/` is generated replay - 9.7 MB for bottling alone, generated from the
# seed in `line.json` on first start - and has no business in an image. The
# repository's `.dockerignore` keeps it out of the context as well.
#
# These lines and that table are two lists of the same ten paths, which is two
# lists that can drift. `tests/test_the_docker_image_carries_the_packs.py` reads
# both and fails the day a forced path is not copied here - from the checkout,
# in a second, with no Docker.
COPY labs/multiplant/bottling/plant.toml \
     labs/multiplant/bottling/line.json \
     labs/multiplant/bottling/floor.json \
     labs/multiplant/bottling/tag_map.json \
     ./labs/multiplant/bottling/
COPY labs/multiplant/bottling/masterdata ./labs/multiplant/bottling/masterdata
COPY labs/multiplant/machining/plant.toml \
     labs/multiplant/machining/line.json \
     labs/multiplant/machining/floor.json \
     labs/multiplant/machining/tag_map.json \
     ./labs/multiplant/machining/
COPY labs/multiplant/machining/masterdata ./labs/multiplant/machining/masterdata

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000
CMD ["fsmes", "run-api", "--host", "0.0.0.0"]
