"""Static tenant-predicate inventory and query-identity ratchet (QA F2-03).

Only Python source under backend/app is scanned. No application or migration is
imported, and no database is opened. This is a conservative structural guard,
not a SQL parser or proof of authorization: complex/dynamic SQL remains review
debt, and parameter provenance still needs behavioral tenant-isolation tests.

The baseline separates unresolved existing debt from individually justified
cross-tenant exceptions. Its identities include query text, function, path and
bypass context; unresolved SQL additionally pins the containing module AST,
including query registries and helpers outside the executor function.
Deleting one violation cannot pay for a new one, and duplicate queries count.
"""

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import runpy


_METHODS = {"execute", "executemany", "fetch", "fetchrow", "fetchval", "fetchmany", "fetchall", "prepare", "cursor"}
_SQL_START = re.compile(r"^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|SET|RESET|SHOW|ALTER|CREATE|DROP|TRUNCATE|BEGIN|COMMIT|ROLLBACK)\b", re.I)
_SQL_PARTS = re.compile(
    r"--[^\n]*|/\*[\s\S]*?\*/|\$(?P<tag>[A-Za-z_][A-Za-z_0-9]*|)\$[\s\S]*?\$(?P=tag)\$"
    r"|'(?:''|\\.|[^'\\])*'|\"(?:\"\"|[^\"])*\""
)
_RELATION = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:ONLY\s+)?"
    r"(?:(?P<schema>[a-z_][\w]*)\.)?(?P<table>[a-z_][\w]*)"
    r"(?:\s+(?:AS\s+)?(?P<alias>[a-z_][\w]*))?", re.I,
)
_KEYWORDS = set("where join left right full inner outer cross on set values returning order group having limit offset union intersect except for using select as from into update delete insert conflict do window fetch tablesample".split())
_TENANT_REF = r"(?:[a-z_][\w]*\.)?tenant_id"
_EQUALITY = re.compile(
    rf"(?<![\w.])(?P<left>{_TENANT_REF}|\$\d+)(?:\s*::\s*uuid)?\s*=\s*"
    rf"(?P<right>{_TENANT_REF}|\$\d+)(?:\s*::\s*uuid)?\b", re.I,
)
_CLAUSE = re.compile(r"\b(WHERE|ON|SELECT|SET|VALUES|RETURNING|HAVING|ORDER|GROUP|LIMIT|OFFSET)\b", re.I)


def _sql_parts(text, *, analysis):
    """Remove comments; keep quoted data out of predicate recognition."""
    def replace(match):
        token = match.group(0)
        if token.startswith(("--", "/*")):
            return " "
        if token.startswith('"'):
            return token[1:-1].replace('""', '"').lower() if analysis else token
        return " __literal__ " if analysis else token
    return _SQL_PARTS.sub(replace, text)


def _normalize(text):
    # Preserve literal values/case in the identity: changing a query's data
    # condition must not reuse the previous query's exception.
    pieces, outside, position = [], "", 0
    for match in _SQL_PARTS.finditer(text):
        outside += text[position:match.start()]
        token = match.group(0)
        if token.startswith(("--", "/*")):
            outside += " "
        else:
            pieces.extend((re.sub(r"\s+", " ", outside), token))
            outside = ""
        position = match.end()
    outside += text[position:]
    pieces.append(re.sub(r"\s+", " ", outside))
    return "".join(pieces).strip()


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    return ast.dump(node, include_attributes=False)


