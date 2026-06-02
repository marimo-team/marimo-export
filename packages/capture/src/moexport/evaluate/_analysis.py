"""Static dependency analysis for targets and notebook cells.

This module is the evaluator's read-only view of Python source. It extracts the
root names used by ad hoc expression targets and asks marimo's compiler for the
body dependencies of notebook cells, while deliberately ignoring display-only
expressions when planning definition recomputation.
"""

from __future__ import annotations

import ast
import builtins

from marimo._ast.cell import CellImpl
from marimo._ast.compiler import compile_cell, ends_with_semicolon
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

_BUILTINS = set(dir(builtins))
_ANALYSIS_CELL_ID = CellId_t("__moexport_analysis__")
_BODY_REFS_CACHE: dict[tuple[str, int], set[str]] = {}


def expression_refs(expression: str) -> list[str]:
    # Expression targets do not exist in marimo's graph, so extract their free
    # root names from Python AST. Attribute/call details are left to eval().
    #
    # Example: `{x: y for x in xs for y in [x + 1]}` depends on `xs`, not on
    # the comprehension-local names `x` or `y`.
    tree = ast.parse(expression, mode="eval")
    visitor = _FreeNameVisitor()
    visitor.visit(tree)
    return sorted(visitor.refs)


class _FreeNameVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.refs: set[str] = set()
        self._scopes: list[set[str]] = [set()]

    def visit_Name(self, node: ast.Name) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and node.id not in _BUILTINS
            and not self._is_bound(node.id)
        ):
            self.refs.add(node.id)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

        self._scopes.append(set())
        for arg in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]:
            self._bind(arg.arg)
        if node.args.vararg is not None:
            self._bind(node.args.vararg.arg)
        if node.args.kwarg is not None:
            self._bind(node.args.kwarg.arg)

        self.visit(node.body)
        self._scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, node.key, node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        *body_nodes: ast.AST,
    ) -> None:
        self._scopes.append(set())
        for generator in generators:
            self.visit(generator.iter)
            self._bind_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for body_node in body_nodes:
            self.visit(body_node)
        self._scopes.pop()

    def _bind_target(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            self._bind(node.id)
            return

        if isinstance(node, ast.Starred):
            self._bind_target(node.value)
            return

        if isinstance(node, ast.Tuple | ast.List):
            for element in node.elts:
                self._bind_target(element)

    def _bind(self, name: str) -> None:
        self._scopes[-1].add(name)

    def _is_bound(self, name: str) -> bool:
        return any(name in scope for scope in reversed(self._scopes))


def _body_code_without_display_expr(cell: CellImpl) -> str:
    module = ast.parse(cell.code)
    body = module.body

    # Match marimo's display-output rule: only a final expression without a
    # trailing statement separator becomes visual output. Ignore that expression
    # for planning so it does not force recomputing its referenced defs.
    if body and isinstance(body[-1], ast.Expr) and not ends_with_semicolon(cell.code):
        body = body[:-1]

    if not body:
        return ""

    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def body_refs(cell: CellImpl) -> set[str]:
    cache_key = (str(cell.cell_id), cell.key)
    if cache_key in _BODY_REFS_CACHE:
        return _BODY_REFS_CACHE[cache_key]

    code = _body_code_without_display_expr(cell)
    if not code:
        _BODY_REFS_CACHE[cache_key] = set()
        return _BODY_REFS_CACHE[cache_key]

    # Use marimo's compiler for refs so dependency analysis stays aligned with
    # notebook semantics.
    refs = compile_cell(code, cell_id=_ANALYSIS_CELL_ID).refs
    _BODY_REFS_CACHE[cache_key] = {
        ref for ref in refs if ref not in _BUILTINS and ref not in _assigned_names(code)
    }
    return _BODY_REFS_CACHE[cache_key]


def _assigned_names(code: str) -> set[str]:
    assigned: set[str] = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            assigned.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            assigned.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assigned.add(alias.asname or alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    assigned.add(alias.asname or alias.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            assigned.add(node.name)
    return assigned


def display_refs(cell: CellImpl) -> set[str]:
    module = ast.parse(cell.code)
    body = module.body
    if not body or not isinstance(body[-1], ast.Expr) or ends_with_semicolon(cell.code):
        return set()

    return set(expression_refs(ast.unparse(body[-1].value))) - _assigned_names(
        _body_code_without_display_expr(cell)
    )


def single_defining_cell(graph: DirectedGraph, name: str) -> CellId_t:
    cells = graph.get_defining_cells(name)
    if len(cells) != 1:
        raise ValueError(f"Expected one defining cell for {name!r}. Got {cells}")
    return next(iter(cells))
