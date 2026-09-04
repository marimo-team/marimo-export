---
layout: home
title: "marimo-export: Prepare notebook results. Share them anywhere."
titleTemplate: false
description: Select the states and outputs to publish from a marimo notebook. marimo-export writes a portable, verified notebook export that browser applications and agents read without a Python runtime or a copy of the notebook source.

hero:
  text: Prepare notebook results. Share them anywhere.
  tagline: Select the states and outputs to publish from a marimo notebook. marimo-export writes a portable, verified notebook export that browser applications and agents read without a Python runtime or a copy of the notebook source.
  image:
    light: /brand/marimo-export-lockup-stacked-light.svg
    dark: /brand/marimo-export-lockup-stacked-dark.svg
    alt: marimo-export
  actions:
    - theme: brand
      text: Get started
      link: ./guide/getting-started
    - theme: alt
      text: When to use it
      link: ./why

features:
  - title: Select states
    icon:
      light: /feature-icons/route-light.svg
      dark: /feature-icons/route-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Name the notebook input combinations available to every consumer.
    link: ./concepts/states-and-inputs
    linkText: States
  - title: Publish outputs
    icon:
      light: /feature-icons/braces-light.svg
      dark: /feature-icons/braces-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Expose structured data, rendered output, files, tables, charts, and widgets under stable names.
    link: ./concepts/outputs-and-representations
    linkText: Outputs
  - title: Read static files
    icon:
      light: /feature-icons/shield-check-light.svg
      dark: /feature-icons/shield-check-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Browser applications and agents verify and read the export without a Python runtime or notebook source.
    link: ./guide/consume-an-export
    linkText: Read an export
---

## Notebook and exported app

Switch between the original [marimo](https://marimo.io/) notebook and a browser
application built from five exported states. The application reads static files
and starts no Python or WebAssembly runtime.

<StaticApp compact />
