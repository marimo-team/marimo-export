import { execFileSync } from "node:child_process";
import { copyFile, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { expect, it } from "vite-plus/test";

it.each([
  { availableAfter: 2, expectedCalls: 2, success: true },
  { availableAfter: 99, expectedCalls: 18, success: false },
])(
  "registry verification retries consumer availability and remains bounded: %j",
  async ({ availableAfter, expectedCalls, success }) => {
    const root = await mkdtemp(join(tmpdir(), "release-registry-"));
    try {
      const scripts = join(root, "scripts");
      const bin = join(root, "bin");
      await mkdir(scripts);
      await mkdir(bin);
      await copyFile(
        new URL("../../../scripts/verify-npm.sh", import.meta.url),
        join(scripts, "verify-npm.sh"),
      );
      await Promise.all([
        writeFile(join(scripts, "publish-npm.sh"), "#!/bin/sh\nexit 0\n", { mode: 0o755 }),
        writeFile(join(bin, "sleep"), "#!/bin/sh\nexit 0\n", { mode: 0o755 }),
        writeFile(
          join(bin, "node"),
          `#!/bin/sh
calls=0
if [ -f "$CALLS" ]; then calls=$(cat "$CALLS"); fi
calls=$((calls + 1))
printf '%s' "$calls" > "$CALLS"
[ "$calls" -ge "$AVAILABLE_AFTER" ]
`,
          { mode: 0o755 },
        ),
      ]);
      let passed = false;
      try {
        execFileSync("bash", [join(scripts, "verify-npm.sh")], {
          cwd: root,
          stdio: "pipe",
          timeout: 10_000,
          env: {
            ...process.env,
            RELEASE_VERSION: "0.0.3",
            CALLS: join(root, "calls"),
            AVAILABLE_AFTER: String(availableAfter),
            PATH: `${bin}${delimiter}${process.env.PATH}`,
          },
        });
        passed = true;
      } catch (error) {
        if (success) throw error;
      }
      expect(passed).toBe(success);
      expect(Number(await readFile(join(root, "calls"), "utf8"))).toBe(expectedCalls);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
);
