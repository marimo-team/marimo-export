---
title: Concepts
description: Learn the state, output, preparation, reuse, integrity, and trust model behind notebook exports.
---

# Understand the model

A notebook export contains a finite **state-output relation**. Every exported
state has one complete input vector and the same set of named outputs. Each
output has one representation that its readers know how to decode.

Read the concepts in the order the producer encounters them:

1. [States and inputs](states-and-inputs) explains authored state rows,
   captured baseline values, complete input vectors, aliases, and observations.
2. [Outputs and representations](outputs-and-representations) follows one
   notebook result through an output source, descriptor, optional asset, loader,
   and mount.
3. [Preparation and reuse](preparation-and-reuse) separates planning,
   prepared-state reuse, the marimo computation cache, and the leased prepared
   export.
4. [Integrity and trust](integrity-and-trust) distinguishes index validation,
   asset verification, publisher authentication, producer execution, and browser
   authority.

[What is marimo-export?](../overview) shows the complete lifecycle first.
Use [Terminology](../reference/terminology) for exact lookup after the model
is familiar.
