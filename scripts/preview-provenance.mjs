import { writeFileSync } from "node:fs";

const [commit, output] = process.argv.slice(2);
if (!/^[a-f0-9]{40}$/.test(commit ?? "") || !output) {
  throw new Error("Usage: node scripts/preview-provenance.mjs COMMIT OUTPUT");
}
const required = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
};
const server = required("GITHUB_SERVER_URL");
const repository = `${server}/${required("GITHUB_REPOSITORY")}`;
const workflow = required("GITHUB_WORKFLOW_REF");
const ref = required("GITHUB_REF");
const workflowCommit = required("GITHUB_SHA");

// workflow_run signs from the current main workflow, which can differ from the
// validated checkout. Record both dependencies and identify the artifact source.
const predicate = {
  buildDefinition: {
    buildType:
      "https://github.com/marimo-team/marimo-export/blob/main/development_docs/releasing.md#preview-provenance-v1",
    externalParameters: {
      workflow: { repository, ref, path: ".github/workflows/publish.yml" },
      checkoutCommit: commit,
    },
    internalParameters: {
      github: {
        event_name: required("GITHUB_EVENT_NAME"),
        repository_id: required("GITHUB_REPOSITORY_ID"),
        repository_owner_id: required("GITHUB_REPOSITORY_OWNER_ID"),
        runner_environment: required("RUNNER_ENVIRONMENT"),
      },
    },
    resolvedDependencies: [
      { uri: `git+${repository}@${ref}`, digest: { gitCommit: workflowCommit } },
      { uri: `git+${repository}@${commit}`, digest: { gitCommit: commit } },
    ],
  },
  runDetails: {
    builder: { id: `${server}/${workflow}` },
    metadata: {
      invocationId: `${repository}/actions/runs/${required("GITHUB_RUN_ID")}/attempts/${required("GITHUB_RUN_ATTEMPT")}`,
    },
  },
};
writeFileSync(output, `${JSON.stringify(predicate, null, 2)}\n`);
