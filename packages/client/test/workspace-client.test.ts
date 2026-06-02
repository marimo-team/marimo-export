import assert from "node:assert/strict";
import test from "node:test";
import {
  createMarimoWorkspaceClient,
  type MarimoWorkspaceClientOptions,
} from "@marimo-team/export-client/workspace";

type WorkspaceFetch = NonNullable<MarimoWorkspaceClientOptions["fetch"]>;

test("MarimoWorkspaceClient lists notebooks and reads source through the workspace subpath", async () => {
  const requests: string[] = [];
  const client = createMarimoWorkspaceClient({
    server: "https://marimo.example.test",
    fetch: workspaceFetch(requests),
  });

  assert.deepEqual(await client.sessions.list(), [
    {
      sessionId: "session-1",
      name: "finance.py",
      path: "/work/finance.py",
      initializationId: "init-1",
    },
  ]);
  assert.deepEqual(await client.notebooks.list(), [
    {
      id: "finance",
      name: "finance.py",
      path: "/work/notebooks/finance.py",
    },
  ]);
  assert.equal(await client.notebooks.source("/work/notebooks/finance.py"), "# Finance\n");
  assert.deepEqual(requests, [
    "/api/home/running_notebooks",
    "/api/home/workspace_files",
    "/api/files/file_details",
  ]);
});

function workspaceFetch(requests: string[]): WorkspaceFetch {
  return async (request) => {
    const path = new URL(request.url).pathname;
    requests.push(path);

    if (path === "/api/home/running_notebooks") {
      return Response.json({
        files: [
          {
            sessionId: "session-1",
            name: "finance.py",
            path: "/work/finance.py",
            initializationId: "init-1",
          },
          { name: "missing-session.py" },
        ],
      });
    }

    if (path === "/api/home/workspace_files") {
      return Response.json({
        files: [
          {
            id: "folder",
            isMarimoFile: false,
            name: "notebooks",
            path: "/work/notebooks",
            children: [
              {
                id: "finance",
                isMarimoFile: true,
                name: "finance.py",
                path: "/work/notebooks/finance.py",
              },
            ],
          },
        ],
      });
    }

    if (path === "/api/files/file_details") {
      return Response.json({ contents: "# Finance\n" });
    }

    throw new Error(`unexpected request: ${path}`);
  };
}
