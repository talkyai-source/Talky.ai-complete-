"""Check changed executable backend/app Python lines against coverage.py XML."""
from __future__ import annotations

import argparse
import ast
import dis
import io
from pathlib import Path, PurePosixPath
import re
import subprocess
import tokenize
from types import CodeType
import xml.etree.ElementTree as ET

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff(text):
    result = {}
    filename = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            filename = None
        elif line.startswith("+++ "):
            value = line[4:].rstrip("\t")
            if value == "/dev/null":
                filename = None
            elif value.startswith("b/backend/app/") and value.endswith(".py"):
                filename = value[2:]
                if ".." in PurePosixPath(filename).parts:
                    raise ValueError("Unsafe diff path")
            elif "backend/app/" in value and value.endswith('.py"'):
                # Quoted/escaped Git paths need human review; never silently
                # certify a changed file that this bounded parser cannot map.
                raise ValueError("Unsupported quoted Python path")
            else:
                filename = None
        elif filename and line.startswith("@@"):
            match = _HUNK.match(line)
            if not match:
                raise ValueError("Malformed diff hunk")
            start = int(match[1])
            count = int(match[2]) if match[2] is not None else 1
            if count:
                result.setdefault(filename, set()).update(range(start, start + count))
    return result


def _class_path(filename, sources, repo_root):
    if not filename or ".." in PurePosixPath(filename.replace("\\", "/")).parts:
        raise ValueError("Unsafe coverage filename")
    candidates = set()
    for source in sources or ["."]:
        candidate = Path(source) / filename
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        try:
            relative = candidate.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if relative.startswith("backend/app/") and relative.endswith(".py"):
            candidates.add(relative)
    if len(candidates) != 1:
        raise ValueError("Unmapped or ambiguous coverage filename")
    return candidates.pop()


def _executable_lines(path, repo_root):
    """Compile metadata only: do not import or execute application code.

    Logical statement starts normalize multiline expressions just as coverage
    source reports do. AST docstrings are excluded; nested code objects are
    included so an entirely unmeasured new function cannot become zero lines.
    """
    try:
        path = path.resolve()
        path.relative_to(repo_root)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Oversized changed Python source")
        source = path.read_bytes()
        tree = ast.parse(source, filename=str(path))
        code = compile(tree, str(path), "exec", dont_inherit=True, optimize=0)
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or not node.body:
                continue
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.update(range(first.lineno, first.end_lineno + 1))
        logical_lines = {}
        start = None
        ignored = {tokenize.ENCODING, tokenize.INDENT, tokenize.DEDENT,
                   tokenize.NL, tokenize.COMMENT, tokenize.ENDMARKER}
        for token in tokenize.tokenize(io.BytesIO(source).readline):
            if token.type == tokenize.NEWLINE:
                if start is not None:
                    logical_lines.update((number, start) for number in range(start, token.end[0] + 1))
                start = None
            elif token.type not in ignored and start is None:
                start = token.start[0]
        lines, pending = set(), [code]
        while pending:
            current = pending.pop()
            lines.update(logical_lines.get(number, number) for _, number in dis.findlinestarts(current)
                         if number > 0 and number not in docstrings)
            pending.extend(value for value in current.co_consts if isinstance(value, CodeType))
        return lines - docstrings
    except (OSError, SyntaxError, tokenize.TokenError, ValueError) as exc:
        raise ValueError("Changed source cannot be independently inspected") from exc


def check_coverage(path, changed_lines, *, repo_root, threshold=80.0):
    repo_root = Path(repo_root).resolve()
    if not 0 <= threshold <= 100:
        raise ValueError("Coverage threshold outside 0..100")
    try:
        if Path(path).stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Oversized coverage XML")
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError("Unreadable coverage XML") from exc
    classes = root.findall(".//class")
    if root.tag != "coverage" or not classes:
        raise ValueError("Missing coverage classes")
    sources = [node.text or "." for node in root.findall("./sources/source")]
    covered_files = {}
    for node in classes:
        filename = _class_path(node.get("filename"), sources, repo_root)
        if filename in covered_files:
            raise ValueError("Duplicate coverage class")
        lines = {}
        for line in node.findall("./lines/line"):
            try:
                number, hits = int(line.attrib["number"]), int(line.attrib["hits"])
            except (KeyError, ValueError) as exc:
                raise ValueError("Invalid coverage line") from exc
            if number <= 0 or hits < 0 or number in lines:
                raise ValueError("Invalid duplicate coverage line")
            lines[number] = hits > 0
        covered_files[filename] = lines
    total = covered = 0
    violations = []
    for filename, changed in sorted(changed_lines.items()):
        if filename not in covered_files:
            violations.append(f"Changed file missing from coverage: {filename}")
            continue
        independent = _executable_lines(repo_root / filename, repo_root) & changed
        missing = independent - set(covered_files[filename])
        if missing:
            violations.append(f"Changed executable lines missing from coverage: {filename}: "
                              + ",".join(map(str, sorted(missing))))
        executable = (set(covered_files[filename]) & changed) | independent
        total += len(executable)
        covered += sum(covered_files[filename].get(number, False) for number in executable)
        missed = sorted(number for number in executable if not covered_files[filename].get(number, False))
        if missed:
            print(f"Uncovered changed lines: {filename}: {','.join(map(str, missed))}")
    percent = 100.0 * covered / total if total else 100.0
    if percent < threshold:
        violations.append(f"Changed-line coverage {percent:.2f}% is below {threshold:g}%")
    return {"ok": not violations, "covered": covered, "total": total,
            "percent": percent, "violations": violations}


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, encoding="utf-8", errors="strict", timeout=20)
    if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
        raise ValueError("Git diff unavailable or oversized")
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=80.0)
    args = parser.parse_args(argv)
    try:
        base = _git(args.repo, "rev-parse", "--verify", "--end-of-options", args.base_ref + "^{commit}").strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise ValueError("Unresolved base commit")
        diff = _git(args.repo, "-c", "core.quotePath=false", "diff", "--no-ext-diff", "--no-renames",
                    "--unified=0", base + "...HEAD", "--", "backend/app")
        result = check_coverage(args.coverage, parse_diff(diff), repo_root=args.repo, threshold=args.threshold)
        for violation in result["violations"]:
            print(violation)
        print(f"Backend changed-line coverage: {result['covered']}/{result['total']} executable lines "
              f"({result['percent']:.2f}%), required {args.threshold:g}%")
        return 0 if result["ok"] else 1
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Changed-line coverage refused: readable base/diff/coverage required.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
