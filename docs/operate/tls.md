# TLS in front of the API

*How-to. The MES does not terminate TLS. Here is the reverse proxy that does,
in one config each, and what the API needs from it.*

## Why there is nothing to configure in the MES

`fsmes run-api` is [uvicorn](https://www.uvicorn.org/) serving the FastAPI
app on `MES_API_HOST:MES_API_PORT`, plain HTTP, bound to `127.0.0.1` by
default. There is no certificate setting and no HTTPS listener, and there is
not going to be one: every plant already has a way it issues certificates,
and a second one that only this MES knows about is a second one nobody
renews. Put a reverse proxy in front and give it the certificate your plant
already uses.

The [security page](security.md) has the rest of the posture. In short: bind
the API to localhost, let the proxy be the only thing listening on the
network, and do not expose the MCP server at all.

## What the API needs from the proxy

Three headers, and one of them matters more than the others.

| Header | Why |
|---|---|
| `X-Forwarded-Proto` | tells the app the browser is on HTTPS. The session cookie is marked `Secure` only when this says `https`, so **without it the dashboard's session cookie is sent back in clear** the first time anyone follows an `http://` link |
| `X-Forwarded-For` | the client's real address, for anything that logs who called |
| `Host` | passed through unchanged, so the app sees the name the browser used |

uvicorn reads the forwarded headers by default, **but only from a proxy on
`127.0.0.1`**. If the proxy runs on the same box as the MES — which is the
usual shape, and the one this page assumes — that is already right and there
is nothing to set.

If the proxy is on a *different* machine, uvicorn will ignore its headers
until you say which address to trust:

```bash
FORWARDED_ALLOW_IPS=10.0.0.5 fsmes run-api
```

Name the proxy's address. `FORWARDED_ALLOW_IPS=*` trusts anything that can
reach the port, which means anyone who can reach the port can claim to be on
HTTPS and can claim to be any client address.

The MES builds no absolute URLs of its own — every link, redirect and
dashboard `fetch` is a root-relative path — so it does not need to be told
its public name or scheme. It does **not** support being mounted under a path
prefix: serve it at the root of a name (`mes.plant.example`), not at
`example/mes/`.

## Caddy

Two lines, and it gets the certificate itself. This is the shortest path if
the name resolves publicly or you have an internal ACME service:

```caddy
mes.plant.example {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy sets `X-Forwarded-Proto`, `X-Forwarded-For` and `X-Forwarded-Host` on
its own. With a certificate your plant's PKI issued, point at it instead:

```caddy
mes.plant.example {
    tls /etc/ssl/plant/mes.crt /etc/ssl/plant/mes.key
    reverse_proxy 127.0.0.1:8000
}
```

## nginx

The repository already ships an nginx config for Compose
(`docker/nginx.conf`), plain HTTP on port 8000. This is the same thing with
TLS in front:

```nginx
server {
    listen 443 ssl;
    server_name mes.plant.example;

    ssl_certificate     /etc/ssl/plant/mes.crt;
    ssl_certificate_key /etc/ssl/plant/mes.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # the one that matters

        # The dashboards poll; long-lived connections should not be cut short.
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name mes.plant.example;
    return 301 https://$host$request_uri;
}
```

Then bind the API to localhost only, which is already the default:

```bash
MES_API_HOST=127.0.0.1 fsmes run-api
```

In Compose the API service is started with `--host 0.0.0.0` so the proxy
container can reach it; there, the thing that must not be published to the
host is the API's own port.

## What changes behind the proxy, and what does not

**The web screens** are unaffected. Every page fetches from root-relative
paths, so they follow whatever name and scheme the browser used.

**Signing in** gets safer, and that is the reason to bother: with
`X-Forwarded-Proto: https` arriving, the session cookie is issued `Secure`
as well as `HttpOnly` and `SameSite=Lax`. Over plain HTTP the `Secure` flag
is deliberately not set — a `Secure` cookie on `http://127.0.0.1:8000` is a
cookie the browser drops, and the laptop path has to keep working.

**API clients** are unaffected: `POST /auth/login` and
`Authorization: Bearer <token>` work the same over HTTPS.

**The MCP server** is a separate process on its own port (8310 by default)
and is *not* behind this proxy. It has no authentication of its own, only a
host allowlist. Do not put it on the network — see
[security](security.md). If an agent must reach it from elsewhere, that is a
tunnel to localhost, not a proxy rule.

**Nothing about production numbers changes.** TLS is transport; the MES
records the same things either way.

## Checking it

From the plant PC:

```bash
curl -sS https://mes.plant.example/health
curl -sS -D- -o/dev/null -X POST https://mes.plant.example/auth/login \
     -H 'content-type: application/json' \
     -d '{"code":"SCOTT","password":"..."}' | grep -i set-cookie
```

The `set-cookie` line must contain `Secure`, `HttpOnly` and `SameSite=lax`.
If `Secure` is missing, the proxy is not sending `X-Forwarded-Proto`, or it
is sending it from an address uvicorn has not been told to trust — the two
failures look identical from outside, and both are fixed above.

!!! warning "Change the default passwords first"
    Putting TLS in front of an install that still has the lab defaults
    protects the password on the wire and nothing else. The API logs a
    warning at start-up when a well-known password is still in use; see
    [security](security.md).

## See also

- [Security](security.md) — the threat model this page sits inside
- [Deploy](deploy.md) — Compose and systemd, including the bundled nginx
- [Running beside your existing MES](first-plant.md)
