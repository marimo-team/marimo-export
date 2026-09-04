from __future__ import annotations

import anywidget
import traitlets

_ESM = r"""
const svgNS = "http://www.w3.org/2000/svg";

function render({ model, el }) {
  const root = document.createElement("section");
  root.className = "quote-detail";
  const header = document.createElement("header");
  const heading = document.createElement("div");
  heading.innerHTML = "<span>Quote detail</span><strong>Daily close and range</strong>";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Ticker");
  header.append(heading, select);

  const stats = document.createElement("div");
  stats.className = "quote-stats";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 640 180");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Closing price history");
  root.append(header, stats, svg);
  el.append(root);

  const formatPrice = (value) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(Number(value));
  const stat = (label, value) => {
    const item = document.createElement("div");
    const name = document.createElement("span");
    const result = document.createElement("strong");
    name.textContent = label;
    result.textContent = value;
    item.append(name, result);
    return item;
  };

  function draw() {
    const rows = model.get("rows") || [];
    const symbols = [...new Set(rows.map((row) => row.Symbol))];
    let symbol = model.get("symbol");
    if (!symbols.includes(symbol)) symbol = symbols[0] || "";
    select.replaceChildren(
      ...symbols.map((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        option.selected = name === symbol;
        return option;
      }),
    );

    const series = rows
      .filter((row) => row.Symbol === symbol)
      .sort((left, right) => new Date(left.Date) - new Date(right.Date));
    const latest = series.at(-1);
    if (!latest) return;
    const first = series[0];
    const periodMove = Number(latest.Close) / Number(first.Close) - 1;
    stats.replaceChildren(
      stat("Close", formatPrice(latest.Close)),
      stat("Period", `${periodMove >= 0 ? "+" : ""}${(periodMove * 100).toFixed(1)}%`),
      stat("Day range", `${formatPrice(latest.Low)} to ${formatPrice(latest.High)}`),
    );

    const values = series.map((row) => Number(row.Close));
    const low = Math.min(...values);
    const high = Math.max(...values);
    const span = Math.max(high - low, 1);
    const x = (index) => 24 + (index / Math.max(series.length - 1, 1)) * 592;
    const y = (value) => 154 - ((value - low) / span) * 128;
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute(
      "d",
      values.map((value, index) => `${index ? "L" : "M"}${x(index)} ${y(value)}`).join(" "),
    );
    path.setAttribute("class", "quote-line");
    const guide = document.createElementNS(svgNS, "line");
    guide.setAttribute("x1", "24");
    guide.setAttribute("x2", "616");
    guide.setAttribute("y1", "154");
    guide.setAttribute("y2", "154");
    guide.setAttribute("class", "quote-guide");
    svg.replaceChildren(guide, path);
  }

  const changeSymbol = () => {
    model.set("symbol", select.value);
    model.save_changes();
  };
  select.addEventListener("change", changeSymbol);
  model.on("change:rows", draw);
  model.on("change:symbol", draw);
  draw();

  return () => {
    select.removeEventListener("change", changeSymbol);
    model.off("change:rows", draw);
    model.off("change:symbol", draw);
  };
}

export default { render };
"""

_CSS = r"""
.quote-detail {
  color: var(--foreground, #0f172a);
  display: grid;
  gap: 18px;
}
.quote-detail header {
  align-items: end;
  display: flex;
  justify-content: space-between;
}
.quote-detail header div {
  display: grid;
  gap: 2px;
}
.quote-detail header span,
.quote-stats span {
  color: var(--muted-foreground, #64748b);
  font-size: 12px;
}
.quote-detail header strong {
  font-family: Lora, serif;
  font-size: 20px;
}
.quote-detail select {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  color: inherit;
  font: inherit;
  min-height: 34px;
  padding: 4px 30px 4px 10px;
}
.quote-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
}
.quote-stats div {
  border-left: 1px solid var(--border, #e2e8f0);
  display: grid;
  gap: 3px;
  padding-left: 14px;
}
.quote-stats div:first-child {
  border-left: 0;
  padding-left: 0;
}
.quote-stats strong {
  font-family: "Fira Mono", monospace;
  font-size: 14px;
}
.quote-detail svg {
  height: auto;
  width: 100%;
}
.quote-line {
  fill: none;
  stroke: #0880ea;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 3;
}
.quote-guide {
  stroke: var(--border, #e2e8f0);
  stroke-dasharray: 4 5;
}
@media (max-width: 520px) {
  .quote-stats {
    gap: 12px;
    grid-template-columns: 1fr;
  }
  .quote-stats div {
    border-left: 0;
    padding-left: 0;
  }
}
"""


class QuoteDetail(anywidget.AnyWidget):
    """Display one selectable quote series from synchronized price rows."""

    _esm = _ESM
    _css = _CSS

    rows = traitlets.List(traitlets.Dict(), default_value=[]).tag(sync=True)
    symbol = traitlets.Unicode("").tag(sync=True)


__all__ = ["QuoteDetail"]
