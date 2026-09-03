# Runtime profiles

An application chooses where Python execution happens. marimo-export supplies
the prepared profile and interoperates with applications that also expose live
server or browser WebAssembly profiles.

| Profile             | Python execution                     | State surface                                   | Primary owner                                   |
| ------------------- | ------------------------------------ | ----------------------------------------------- | ----------------------------------------------- |
| Live server         | Application server and kernel        | Inputs accepted by the running notebook         | marimo server and host application              |
| Browser WebAssembly | Browser Python runtime               | Inputs accepted by the browser notebook runtime | marimo WebAssembly runtime and host application |
| Prepared            | Producer environment before delivery | Finite states in a notebook export              | marimo-export producer and consumer APIs        |

## One cache foundation, two static delivery paths

marimo's [automatic cell cache](https://docs.marimo.io/api/caching/#automatic-cell-caching)
supports both static profiles. The [SciPy 2026 caching
article](https://dmadisetti.github.io/scipy_proceedings_2026/) demonstrates the
WebAssembly path and explains its content-addressed keys and lazy value stubs.

| Static profile       | Producer boundary                                                                   | Browser boundary                                                       |
| -------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Browser WebAssembly  | Bundle marimo's native cache manifests, value blobs, and notebook code              | Pyodide derives native keys, restores cached values, and runs Python   |
| Prepared Zero-Python | Use native cached execution, verify selected receipts, and create a notebook export | A reader resolves the state-output relation and loads declared outputs |

Both profiles can avoid repeating expensive producer work. Their deployed
runtime contracts differ. Browser WebAssembly retains a Python notebook runtime.
Prepared delivery moves selected results across the export format boundary
before deployment.

The product and Studio code sometimes call the prepared profile
**Zero-Python**. The name describes the deployed browser request path. Python
still runs while `prepare()`, `build()`, or `capture()` creates the notebook
export.

## Prepared profile

The prepared profile follows this boundary:

```text
Python producer
  -> ExportSpec states
  -> marimo execution
  -> notebook export
  -> browser open, resolve, load, and mount
```

The deployed browser resolves one state already present in the notebook export.
It opens no Python kernel or notebook WebSocket for that transition. Browser
code can continue local interaction when the exported representation supports
it, such as a Vega-Lite chart or AnyWidget model graph.

A request for an absent input vector needs another producer run or a live Python
profile. Refreshing a prepared manifest can reveal a new export instance. It
does not compute notebook Python in the browser.

## Application ownership

marimo-export owns:

- ExportSpec planning and preparation
- repository reuse and prepared publication coordination
- the notebook export format and verification
- immutable browser reading and prepared-publication transitions
- representation loaders and mount contracts

The application owns:

- runtime selection and user-facing runtime names
- live server or WebAssembly lifecycle
- view documents, routes, and presentation bindings
- fallback behavior when a requested state is absent
- deployment, origin, and Content Security Policy

marimo-studio uses this split. Its runtime selector can choose live server,
WebAssembly, or prepared delivery. Its Zero-Python path compiles view mounts into
public `OutputSpec` values and consumes marimo-export's prepared publication
APIs. The other runtime profiles remain Studio and marimo responsibilities.

## Validation

A prepared-profile browser test confirms that manifest, index, asset, and state
requests use the static application origin and that state changes open no kernel
or WebSocket connection. A runtime-selector test belongs to the consuming
application because marimo-export exposes no application runtime switch.

Read [marimo-studio integration](studio-integration.md) for the concrete
consumer and [Application publication and delivery](application-publication-and-delivery.md)
for the generic prepared route lifecycle.
