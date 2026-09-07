import type {
  MarimoCellSnapshot,
  MarimoModelLifecycleNotification,
  MarimoOutputSnapshot,
  MarimoReplayResources,
} from "./marimo-snapshot.js";
import { parseResources } from "./marimo-snapshot.js";
import { canonicalJson } from "./schema.js";
import type { JsonObject, JsonValue } from "./types.js";

interface ReplayEvent {
  readonly notification: MarimoModelLifecycleNotification;
  readonly successors: Set<string>;
  predecessors: number;
}

const notificationValue = (notification: MarimoModelLifecycleNotification): JsonObject => {
  const message = notification.message;
  return {
    ...notification,
    message:
      message.method === "open" || message.method === "update"
        ? { ...message, esm_spec: message.esm_spec === null ? null : { ...message.esm_spec } }
        : { ...message },
  };
};

/** Compose snapshot resources while preserving every source's replay order. */
export function mergeMarimoReplayResources(
  snapshots: readonly (MarimoOutputSnapshot | MarimoCellSnapshot)[],
): MarimoReplayResources {
  const files = new Map<string, string>();
  const uiValues = new Map<string, JsonValue>();
  const functions = new Map<string, readonly string[]>();
  const sequences = new Map<string, string>();
  const events = new Map<string, ReplayEvent>();

  for (const snapshot of snapshots) {
    const owner = snapshot.schema === "marimo.output.v1" ? snapshot.ownerCellId : snapshot.cell.id;
    const resources = parseResources(
      {
        ...snapshot.resources,
        modelNotifications: snapshot.resources.modelNotifications.map(notificationValue),
      },
      owner,
      snapshot.projectionSha256,
    );
    for (const [name, value] of Object.entries(resources.files)) {
      if (files.has(name) && files.get(name) !== value) {
        throw new TypeError(
          `Replay resource file ${JSON.stringify(name)} has conflicting contents.`,
        );
      }
      files.set(name, value);
    }
    for (const [id, value] of Object.entries(resources.uiValues)) {
      const previous = uiValues.get(id);
      if (previous !== undefined && canonicalJson(previous) !== canonicalJson(value)) {
        throw new TypeError(`Replay UI object ${JSON.stringify(id)} has conflicting UI values.`);
      }
      uiValues.set(id, value);
      functions.set(id, resources.functions[id]!);
    }

    const grouped = new Map<string, MarimoModelLifecycleNotification[]>();
    for (const notification of resources.modelNotifications) {
      const sequence = grouped.get(notification.model_id) ?? [];
      sequence.push(notification);
      grouped.set(notification.model_id, sequence);
    }
    for (const [id, sequence] of grouped) {
      const canonical = canonicalJson(sequence.map(notificationValue));
      const previous = sequences.get(id);
      if (previous !== undefined && previous !== canonical) {
        throw new TypeError(
          `Replay model ${JSON.stringify(id)} has conflicting lifecycle records.`,
        );
      }
      sequences.set(id, canonical);
    }

    // Shared snapshots can overlap. Ordering edges retain their cross-model
    // dependencies while identical events are emitted once.
    const ordinals = new Map<string, number>();
    let previous: ReplayEvent | undefined;
    for (const notification of resources.modelNotifications) {
      const ordinal = ordinals.get(notification.model_id) ?? 0;
      ordinals.set(notification.model_id, ordinal + 1);
      const key = `${notification.model_id}:${ordinal}`;
      let event = events.get(key);
      if (event === undefined) {
        event = { notification, successors: new Set(), predecessors: 0 };
        events.set(key, event);
      }
      if (previous !== undefined && !previous.successors.has(key)) {
        previous.successors.add(key);
        event.predecessors += 1;
      }
      previous = event;
    }
  }

  const ready = [...events.values()].filter((event) => event.predecessors === 0);
  const notifications: MarimoModelLifecycleNotification[] = [];
  for (let index = 0; index < ready.length; index += 1) {
    const event = ready[index]!;
    notifications.push(event.notification);
    for (const key of event.successors) {
      const successor = events.get(key)!;
      successor.predecessors -= 1;
      if (successor.predecessors === 0) ready.push(successor);
    }
  }
  if (notifications.length !== events.size) {
    throw new TypeError("Replay snapshots require conflicting model lifecycle order.");
  }
  return Object.freeze({
    files: Object.freeze(Object.fromEntries(files)),
    uiValues: Object.freeze(Object.fromEntries(uiValues)),
    functions: Object.freeze(Object.fromEntries(functions)),
    modelNotifications: Object.freeze(notifications),
  });
}
