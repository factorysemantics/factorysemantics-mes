#!/usr/bin/env bash
# The body of the GitHub Release, composed the same way whether it is being
# rehearsed or published. Prints it on stdout.
#
# WHY THIS IS NOT JUST THE CHANGELOG SECTION. It was, until 0.3.0: the job read
# the version's section out of CHANGELOG.md and handed it straight to the
# release API. That release's section is 256,271 characters and a GitHub
# release body may be 125,000, so the publish would have failed with a 422 --
# in `github-release`, which runs *after* PyPI and ghcr.io have published, and
# therefore at the one moment in this workflow where a failure cannot be
# retried from the top. It was found by a `workflow_dispatch` rehearsal on
# 2026-10-12, before the tag existed, which is what decision 0018 and this
# file's own header are about.
#
# So the body is the release's own page under docs/releases/ -- written before
# the tag precisely so that it is reviewed rather than typed on release night,
# and the thing docs/releases/index.md has always said the body is taken from.
# The changelog section is the fallback for a version with no page, and the
# full changelog is linked either way.
#
# Stdout is the body and nothing else. Every diagnostic goes to stderr, so a
# notice about a missing page cannot end up inside what gets published.
#
# Usage: release_notes.sh <version>
set -euo pipefail

v="${1:?usage: release_notes.sh <version>}"
page="docs/releases/${v}.md"
changelog_url="https://github.com/factorysemantics/factorysemantics-mes/blob/v${v}/CHANGELOG.md"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT

if [ -f "$page" ]; then
    # Three things the page carries for its readers on the documentation site
    # and must not carry into a release body.
    #
    # 1. The "Not yet tagged" box -- a note to the maintainer about a page
    #    written ahead of the tag, untrue of the release somebody is looking at.
    #    It is one blockquote, and a blockquote's lines all start with ">".
    # 2. "Released: *on tagging.*" -- the date is known here: it is today.
    # 3. Relative links between documentation pages. `../operate/upgrade.md`
    #    resolves on the site and is dead in a release body, so each one
    #    becomes the published URL of the same page.
    awk '
      /^> \*\*Not yet tagged\.\*\*/ { skipping = 1 }
      skipping && !/^>/             { skipping = 0 }
      !skipping                     { print }
    ' "$page" \
      | sed -e "s|\*\*Released:\*\* \*on tagging\.\*|**Released:** $(date -u +%Y-%m-%d).|" \
            -e "s|](\.\./\([^)]*\)\.md#|](https://docs.factorysemantics.com/\1/#|g" \
            -e "s|](\.\./\([^)]*\)\.md)|](https://docs.factorysemantics.com/\1/)|g" \
      > "$body"
else
    echo "::notice::no $page, so the release body comes from CHANGELOG.md" >&2
    awk -v v="$v" '$0 ~ "^## \\["v"\\]" {p=1; next} /^## \[/ {if(p) exit} p' CHANGELOG.md > "$body"
fi

if [ ! -s "$body" ]; then
    echo "See CHANGELOG.md." > "$body"
fi

printf '\n---\n\nEvery change in this release: [CHANGELOG](%s).\n' "$changelog_url" >> "$body"

# A release body may be 125,000 characters. Over that the API answers 422 and
# nothing explains why, so the number is asserted here rather than discovered
# there -- and asserted in a step that runs on a dispatch as well as on a tag.
chars="$(wc -m < "$body")"
limit=125000
if [ "$chars" -gt "$limit" ]; then
    echo "::error::the release body for ${v} is ${chars} characters and GitHub allows ${limit}. Shorten docs/releases/${v}.md." >&2
    exit 1
fi
echo "release body for ${v}: ${chars} of ${limit} characters" >&2

cat "$body"
