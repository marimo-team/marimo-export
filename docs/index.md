---
layout: home
title: "marimo-export: Prepare notebook results. Read them anywhere."
titleTemplate: false
description: Select the states to run through marimo and the outputs to publish. marimo-export writes them as a portable, verified notebook export. Browser applications and agents read it after the Python producer stops. They need neither its runtime nor the notebook source code.

hero:
  text: Prepare notebook results. Read them anywhere.
  tagline: Select the states to run through marimo and the outputs to publish. marimo-export writes them as a portable, verified notebook export. Browser applications and agents read it after the Python producer stops. They need neither its runtime nor the notebook source code.
  image:
    light: /brand/marimo-export-lockup-stacked-light.svg
    dark: /brand/marimo-export-lockup-stacked-dark.svg
    alt: marimo-export
  actions:
    - theme: brand
      text: Build your first export
      link: ./guide/getting-started
    - theme: alt
      text: When to use marimo-export
      link: ./why
---

## Explore the exported dashboard

The Notebook tab opens a static HTML export of the original
[marimo](https://marimo.io/) source and its captured outputs. The Exported app
tab reads five exported states from a verified notebook export. The application
starts no Python runtime, WebAssembly runtime, or application server.

<StaticApp />

[Run the market dashboard](guide/market-dashboard.md) or
[build your first export](guide/getting-started.md).

## How marimo-export works

- [When to use marimo-export](why) compares published results with a Python
  service and browser Python.
- [What is marimo-export?](overview) traces notebook source into states,
  outputs, files, and consumer views.
- [How notebook caching fits](concepts/caching) separates marimo cell reuse from
  reusing completed exports.
- [Verify and trust an export](concepts/integrity-and-trust) explains file
  verification, publisher trust, and when browser code can run.

Verification checks that the files match `index.json`. A trusted origin
establishes who published that index.
