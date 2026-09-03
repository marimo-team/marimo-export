---
layout: home
title: "marimo-export: Prepare notebook results for any application"
titleTemplate: false
description: Select the marimo notebook states and outputs an application needs, prepare them once, and read the resulting export from Python, TypeScript, agents, or custom clients.

hero:
  text: Prepare notebook results for any application.
  tagline: Select the notebook states and outputs your application needs. Run them through marimo, write one portable export, then read it from Python, TypeScript, agents, or custom clients.
  image:
    light: /brand/marimo-export-lockup-stacked-light.svg
    dark: /brand/marimo-export-lockup-stacked-dark.svg
    alt: marimo-export
  actions:
    - theme: brand
      text: Build your first export
      link: ./guide/getting-started
    - theme: alt
      text: Why export notebook states?
      link: ./why

features:
  - title: Declare finite states
    icon:
      light: /feature-icons/route-light.svg
      dark: /feature-icons/route-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Name the state rows and outputs that every consumer can select.
    link: ./concepts/states-and-inputs
    linkText: Learn states and inputs
  - title: Reuse prepared results
    icon:
      light: /feature-icons/database-light.svg
      dark: /feature-icons/database-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Reuse matching states and complete exports across producer runs.
    link: ./concepts/preparation-and-reuse
    linkText: Understand reuse
  - title: Build or capture
    icon:
      light: /feature-icons/git-fork-light.svg
      dark: /feature-icons/git-fork-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Prepare from a saved notebook or a named live marimo session.
    link: ./guide/build-and-capture
    linkText: Choose a producer
  - title: Read from any client
    icon:
      light: /feature-icons/braces-light.svg
      dark: /feature-icons/braces-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Resolve exported states and load named outputs from Python or TypeScript.
    link: ./guide/consume-an-export
    linkText: Choose a reader
  - title: Publish and deploy
    icon:
      light: /feature-icons/cloud-upload-light.svg
      dark: /feature-icons/cloud-upload-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Serve immutable exports and configure caching, origins, and browser policy.
    link: ./guide/deploy
    linkText: Deploy an export
  - title: Verify before use
    icon:
      light: /feature-icons/shield-check-light.svg
      dark: /feature-icons/shield-check-dark.svg
      alt: ""
      width: "24"
      height: "24"
      wrap: true
    details: Check canonical index bytes and every declared asset before consumption.
    link: ./concepts/integrity-and-trust
    linkText: Understand integrity
---
