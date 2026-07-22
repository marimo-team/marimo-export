# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "anywidget==0.9.21",
#     "marimo==0.23.14",
#     "marimo-export[anywidget]==0.0.0",
#     "traitlets==5.14.3",
# ]
# [tool.uv.sources]
# marimo-export = { path = "../../packages/producer", editable = true }
# ///

import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    import anywidget
    import marimo as mo
    import traitlets

    return anywidget, mo, traitlets


@app.cell
def _(mo):
    mo.md("""
    # Portable AnyWidget controls

    This notebook publishes a raw AnyWidget and a composed
    `mo.ui.anywidget(...)` value. Their synchronized state, binary buffers,
    frontend modules, styles, and nested model references can be mounted by a
    browser after the notebook server stops.
    """)
    return


@app.cell
def _():
    seed = 2
    return (seed,)


@app.cell
def _():
    accent = "#2563eb"
    return (accent,)


@app.cell
def _(anywidget, traitlets):
    class CounterWidget(anywidget.AnyWidget):
        _esm = r"""
        function byteView(value) {
          if (value instanceof ArrayBuffer) return new Uint8Array(value);
          if (ArrayBuffer.isView(value)) {
            return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
          }
          return new Uint8Array();
        }

        function checksum(value) {
          return byteView(value).reduce((total, item) => total + item, 0);
        }

        export default {
          initialize({ model }) {
            const initialCount = model.get("count");
            return {
              reset() {
                model.set("count", initialCount);
                model.save_changes();
              },
            };
          },

          render({ model, el, signal }) {
            const root = document.createElement("section");
            root.className = "portable-counter";

            const label = document.createElement("strong");
            const value = document.createElement("output");
            value.className = "portable-counter__value";
            const buffer = document.createElement("span");
            buffer.className = "portable-counter__buffer";
            const increment = document.createElement("button");
            increment.type = "button";
            increment.textContent = "Increment counter";

            root.append(label, value, buffer, increment);
            el.replaceChildren(root);

            const renderState = () => {
              root.style.setProperty("--widget-accent", model.get("accent"));
              label.textContent = model.get("label");
              value.textContent = String(model.get("count"));
              buffer.textContent = `Buffer checksum ${checksum(model.get("payload"))}`;
            };
            const updateCount = () => {
              model.set("count", model.get("count") + 1);
              model.save_changes();
            };

            model.on("change:accent", renderState);
            model.on("change:count", renderState);
            model.on("change:label", renderState);
            model.on("change:payload", renderState);
            increment.addEventListener("click", updateCount, { signal });
            renderState();

            return () => {
              model.off("change:accent", renderState);
              model.off("change:count", renderState);
              model.off("change:label", renderState);
              model.off("change:payload", renderState);
              root.dispatchEvent(
                new CustomEvent("marimo-export-widget-cleanup", {
                  bubbles: true,
                  detail: { widget: "counter" },
                }),
              );
              root.remove();
            };
          },
        };
        """
        _css = r"""
        .portable-counter {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 0.65rem 1rem;
          align-items: center;
          padding: 1rem;
          border: 2px solid var(--widget-accent, #2563eb);
          border-radius: 0.5rem;
          background: color-mix(in srgb, var(--widget-accent, #2563eb) 7%, white);
          color: #17211b;
          font-family: ui-sans-serif, system-ui, sans-serif;
        }

        .portable-counter__value {
          color: var(--widget-accent, #2563eb);
          font-size: 2rem;
          font-variant-numeric: tabular-nums;
          font-weight: 700;
        }

        .portable-counter__buffer {
          color: #56635a;
          font-family: ui-monospace, monospace;
          font-size: 0.8rem;
        }

        .portable-counter button {
          padding: 0.45rem 0.7rem;
          border: 0;
          border-radius: 0.35rem;
          background: var(--widget-accent, #2563eb);
          color: white;
          cursor: pointer;
        }
        """

        count = traitlets.Int(0).tag(sync=True)
        label = traitlets.Unicode("Raw counter").tag(sync=True)
        accent = traitlets.Unicode("#2563eb").tag(sync=True)
        payload = traitlets.Bytes(b"").tag(sync=True)

    return (CounterWidget,)


