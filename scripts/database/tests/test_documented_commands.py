"""Every command the runbooks tell an operator to type must be accepted by the
tool's own argument parser (no database). Catches documentation drift such as a
subcommand option written before its subcommand."""
from __future__ import annotations

import contextlib
import importlib
import io
from pathlib import Path
import re
import shlex
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))
DOCUMENTS = [REPO / "docs" / "production-readiness" / "database-separation.md",
             REPO / "docs" / "production-readiness" / "database-recovery.md",
             TOOLS / "README.md"]
PARSED = {"recovery", "roles", "cutover", "preflight", "monitor", "pitr", "scheduled_backup"}
COMMAND = re.compile(r"(?:^|\s)python3?\s+(?:-\S+\s+)*(?:\S*/)?scripts/database/([a-z_]+)\.py(\s.*)?$")


def documented_commands(document: Path) -> list[tuple[str, list[str], str]]:
    commands = []
    for block in re.findall(r"```(?:bash|sh)?\n(.*?)```", document.read_text(encoding="utf-8"), re.S):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            line = re.sub(r"\s+#\s.*$", "", line.strip())                       # trailing comment
            match = COMMAND.search(line)
            if not match or match.group(1) not in PARSED:
                continue
            arguments = re.sub(r"\$\([^)]*\)", "X", match.group(2) or "")       # $(date ...)
            arguments = re.sub(r"<[^<>]*>", "1", arguments)                     # <placeholder>
            commands.append((match.group(1), shlex.split(arguments), line))
    return commands


class DocumentedCommandTests(unittest.TestCase):
    def test_every_documented_command_parses(self):
        seen = 0
        for document in DOCUMENTS:
            for tool, arguments, line in documented_commands(document):
                with self.subTest(document=document.name, command=line[:120]):
                    with contextlib.redirect_stderr(io.StringIO()) as err:
                        try:
                            importlib.import_module(tool).parser().parse_args(arguments)
                        except SystemExit:
                            self.fail(f"{document.name}: {tool}.py rejects: {line}\n{err.getvalue().strip()}")
                    seen += 1
        self.assertGreaterEqual(seen, 20)


if __name__ == "__main__":
    unittest.main()
