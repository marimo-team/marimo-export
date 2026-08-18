---
layout: home
title: "marimo-export: Precompute notebook results. Use them anywhere."
titleTemplate: false
description: Precompute selected marimo notebook results as one verified export for applications, agents, Python automation, and custom clients.

hero:
  text: Precompute notebook results. Use them anywhere.
  tagline: Run selected notebook states through marimo and package the results as one verified export.
  image:
    light: /brand/marimo-export-lockup-stacked-light.svg
    dark: /brand/marimo-export-lockup-stacked-dark.svg
    alt: marimo-export
  actions:
    - theme: brand
      text: Run example
      link: ./guide/getting-started
    - theme: alt
      text: Create an export
      link: ./guide/choose-states
    - theme: alt
      text: How it works
      link: ./overview

features:
  - title: Static delivery
    icon:
      light: /feature-icons/cloud-upload-light.svg
      dark: /feature-icons/cloud-upload-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Serve the same export without a live Python kernel.
    link: ./guide/deploy
    linkText: Deploy
  - title: marimo execution
    icon:
      light: /brand/marimo-logo-light.svg
      dark: /brand/marimo-logo-dark.svg
      alt: ""
      width: "32"
      height: "32"
      wrap: true
    details: Run states and representation exporters through marimo's graph and cache.
    link: ./overview
    linkText: How it works
  - title: Python and browser readers
    icon:
      light: /feature-icons/braces-light.svg
      dark: /feature-icons/braces-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Open the same prepared states and outputs from Python or TypeScript.
    link: ./guide/consume-an-export
    linkText: Consume
  - title: Agent-readable outputs
    icon:
      light: /feature-icons/bot-light.svg
      dark: /feature-icons/bot-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Give agents structured data with provenance and content identity.
    link: ./guide/agents-and-automation
    linkText: Use with agents
  - title: Build or capture
    icon:
      light: /feature-icons/git-fork-light.svg
      dark: /feature-icons/git-fork-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Start from a notebook file or reuse an active session.
    link: ./guide/build-and-capture
    linkText: Choose a producer
  - title: Verified exports
    icon:
      light: /feature-icons/badge-check-light.svg
      dark: /feature-icons/badge-check-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Check the index and every declared asset before use.
    link: ./reference/export-format
    linkText: Export format
---

## Keep Python in the producer environment

The producer can use private data, Python packages, credentials, and expensive
computation. Applications, agents, Python automation, and custom clients
consume the completed export.

A request that needs a new Python result requires another export or a Python
service.

## Start with one complete path

[Run the market dashboard](guide/getting-started.md) to precompute five
result sets from a live Yahoo Finance notebook, verify the export, and open the
vanilla TypeScript application that consumes it.
