type ParseExportSpec<T> = (spec: QueueingArchiveSpecInput) => T;

type PythonExpression = {
  readonly code: string;
};

type QueueScenario = {
  readonly id: string;
  readonly state: {
    readonly arrival_rate: PythonExpression;
    readonly service_rate: PythonExpression;
    readonly server_count: PythonExpression;
    readonly scenario_name: string;
  };
};

type QueueingArchiveSpecInput = {
  readonly scenarios: readonly QueueScenario[];
  readonly values: {
    readonly summary: {
      readonly source: { readonly def: "queue_summary" };
      readonly formats: {
        readonly json: {
          readonly filename: "queue-summary.json";
          readonly format_id: "queue.summary.json.v1";
          readonly metadata: { readonly kind: "queue-summary" };
        };
      };
    };
    readonly chart: {
      readonly source: { readonly def: "wait_chart" };
      readonly formats: readonly ["vegalite"];
    };
    readonly note: {
      readonly source: { readonly def: "queue_note" };
      readonly formats: {
        readonly html: {
          readonly filename: "queue-note.html";
          readonly format_id: "queue.note.html.v1";
          readonly metadata: { readonly kind: "marimo-markdown-output" };
        };
      };
    };
  };
};

export function createQueueingArchiveSpec<T>(parseSpec: ParseExportSpec<T>): T {
  return parseSpec({
    scenarios: [
      queueScenario("steady_single", 6.0, 11.0, 1),
      queueScenario("rush_single", 8.0, 9.0, 1),
      queueScenario("pooled_rush", 16.0, 11.0, 2),
    ],
    values: {
      summary: {
        source: { def: "queue_summary" },
        formats: {
          json: {
            filename: "queue-summary.json",
            format_id: "queue.summary.json.v1",
            metadata: { kind: "queue-summary" },
          },
        },
      },
      chart: {
        source: { def: "wait_chart" },
        formats: ["vegalite"],
      },
      note: {
        source: { def: "queue_note" },
        formats: {
          html: {
            filename: "queue-note.html",
            format_id: "queue.note.html.v1",
            metadata: { kind: "marimo-markdown-output" },
          },
        },
      },
    },
  });
}

function queueScenario(
  id: string,
  arrival: number,
  service: number,
  servers: number,
): QueueScenario {
  return {
    id,
    state: {
      arrival_rate: pythonExpression(arrival.toFixed(1)),
      service_rate: pythonExpression(service.toFixed(1)),
      server_count: pythonExpression(String(servers)),
      scenario_name: id,
    },
  };
}

function pythonExpression(expression: string): PythonExpression {
  return { code: expression };
}
