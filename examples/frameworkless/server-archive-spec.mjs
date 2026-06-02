export function createQueueingArchiveSpec(parseSpec) {
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

function queueScenario(id, arrival, service, servers) {
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

function pythonExpression(expression) {
  return { code: expression };
}
