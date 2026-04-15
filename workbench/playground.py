import marimo

__generated_with = "0.22.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    from moxport import ExportError, MarimoClient

    return ExportError, MarimoClient, mo


@app.cell
def _(client):
    mnb = client.connect(
        "https://ta-01kp8wxrxp0f1w1e0dtaysbdtg-2718-xceph4e8uxa7954iz7qbnv4b0.w.modal.host",
        token="3eecf5bbb47b6b66e565b6564efe3515",
        notebook_name="notebook.py"
    )
    mnb
    return (mnb,)


@app.cell
def _(mnb):
    mnb.cell_ref('df').query_json('value.limit(5).to_dicts()')
    return


@app.cell
def _(MarimoClient):
    client = MarimoClient()
    notebook = client.connect(
        "http://127.0.0.1:2718",
        notebook_name="02_linear_program.py",
    )
    return client, notebook


@app.cell
def _(notebook):
    notebook.summary().model_dump()
    return


@app.cell
def _(cells, notebook):
    notebook.cell_ref(cells[-1].id)
    return


@app.cell
def _(notebook):
    notebook.cell_ref('wiggly_matrix').query_json('value.value')
    return


@app.cell
def _(notebook):
    cells = notebook.get_ir_summary()
    [c.model_dump() for c in cells]
    return (cells,)


@app.cell
def _(notebook):
    notebook.ref('c_widget').query_json('value.value')
    return


@app.cell
def _(notebook):
    bunlde = {
        'count': notebook.cell_ref('counter').query_json('value.count'),
    }
    bunlde
    return


@app.cell
def _(notebook):
    notebook.cell_ref('counter').query_json('value.count')
    return


@app.cell
def _(notebook, optimal_value_cell):
    [{ref: notebook.ref(ref).query_json()} for ref in optimal_value_cell.refs]
    return


@app.cell
def _(optimal_value_cell_output):
    optimal_value_cell_output.model_dump()
    return


@app.cell
def _(cells, mo, notebook):
    optimal_value_cell = cells[-1]
    optimal_value_cell_output = notebook.get_materialized_output(optimal_value_cell.id)
    optimal_value_md = "".join(optimal_value_cell_output.outputs[0]["data"]["text/markdown"])
    mo.md(optimal_value_md)
    return optimal_value_cell, optimal_value_cell_output


@app.cell
def _(notebook):
    notebook.cell_ref('feasible_region_plot').query_json('')
    return


@app.cell
def runtime_query(cells, notebook):
    notebook.get_cell(cells[-1].id)
    notebook.cell_ref(cells[-2].id)
    return


@app.cell
def _():
    export_bundle = {}
    return (export_bundle,)


@app.cell
def _(cells, export_bundle, notebook):
    export_bundle['count'] = notebook.cell_ref(cells[-2].id).query_json('value.count')
    return


@app.cell
def _(export_bundle):
    export_bundle
    return


@app.cell
def packages(notebook):
    package_list = notebook.packages.list()
    return


@app.cell
def _():
    return


@app.cell
def _(cells):
    cells[5].model_dump()
    return


@app.cell
def _(notebook):
    notebook.get_materialized_notebook().model_dump()
    return


@app.cell
def _(notebook):
    notebook.get_materialized_notebook().model_dump()
    return


@app.cell
def export_state(ExportError, notebook):
    try:
        materialized = notebook.get_materialized_notebook().model_dump()
    except ExportError as exc:
        materialized = {"error": str(exc)}
    script_preview = notebook.get_exported_script()[:400]
    return


if __name__ == "__main__":
    app.run()
