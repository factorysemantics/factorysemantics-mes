# Cutting a release

*How-to, for a maintainer. One screen. The long version is the header comment
of `.github/workflows/release.yml`, which is the only place the reasoning
lives.*

A release is a tag. Everything else — the wheel, the sdist, PyPI, the
container image, the signature, the SBOM, the GitHub Release — is made by
`.github/workflows/release.yml` from that tag, in one run, and a human
approves the part that goes public.

## The pull request, before any tag

One pull request, nothing in it but the release:

1. `__version__` in `src/fsmes/__init__.py`. Everything reads it: the wheel,
   the sdist, the image tag, `fsmes --version`. Nothing else carries the
   number by hand.
2. `version:` and `date-released:` in `CITATION.cff`. The workflow refuses a
   release whose citation disagrees with the wheel.
3. `CHANGELOG.md`: `## [Unreleased]` becomes `## [<version>] — <date>`, with
   a fresh empty `## [Unreleased]` above it, and the link definitions at the
   bottom brought forward.
4. A page, `docs/releases/<version>.md`, in the shape of the one before it,
   and a row in `docs/releases/index.md`. **That page is the GitHub Release
   body** — see below — so write it for a stranger: what they type, what
   they will see, what is simulated, what needs a key.
5. A section in `docs/operate/upgrade.md` naming the migrations since the
   last release and the command that applies them. Try it on a database made
   by the release that is on PyPI now; if you cannot try it, the page says so
   rather than promising.
6. The status line in `README.md`, and the dates in
   `docs/operate/compatibility.md` for the rows whose evidence still holds.

Then **rehearse it**: `gh workflow run release.yml --ref <branch>`. The same
file runs, every job that produces evidence runs, and the three jobs that
make something public are skipped by `if:` — `pypi`, `image-push` and
`github-release`. A rehearsal is how a broken artifact hand-off, a bumped
action or a release body GitHub will not accept is found on a Tuesday instead
of on release night, when the wheel is already on PyPI and cannot be taken
back.

## The tag

After the pull request is merged, from the merge commit:

```bash
git tag -a v<version> -m "<version>"
git push <remote> v<version>
```

The run then builds, checks the tag against the wheel and the wheel against
`CITATION.cff`, runs the demo from the built wheel, builds the container for
both platforms — and stops at the `pypi` environment, which is reviewer-gated.
A person approves it. That approval is the decision to publish this version.
Only after it do the bytes reach PyPI, then ghcr.io (pushed, signed with
cosign, with an SBOM), and only then is the GitHub Release cut.

If a tag already exists on that number from an abandoned attempt, delete it
everywhere it is before tagging — `git tag -d v<version>` locally and
`git push <remote> :refs/tags/v<version>` on any remote that has it.

## The release body

`.github/scripts/release_notes.sh <version>` composes it from
`docs/releases/<version>.md`: it drops the "not yet tagged" box, fills in the
release date, turns the page's relative documentation links into
`docs.factorysemantics.com` URLs, and links the full changelog at the tag.
Its stdout is the body and nothing else; every diagnostic goes to stderr.

A GitHub Release body may be **125,000 characters**. The script asserts that
and fails loudly, and it is run twice on purpose: once in `github-release`,
which publishes it, and once in `build`, which composes it and throws it away.
The second one is the one a rehearsal reaches. It exists because the body used
to be the version's section of `CHANGELOG.md`, and for 0.3.0 that section is
256,271 characters — a 422 from the release API with the version already live
on two registries, in the one job downstream of both of them.

## Afterwards

```bash
pip index versions factorysemantics-mes
docker pull ghcr.io/factorysemantics/fsmes:<version>
```

Then the things no workflow touches: the demo site's version line, and
anything that quotes the version in prose.
