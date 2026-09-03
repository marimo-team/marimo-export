---
layout: home
title: "marimo-export: Turn notebook states into verified files"
titleTemplate: false
description: Select finite marimo notebook states and named outputs, prepare them once, and open the same notebook export from Python, browsers, agents, or custom clients.

hero:
  text: Turn notebook states into verified files.
  tagline: Select finite inputs and named outputs. Run them through marimo once, then open the same notebook export from Python, browser applications, agents, or custom clients.
  image:
    light: /brand/export-flow-light.svg
    dark: /brand/export-flow-dark.svg
    alt: A notebook and ExportSpec become a state-output grid consumed by Python, a browser, and an agent.
  actions:
    - theme: brand
      text: Build your first export
      link: ./guide/getting-started
    - theme: alt
      text: What is a notebook export?
      link: ./overview
    - theme: alt
      text: Browse reference
      link: ./reference/

features:
  - title: Understand the model
    icon:
      light: /brand/marimo-logo-light.svg
      dark: /brand/marimo-logo-dark.svg
      alt: ""
      width: "32"
      height: "32"
      wrap: true
    details: See how a notebook, ExportSpec, state, output, representation, and consumer fit together.
    link: ./overview
    linkText: Learn the model
  - title: Build or capture
    icon:
      light: /feature-icons/git-fork-light.svg
      dark: /feature-icons/git-fork-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Start an owned notebook process or borrow one named live session.
    link: ./guide/build-and-capture
    linkText: Choose a producer
  - title: Read the same export
    icon:
      light: /feature-icons/braces-light.svg
      dark: /feature-icons/braces-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Select exported states and load named outputs from Python or TypeScript.
    link: ./guide/consume-an-export
    linkText: Choose a consumer
  - title: Follow changing publications
    icon:
      light: /feature-icons/cloud-upload-light.svg
      dark: /feature-icons/cloud-upload-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Point one mutable manifest at verified immutable export instances.
    link: ./guide/prepared-publications
    linkText: Serve a publication
  - title: Extend representations
    icon:
      light: /feature-icons/bot-light.svg
      dark: /feature-icons/bot-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Pair a versioned Python BlobAsset exporter with a validating consumer loader.
    link: ./guide/custom-representations
    linkText: Add a representation
  - title: Diagnose and recover
    icon:
      light: /feature-icons/badge-check-light.svg
      dark: /feature-icons/badge-check-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Trace environment, repository, state, integrity, browser, and mount failures.
    link: ./guide/troubleshooting
    linkText: Troubleshoot
---

## One finite relation, several consumers

An `ExportSpec` names the input rows and outputs the application needs.
marimo-export completes each row from the notebook's initial input vector,
executes missing states through marimo, and writes one notebook export.

```text
notebook + ExportSpec
  -> complete states × named outputs
  -> index.json + declared assets
  -> Python | browser | agent | custom reader
```

The deployed browser selects results already present in that finite relation. A
request for another Python-derived input vector requires another preparation run
or a Python service.

## Follow the learning path

1. [Build your first export](guide/getting-started.md) from a local notebook.
2. [Learn the product model](overview.md) from the same two-state example.
3. [Choose states and outputs](guide/choose-states.md) for your notebook.
4. [Build or capture](guide/build-and-capture.md) from the environment that owns execution.
5. [Consume the export](guide/consume-an-export.md) from Python or a browser.

The [Guide](guide/) routes complete tasks. [Concepts](concepts/) explain the
state, output, reuse, and trust models. [Reference](reference/) defines exact
CLI, Python, browser, package, and wire contracts.