@app.cell
def _(CounterWidget, anywidget, traitlets):
    class ComposedDashboard(anywidget.AnyWidget):
        _esm = r"""
        export default {
          initialize({ model }) {
            return {
              rename(title) {
                model.set("title", title);
                model.save_changes();
              },
            };
          },

          async render({ model, el, host, signal }) {
            const root = document.createElement("section");
            root.className = "composed-dashboard";
            const heading = document.createElement("h3");
            const summary = document.createElement("p");
            summary.className = "composed-dashboard__summary";
            const increment = document.createElement("button");
            increment.type = "button";
            increment.textContent = "Increment child from parent";
            const childHost = document.createElement("div");
            childHost.className = "composed-dashboard__child";
            root.append(heading, summary, increment, childHost);
            el.replaceChildren(root);

            const childRef = model.get("child");
            const [childModel, childWidget] = await Promise.all([
              host.getModel(childRef),
              host.getWidget(childRef),
            ]);
            await childWidget.render({ el: childHost, signal });
            if (signal.aborted) return;

            const renderState = () => {
              root.style.setProperty("--widget-accent", model.get("accent"));
              heading.textContent = model.get("title");
              summary.textContent = `Parent observes child count ${childModel.get("count")}`;
            };
            const updateChild = () => {
              childModel.set("count", childModel.get("count") + 1);
              childModel.save_changes();
            };

            model.on("change:accent", renderState);
            model.on("change:title", renderState);
            childModel.on("change:count", renderState);
            increment.addEventListener("click", updateChild, { signal });
            renderState();

            return () => {
              model.off("change:accent", renderState);
              model.off("change:title", renderState);
              childModel.off("change:count", renderState);
              root.dispatchEvent(
                new CustomEvent("marimo-export-widget-cleanup", {
                  bubbles: true,
                  detail: { widget: "composed-dashboard" },
                }),
              );
              root.remove();
            };
          },
        };
        """
        _css = r"""
        .composed-dashboard {
          display: grid;
          gap: 0.75rem;
          padding: 1rem;
          border-left: 0.35rem solid var(--widget-accent, #2563eb);
          background: color-mix(in srgb, var(--widget-accent, #2563eb) 6%, white);
          color: #17211b;
          font-family: ui-sans-serif, system-ui, sans-serif;
        }

        .composed-dashboard h3,
        .composed-dashboard p {
          margin: 0;
        }

        .composed-dashboard__summary {
          color: #56635a;
        }

        .composed-dashboard button {
          width: fit-content;
          padding: 0.45rem 0.7rem;
          border: 1px solid var(--widget-accent, #2563eb);
          border-radius: 0.35rem;
          background: white;
          color: var(--widget-accent, #2563eb);
          cursor: pointer;
        }
        """

        title = traitlets.Unicode("Composed dashboard").tag(sync=True)
        accent = traitlets.Unicode("#2563eb").tag(sync=True)
        child = traitlets.Instance(CounterWidget, allow_none=False).tag(sync=True)

    return (ComposedDashboard,)


@app.cell
def _(ComposedDashboard, CounterWidget, accent, mo, seed):
    raw_counter = CounterWidget(
        count=seed,
        label="Raw counter",
        accent=accent,
        payload=bytes((seed, seed + 1, seed + 2, seed + 3)),
    )
    raw_preview = mo.ui.anywidget(raw_counter)

    _child = CounterWidget(
        count=seed + 1,
        label="Nested child",
        accent=accent,
        payload=bytes((seed + 4, seed + 5, seed + 6, seed + 7)),
    )
    _dashboard = ComposedDashboard(
        title="Composed dashboard",
        accent=accent,
        child=_child,
    )
    wrapped_dashboard = mo.ui.anywidget(_dashboard)

    mo.hstack([raw_preview, wrapped_dashboard], widths="equal")
    return raw_counter, raw_preview, wrapped_dashboard


@app.cell
def _(accent, seed):
    projected = {
        "accent": accent,
        "child_count": seed + 1,
        "raw_count": seed,
    }
    return (projected,)


@app.cell
def _(projected):
    chart = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.1.0.json",
        "data": {
            "values": [
                {"counter": "Raw", "value": projected["raw_count"]},
                {"counter": "Nested child", "value": projected["child_count"]},
            ]
        },
        "mark": {"type": "bar", "color": projected["accent"]},
        "encoding": {
            "x": {"field": "counter", "type": "nominal", "title": None},
            "y": {"field": "value", "type": "quantitative", "title": "Initial count"},
        },
        "height": 220,
        "width": 420,
    }
    return (chart,)


if __name__ == "__main__":
    app.run()
