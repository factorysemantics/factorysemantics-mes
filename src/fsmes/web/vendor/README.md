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
| `loaders/GLTFLoader.js` | three.js r185 (npm `three@0.185.1`) | MIT | `97642d720f16cc9a0c9844934198e4d0c023bea8e89576d0f7545d03b2d103d2` |
| `utils/BufferGeometryUtils.js` | three.js r185 (npm `three@0.185.1`) | MIT | `5c552223a9309883743b80538d6e9cdb45e3227f30d3ec56fb2c39b46e78d595` |
| `utils/SkeletonUtils.js` | three.js r185 (npm `three@0.185.1`) | MIT | `b1632a703206c3d830de9fcbe515696770d04b71a15ee6b50afa6d2c3298c86f` |

Downloaded 2026-08-17 from:

- `https://unpkg.com/three@0.185.1/build/three.module.min.js`
- `https://unpkg.com/three@0.185.1/build/three.core.min.js`
- `https://unpkg.com/three@0.185.1/examples/jsm/controls/OrbitControls.js`

`three.module.min.js` is a thin re-export of `three.core.min.js` and expects it as
a sibling file — keep the pair together. `OrbitControls.js` imports the bare
specifier `three`, which the import map in `line3d.html` points at
`three.module.min.js`.

`loaders/GLTFLoader.js` and the two `utils/` files were added 2026-11-02, so the
line view can draw a plant's own machine models (GLB files in its pack's
`models/` folder; see the README's "Your own machine models"). Downloaded the
same way, from the same release - `OrbitControls.js` fetched again that day
hashed exactly as in the table above:

- `https://unpkg.com/three@0.185.1/examples/jsm/loaders/GLTFLoader.js`
- `https://unpkg.com/three@0.185.1/examples/jsm/utils/BufferGeometryUtils.js`
- `https://unpkg.com/three@0.185.1/examples/jsm/utils/SkeletonUtils.js`

They keep upstream's folder layout because `GLTFLoader.js` imports
`../utils/BufferGeometryUtils.js` and `../utils/SkeletonUtils.js` by relative
path; the files are unedited. The import map maps
`three/addons/loaders/GLTFLoader.js` to `loaders/GLTFLoader.js`. The loader
fetches only the URLs the line view gives it, all of them `/line/models/` on the
plant's own server.

Upstream project and full licence text: https://github.com/mrdoob/three.js
(MIT, © 2010-2026 three.js authors).

## Verifying

```powershell
Get-ChildItem -Recurse *.js | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash.ToLower() + "  " + $_.Name }
```

## What must not happen here

Nothing in this folder may reach out to the network at runtime, and nothing else
in the app may start depending on a package manager. If a future feature needs a
library that cannot be vendored as a single readable file, that is a decision to
make on purpose, not one to arrive at by adding a `<script src="https://...">`.
