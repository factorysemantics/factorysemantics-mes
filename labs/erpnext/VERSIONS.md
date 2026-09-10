# What the live ERPNext test runs against

`docker-compose.yml` pins images by digest, so the digests are the truth and
this file is the readable half of it. **Change both together**, and put the
run that proved the new pin green in the pull request.

| Service | Image | Version | Digest |
|---|---|---|---|
| ERPNext / Frappe | `frappe/erpnext` | **v15.120.0** (released 2026-09-02) | `sha256:ea656debafa8a60971d3c6e3cce65579e73793dc0b448154ec875f2f285ab7da` |
| Database | `mariadb` | 10.6 | `sha256:1e008264230a4ac642f1c1165ab8856ff0c4e72f362200e13a3b814e3cc05695` |
| Redis (cache and queue) | `redis` | 6.2-alpine | `sha256:d0c875bdacfb5c4d2c2d9124de3f53cee1dc9ceff8936bd459fabc135cb33015` |

Digests read from Docker Hub on 2026-09-09. They are multi-architecture
index digests; the workflow runs on `ubuntu-latest`, so what is actually
tested is linux/amd64.

## Why these

**v15.120.0, not v15 and not v16.** `v15` is a moving tag and would make this
test mean something different every week. v15.120.0 was the newest v15 release
that had had a week to settle — v15.121.0 through .2 went out in the two days
before this was pinned. **ERPNext v16 is not tested by this job at all**, and
the connector therefore claims nothing about it.

**MariaDB 10.6 and Redis 6.2** are what frappe_docker's own reference compose
used for v15. They are the versions ERPNext is developed against; picking
newer ones here would test our guesses about ERPNext rather than the
connector.

## What one run costs

See the timings recorded in `.github/workflows/erpnext-live.yml`. The
site install is most of it, and it is why the job is not on every pull
request.
