import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    days = mo.ui.slider(1, 30, value=7, label="Days")
    days
    return (days,)


@app.cell
def _(days):
    summary = {"days": days.value, "label": f"Last {days.value} days"}
    summary
    return (summary,)


@app.cell
def _(days, mo, summary):
    report = mo.md(f"## {summary['label']}\n\nSelected window: **{days.value} days**")
    report
    return (report,)


if __name__ == "__main__":
    app.run()
