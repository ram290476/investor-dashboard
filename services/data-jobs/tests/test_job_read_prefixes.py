"""Every static lake key a job handler reads must be covered by that job's read_prefixes.

The dashboard build broke after the yield-curve change because curated/macro_daily/source=fred/
and serving/rates/ were read by the handler and never added to the job's IAM prefixes.
This walks each handler (not the whole module, so the three company-IR jobs stay separate)
and resolves the keys it passes to the lake read helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import lake

REPO = Path(__file__).resolve().parents[3]
JOBS_TF = REPO / "infra" / "terraform" / "variables.tf"
SRC = REPO / "services" / "data-jobs" / "src"
LAKE_ROOTS = ("curated/", "serving/", "raw/", "manual/")
SKIP_MODULES = {"lake", "observability", "http_client", "api_keys", "collectors"}
READ_ARG = {
    "read_json": (0, "key"),
    "read_parquet_prefix": (0, "prefix"),
    "job_lease": (0, "key"),
    "get_object": (None, "Key"),
    "list_objects_v2": (None, "Prefix"),
    "head_object": (None, "Key"),
    "upsert_ranked": (1, "key"),
    "upsert_parquet": (1, "key"),
}
PRICE_READS = {"read_prices": lake.PRICES_PREFIX, "upsert_prices": lake.PRICES_PREFIX}


class Scope:
    def __init__(self, parent, module):
        self.parent = parent
        self.module = module
        self.assigns: list[tuple[int, str, ast.expr]] = []
        self.imports: dict[str, tuple[str, str]] = {}
        self.funcs: dict[str, ast.FunctionDef] = {}
        self.params: dict[str, tuple[ast.expr, Scope] | None] = {}


class Mod:
    def __init__(self, name: str, tree: ast.AST):
        self.name = name
        self.tree = tree
        self.scope = Scope(None, name)
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef):
                self.scope.funcs[stmt.name] = stmt
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        self.scope.assigns.append((stmt.lineno, target.id, stmt.value))
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                self.scope.assigns.append((stmt.lineno, stmt.target.id, stmt.value))
            elif isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    self.scope.imports[alias.asname or alias.name] = (stmt.module or "", alias.name)


def _module_paths() -> dict[str, Path]:
    found = {path.stem: path for path in (SRC / "app").glob("*.py")}
    for path in (SRC / "functions").glob("*/*.py"):
        found[path.stem] = path
    return found


_PATHS = _module_paths()
_LOADED: dict[str, Mod] = {}


def load_module(name: str) -> Mod:
    if name not in _LOADED:
        _LOADED[name] = Mod(name, ast.parse(_PATHS[name].read_text()))
    return _LOADED[name]


def _is_lake(prefix: str) -> bool:
    return any(prefix.startswith(root) and len(prefix) > len(root) for root in LAKE_ROOTS)


def _positional_names(fn: ast.FunctionDef | ast.Lambda) -> list[str]:
    args = fn.args
    return [arg.arg for arg in args.posonlyargs] + [arg.arg for arg in args.args]


def _defaults(fn: ast.FunctionDef | ast.Lambda) -> dict[str, ast.expr]:
    args = fn.args
    positional = args.posonlyargs + args.args
    missing = len(positional) - len(args.defaults)
    found = {arg.arg: default for arg, default in zip(positional[missing:], args.defaults, strict=True)}
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if default is not None:
            found[arg.arg] = default
    return found


def _param_names(fn: ast.FunctionDef | ast.Lambda) -> list[str]:
    args = fn.args
    return _positional_names(fn) + [arg.arg for arg in args.kwonlyargs]


def _bindings(fn: ast.FunctionDef | ast.Lambda, call: ast.Call, scope: Scope) -> dict[str, tuple[ast.expr, Scope]]:
    names = _positional_names(fn)
    bound = {}
    for index, arg in enumerate(call.args):
        if index < len(names):
            bound[names[index]] = (arg, scope)
    for keyword in call.keywords:
        if keyword.arg:
            bound[keyword.arg] = (keyword.value, scope)
    return bound


def _lookup_assign(name: str, scope: Scope | None, lineno: int):
    """Latest assignment, else the parameter binding (caller expression or default)."""
    while scope is not None:
        found = [expr for line, item, expr in scope.assigns if item == name and line < lineno]
        if found:
            return found[-1], scope
        if name in scope.params and scope.params[name] is not None:
            return scope.params[name]
        scope = scope.parent
    return None


def _resolve_callable(name: str, scope: Scope | None):
    while scope is not None:
        if name in scope.params:
            bound = scope.params[name]
            if bound is None:
                return ("param", None, scope)
            expr, expr_scope = bound
            unwrapped = _unwrap(expr, expr_scope)
            if unwrapped:
                return unwrapped
            return ("param", expr, expr_scope)
        if name in scope.funcs:
            return ("func", scope.funcs[name], scope)
        if name in scope.imports:
            module, original = scope.imports[name]
            return ("import", module, original)
        scope = scope.parent
    return None


def _unwrap(expr: ast.expr, scope: Scope):
    if isinstance(expr, ast.Lambda):
        return ("lambda", expr, scope)
    if isinstance(expr, ast.FunctionDef):
        return ("func", expr, scope)
    if isinstance(expr, ast.Name):
        found = _resolve_callable(expr.id, scope)
        if found and found[0] in {"func", "lambda"}:
            return found
    return None


def _returns(fn: ast.FunctionDef | ast.Lambda) -> list[ast.Return]:
    found = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            if node is fn:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            return

        def visit_Return(self, node):
            found.append(node)

        def visit_ClassDef(self, node):
            return

    if isinstance(fn, ast.Lambda):
        if isinstance(fn.body, ast.expr):
            return [ast.Return(value=fn.body)]
        return found
    Visitor().visit(fn)
    return found


def _collect_assigns(fn: ast.FunctionDef, scope: Scope) -> None:
    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            if node is fn:
                self.generic_visit(node)

        def visit_Lambda(self, node):
            return

        def visit_Assign(self, node):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scope.assigns.append((node.lineno, target.id, node.value))
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if isinstance(node.target, ast.Name) and node.value is not None:
                scope.assigns.append((node.lineno, node.target.id, node.value))
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            return

    Visitor().visit(fn)


def _function_scope(fn: ast.FunctionDef, defining: Scope) -> Scope:
    scope = Scope(defining, defining.module)
    _collect_assigns(fn, scope)
    return scope


def prefixes_from_expr(expr: ast.expr | None, scope: Scope, stack: set) -> list[tuple[str, bool]]:
    if expr is None or id(expr) in stack:
        return []
    stack = set(stack) | {id(expr)}
    if isinstance(expr, ast.Constant):
        if isinstance(expr.value, str) and _is_lake(expr.value):
            return [(expr.value, True)]
        return []
    if isinstance(expr, ast.JoinedStr):
        return _joined(expr, scope, stack)
    if isinstance(expr, ast.IfExp):
        return prefixes_from_expr(expr.body, scope, stack) + prefixes_from_expr(expr.orelse, scope, stack)
    if isinstance(expr, ast.Name):
        found = _lookup_assign(expr.id, scope, getattr(expr, "lineno", 10**9))
        if not found:
            return []
        value, value_scope = found
        return prefixes_from_expr(value, value_scope, stack)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _concat(prefixes_from_expr(expr.left, scope, stack), prefixes_from_expr(expr.right, scope, stack))
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        resolved = _resolve_callable(expr.func.id, scope)
        if resolved and resolved[0] == "func":
            return _return_prefixes(resolved[1], resolved[2], stack)
        return []
    if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, str):
        return _subscript_prefixes(expr, scope, stack)
    return []


def _concat(left: list[tuple[str, bool]], right: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    found = []
    for text, complete in left:
        if not complete:
            found.append((text, False))
            continue
        if not right:
            found.append((text, False))
            continue
        for extra, extra_complete in right:
            found.append((text + extra, extra_complete))
    return [(text, complete) for text, complete in found if _is_lake(text)]


def _joined(expr: ast.JoinedStr, scope: Scope, stack: set) -> list[tuple[str, bool]]:
    options = [("", True)]
    for part in expr.values:
        nxt = []
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            for text, complete in options:
                nxt.append((text + part.value, complete) if complete else (text, False))
        elif isinstance(part, ast.FormattedValue):
            parts = [(text, complete) for text, complete in prefixes_from_expr(part.value, scope, stack) if complete]
            for text, complete in options:
                if not complete:
                    nxt.append((text, False))
                elif parts:
                    for extra, extra_complete in parts:
                        nxt.append((text + extra, extra_complete))
                else:
                    nxt.append((text, False))
        else:
            nxt.extend((text, False) for text, _complete in options)
        options = nxt
    return [(text, complete) for text, complete in options if _is_lake(text)]


def _return_prefixes(fn: ast.AST, defining: Scope, stack: set) -> list[tuple[str, bool]]:
    if id(fn) in stack:
        return []
    stack = set(stack) | {id(fn)}
    if isinstance(fn, ast.Lambda):
        return prefixes_from_expr(fn.body if isinstance(fn.body, ast.expr) else None, defining, stack)
    scope = _function_scope(fn, defining)
    found = []
    for node in _returns(fn):
        found.extend(prefixes_from_expr(node.value, scope, stack))
    return found


def _return_dicts(fn: ast.FunctionDef, defining: Scope, stack: set) -> list[tuple[ast.Dict, Scope]]:
    if id(fn) in stack:
        return []
    stack = set(stack) | {id(fn)}
    scope = _function_scope(fn, defining)
    return [(node.value, scope) for node in _returns(fn) if isinstance(node.value, ast.Dict)]


def _dicts_from_expr(expr: ast.expr, scope: Scope, stack: set) -> list[tuple[ast.Dict, Scope]]:
    if isinstance(expr, ast.Name):
        found = _lookup_assign(expr.id, scope, getattr(expr, "lineno", 10**9))
        if not found:
            return []
        value, value_scope = found
        return _dicts_from_expr(value, value_scope, stack)
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        resolved = _resolve_callable(expr.func.id, scope)
        if resolved and resolved[0] == "func":
            return _return_dicts(resolved[1], resolved[2], stack)
    return []


def _module_field_prefixes(module_name: str, field: str, stack: set) -> list[tuple[str, bool]]:
    token = ("field", module_name, field)
    if token in stack or module_name not in _PATHS:
        return []
    stack = set(stack) | {token}
    module = load_module(module_name)
    found = []
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if isinstance(key, ast.Constant) and key.value == field:
                found.extend(prefixes_from_expr(value, module.scope, stack))
    return found


def _subscript_prefixes(expr: ast.Subscript, scope: Scope, stack: set) -> list[tuple[str, bool]]:
    field = expr.slice.value
    found = []
    for document, document_scope in _dicts_from_expr(expr.value, scope, stack):
        for key, value in zip(document.keys, document.values, strict=False):
            if isinstance(key, ast.Constant) and key.value == field:
                found.extend(prefixes_from_expr(value, document_scope, stack))
    if found:
        return found
    if isinstance(field, str):
        return _module_field_prefixes(scope.module, field, stack)
    return []


def _key_expr(call: ast.Call, api: str) -> ast.expr | None:
    positional, keyword = READ_ARG[api]
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    if positional is not None and positional < len(call.args):
        return call.args[positional]
    return None


class Scanner:
    def __init__(self):
        self.prefixes: set[str] = set()
        self.unresolved: list[str] = []
        self.seen: set[int] = set()

    def add(self, prefix: str) -> None:
        if _is_lake(prefix):
            self.prefixes.add(prefix)

    def record(self, call: ast.Call, api: str, scope: Scope) -> None:
        if api in PRICE_READS:
            self.add(PRICE_READS[api])
            return
        expr = _key_expr(call, api)
        if expr is None or (isinstance(expr, ast.Constant) and expr.value is None):
            if expr is None:
                self.unresolved.append(f"{scope.module}:{call.lineno} {api} missing key")
            return
        found = [prefix for prefix, _complete in prefixes_from_expr(expr, scope, set()) if _is_lake(prefix)]
        if found:
            self.prefixes.update(found)
            return
        self.unresolved.append(f"{scope.module}:{call.lineno} {api} {ast.dump(expr)[:180]}")

    def lease(self, call: ast.Call, scope: Scope) -> None:
        for keyword in call.keywords:
            if keyword.arg == "lease_key":
                for prefix, _complete in prefixes_from_expr(keyword.value, scope, set()):
                    self.add(prefix)

    def analyze(self, fn: ast.FunctionDef, defining: Scope, bindings: dict) -> None:
        if id(fn) in self.seen:
            return
        self.seen.add(id(fn))
        scope = Scope(defining, defining.module)
        for name, default in _defaults(fn).items():
            scope.params[name] = (default, defining)
        for name in _param_names(fn):
            if name not in scope.params:
                scope.params[name] = None
        scope.params.update(bindings)
        walker = _Walker(self, scope)
        for stmt in fn.body:
            walker.visit(stmt)

    def analyze_lambda(self, fn: ast.Lambda, defining: Scope, bindings: dict) -> None:
        scope = Scope(defining, defining.module)
        for name, default in _defaults(fn).items():
            scope.params[name] = (default, defining)
        for name in _param_names(fn):
            if name not in scope.params:
                scope.params[name] = None
        scope.params.update(bindings)
        walker = _Walker(self, scope)
        walker.visit(fn.body)

    def follow(self, fn: ast.FunctionDef, defining: Scope, call: ast.Call, scope: Scope) -> None:
        self.analyze(fn, defining, _bindings(fn, call, scope))


class _Walker(ast.NodeVisitor):
    def __init__(self, scanner: Scanner, scope: Scope):
        self.scanner = scanner
        self.scope = scope

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.funcs[node.name] = node
        for decorator in node.decorator_list:
            self.visit(decorator)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.scope.imports[alias.asname or alias.name] = (node.module or "", alias.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.scope.assigns.append((node.lineno, target.id, node.value))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        if isinstance(node.target, ast.Name) and node.value is not None:
            self.scope.assigns.append((node.lineno, node.target.id, node.value))

    def visit_Call(self, node: ast.Call) -> None:
        self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._handle(node)

    def _handle(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in READ_ARG or node.func.attr in PRICE_READS:
                self.scanner.record(node, node.func.attr, self.scope)
            return
        if not isinstance(node.func, ast.Name):
            return
        name = node.func.id
        if name == "job_handler":
            self.scanner.lease(node, self.scope)
            return
        resolved = _resolve_callable(name, self.scope)
        if resolved is None:
            if name in READ_ARG or name in PRICE_READS:
                self.scanner.record(node, name, self.scope)
            return
        kind = resolved[0]
        if kind == "import":
            module, original = resolved[1], resolved[2]
            if original == "job_handler":
                self.scanner.lease(node, self.scope)
                return
            if module == "lake" and (original in READ_ARG or original in PRICE_READS):
                self.scanner.record(node, original, self.scope)
                return
            if module in SKIP_MODULES or module not in _PATHS:
                return
            target = load_module(module).scope.funcs.get(original)
            if target is not None:
                self.scanner.follow(target, load_module(module).scope, node, self.scope)
            return
        if kind == "func":
            self.scanner.follow(resolved[1], resolved[2], node, self.scope)
            return
        if kind == "lambda":
            self.scanner.analyze_lambda(resolved[1], resolved[2], _bindings(resolved[1], node, self.scope))
            return
        if kind == "param" and name in READ_ARG and node.args:
            self.scanner.record(node, name, self.scope)


def handler_reads(module_name: str, func_name: str) -> tuple[set[str], list[str]]:
    module = load_module(module_name)
    scanner = Scanner()
    scanner.analyze(module.scope.funcs[func_name], module.scope, {})
    return scanner.prefixes, scanner.unresolved


def parse_jobs(text: str) -> dict[str, dict]:
    start = text.index('variable "jobs"')
    default_at = text.index("default = {", start)
    brace = text.index("{", default_at)
    depth = 0
    end = brace
    for index, char in enumerate(text[brace:], brace):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    block = text[brace : end + 1]
    import re

    parts = re.split(r"(?m)^    ([a-z0-9-]+) = \{$", block)
    jobs = {}
    for name, body in zip(parts[1::2], parts[2::2], strict=False):
        handler = re.search(r'handler\s*=\s*"([^"]+)"', body)
        memory = re.search(r"memory\s*=\s*(\d+)", body)
        triggers = re.search(r"triggers\s*=\s*\[(.*?)\]", body, re.S)
        reads = re.search(r"read_prefixes\s*=\s*\[(.*?)\]", body, re.S)
        jobs[name] = {
            "handler": handler.group(1) if handler else "",
            "memory": int(memory.group(1)) if memory else 0,
            "triggers": re.findall(r'"([^"]*)"', triggers.group(1)) if triggers else [],
            "read_prefixes": re.findall(r'"([^"]*)"', reads.group(1)) if reads else [],
        }
    return jobs


def _covered(prefix: str, grants: list[str]) -> bool:
    return any(prefix.startswith(grant) for grant in grants)


def test_dashboard_build_can_read_rates_and_runs_with_more_memory():
    jobs = parse_jobs(JOBS_TF.read_text())
    dashboard = jobs["dashboard-build"]
    assert dashboard["memory"] == 2048
    assert "job:BACKFILL?tickers_completed>0" in dashboard["triggers"]
    assert "job:BACKFILL?batches>0" not in dashboard["triggers"]
    assert "curated/macro_daily/source=fred/" in dashboard["read_prefixes"]
    assert "serving/rates/" in dashboard["read_prefixes"]
    assert "ticker-added" in jobs["short-interest"]["triggers"]


def test_every_handler_read_is_granted_by_that_jobs_read_prefixes():
    jobs = parse_jobs(JOBS_TF.read_text())
    assert set(jobs) >= {
        "trend-metrics", "macro-daily", "daily-prices", "news-sentiment", "hourly-prices",
        "regulatory-feeds", "gov-contracts", "dashboard-build", "q1-fundamentals",
        "q2-company-ir-collect", "q2-company-ir-extract", "q2-company-ir-serve",
        "short-interest", "options-daily", "backfill", "price-reconcile", "release-day", "status-feed",
    }
    missing = {}
    unresolved = {}
    discovered = {}
    for name, job in jobs.items():
        module_name, func_name = job["handler"].split(".")
        prefixes, open_reads = handler_reads(module_name, func_name)
        discovered[name] = prefixes
        gaps = sorted(prefix for prefix in prefixes if not _covered(prefix, job["read_prefixes"]))
        if gaps:
            missing[name] = gaps
        if open_reads:
            unresolved[name] = open_reads
    assert any(prefix.startswith("curated/macro_daily/source=fred/") for prefix in discovered["dashboard-build"])
    assert any(prefix.startswith("serving/rates/") for prefix in discovered["dashboard-build"])
    assert any(prefix.startswith("raw/company_ir/") for prefix in discovered["q2-company-ir-collect"])
    assert any(prefix.startswith("raw/company_ir/") for prefix in discovered["q2-company-ir-extract"])
    assert any(prefix.startswith("serving/stock/") for prefix in discovered["q2-company-ir-serve"])
    assert missing == {}, missing
    assert unresolved == {}, unresolved
