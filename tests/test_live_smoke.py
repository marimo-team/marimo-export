from __future__ import annotations

import os

import pytest

from moxport import ExportError, MarimoClient

LIVE_SERVER = os.getenv("MOXPORT_LIVE_SERVER", "http://127.0.0.1:2718")
LIVE_NOTEBOOK = os.getenv("MOXPORT_LIVE_NOTEBOOK", "02_linear_program.py")
LIVE_ENABLED = os.getenv("MOXPORT_LIVE") == "1"


@pytest.mark.skipif(not LIVE_ENABLED, reason="set MOXPORT_LIVE=1 to run live smoke")
def test_live_notebook_smoke() -> None:
    with MarimoClient() as client:
        notebook = client.connect(LIVE_SERVER, notebook_name=LIVE_NOTEBOOK)
        summary = notebook.summary()
        assert summary.session.session_id
        assert summary.cell_count > 0

        by_session = client.connect(LIVE_SERVER, session_id=summary.session.session_id)
        assert by_session.session.session_id == summary.session.session_id

        source = notebook.get_live_source()
        assert "marimo" in source

        script = notebook.get_exported_script()
        assert "__generated_with" in script

        cells = notebook.get_ir_summary()
        assert cells
        concrete = notebook.get_cell(cells[0].id)
        assert concrete.id == cells[0].id
        counter = notebook.get_cell("counter")
        assert counter.name == "counter"
        counter_ref = notebook.cell_ref("counter")
        counter_description = counter_ref.describe()
        assert counter_description.selector == counter.id
        assert counter_description.type in {"widget", "object"}
        assert counter_description.resolution in {
            "retained",
            "materialized",
            "recomputed",
        }
        counter_value = counter_ref.query_json("value.count")
        assert isinstance(counter_value, int)

        assert notebook.ref("1 + 1").query_json() == 2

        packages = notebook.packages.list()
        assert packages.packages

        try:
            materialized = notebook.get_materialized_notebook()
            assert isinstance(materialized.cells, list)
        except ExportError as exc:
            assert 'nb.packages.install_missing("nbformat", source="server")' in str(
                exc
            )

        markdown_desc = notebook.cell_ref(1).describe()
        assert markdown_desc.type in {"html", "object"}
        assert markdown_desc.resolution in {"materialized", "recomputed", "retained"}

        markdown_value = notebook.cell_ref(1).query_json(
            "value.text if hasattr(value, 'text') else repr(value)"
        )
        assert isinstance(markdown_value, str)
        assert "Linear Program" in markdown_value

        last_desc = notebook.cell_ref(cells[-1].id).describe()
        assert last_desc.type in {"html", "widget", "object", "dataframe", "array"}
        assert last_desc.resolution in {"materialized", "recomputed", "retained"}
