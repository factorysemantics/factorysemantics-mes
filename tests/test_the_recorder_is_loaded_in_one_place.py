"""The recorder is loaded in one place: the `runner` fixture in conftest.py.

Structure report 2026-11-18, finding 3 and fold-together T1: "22 test files
now load it by path, each with its own `runner` fixture. One `conftest.py`
fixture would do." There were 31 by the time it was done. Every copy was
the same six lines with its own module name, and a change to how the
recorder is loaded (a path, a flag, an environment it needs) had 31 places
to be forgotten in.

The fixture stays module-scoped, as every copy was: each test file gets its
own fresh recorder, so a test that patches it touches no other file.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _loads_the_recorder_by_path(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "spec_from_file_location":
            return True
    return False


def test_no_test_file_keeps_its_own_runner_fixture():
    own = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "runner" and _loads_the_recorder_by_path(node):
                own.append(path.name)
    assert not own, f"these load the recorder themselves; use conftest's `runner`: {own}"


def test_conftest_holds_the_one_runner_and_it_is_module_scoped():
    source = (TESTS / "conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    runners = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "runner"]
    assert len(runners) == 1, "conftest.py has no `runner` fixture"
    (runner,) = runners
    assert _loads_the_recorder_by_path(runner)
    assert "record-scene.py" in source
    scopes = [kw.value.value for d in runner.decorator_list if isinstance(d, ast.Call)
              for kw in d.keywords if kw.arg == "scope"]
    assert scopes == ["module"], "one recorder per test file, as every copy had: a patch must not leak"
