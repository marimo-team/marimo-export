import { directorySource } from "@marimo-team/marimo-export/node";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

interface RouteContext {
  readonly params: Promise<{ readonly path: readonly string[] }>;
}

export async function GET(_request: Request, context: RouteContext) {
  const root = process.env.MARIMO_EXPORT_DIR;
  if (root === undefined) {
    return Response.json({ error: "MARIMO_EXPORT_DIR is not configured." }, { status: 503 });
  }

  const { path } = await context.params;
  const objectPath = path.join("/");
  try {
    const bytes = await directorySource(root).read(objectPath);
    const body = new Uint8Array(bytes.byteLength);
    body.set(bytes);
    return new Response(body.buffer, {
      headers: {
        "Cache-Control": "no-store",
        "Content-Length": String(bytes.byteLength),
        "Content-Type": objectPath.endsWith(".json")
          ? "application/json; charset=utf-8"
          : "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return Response.json({ error: "Publication object not found." }, { status: 404 });
  }
}
