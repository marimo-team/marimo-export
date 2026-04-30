import marimo

__generated_with = "0.23.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import math
    from html import escape

    app_title = "Agentic Export Playground"
    return app_title, escape, math, mo


@app.cell(hide_code=True)
def _():
    regions = ["North", "South", "Coast"]
    region = "North"
    window = 6
    demand_bias = 0
    scenario_name = "baseline"
    return demand_bias, region, regions, scenario_name, window


@app.cell(hide_code=True)
def _(mo, region, regions):
    region_picker = mo.ui.dropdown(
        options=regions,
        value=region,
        label="Region",
    )
    mode_picker = mo.ui.radio(
        options=["balanced", "surge", "conserve"],
        value="balanced",
        label="Mode",
    )
    controls = mo.vstack([region_picker, mode_picker])
    controls
    return controls, mode_picker, region_picker


@app.cell(hide_code=True)
def _(demand_bias, math, mode_picker, region_picker, window):
    def _daily_value(index: int, selected_region: str, mode: str, bias: int) -> int:
        region_offset = {"North": 12, "South": -4, "Coast": 7}[selected_region]
        mode_offset = {"balanced": 0, "surge": 18, "conserve": -12}[mode]
        weekly = math.sin(index / 2.1) * 9
        trend = index * (1.6 if mode == "surge" else 0.9)
        return round(92 + region_offset + mode_offset + weekly + trend + bias)

    rows = [
        {
            "day": f"Day {index + 1:02d}",
            "region": region_picker.value,
            "mode": mode_picker.value,
            "demand": _daily_value(index, region_picker.value, mode_picker.value, demand_bias),
            "target": 124 + (index % 4) * 3,
        }
        for index in range(14)
    ]
    rolling = [
        round(sum(row["demand"] for row in rows[max(0, index - window + 1): index + 1]) / min(index + 1, window), 1)
        for index, _row in enumerate(rows)
    ]
    return rolling, rows


@app.cell(hide_code=True)
def _(app_title, mode_picker, region_picker, rows, scenario_name, window):
    latest = rows[-1]["demand"]
    peak = max(row["demand"] for row in rows)
    trough = min(row["demand"] for row in rows)
    average = round(sum(row["demand"] for row in rows) / len(rows), 1)
    summary = {
        "title": app_title,
        "scenario": scenario_name,
        "region": region_picker.value,
        "mode": mode_picker.value,
        "window": window,
        "latest": latest,
        "peak": peak,
        "trough": trough,
        "average": average,
        "delta_vs_target": latest - rows[-1]["target"],
    }
    return (summary,)


@app.cell(hide_code=True)
def _(escape, mode_picker, region_picker, rolling, rows):
    def _points(values: list[float], width: int = 640, height: int = 180) -> str:
        lo = min(values)
        hi = max(values)
        span = max(1, hi - lo)
        return " ".join(
            f"{round(index * width / (len(values) - 1), 1)},{round(height - ((value - lo) / span * height), 1)}"
            for index, value in enumerate(values)
        )

    row_points = _points([row["demand"] for row in rows])
    rolling_points = _points(rolling)
    sparkline_svg = f'''
    <svg viewBox="0 0 720 240" role="img" aria-label="Demand sparkline" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="718" height="238" rx="10" fill="#FFFFFF" stroke="#E2E8F0"/>
      <g transform="translate(40 28)">
        <line x1="0" x2="640" y1="45" y2="45" stroke="#E2E8F0" stroke-dasharray="5 8"/>
        <line x1="0" x2="640" y1="135" y2="135" stroke="#E2E8F0" stroke-dasharray="5 8"/>
        <polyline points="{row_points}" fill="none" stroke="#0880EA" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="{rolling_points}" fill="none" stroke="#14B8A6" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>
        <text x="0" y="-8" fill="#64748B" font-size="13">{escape(region_picker.value)} / {escape(mode_picker.value)}</text>
      </g>
    </svg>
    '''.strip()
    return (sparkline_svg,)


@app.cell(hide_code=True)
def _(app_title, mo, summary):
    market_note = mo.md(
        f"""
        ### {app_title}

        **{summary['region']}** is running in **{summary['mode']}** mode. Latest demand is **{summary['latest']}**, average demand is **{summary['average']}**, and the rolling window is **{summary['window']}** days.
        """
    )
    market_note
    return (market_note,)


@app.cell(hide_code=True)
def _(app_title, controls, market_note, mo, sparkline_svg):
    dashboard_preview = mo.vstack([
        mo.md(f"## {app_title}"),
        controls,
        market_note,
        mo.Html(sparkline_svg),
    ])
    dashboard_preview
    return


if __name__ == "__main__":
    app.run()
