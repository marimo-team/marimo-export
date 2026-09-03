---
title: Concepts
description: Learn the state, output, preparation, reuse, integrity, and trust model behind notebook exports.
---

# Concepts

One notebook export is a finite table that pairs every complete input state with
the same named output representations. marimo-export calls this table the
state-output relation. The concept pages build that model in four steps.

| Read                                                          | Question answered                                                | Result                                                             |
| ------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------ |
| [States and inputs](states-and-inputs.md)                     | Which notebook input assignments are available?                  | Predict normalization, aliases, defaults, and state selection      |
| [Outputs and representations](outputs-and-representations.md) | What result does each output publish, and how is it stored?      | Choose source kinds, exporters, and consumer loaders               |
| [Preparation and reuse](preparation-and-reuse.md)             | Which work runs again after a change?                            | Choose `plan`, `prepare`, `capture`, `build`, and repository reuse |
| [Integrity and trust](integrity-and-trust.md)                 | What does verification prove, and which code receives authority? | Verify files and place trust at the correct boundary               |

Use [How notebook exports work](../overview.md) for the end-to-end lifecycle.
Use [Terminology](../reference/terminology.md) when you need the exact meaning of
one project noun.
