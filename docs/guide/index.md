---
title: Guides
description: Build, inspect, consume, and deploy notebook exports from saved notebooks and live marimo sessions.
---

# Guides

marimo-export runs the notebook states selected by an `ExportSpec` and writes
their named outputs as one verified notebook export. Start by building the
deterministic quickstart export, then follow the guide for your next task.

| Task                                                      | Guide                                                       |
| --------------------------------------------------------- | ----------------------------------------------------------- |
| Build, verify, and read your first notebook export        | [Build your first notebook export](getting-started.md)      |
| Run the complete Yahoo Finance browser example            | [Run the market dashboard](market-dashboard.md)             |
| Select the notebook states and outputs to include         | [Choose states and outputs](choose-states.md)               |
| Prepare from a file or capture a live session             | [Build or capture](build-and-capture.md)                    |
| Inspect retention, observations, and stored work          | [Manage the export repository](manage-repository.md)        |
| Open the export from Python, a browser, or another client | [Consume an export](consume-an-export.md)                   |
| Build a purpose-specific frontend outside Python          | [Build a browser application](browser-applications.md)      |
| Follow changing exports from a browser application        | [Serve a prepared publication](prepared-publications.md)    |
| Add an application-specific output format                 | [Create a custom representation](custom-representations.md) |
| Ground an answer or generated application in export data  | [Use with agents](agents-and-automation.md)                 |
| Verify files and configure the static host                | [Deploy an export](deploy.md)                               |
| Diagnose preparation, verification, and browser failures  | [Troubleshoot notebook exports](troubleshooting.md)         |

Read [How notebook exports work](../overview.md) when you need the complete
model for notebooks, `ExportSpec`, states, outputs, assets, and consumers. Use
the [Reference](../reference/) for exact CLI, Python, TypeScript, and format
contracts.
