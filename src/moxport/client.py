from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import PurePath
from typing import Any, Literal, cast
import httpx

from .errors import (
    ConnectionSpecError,
    ExportError,
    NotebookNotFoundError,
    NotebookNotRunningError,
    ScratchpadExecutionError,
    SessionNotebookMismatchError,
    SessionNotFoundError,
)
from .models import (
    CellInfo,
    JsonValue,
    LiveCellInfo,
    MaterializedCell,
    MaterializedNotebook,
    NotebookSummary,
    REMOTE_REF_DESCRIPTION_ADAPTER,
    RemoteRefDescription,
    RuntimeVariable,
    ScratchpadResult,
    SessionInfo,
    WorkspaceFile,
    WorkspaceFilesResult,
)
from .packages import NotebookPackagesClient
from .parsing import overlay_live_cells, parse_notebook_cells
from .scratchpad import parse_execute_stream

_DEFAULT = object()
_RPC_SENTINEL = "__MOXPORT_JSON__"
_LOG = logging.getLogger(__name__)
_REMOTE_HELPERS = """
import ast
import json

from marimo._runtime.context import get_context

def _json_default(value):
    if isinstance(value, set):
        return sorted(value, key=repr)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return {"__repr__": repr(value), "__type__": type(value).__name__}

def _evaluate_cell_code(code):
    module = ast.parse(code, mode="exec")
    runtime_globals = globals()
    local_ns = {}
    body = list(module.body)
    tail = None
    if body and isinstance(body[-1], ast.Expr):
        tail = body.pop().value
    if body:
        exec(
            compile(ast.Module(body=body, type_ignores=[]), "<remote-cell>", "exec"),
            runtime_globals,
            local_ns,
        )
    if tail is not None:
        return eval(
            compile(ast.Expression(tail), "<remote-cell>", "eval"),
            runtime_globals,
            local_ns,
        )
    return {name: local_ns[name] for name in sorted(local_ns)}

def _lookup_retained_cell_output(cell_id, code):
    graph = get_context().graph
    cells = getattr(graph, "cells", None)
    if cells is None:
        cells = getattr(getattr(graph, "topology", None), "_cells", None)
    if cells is None:
        raise RuntimeError("Could not access live marimo cells from runtime graph")

    cell = cells.get(cell_id)
    if cell is None:
        raise KeyError(f"Unknown live cell id: {cell_id}")
    if cell.output is not None:
        return cell.output

    module = ast.parse(code, mode="exec")
    if module.body and isinstance(module.body[-1], ast.Expr):
        expr = module.body[-1].value
        if isinstance(expr, ast.Name) and expr.id in globals():
            return globals()[expr.id]

    raise RuntimeError(
        f"Cell {cell_id!r} does not expose a retained output object; "
        "stable identity is unavailable for this cell output."
    )

def _resolve_cell_value(cell_id, code):
    try:
        return _lookup_retained_cell_output(cell_id, code), "retained"
    except Exception:
        return _evaluate_cell_code(code), "recomputed"
""".strip()


class MarimoClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.default_token = token
        self._http = client or httpx.Client(timeout=timeout)
        self._owns_http = client is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> MarimoClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.close()

    def connect(
        self,
        server_url: str,
        notebook_name: str | None = None,
        *,
        session_id: str | None = None,
        token: str | None | object = _DEFAULT,
    ) -> MarimoNotebookClient:
        effective_token = (
            self.default_token if token is _DEFAULT else cast(str | None, token)
        )
        resolver = _SessionResolver(
            http=self._http,
            base_url=server_url.rstrip("/"),
            token=effective_token,
        )
        session = resolver.resolve(session_id=session_id, notebook_name=notebook_name)
        return MarimoNotebookClient(
            server_url=server_url,
            session=session,
            notebook_name=notebook_name
            or session.filename
            or session.path
            or session.session_id,
            token=effective_token,
            client=self._http,
            _owns_http=False,
        )


