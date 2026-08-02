"""Helpers for tests that assert on the SHAPE of source code.

Several invariants in this codebase are structural rather than behavioural
("no endpoint may write a constant outcome", "there must be exactly one
definition of minutes-used"). Those are asserted by reading the source.

The trap such tests fall into: a source scan that reads comments will
re-detect whatever bug it documents. The first version of
`test_billing_minutes_agree_with_gate` failed on four counts, every one of
them matching the comment explaining the fix rather than any code.

Not collected by pytest — the leading underscore keeps it out of `test_*.py`.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def code_only(src: str) -> str:
    """Source with docstrings and `#` comments blanked out.

    Docstrings are located via `ast` rather than by stripping triple-quoted
    strings, because the SQL in these modules is triple-quoted and must
    survive. Line numbering is preserved so failures can report a usable
    location.
    """
    lines = src.splitlines()
    blank: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        head = body[0]
        if (
            isinstance(head, ast.Expr)
            and isinstance(head.value, ast.Constant)
            and isinstance(head.value.value, str)
        ):
            for ln in range(head.lineno, (head.end_lineno or head.lineno) + 1):
                blank.add(ln)

    cuts: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            cuts[row] = min(cuts.get(row, col), col)

    out = []
    for i, line in enumerate(lines, 1):
        if i in blank:
            out.append("")
        elif i in cuts:
            out.append(line[: cuts[i]])
        else:
            out.append(line)
    return "\n".join(out)


def read(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


def code(rel: str) -> str:
    return code_only(read(rel))


def app_sources() -> list[tuple[str, str]]:
    """(relative posix path, comment-free source) for every module under app/."""
    out: list[tuple[str, str]] = []
    for path in (BACKEND / "app").rglob("*.py"):
        rel = str(path.relative_to(BACKEND)).replace("\\", "/")
        try:
            out.append((rel, code_only(path.read_text(encoding="utf-8"))))
        except SyntaxError:  # pragma: no cover - would fail the import gate first
            continue
    return out


def function_body(src: str, name: str) -> str:
    """Source of one function, sliced at its DEFINITION.

    Splitting on the bare name grabs dispatch-table entries and docstring
    mentions hundreds of lines away; that mistake made two separate tests
    fail against perfectly correct code.
    """
    for marker in ("async def " + name, "def " + name):
        if marker in src:
            after = src.split(marker, 1)[1]
            for stop in ("\n@", "\ndef ", "\nasync def ", "\n    def ", "\n    async def "):
                after = after.split(stop, 1)[0]
            return after
    raise AssertionError(f"no definition of {name!r} found")
