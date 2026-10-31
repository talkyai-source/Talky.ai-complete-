"""Read-only marker-aware check of declared production requirements."""
import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import json
import platform
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

parser = argparse.ArgumentParser()
parser.add_argument("requirements", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--expected-target", type=Path)
args = parser.parse_args()
environment = default_environment()
environment["extra"] = ""
rows = []
for line in args.requirements.read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    requirement = Requirement(line)
    row = {"requirement": str(requirement), "active": not requirement.marker or requirement.marker.evaluate(environment)}
    if row["active"]:
        try:
            distribution = metadata.distribution(requirement.name)
            row.update(installed=distribution.version,
                       satisfied=requirement.specifier.contains(distribution.version, prereleases=True))
        except metadata.PackageNotFoundError:
            row.update(installed=None, satisfied=False)
    rows.append(row)
imports = {}
for package, module_name in (("urllib3", "urllib3"), ("PyJWT", "jwt")):
    module = importlib.import_module(module_name)
    path = Path(module.__file__).resolve()
    imports[package] = {"metadata_version": metadata.version(package), "imported_version": module.__version__,
                        "module_path": str(path), "metadata_path": str(metadata.distribution(package).locate_file(""))}
    if args.expected_target:
        imports[package]["from_expected_target"] = path.is_relative_to(args.expected_target.resolve())
failures = [row for row in rows if row["active"] and not row["satisfied"]]
result = {"requirements_path": str(args.requirements.resolve()),
          "requirements_sha256": hashlib.sha256(args.requirements.read_bytes()).hexdigest(),
          "python": sys.version, "platform": platform.platform(),
          "markers": environment, "declared_count": len(rows),
          "active_count": sum(bool(row["active"]) for row in rows),
          "failures": failures, "imports": imports, "requirements": rows,
          "scope": "All declared active production requirement version specifiers; not a fresh resolver/transitive-environment or provider qualification claim."}
args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
valid_imports = not args.expected_target or all(row["from_expected_target"] for row in imports.values())
print(json.dumps({"declared": result["declared_count"], "active": result["active_count"],
                  "failures": failures, "imports": imports, "valid_import_paths": valid_imports}, indent=2))
raise SystemExit(0 if not failures and valid_imports else 1)