def _resolve(node, values):
    """Return (known SQL template, unresolved); never execute an expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.Name):
        return values.get(node.id, ("{" + node.id + "}", True))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, left_unknown = _resolve(node.left, values)
        right, right_unknown = _resolve(node.right, values)
        return left + right, left_unknown or right_unknown
    if isinstance(node, ast.JoinedStr):
        parts, unknown = [], False
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(value.value)
            else:
                resolved, dynamic = _resolve(value.value, values)
                parts.append(resolved)
                unknown = unknown or dynamic or value.format_spec is not None or value.conversion != -1
        return "".join(parts), unknown
    return "{" + ast.dump(node, include_attributes=False) + "}", True


def _predicate_scope(sql, protected_tables, tenant_tables):
    clean = _sql_parts(sql, analysis=True)
    relations = []
    for match in _RELATION.finditer(clean):
        table = match.group("table").lower()
        if table in protected_tables:
            alias = (match.group("alias") or table).lower()
            relations.append((table, table if alias in _KEYWORDS else alias))
    tables = sorted({table for table, _alias in relations})
    if not tables:
        return tables, False
    # Refuse to infer SQL boolean/scope semantics beyond a single simple DML
    # statement. These forms stay visible as debt rather than being waved through.
    if (any(table not in tenant_tables for table in tables)
            or re.search(r"\b(?:OR|UNION|INTERSECT|EXCEPT|EXISTS|NOT|CASE|COALESCE|NULLIF|LEFT|RIGHT|FULL|OUTER)\b", clean, re.I)
            or len(re.findall(r"\bSELECT\b", clean, re.I)) > 1
            or (not re.match(r"\s*SELECT\b", clean, re.I) and re.search(r"\bSELECT\b", clean, re.I))
            or re.search(r"\)\s*(?:=|<>|!=)|\bIS\s+(?:FALSE|UNKNOWN|DISTINCT\s+FROM)\b|=\s*FALSE\b", clean, re.I)
            or re.match(r"\s*WITH\b", clean, re.I)
            or ";" in clean.rstrip().rstrip(";")):
        return tables, False
    from_clause = re.search(
        r"\bFROM\b(.*?)(?=\b(?:WHERE|GROUP|ORDER|LIMIT|OFFSET|RETURNING|FOR)\b|$)", clean, re.I | re.S,
    )
    if from_clause:
        depth = 0
        for character in from_clause.group(1):
            depth += character == "("
            depth -= character == ")"
            if character == "," and depth == 0:
                # Comma joins are deliberately unresolved, never treated as
                # one tenant-scoped table merely because FROM named it first.
                return tables, False
    aliases = {alias for _table, alias in relations}
    if len(aliases) != len(relations):
        return tables, False
    if re.match(r"\s*INSERT\b", clean, re.I):
        # An INSERT binds the destination tenant instead of filtering existing
        # rows. Only the direct single-row VALUES form is proved here.
        match = re.match(
            r"\s*INSERT\s+INTO\s+(?:public\.)?\w+\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*(?:RETURNING\b[\s\S]*)?$",
            clean.strip().rstrip(";"), re.I,
        )
        if not match or len(relations) != 1:
            return tables, False
        columns = [column.strip().lower() for column in match.group(1).split(",")]
        values = [value.strip() for value in match.group(2).split(",")]
        if "tenant_id" not in columns or len(columns) != len(values):
            return tables, False
        return tables, bool(re.fullmatch(r"\$\d+(?:\s*::\s*uuid)?", values[columns.index("tenant_id")], re.I))

    def reference(value):
        value = value.lower()
        if value.startswith("$"):
            return value
        if "." in value:
            alias = value.split(".")[0]
            return alias if alias in aliases else None
        return next(iter(aliases)) if len(aliases) == 1 else None

    edges, anchors = [], set()
    for match in _EQUALITY.finditer(clean):
        clauses = list(_CLAUSE.finditer(clean, 0, match.start()))
        if not clauses or clauses[-1].group(1).lower() not in {"where", "on"}:
            continue
        prefix = clean[:match.start()]
        parentheses = []
        for token in re.finditer(r"([a-z_][\w]*)\s*\(|[()]", prefix, re.I):
            if token.group(0).endswith("("):
                name = (token.group(1) or "").lower()
                parentheses.append(name not in {"", "where", "on", "and", "or", "not"})
            elif parentheses:
                parentheses.pop()
        if any(parentheses):
            # A function receiving tenant_id=$1 may invert the boolean. Only
            # direct boolean predicates, not arbitrary function arguments,
            # establish scope.
            continue
        following = clean[match.end():].lstrip()
        following = re.sub(r"^(?:\)\s*)+", "", following)
        if following and not re.match(
            r"^(?:;\s*$|(?:AND|JOIN|INNER|CROSS|WHERE|GROUP|ORDER|LIMIT|OFFSET|FETCH|FOR)\b)", following, re.I,
        ):
            continue
        left, right = reference(match.group("left")), reference(match.group("right"))
        if left is None or right is None:
            continue
        if left.startswith("$") and right in aliases:
            anchors.add(right)
        elif right.startswith("$") and left in aliases:
            anchors.add(left)
        elif left in aliases and right in aliases:
            edges.append((left, right))
    while True:
        previous = set(anchors)
        for left, right in edges:
            if left in anchors or right in anchors:
                anchors.update((left, right))
        if previous == anchors:
            break
    return tables, bool(aliases) and aliases <= anchors


class _SourceScanner(ast.NodeVisitor):
    def __init__(self, relative_path, protected_tables, tenant_tables, uncertain_globals, module_identity):
        self.path = relative_path.replace("\\", "/")
        self.protected = set(protected_tables) | set(tenant_tables)
        self.tenant_tables = set(tenant_tables)
        self.names, self.functions, self.findings = [], [], []
        self.values, self.connections = {}, {}
        self.uncertain_globals = uncertain_globals
        self.module_identity = module_identity

    def visit_ClassDef(self, node):
        outer_values, outer_connections = self.values, self.connections
        self.values, self.connections = dict(self.values), dict(self.connections)
        self.names.append(node.name)
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                class_values, class_connections = self.values, self.connections
                # A method's unqualified names do not resolve through its
                # class namespace. Keep ordinary module constants separate.
                self.values, self.connections = outer_values, outer_connections
                self.visit(statement)
                self.values, self.connections = class_values, class_connections
            else:
                self.visit(statement)
        self.names.pop()
        self.values, self.connections = outer_values, outer_connections

    def visit_FunctionDef(self, node):
        saved_values, saved_connections = self.values, self.connections
        self.values, self.connections = dict(self.values), dict(self.connections)
        for name in self.uncertain_globals:
            self.values[name] = ("{rebound-module-name}", True)
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            self.values.pop(arg.arg, None)
            self.connections.pop(arg.arg, None)
        self.names.append(node.name)
        self.functions.append(node)
        for statement in node.body:
            self.visit(statement)
        self.names.pop()
        self.functions.pop()
        self.values, self.connections = saved_values, saved_connections

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.values[target.id] = _resolve(node.value, self.values)
            else:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                        self.values[name.id] = ("{unpacked}", True)

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self.values[node.target.id] = _resolve(node.value, self.values)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
            if isinstance(node.target, ast.Name):
                self.values[node.target.id] = _resolve(node.value, self.values)

    def visit_AugAssign(self, node):
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            previous = self.values.get(node.target.id, ("{unbound}", True))
            value = _resolve(node.value, self.values)
            self.values[node.target.id] = (
                previous[0] + value[0], previous[1] or value[1]
            ) if isinstance(node.op, ast.Add) else ("{augmented}", True)

    def visit_AsyncWith(self, node):
        previous = self.connections
        self.connections = dict(previous)
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and isinstance(item.optional_vars, ast.Name):
                name = _dotted(call.func).split(".")[-1]
                bypass = None
                if name == "acquire_with_tenant":
                    tenant = call.args[1] if len(call.args) > 1 else next(
                        (keyword.value for keyword in call.keywords if keyword.arg == "tenant_id"), None
                    )
                    if isinstance(tenant, ast.Constant) and tenant.value is None:
                        bypass = True
                    elif isinstance(tenant, (ast.Name, ast.Attribute, ast.Constant)):
                        bypass = False
                self.connections[item.optional_vars.id] = bypass
            self.visit(call)
        for statement in node.body:
            self.visit(statement)
        self.connections = previous

    visit_With = visit_AsyncWith

    def visit_If(self, node):
        self.visit(node.test)
        values, connections = self.values, self.connections
        results = []
        for branch in (node.body, node.orelse):
            self.values, self.connections = dict(values), dict(connections)
            for statement in branch:
                self.visit(statement)
            results.append((self.values, self.connections))
        self.values = {
            key: results[0][0].get(key, ("{branch}", True))
            if results[0][0].get(key) == results[1][0].get(key) else ("{branch}", True)
            for key in results[0][0].keys() | results[1][0].keys()
        }
        self.connections = {
            key: results[0][1].get(key) if results[0][1].get(key) == results[1][1].get(key) else None
            for key in results[0][1].keys() | results[1][1].keys()
        }

    def _visit_loop(self, node):
        # A loop may execute zero times. A WHERE fragment appended only in the
        # body therefore cannot prove that the query after the loop is scoped.
        self.visit(node.iter if hasattr(node, "iter") else node.test)
        before_values, before_connections = dict(self.values), dict(self.connections)
        if hasattr(node, "target"):
            for name in ast.walk(node.target):
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                    self.values[name.id] = ("{loop-value}", True)
        for statement in node.body:
            self.visit(statement)
        for key in self.values.keys() | before_values.keys():
            if self.values.get(key) != before_values.get(key):
                self.values[key] = ("{loop-dependent}", True)
        for key in self.connections.keys() | before_connections.keys():
            if self.connections.get(key) != before_connections.get(key):
                self.connections[key] = None
        for statement in node.orelse:
            self.visit(statement)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def visit_Try(self, node):
        # Exception paths can leave assignments partially applied. Literal
        # queries within the branches are still inventoried, while SQL carried
        # out of a try block is unresolved unless finally assigns it explicitly.
        before_values, before_connections = dict(self.values), dict(self.connections)
        for statement in node.body + node.orelse:
            self.visit(statement)
        for handler in node.handlers:
            self.values, self.connections = dict(before_values), dict(before_connections)
            for statement in handler.body:
                self.visit(statement)
        self.values, self.connections = before_values, before_connections
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                self.values[child.id] = ("{exception-dependent}", True)
        for key in self.connections:
            self.connections[key] = None
        for statement in node.finalbody:
            self.visit(statement)

    visit_TryStar = visit_Try

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in _METHODS:
            query = node.args[0] if node.args else next(
                (keyword.value for keyword in node.keywords if keyword.arg in {"query", "command", "sql"}), None
            )
            if query is not None:
                sql, unresolved = _resolve(query, self.values)
                self._record(node, _dotted(node.func.value), sql, unresolved)
        else:
            # A new literal delegated to an old generic SQL helper must not
            # hide behind that helper's pre-existing dynamic-SQL baseline.
            # Delegation is reported as unresolved; no callee is executed.
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                sql, _unresolved = _resolve(argument, self.values)
                if _SQL_START.match(_sql_parts(sql, analysis=True)):
                    self._record(node, _dotted(node.func), sql, True)
        self.generic_visit(node)

    def _record(self, node, receiver, sql, unresolved):
        if not unresolved:
            bypass_set = re.match(
                r"\s*SET\s+(?:LOCAL\s+)?app\.bypass_rls\s*(?:=|TO\s+)\s*'?(on|true|off|false)'?\s*;?\s*$", sql, re.I
            )
            if bypass_set:
                self.connections[receiver] = bypass_set.group(1).lower() in {"on", "true"}
            if re.search(r"set_config\s*\(\s*'app\.bypass_rls'\s*,", sql, re.I):
                literal = re.search(r"set_config\s*\(\s*'app\.bypass_rls'\s*,\s*'(on|true|off|false)'", sql, re.I)
                self.connections[receiver] = literal.group(1).lower() in {"on", "true"} if literal else None
        tables, scoped = _predicate_scope(sql, self.protected, self.tenant_tables)
        if not tables and not unresolved:
            return
        bypass = self.connections.get(receiver)
        normalized = _normalize(sql)
        identity = [self.path, ".".join(self.names) or "<module>", normalized, bypass]
        if unresolved:
            identity.append(ast.dump(self.functions[-1] if self.functions else node, include_attributes=False))
            # A query registry/helper outside this executor can change the SQL
            # without changing its function. Pin module syntax conservatively;
            # comments and source line movement do not change this digest.
            identity.append(self.module_identity)
        query_id = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
        self.findings.append({
            "id": query_id, "file": self.path, "function": identity[1], "line": node.lineno,
            "method": _dotted(node.func).split(".")[-1], "tables": tables, "sql": normalized,
            "scoped": scoped and not unresolved, "unresolved": unresolved, "bypass": bypass,
        })


def scan_source(source, *, relative_path, protected_tables, tenant_tables):
    tree = ast.parse(source, filename=relative_path)
    bindings = Counter(
        child.id for statement in tree.body
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for child in ast.walk(statement)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
    )
    scanner = _SourceScanner(relative_path, protected_tables, tenant_tables,
                             {name for name, count in bindings.items() if count > 1},
                             hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest())
    scanner.visit(tree)
    return scanner.findings


def scan_repository(backend_root):
    root = Path(backend_root)
    schema = runpy.run_path(str(root / "scripts" / "rls_acquire_inventory.py"))
    protected, tenants = schema["discover_rls_tables"](root)
    if not protected or not tenants:
        raise ValueError("Tenant SQL scan has no schema inventory")
    findings = []
    for path in sorted((root / "app").rglob("*.py")):
        if "__pycache__" not in path.parts:
            findings.extend(scan_source(
                path.read_text(encoding="utf-8-sig"), relative_path=path.relative_to(root).as_posix(),
                protected_tables=protected, tenant_tables=tenants,
            ))
    return findings


def audit_findings(findings, baseline):
    errors, allowed = [], {}
    if baseline.get("version") != 1:
        return ["Tenant SQL baseline has an unsupported version"]
    for category in ("violations", "exceptions"):
        entries = baseline.get(category)
        if not isinstance(entries, list):
            errors.append(f"Baseline {category} must be an explicit list")
            continue
        for entry in entries:
            key, count = entry.get("id"), entry.get("count")
            if not isinstance(key, str) or not key or type(count) is not int or count < 1:
                errors.append(f"Invalid {category} entry")
                continue
            if key in allowed:
                errors.append(f"Duplicate baseline identity: {key}")
            if category == "exceptions" and (not isinstance(entry.get("reason"), str) or not entry["reason"].strip()):
                errors.append(f"System exception needs an individual reason: {key}")
            allowed[key] = count
    actual = Counter(row["id"] for row in findings if not row["scoped"])
    locations = {row["id"]: f"{row['file']}:{row['line']} {row['function']}" for row in findings}
    for key, count in sorted(actual.items()):
        if count > allowed.get(key, 0):
            errors.append(f"New unscoped SQL ({count} > {allowed.get(key, 0)}): {locations[key]} [{key}]")
    for key, count in sorted(allowed.items()):
        if actual[key] < count:
            errors.append(f"Remove stale baseline debt/exception ({actual[key]} < {count}): {key}")
    return errors


def make_baseline(findings):
    counts = Counter(row["id"] for row in findings if not row["scoped"])
    examples = {row["id"]: row for row in findings if not row["scoped"]}
    return {
        "version": 1,
        "scope": "SQL execution/delegation sites in backend/app; known RLS/tenant tables plus unresolved SQL arguments",
        "limitations": "Structural SQL scope only; complex/dynamic SQL is debt, not proven safe. No parameter authorization proof.",
        "violations": [dict(examples[key], count=count, reason="Existing unresolved debt; tenant-scoping review required.")
                       for key, count in sorted(counts.items())],
        "exceptions": [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Print the full inventory")
    parser.add_argument("--baseline", type=Path, help="Check a reviewed baseline")
    parser.add_argument("--write-baseline", type=Path, help="Explicit first inventory only; refuses to overwrite existing evidence")
    args = parser.parse_args()
    findings = scan_repository(args.root)
    summary = {
        "queries": len(findings), "scoped": sum(row["scoped"] for row in findings),
        "unscoped": sum(not row["scoped"] for row in findings),
        "bypass_unscoped": sum(not row["scoped"] and row["bypass"] is True for row in findings),
        "unresolved": sum(row["unresolved"] for row in findings),
    }
    if args.write_baseline:
        with args.write_baseline.open("x", encoding="utf-8") as output:
            json.dump(make_baseline(findings), output, indent=2)
            output.write("\n")
    errors = audit_findings(findings, json.loads(args.baseline.read_text(encoding="utf-8"))) if args.baseline else []
    print(json.dumps(dict(summary=summary, errors=errors, **({"queries": findings} if args.json else {})), indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
