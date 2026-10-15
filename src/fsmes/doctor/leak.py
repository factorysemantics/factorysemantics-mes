"""Read the finished file back, and refuse to write it if a plant's words are in it.

The scrub in `scrub.py` is a promise about how values are turned into shapes.
This is the check that the promise held — the same arrangement the shape skill
uses, and the same code: `skills/plant-from-your-mes/scripts/leakcheck.py` is
loaded out of whatever copy of the skill this installation carries and asked
the question it was written to answer, *does any identifying value from the
source appear in this output*.

Loaded rather than copied because there must be exactly one leak check. The
script is standalone by design — it has to run on a plant PC with no `fsmes`
installed, on the standard library alone — so it is not an importable part of
this package; `fsmes.skills.read_file` finds the copy inside the wheel, or the
one in a checkout, and it is executed into a module of its own here.

**No check, no file.** If this installation carries no copy of the skill, the
check cannot run, and `fsmes doctor` writes nothing and says so. A file whose
first line promises a leak check and did not run one is worse than no file.

### What the values are

The shape skill samples the customer's database. `doctor` never reads a plant's
business tables, so its sources are different and are collected as it goes
(`Seen`): every raw line of every log it read, every string in every pack file
it opened, the ownership record, this machine's host name and OS user, the
database URL and the configured secrets. Those are the values that must not be
in the file. The file is then scanned for all of them, and the verdict goes on
its first screen with the total, so a reader can see how much was checked.

### The one documented exception

**A plant's name is in the file**, because every command the file prints —
`fsmes demo <plant> stop` — needs it, and a file whose commands cannot be
typed is a file nobody uses. Plant names are therefore passed as vocabulary
rather than as values, exactly as the skill passes its own words, and the file
says so in its own first lines. Nothing else of a plant's words is in it.
"""

from __future__ import annotations

import os
import re
import types
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: What `doctor` writes when the check cannot be run at all.
NO_CHECK = (
    "This installation carries no copy of the plant-from-your-mes skill, so the "
    "leak check cannot run — and nothing is written without it. `fsmes skills "
    "unpack plant-from-your-mes .` says the same thing: a release that ships "
    "`doctor` ships the check. Reinstall the package and run this again."
)

#: Tokens worth comparing, pulled out of raw text. Deliberately greedy about
#: punctuation inside a token - `WO-ACME-4711` and `SECRET_TAG_XYZ` are each
#: one value, not two and three.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{2,}")


class Leak(Exception):
    """Raised instead of writing the file."""


@lru_cache(maxsize=1)
def leakcheck() -> types.ModuleType | None:
    """The shape skill's own leak check, as a module, or `None` if absent."""
    from fsmes import skills

    text = skills.read_file("plant-from-your-mes", "scripts/leakcheck.py")
    if not text:
        return None
    module = types.ModuleType("fsmes.doctor._leakcheck_from_the_skill")
    module.__dict__["__file__"] = "skills/plant-from-your-mes/scripts/leakcheck.py"
    try:
        exec(compile(text, "leakcheck.py", "exec"), module.__dict__)
    except Exception:                                           # pragma: no cover
        return None
    return module


