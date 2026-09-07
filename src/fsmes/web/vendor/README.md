# Vendored browser libraries

The dashboard is deliberately built without a node toolchain: no npm, no bundler,
no CDN. It has to still start on a plant PC in ten years, on a machine that may
never have had internet. The 3D line view needs a WebGL library, so the library
is committed here as plain ES modules and loaded straight by the browser through
an import map in `line.html`.

That means these files are **read, never built**. Nothing generates them and
nothing minifies them further. Upgrading is: download the new versions, replace
the files, update this table, reload the page.

## What is here

| File | Version | Licence | SHA-256 |
|---|---|---|---|
| `three.module.min.js` | three.js r185 (npm `three@0.185.1`) | MIT | `86bcee248b64f44bcfc23c331ae74619061957d59cab040171dcb6fb5900beb6` |
| `three.core.min.js` | three.js r185 (npm `three@0.185.1`) | MIT | `05b2609338c76cd65daf74f3ac515bc9a5045e1b3b33edc07d8c9bd55250fa90` |
| `OrbitControls.js` | three.js r185 (npm `three@0.185.1`) | MIT | `faabb4e8dfd9235ee4a9fd7c9a3d75f90f1689dbd4944bd6fd32117dacec5f93` |

Downloaded 2026-08-17 from:

- `https://unpkg.com/three@0.185.1/build/three.module.min.js`
- `https://unpkg.com/three@0.185.1/build/three.core.min.js`
- `https://unpkg.com/three@0.185.1/examples/jsm/controls/OrbitControls.js`

`three.module.min.js` is a thin re-export of `three.core.min.js` and expects it as
a sibling file — keep the pair together. `OrbitControls.js` imports the bare
specifier `three`, which the import map in `line.html` points at
`three.module.min.js`.

Upstream project and full licence text: https://github.com/mrdoob/three.js
(MIT, © 2010-2026 three.js authors).

## Verifying

```powershell
Get-ChildItem *.js | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash.ToLower() + "  " + $_.Name }
```

## What must not happen here

Nothing in this folder may reach out to the network at runtime, and nothing else
in the app may start depending on a package manager. If a future feature needs a
library that cannot be vendored as a single readable file, that is a decision to
make on purpose, not one to arrive at by adding a `<script src="https://...">`.
