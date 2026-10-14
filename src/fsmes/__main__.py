"""`python -m fsmes`, for the machine where `fsmes` is not on PATH.

On 2026-10-13 the first person to install 0.3.0 on Windows from a plain
`py -m pip install` could not run it: pip put `fsmes.exe` in a Scripts
folder that was not on PATH - the same reason `pip` itself had to be
reached as `py -m pip` - and PowerShell answered *fsmes is not recognized
as an internal or external command*. There was no second way in: the
package had no `__main__.py`, so `py -m fsmes` printed nothing either.

This is that second way in. It is not a second code path: it calls
`fsmes.cli.run`, the same function `[project.scripts]` points the console
script at, so the Windows console's encoding is retuned and a
misconfiguration still arrives as one sentence rather than a traceback.
`python -m fsmes --version` is therefore the answer to *is it installed at
all*, on a machine where PATH says no.
"""

from fsmes.cli import run

if __name__ == "__main__":
    run()