@dataclass
class Seen:
    """Every value this run read, before anything was shaped.

    Fed as `doctor` gathers rather than collected afterwards, because the only
    honest list of what might have leaked is the list of what was actually
    read. `words` is the vocabulary side: plant names, and this product's own
    words, which are forgiven.
    """

    values: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)

    def value(self, text) -> None:
        """One value, whole. Shortest useful unit of a leak."""
        said = str(text).strip()
        if said:
            self.values.add(said)

    def text(self, text) -> None:
        """A blob of raw text: the whole of it, and every token in it.

        Both, because a leak can be a free-text note entire or one word
        inside a log sentence, and the check is cheap.
        """
        said = str(text).strip()
        if not said:
            return
        if len(said) <= 4000:
            self.values.add(said)
        self.values.update(_TOKEN.findall(said))

    def structure(self, value, *, depth: int = 0) -> None:
        """Every string and key in a parsed JSON or TOML document."""
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self.value(key)
                self.structure(item, depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.structure(item, depth=depth + 1)
        elif isinstance(value, str):
            self.text(value)
        elif value is not None and not isinstance(value, bool):
            self.value(value)

    def ours(self, word) -> None:
        """A word that is this file's own: a plant's name, chiefly."""
        said = str(word).strip().lower()
        if said:
            self.words.add(said)

    def machine(self) -> None:
        """This machine's own identifying words, which are a plant's too.

        A host name and an OS user are in the ownership record and in half
        the paths on the box, and both are things somebody owns.
        """
        import getpass
        import socket

        # `socket.getfqdn` is not here on purpose: it is a reverse DNS lookup,
        # it took five seconds of a five-second budget on the machine this was
        # written on, and the part of it that is not already `gethostname` is a
        # domain this file never prints anyway.
        for probe in (socket.gethostname, getpass.getuser):
            try:
                self.value(probe())
            except Exception:                                   # pragma: no cover
                continue
        for key in ("USER", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "HOSTNAME",
                    "LOGNAME", "HOME", "USERPROFILE"):
            got = os.environ.get(key)
            if got:
                self.value(got)
                self.value(Path(got).name)

    def secrets(self, settings) -> None:
        """Configured passwords and keys, so that a leak of one is a refusal.

        None of these is ever written on purpose. They are here because the
        check that matters is the one that catches what was not on purpose.
        """
        for name in dir(settings):
            if not any(part in name for part in ("password", "secret", "token", "api_key")):
                continue
            with_value = getattr(settings, name, None)
            if isinstance(with_value, str) and len(with_value.strip()) >= 4:
                self.value(with_value)


def vocabulary(seen: Seen) -> frozenset[str]:
    """The words the check forgives: ours, the skill's, units, plant names.

    And the composites of our own words - `opc-agent.jsonl`, `uvicorn.error`,
    `seconds_per_row` - which `scrub.carried_whole` is the single rule for.
    They are added from the values actually read rather than enumerated,
    because nobody can enumerate every logger name a library invents; what is
    enumerated is the ~400 words a piece of one has to be made of.
    """
    from fsmes.doctor import scrub

    ours = frozenset(w.lower() for w in seen.words)
    forgiven = set(ours)
    # The exception classes the scrub decided to carry whole this run, and
    # only those: `scrub.exception_type` is the single rule for which names
    # reach the file, so it is the single rule for which are forgiven here.
    forgiven |= {w.lower() for w in scrub.carried()}
    forgiven |= {v.lower() for v in seen.values
                 if scrub.carried_whole(v, extra=ours)}
    return frozenset(scrub.vocabulary() | forgiven)


#: A value made of nothing but letters. Only these can be judged by whether
#: they appear as a word, because anything with a digit, a dash or a dot in it
#: is an identity on its own and is compared as it stands.
_LETTERS_ONLY = re.compile(r"^[A-Za-z]+$")


@lru_cache(maxsize=1)
def the_files_own_words() -> frozenset[str]:
    """Every word this file writes of its own accord.

    The file is mostly its own prose: what each section is, what the product
    does not do yet, the three questions at the end. A plant's log line is
    full of English too - the templates of our own event messages, the
    sentences our own `detail=` fields carry - and `Seen` collects every word
    of it, so a value read from a log and a word of this file's own writing
    collide on nearly every run: `history`, `production`, `books`, `writes`.
    None of them is evidence of anything. The file would have said them if it
    had read nothing at all.

    So the words of the modules that write the file are read out of their own
    source and handed to the check as vocabulary, the same way the shape skill
    hands it `OWN_WORDS`. A plant's word is never in this product's source,
    which is what makes the source safe to use as the list.

    The skill's `is_identity` then forgives one of these words only where the
    value is a single bare word: anything with a space, a digit or a separator
    in it is still compared, so `SECRET_TAG_XYZ` and `WO-LEAK-1` are still
    looked for. What this does forgive is a plant's one-word name that happens
    to be a word this product also writes - a machine called `Budget` - which
    is the same trade-off the skill has always made with its own vocabulary,
    and the reason the file says in its first lines that it names the plants
    in this home.
    """
    import inspect

    from fsmes import migration
    from fsmes.doctor import gather, render, scrub

    words: set[str] = set()
    for module in (render, migration, gather, scrub):
        try:
            source = inspect.getsource(module)
        except (OSError, TypeError):                            # pragma: no cover
            continue
        words |= {w.lower() for w in re.findall(r"[A-Za-z]{3,}", source)}
    return frozenset(words)


def never_a_word_of_its_own(value: str, text: str) -> bool:
    """Is this value absent from the file as a word, in longer words only?

    `scan` asks whether the value appears anywhere in the text at all, so
    `live` is found inside `alive` and `reach` inside `reachable` - both of
    them this file's own words. A value that appears nowhere in the file at a
    word boundary is not in the file: nobody reading it can tell that the
    letters of somebody's word are inside one of ours, because they are not
    that word.

    Only a value of letters alone is judged this way. Everything with a digit
    or a separator in it - every order code, every tag, every path - is
    compared as it stands.
    """
    if not _LETTERS_ONLY.match(value):
        return False
    return re.search(rf"\b{re.escape(value)}\b", text, re.IGNORECASE) is None


def as_the_scrub_leaves_it(value: str, ours: frozenset[str]) -> bool:
    """Would the scrub carry this value through exactly as it found it?

    If it would, the value is not evidence of anything: by the scrub's single
    rule, every piece of it is a word this product itself writes. The keys of
    a log line, its level, its logger, the template of its message, a
    loopback URL and the whole of a log line that happens to contain nothing
    but our own words are all in the file on purpose, and a check that looked
    for them would refuse to write a file on every run.

    This is not the check forgiving itself. A value with a digit in it, a
    timestamp, a number, a path part somebody named, a tag, an order, a
    product, a person: none of those comes back unchanged, because the scrub
    has no rule that would carry them. What comes back unchanged is the ~450
    words in `scrub.OUR_WORDS`, in some arrangement.
    """
    from fsmes.doctor import scrub

    if scrub.carried_whole(value, extra=ours):
        return True
    # `Seen` tokenises raw text, and its tokeniser takes a colon with the
    # word in front of it: `RuntimeError:` off a console traceback is the
    # word the scrub carried, plus the punctuation of the sentence it sits
    # in. Judge the word.
    bare = value.strip(":.,;()[]'\"")
    if bare != value and scrub.carried_whole(bare, extra=ours):
        return True
    shapers = [scrub.scrub_jsonl, scrub.scrub_text, scrub.shape_token]
    if "://" in value:
        shapers.append(scrub.url_shape)
    for shaper in shapers:
        try:
            if shaper(value) == value:
                return True
        except Exception:                                       # pragma: no cover
            continue
    return False


def verdict(text: str, seen: Seen) -> str:
    """The one line that goes at the top of the file, or `Leak` and no file.

    The sentence is the skill's own (`verdict_line`), so a reader who has seen
    a `plant-shape.toml` recognises it.
    """
    module = leakcheck()
    if module is None:
        raise Leak(NO_CHECK)
    ours = vocabulary(seen)
    # Dropped before the scan rather than forgiven inside it: the skill's
    # `is_identity` forgives a known word only when the value is a single
    # bare word, and half of what the scrub carries through verbatim is a
    # sentence of them.
    values = sorted(v for v in seen.values
                    if not as_the_scrub_leaves_it(v, ours)
                    and not never_a_word_of_its_own(v, text))
    found, considered = module.scan(text, values, ours | the_files_own_words())
    if found:
        shown = ", ".join(repr(v) for v in found[:5])
        raise Leak(
            f"{len(found)} value(s) this installation read appear in the file: {shown}"
            + (" …" if len(found) > 5 else "")
            + f"\n{considered} values were checked. The file was NOT written. "
              "This is a fault in the scrub, not "
              "in your plant: please report it with the names of the keys involved and "
              "nothing else, and do not edit the check."
        )
    return "leak check: " + module.verdict_line(len(values), considered, found)
