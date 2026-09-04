import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    days = mo.ui.slider(1, 30, value=7, label="Days")
    return days, mo


@app.cell
def _(days, mo):
    summary = {"days": days.value, "label": f"Last {days.value} days"}
    report = mo.md(f"## {summary['label']}\n\nSelected window: **{days.value} days**")
    return report, summary


if __name__ == "__main__":
    app.run()
