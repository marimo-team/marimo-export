---
title: Understand notebook exports
description: See how states, outputs, reuse, caching, publication, integrity, and trust fit together.
---

# Understand notebook exports

marimo-export runs selected notebook states, publishes named outputs, and writes
a notebook export that other programs can read. These pages explain how the
states, outputs, files, and readers fit together.

Start with [What is marimo-export?](../overview) for the complete path or [build
your first notebook export](../guide/getting-started) to run it.

- [Choose notebook states](states-and-inputs) shows how sparse rows become
  complete input values that readers can select.
- [Store and load outputs](outputs-and-representations) shows how notebook results
  receive stable names and stored forms.
- [Reuse earlier results](preparation-and-reuse) explains when states
  run and when earlier results can be reused.
- [How notebook caching fits](caching) separates notebook cell caching from
  reusing finished export results.
- [Update an application with new exports](exports-and-publications) compares one
  fixed export URL with a stable route that follows newer exports.
- [Verify and trust an export](integrity-and-trust) explains what verification
  checks, how to trust a publisher, and when an output can run browser code.

[Choose a guide](../guide/) for a complete task or use
[Terminology](../reference/terminology) for an exact definition.
