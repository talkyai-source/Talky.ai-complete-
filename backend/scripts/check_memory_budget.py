"""Read-only audit of systemd/container caps. Exit 1 until the budget fits.

No environment files, services, Docker daemon or database are accessed.
Uncapped/ambiguous units are unresolved allocation, never zero-cost services.
"""
from __future__ import annotations

import argparse
import configparser
import json
from pathlib import Path
import re

import yaml


def _mib(value):
    if value is None or isinstance(value, bool):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*", str(value), re.I)
    if not match:
        return None
    amount, suffix = match.groups()
    power = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}[suffix.lower()]
    result = float(amount) * 1024 ** power / 1024 ** 2
    return result if result > 0 else None


def inspect_budget(units_dir, compose_path, *, host_mib=3600, headroom_mib=768):
    if not 0 < headroom_mib < host_mib:
        raise ValueError("Headroom must be positive and smaller than host memory")
    allocations, unbounded, issues = {}, [], []
    units = sorted(Path(units_dir).glob("*.service"))
    if not units:
        issues.append("No service units found")
    for path in units:
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(path, encoding="utf-8")
        maximum = _mib(config.get("Service", "MemoryMax", fallback=None))
        high = _mib(config.get("Service", "MemoryHigh", fallback=None))
        if maximum is None:
            unbounded.append(path.name)
        else:
            allocations[path.name] = maximum
        if high is None:
            issues.append(f"{path.name}: MemoryHigh is missing or unbounded")
        elif maximum is not None and high > maximum:
            issues.append(f"{path.name}: MemoryHigh exceeds MemoryMax")
    compose = yaml.safe_load(Path(compose_path).read_text(encoding="utf-8"))
    services = compose.get("services", {}) if isinstance(compose, dict) else {}
    if not isinstance(services, dict) or not services:
        raise ValueError("Compose must contain service allocations")
    for name, service in sorted(services.items()):
        maximum = _mib(service.get("mem_limit") if isinstance(service, dict) else None)
        label = f"container:{name}"
        if maximum is None:
            unbounded.append(label)
        else:
            allocations[label] = maximum
    configured = sum(allocations.values())
    available = host_mib - headroom_mib
    if configured > available:
        issues.append("Configured hard caps exceed the host budget after headroom")
    return {
        "read_only": True, "host_mib": host_mib, "headroom_mib": headroom_mib,
        "available_mib": available, "configured_mib": configured,
        "allocations_mib": allocations, "unbounded": sorted(unbounded),
        "issues": issues, "fits": not issues and not unbounded,
        "runtime_capacity_verified": False,
    }


def main(argv=None):
    backend = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host-mib", type=int, default=3600,
                        help="Conservative 3.8 GB host budget, in MiB (default 3600)")
    parser.add_argument("--headroom-mib", type=int, default=768)
    args = parser.parse_args(argv)
    try:
        report = inspect_budget(backend / "systemd", backend / "deploy/infra/compose.prod.yml",
                                host_mib=args.host_mib, headroom_mib=args.headroom_mib)
    except (ValueError, OSError, configparser.Error, yaml.YAMLError) as exc:
        print(json.dumps({"read_only": True, "fits": False, "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["fits"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
