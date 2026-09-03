---
title: Guides
description: Build, inspect, consume, and deploy notebook exports from saved notebooks and live marimo sessions.
---

# Guides

Start with the deterministic quickstart, then choose the task that matches the
next boundary in your application.

## Start

1. [Build your first notebook export](getting-started) creates two exported
   states, verifies the directory, and reads one result from Python.
2. [Run the market dashboard](market-dashboard) builds a browser application
   with five states and several output representations.

## Author an export

- [Choose states and outputs](choose-states) inspects notebook inputs and
  declares the finite results consumers can select.
- [Build or capture](build-and-capture) chooses a saved notebook or a running
  marimo session as the producer.

## Consume an export

- [Read an export](consume-an-export) compares Python, browser, agent, and
  custom-reader paths.
- [Build a browser application](browser-applications) resolves exported
  states, loads output representations, and owns mount disposal.
- [Serve a prepared publication](prepared-publications) lets a browser follow
  a changing manifest that points at immutable exports.
- [Use exports with agents](agents-and-automation) retains the state,
  representation, and verification evidence behind an answer.
- [Create a representation](custom-representations) pairs a Python exporter
  with a validating consumer loader.

## Operate the producer and files

- [Manage repository storage](manage-repository) inspects reuse, retention,
  and observation history.
- [Deploy an export](deploy) serves the written directory and configures
  browser security policy.
- [Troubleshoot](troubleshooting) starts from observable producer, repository,
  reader, and browser failures.

Use [Reference](../reference/index) for exact command, Python, TypeScript, and
format contracts.