class MarimoNotebookClient:
    def __init__(
        self,
        server_url: str,
        session: SessionInfo,
        notebook_name: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        _owns_http: bool | None = None,
    ) -> None:
        self.base_url = server_url.rstrip("/")
        self.notebook_name = notebook_name
        self.token = token
        self._http = client or httpx.Client(timeout=timeout)
        self._owns_http = client is None if _owns_http is None else _owns_http
        self._session = session
        self.packages = NotebookPackagesClient(
            http=self._http,
            base_url=self.base_url,
            token=self.token,
            session_id=self._session.session_id,
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> MarimoNotebookClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.close()

    @property
    def session(self) -> SessionInfo:
        return self._session

    def summary(self) -> NotebookSummary:
        return NotebookSummary(
            session=self.session, cell_count=len(self.get_ir_summary())
        )

    def get_live_source(self) -> str:
        path = self.session.path or self.session.filename
        if path is None:
            raise ExportError("Notebook has no path; cannot fetch live source")
        payload = self._run_json(
            f"""
from pathlib import Path
payload = {{'source': Path({path!r}).read_text(encoding='utf-8')}}
"""
        )
        if not isinstance(payload, dict) or "source" not in payload:
            raise ExportError(
                "Scratchpad source retrieval returned an unexpected payload"
            )
        return str(payload["source"])

    def get_exported_script(self) -> str:
        response = self._post_export("/api/export/script")
        return response.text

    def get_materialized_notebook(self) -> MaterializedNotebook:
        try:
            response = self._post_export("/api/export/ipynb")
        except httpx.HTTPStatusError as exc:
            raise self._build_export_error(exc) from exc
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ExportError(
                "IPYNB export returned invalid JSON", status_code=response.status_code
            ) from exc
        return MaterializedNotebook.model_validate(payload)

    def get_ir_summary(self) -> list[CellInfo]:
        parsed = parse_notebook_cells(self.get_live_source())
        return overlay_live_cells(parsed, self._get_live_cells())

    def get_cell(self, target: int | str) -> CellInfo:
        cells = self.get_ir_summary()
        if isinstance(target, int):
            return cells[target]
        for cell in cells:
            if cell.id == target or cell.name == target:
                return cell
        raise KeyError(f"Unknown cell target: {target!r}")

    def get_materialized_output(self, target: int | str) -> MaterializedCell:
        cell = self.get_cell(target)
        notebook = self.get_materialized_notebook()
        for entry in notebook.cells:
            if entry.id == cell.id:
                return entry
        if 0 <= cell.index < len(notebook.cells):
            return notebook.cells[cell.index]
        raise KeyError(f"No materialized output found for cell {target!r}")

    def _describe_materialized_cell(
        self,
        target: int | str,
    ) -> dict[str, Any] | None:
        try:
            cell = self.get_cell(target)
            materialized = self.get_materialized_output(target)
        except (ExportError, KeyError):
            return None

        source_preview = _materialized_source_text(materialized.source)
        preview = source_preview

        if materialized.outputs:
            output_mimetype, output_preview = _materialized_output_preview(
                materialized.outputs[0]
            )
            if output_mimetype:
                return {
                    "type": "html",
                    "kind": "cell",
                    "selector": cell.id,
                    "resolution": "materialized",
                    "python_type": "MaterializedHtml",
                    "module": "moxport.materialized",
                    "preview": output_preview[:400],
                    "mime": output_mimetype,
                    "text": output_preview,
                }
        elif materialized.cell_type == "markdown":
            return {
                "type": "html",
                "kind": "cell",
                "selector": cell.id,
                "resolution": "materialized",
                "python_type": "MaterializedMarkdown",
                "module": "moxport.materialized",
                "preview": source_preview[:400] or "materialized markdown",
                "mime": "text/markdown",
                "text": source_preview,
            }

        if not preview:
            preview = f"materialized {materialized.cell_type or 'cell'}"

        return {
            "type": "object",
            "kind": "cell",
            "selector": cell.id,
            "resolution": "materialized",
            "python_type": "MaterializedCell",
            "module": "moxport.materialized",
            "preview": preview[:400],
        }

    def runtime_variables(self) -> dict[str, RuntimeVariable]:
        names = sorted({name for cell in self.get_ir_summary() for name in cell.defs})
        payload = self._run_json(
            f"""
names = {names!r}
payload = {{
    name: {{'type': type(globals()[name]).__name__, 'repr': repr(globals()[name])[:160]}}
    for name in names
    if name in globals()
}}
"""
        )
        if not isinstance(payload, dict):
            return {}
        return {
            name: RuntimeVariable(
                name=name,
                datatype=str(summary.get("type"))
                if summary.get("type") is not None
                else None,
                preview=str(summary.get("repr"))
                if summary.get("repr") is not None
                else None,
                ref=self.ref(name),
            )
            for name, summary in payload.items()
            if isinstance(summary, dict)
        }

    def ref(self, expression: str) -> RemoteRef:
        return RemoteRef(client=self, kind="expression", selector=expression)

    def cell_ref(self, target: int | str) -> RemoteRef:
        return RemoteRef(client=self, kind="cell", selector=self.get_cell(target).id)

    def _base_headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def _session_headers(self) -> dict[str, str]:
        return {
            **self._base_headers(),
            "Marimo-Session-Id": self.session.session_id,
        }

    def _post_export(self, path: str) -> httpx.Response:
        response = self._http.post(
            f"{self.base_url}{path}",
            headers={
                **self._session_headers(),
                "Marimo-Server-Token": self._get_server_token(),
                "Content-Type": "application/json",
            },
            json={"download": False},
        )
        response.raise_for_status()
        return response

    def _execute_scratchpad(self, code: str) -> ScratchpadResult:
        with self._http.stream(
            "POST",
            f"{self.base_url}/api/kernel/execute",
            headers={**self._session_headers(), "Content-Type": "application/json"},
            json={"code": code},
        ) as response:
            response.raise_for_status()
            return parse_execute_stream(response.iter_lines())

    def _run_json(self, body: str) -> JsonValue | dict[str, Any]:
        code = (
            f"{_REMOTE_HELPERS}\n\n{body}\n"
            f"print({_RPC_SENTINEL!r} + json.dumps(payload, default=_json_default))"
        )
        result = self._execute_scratchpad(code)
        if not result.success:
            message = None
            if result.error is not None:
                message = result.error.msg or result.error.type
            raise ScratchpadExecutionError(
                message or result.stderr or "Scratchpad execution failed"
            )
        for line in reversed(result.stdout.splitlines()):
            if line.startswith(_RPC_SENTINEL):
                return cast(
                    JsonValue | dict[str, Any], json.loads(line[len(_RPC_SENTINEL) :])
                )
        raise ScratchpadExecutionError("Scratchpad JSON sentinel not found in stdout")

    def _get_live_cells(self) -> list[LiveCellInfo]:
        payload = self._run_json(
            """
import marimo._code_mode as cm

async with cm.get_context(skip_validation=True) as ctx:
    payload = [
        {
            'index': index,
            'id': cell_id,
            'name': ctx.cells[index].name or None,
            'code': ctx.cells[index].code,
        }
        for index, cell_id in enumerate(ctx.cells.keys())
    ]
"""
        )
        if not isinstance(payload, list):
            return []
        return [
            LiveCellInfo.model_validate(item)
            for item in payload
            if isinstance(item, dict)
        ]

    def _get_server_token(self) -> str:
        response = self._http.get(self.base_url, headers=self._base_headers())
        response.raise_for_status()
        match = re.search(r'"serverToken"\s*:\s*"([^"]+)"', response.text)
        if match is None:
            raise ExportError("Could not extract Marimo-Server-Token from server HTML")
        return match.group(1)

    def _build_export_error(self, exc: httpx.HTTPStatusError) -> ExportError:
        response = exc.response
        body = response.text
        message = f"IPYNB export failed with HTTP {response.status_code}."
        if "nbformat" in body or response.status_code >= 500:
            message += (
                " If the export environment is missing nbformat, try "
                'nb.packages.install_missing("nbformat", source="server").'
            )
        return ExportError(message, status_code=response.status_code)


class RemoteRef:
    def __init__(
        self,
        *,
        client: MarimoNotebookClient,
        kind: Literal["expression", "cell"],
        selector: str,
    ) -> None:
        self.client = client
        self.kind = kind
        self.selector = selector

    def describe(self) -> RemoteRefDescription:
        payload = self._describe_payload()
        if not isinstance(payload, dict):
            raise ScratchpadExecutionError(
                "Remote describe returned an unexpected payload"
            )
        return cast(
            RemoteRefDescription,
            REMOTE_REF_DESCRIPTION_ADAPTER.validate_python(payload),
        )

    def query_json(self, expression: str = "value") -> JsonValue | Any:
        payload = self.client._run_json(
            f"""
{self._query_value_script()}
result = ({expression})
payload = {{'data': result}}
"""
        )
        if not isinstance(payload, dict):
            raise ScratchpadExecutionError(
                "Remote query returned an unexpected payload"
            )
        return payload.get("data")

    def _retained_value_script(self) -> str:
        if self.kind == "expression":
            return f"value = ({self.selector})"
        cell = self.client.get_cell(self.selector)
        return (
            f"value = _lookup_retained_cell_output({cell.id!r}, {cell.code!r})\n"
            "resolution = 'retained'"
        )

    def _query_value_script(self) -> str:
        if self.kind == "expression":
            return f"value = ({self.selector})"
        cell = self.client.get_cell(self.selector)
        return f"value, resolution = _resolve_cell_value({cell.id!r}, {cell.code!r})"

    def _describe_payload(self) -> dict[str, Any]:
        if self.kind == "expression":
            return self._run_describe_script(
                value_script=f"value = ({self.selector})",
                resolution=None,
            )

        try:
            return self._run_describe_script(
                value_script=self._retained_value_script(),
                resolution="retained",
            )
        except ScratchpadExecutionError:
            materialized = self.client._describe_materialized_cell(self.selector)
            if materialized is not None:
                return materialized
            return self._run_describe_script(
                value_script=self._query_value_script(),
                resolution="recomputed",
            )

    def _run_describe_script(
        self,
        *,
        value_script: str,
        resolution: Literal["retained", "recomputed"] | None,
    ) -> dict[str, Any]:
        payload = cast(
            dict[str, Any],
            self.client._run_json(
                f"""
{value_script}
payload = {{
    'kind': {self.kind!r},
    'selector': {self.selector!r},
    'python_type': type(value).__name__,
    'module': type(value).__module__,
    'preview': repr(value)[:400],
    'has_dataframe_protocol': callable(getattr(value, '__dataframe__', None)),
    'has_array_protocol': callable(getattr(value, '__array__', None)),
    'has_array_namespace': callable(getattr(value, '__array_namespace__', None)),
}}
for attr in ('shape', 'columns', 'dtype', 'ndim', 'size', 'name'):
    if hasattr(value, attr):
        try:
            attr_value = getattr(value, attr)
            if attr == 'shape':
                attr_value = list(attr_value)
            elif attr == 'columns':
                attr_value = [str(item) for item in list(attr_value)]
            elif attr == 'name' and attr_value is not None:
                attr_value = str(attr_value)
            payload[attr] = attr_value
        except Exception:
            pass
if hasattr(value, 'text'):
    try:
        payload['text'] = str(value.text)[:4000]
    except Exception:
        pass
"""
            ),
        )
        return _classify_runtime_description(payload, resolution=resolution)

    def __repr__(self) -> str:
        return f"RemoteRef(kind={self.kind!r}, selector={self.selector!r})"


class _SessionResolver:
    def __init__(self, *, http: httpx.Client, base_url: str, token: str | None) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._token = token

    def resolve(
        self,
        *,
        session_id: str | None,
        notebook_name: str | None,
    ) -> SessionInfo:
        if session_id is None and notebook_name is None:
            raise ConnectionSpecError(
                "connect() requires at least one of notebook_name or session_id"
            )

        sessions = self._list_sessions()

        if session_id is not None:
            session = next(
                (item for item in sessions if item.session_id == session_id), None
            )
            if session is None:
                raise SessionNotFoundError(f"No active session matched {session_id!r}")
            if (
                notebook_name is not None
                and self._match_score(session, notebook_name) is None
            ):
                raise SessionNotebookMismatchError(
                    f"Session {session_id!r} does not match notebook {notebook_name!r}"
                )
            return session

        assert notebook_name is not None
        matches: list[tuple[tuple[int, str], SessionInfo]] = []
        for session in sessions:
            score = self._match_score(session, notebook_name)
            if score is not None:
                matches.append((score, session))

        if not matches:
            if self._workspace_notebook_exists(notebook_name):
                raise NotebookNotRunningError(
                    f"Notebook {notebook_name!r} exists in the workspace but has no active session"
                )
            raise NotebookNotFoundError(
                f"Notebook {notebook_name!r} was not found in the workspace"
            )

        matches.sort(key=lambda item: item[0])
        best_score = matches[0][0][0]
        best_matches = [session for score, session in matches if score[0] == best_score]
        if len(best_matches) > 1:
            chosen = best_matches[0]
            _LOG.warning(
                "Multiple active sessions matched %r; choosing %s. Candidates: %s",
                notebook_name,
                chosen.session_id,
                [item.session_id for item in best_matches],
            )
            return chosen
        return matches[0][1]

    def _list_sessions(self) -> list[SessionInfo]:
        response = self._http.get(
            f"{self._base_url}/api/sessions",
            headers=self._base_headers(),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        return [
            SessionInfo(
                session_id=session_key,
                filename=data.get("filename") if isinstance(data, dict) else None,
                path=data.get("path") if isinstance(data, dict) else None,
            )
            for session_key, data in payload.items()
        ]

    def _workspace_notebook_exists(self, notebook_name: str) -> bool:
        response = self._http.post(
            f"{self._base_url}/api/home/workspace_files",
            headers={**self._base_headers(), "Content-Type": "application/json"},
            json={"includeMarkdown": False},
        )
        response.raise_for_status()
        workspace = WorkspaceFilesResult.model_validate(response.json())
        return any(
            self._match_workspace_file(file_info, notebook_name)
            for file_info in _flatten_workspace_files(workspace.files)
        )

    def _base_headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    def _match_workspace_file(
        self, file_info: WorkspaceFile, notebook_name: str
    ) -> bool:
        if file_info.is_directory or not file_info.is_marimo_file:
            return False
        return (
            self._match_strings(
                notebook_name,
                path=file_info.path,
                filename=file_info.name,
            )
            is not None
        )

    def _match_score(
        self,
        session: SessionInfo,
        notebook_name: str,
    ) -> tuple[int, str] | None:
        return self._match_strings(
            notebook_name,
            path=session.path or "",
            filename=session.filename or PurePath(session.path or "").name,
        )

    def _match_strings(
        self,
        notebook_name: str,
        *,
        path: str,
        filename: str,
    ) -> tuple[int, str] | None:
        target = notebook_name.strip().replace("\\", "/").casefold()
        variants = {target}
        if not target.endswith(".py"):
            variants.add(f"{target}.py")

        normalized_path = path.replace("\\", "/")
        basename = PurePath(filename).name
        stem = PurePath(filename).stem
        target_stem = PurePath(target).stem.casefold()

        path_cf = normalized_path.casefold()
        filename_cf = filename.casefold()
        basename_cf = basename.casefold()
        stem_cf = stem.casefold()

        if path_cf == target or filename_cf == target:
            return (0, normalized_path or filename)
        if basename_cf in variants:
            return (1, basename)
        if stem_cf == target_stem:
            return (2, stem)
        if any(
            path_cf.endswith("/" + variant) or path_cf.endswith(variant)
            for variant in variants
        ):
            return (3, normalized_path)
        if any(variant in basename_cf for variant in variants):
            return (4, basename)
        return None


def _flatten_workspace_files(files: Iterable[WorkspaceFile]) -> list[WorkspaceFile]:
    items: list[WorkspaceFile] = []
    for file_info in files:
        items.append(file_info)
        if file_info.children:
            items.extend(_flatten_workspace_files(file_info.children))
    return items


def _materialized_source_text(source: str | list[str] | None) -> str:
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    if source is None:
        return ""
    return str(source)


def _materialized_output_type(output: dict[str, Any]) -> str | None:
    value = output.get("output_type")
    return str(value) if value is not None else None


def _materialized_output_preview(output: dict[str, Any]) -> tuple[str | None, str]:
    data = output.get("data")
    if isinstance(data, dict):
        for mimetype in ("text/plain", "text/markdown", "text/html"):
            if mimetype in data:
                return mimetype, str(data[mimetype])[:400]

    if isinstance(output.get("text"), list):
        return "text/plain", "".join(str(part) for part in output["text"])[:400]
    if output.get("text") is not None:
        return "text/plain", str(output["text"])[:400]

    output_type = _materialized_output_type(output)
    return None, f"materialized {output_type or 'output'}"


def _classify_runtime_description(
    payload: dict[str, Any],
    *,
    resolution: Literal["retained", "recomputed"] | None,
) -> dict[str, Any]:
    base = {
        "kind": payload["kind"],
        "selector": payload["selector"],
        "resolution": resolution,
        "python_type": str(payload.get("python_type") or "object"),
        "module": str(payload.get("module") or ""),
        "preview": str(payload.get("preview") or "")[:400],
    }

    module = base["module"]
    python_type = base["python_type"]
    preview = base["preview"]
    columns = payload.get("columns")
    shape = payload.get("shape")
    ndim = payload.get("ndim")
    dtype = payload.get("dtype")
    text = payload.get("text")
    has_dataframe_protocol = bool(payload.get("has_dataframe_protocol"))
    has_array_protocol = bool(payload.get("has_array_protocol"))
    has_array_namespace = bool(payload.get("has_array_namespace"))

    if _is_widget_like(module, python_type):
        return {
            **base,
            "type": "widget",
        }

    if _is_html_like(module, python_type, text):
        return {
            **base,
            "type": "html",
            "mime": "text/html",
            "text": str(text or preview),
        }

    if _is_dataframe_like(
        module,
        python_type,
        shape,
        columns,
        has_dataframe_protocol=has_dataframe_protocol,
    ):
        rows = _shape_dim(shape, 0)
        cols = _shape_dim(shape, 1)
        return {
            **base,
            "type": "dataframe",
            "rows": rows if rows is not None else 0,
            "cols": cols if cols is not None else len(columns or []),
            "columns": list(columns or []),
        }

    if _is_array_like(
        module,
        python_type,
        shape,
        ndim,
        has_array_protocol=has_array_protocol,
        has_array_namespace=has_array_namespace,
    ):
        return {
            **base,
            "type": "array",
            "shape": list(shape or []),
            "ndim": int(ndim if ndim is not None else len(shape or [])),
            "dtype": dtype,
        }

    return {
        **base,
        "type": "object",
    }


def _is_widget_like(module: str, python_type: str) -> bool:
    lowered_module = module.lower()
    lowered_type = python_type.lower()
    return (
        "widget" in lowered_type
        or "widget" in lowered_module
        or "uielement" in lowered_type
        or "uielement" in lowered_module
    )


def _is_html_like(module: str, python_type: str, text: Any) -> bool:
    lowered_module = module.lower()
    lowered_type = python_type.lower()
    return (
        python_type == "Html"
        or lowered_module.startswith("marimo._output")
        or lowered_type == "html"
        or text is not None
    )


def _is_dataframe_like(
    module: str,
    python_type: str,
    shape: Any,
    columns: Any,
    *,
    has_dataframe_protocol: bool,
) -> bool:
    lowered_module = module.lower()
    lowered_type = python_type.lower()
    if has_dataframe_protocol:
        return True
    if callable(getattr(shape, "__len__", None)) and len(shape or []) >= 2 and columns:
        return True
    return (
        "dataframe" in lowered_type
        or "table" in lowered_type
        or "dataframe" in lowered_module
    )


def _is_array_like(
    module: str,
    python_type: str,
    shape: Any,
    ndim: Any,
    *,
    has_array_protocol: bool,
    has_array_namespace: bool,
) -> bool:
    lowered_module = module.lower()
    lowered_type = python_type.lower()
    if has_array_protocol or has_array_namespace:
        return True
    if shape is not None and ndim is not None:
        return True
    return (
        "ndarray" in lowered_type
        or "tensor" in lowered_type
        or "array" in lowered_type
        or "numpy" in lowered_module
        or "torch" in lowered_module
        or "jax" in lowered_module
    )


def _shape_dim(shape: Any, index: int) -> int | None:
    if not isinstance(shape, list):
        return None
    if len(shape) <= index:
        return None
    try:
        return int(shape[index])
    except Exception:
        return None
