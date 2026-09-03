import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    days = mo.ui.slider(1, 30, value=7, label="Days")
    return (days,)


@app.cell
def _(days):
    summary = {"days": days.value, "label": f"Last {days.value} days"}
    return (summary,)


if __name__ == "__main__":
    app.run()
