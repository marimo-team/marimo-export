---
title: marimo snapshots
description: Rendered-output and complete-cell replay records, resource closure, model lifecycle messages, and parser contracts.
---

# marimo snapshots

marimo snapshots preserve rendered output and the browser resources reachable
from one planned output. They are immutable replay records. Loading or parsing a
snapshot does not render it or import its widget modules.

```ts
import { openExport } from "@marimo-team/marimo-export";
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";

const state = (await openExport("/exports/report/")).defaultState;
const snapshot = await state.output("report").load(marimoOutputLoader());
console.log(snapshot.ownerCellId, snapshot.output, snapshot.resources);
```

Use `marimo.output.v1` for one rendered-output snapshot. Use `marimo.cell.v1`
when the consumer also needs cell identity, ordered console records, and the
completed outcome.

## Parse exact snapshot bytes

The package root exports byte parsers independently of the output loaders:

```ts
function parseMarimoOutputSnapshot(bytes: Uint8Array): MarimoOutputSnapshot;
function parseMarimoCellSnapshot(bytes: Uint8Array): MarimoCellSnapshot;
```

Each parser requires strict canonical UTF-8 JSON, the exact field set, a known
schema, valid digests, and at most 2,000,000 JSON values. It returns a recursively
frozen record. Invalid bytes raise `TypeError`.

`marimoOutputLoader()` and `marimoCellLoader()` apply the same parsers after the
browser reader verifies the asset. A parser failure reached through
`ExportOutput.load()` becomes `NotebookExportError` code `decode_failed` with
output context.

## Rendered-output snapshot

```ts
interface MarimoOutputSnapshot {
  readonly schema: "marimo.output.v1";
  readonly projectionSha256: string;
  readonly ownerCellId: string;
  readonly output: MarimoCellOutput | null;
  readonly resources: MarimoReplayResources;
}
```

`projectionSha256` identifies the planned output projection. `ownerCellId`
identifies the authored cell that owns the output's UI object graph. `output` is
`null` when the formatted value has no terminal output.

## Complete-cell snapshot

```ts
interface MarimoCellIdentity {
  readonly id: string;
  readonly name: string | null;
  readonly codeSha256: string;
  readonly config: JsonObject;
}

interface MarimoCellSnapshot {
  readonly schema: "marimo.cell.v1";
  readonly projectionSha256: string;
  readonly cell: MarimoCellIdentity;
  readonly outcome: "completed";
  readonly output: MarimoCellOutput | null;
  readonly console: readonly MarimoCellOutput[];
  readonly resources: MarimoReplayResources;
}
```

`cell.name` is `null` for an unnamed cell. `codeSha256` identifies the selected
cell source. `console` preserves captured records in order. A portable complete
cell always has outcome `completed`.

## Cell output records

```ts
type MarimoCellChannel =
  | "stdout"
  | "stderr"
  | "stdin"
  | "pdb"
  | "output"
  | "marimo-error"
  | "media";

interface MarimoCellOutput {
  readonly channel: MarimoCellChannel;
  readonly mimetype: string;
  readonly data: JsonValue;
}
```

The record preserves marimo's channel, media type, and portable JSON data. The
consumer chooses a renderer for each media type. The snapshot reader performs no
HTML insertion, Markdown rendering, terminal emulation, or media decode.

## Replay resources

```ts
interface MarimoReplayResources {
  readonly files: Readonly<Record<string, string>>;
  readonly modelNotifications: readonly MarimoModelLifecycleNotification[];
  readonly functions: Readonly<Record<string, readonly string[]>>;
  readonly uiValues: Readonly<Record<string, JsonValue>>;
}
```

### `files`

Maps each closed virtual resource path to a data URL. Paths are nonempty and
values must start with `data:`. Model module records can refer to the normalized
`/@file/` key through a relative `./@file/` URL.

### `modelNotifications`

Contains the reachable AnyWidget model lifecycle closure in replay order. Every
model ID belongs to the snapshot's `projectionSha256` namespace.

```ts
interface MarimoEsmSpec {
  readonly hash: string;
  readonly url: string;
}

interface MarimoModelOpenMessage {
  readonly method: "open";
  readonly state: JsonObject;
  readonly buffer_paths: readonly MarimoBufferPath[];
  readonly buffers: readonly string[];
  readonly esm_spec: MarimoEsmSpec | null;
}

interface MarimoModelUpdateMessage {
  readonly method: "update";
  readonly state: JsonObject;
  readonly buffer_paths: readonly MarimoBufferPath[];
  readonly buffers: readonly string[];
  readonly esm_spec: MarimoEsmSpec | null;
}

interface MarimoModelCustomMessage {
  readonly method: "custom";
  readonly content: JsonValue;
  readonly buffers: readonly string[];
}

interface MarimoModelCloseMessage {
  readonly method: "close";
}

type MarimoBufferPathToken = string | number;
type MarimoBufferPath = readonly [MarimoBufferPathToken, ...MarimoBufferPathToken[]];

interface MarimoModelLifecycleNotification {
  readonly op: "model-lifecycle";
  readonly model_id: string;
  readonly message: MarimoModelLifecycleMessage;
}
```

`MarimoModelLifecycleMessage` is the closed union of those messages.
`MarimoModelLifecycleNotification` adds `op: "model-lifecycle"` and `model_id`.

`buffer_paths` contains nonempty string-or-index paths into model state.
`buffers` contains the corresponding encoded buffers. Both arrays must have the
same length.

An ECMAScript module (ESM) specification names a widget's browser module. Its
URL must resolve to an embedded file, a data URL, or an HTTP or HTTPS URL.
Parsing records that location but does not import it.

### `functions` and `uiValues`

`functions` records every projection-scoped UI object namespace. Each value must
be an empty array because a live Python function cannot cross the static export
boundary. `uiValues` stores the accepted frontend value for the same UI object.
The parser requires an exact one-to-one key set.

Each UI object ID must start with the snapshot owner and projection namespace.
This preserves cell ownership when an application merges resources from several
planned outputs. The application should key merged model and UI state by these
scoped IDs and reject collisions.

## Adapt a snapshot

A snapshot is renderer-neutral data. A consumer adapter decides how to:

1. render `MarimoCellOutput` media types
2. expose closed files to that renderer
3. replay model messages into a browser-local AnyWidget registry
4. route UI values through exported control bindings
5. own view replacement and disposal

Use [`anyWidgetLoader()`](loaders.md#anywidget) when the output representation is
an `anywidget.bundle` intended to mount directly. Use snapshot records when an
application owns a broader marimo output renderer or merges resources from
several projections.

## Trust boundary

Snapshot parsing validates record integrity and scope. It does not sanitize
rendered HTML or authenticate an external module URL. A later adapter that
inserts output markup or imports a model module grants that content the
application's page authority.

[Output loaders](loaders.md#mount-and-policy-requirements) describes Content
Security Policy and module-origin requirements. [Export format](../export-format.md)
contains exact wire examples for both snapshot schemas.
