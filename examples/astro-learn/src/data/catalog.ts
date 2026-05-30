import type { LearnCatalog } from "@/types";

export const fixtureCatalog: LearnCatalog = {
  notebooks: [
    {
      name: "01_introduction.py",
      path: "altair/01_introduction.py",
      slug: "altair--01-introduction",
      title: "Introduction to Altair",
      description:
        "Introduces Altair renderers, source data, chart objects, marks, encodings, and interactive selections.",
      topic: "altair",
      cell_count: 59,
    },
    {
      name: "02_marks_encoding.py",
      path: "altair/02_marks_encoding.py",
      slug: "altair--02-marks-encoding",
      title: "Data Types, Graphical Marks, and Visual Encoding Channels",
      description:
        "Covers nominal, ordinal, quantitative, and temporal data through marks and encoding channels.",
      topic: "altair",
      cell_count: 102,
    },
    {
      name: "01_least_squares.py",
      path: "optimization/01_least_squares.py",
      slug: "optimization--01-least-squares",
      title: "Least Squares",
      description:
        "Builds a small least-squares optimization problem and links the result to further reading.",
      topic: "optimization",
      cell_count: 9,
    },
    {
      name: "04_quadratic_program.py",
      path: "optimization/04_quadratic_program.py",
      slug: "optimization--04-quadratic-program",
      title: "Quadratic Program",
      description: "Solves a constrained quadratic program and visualizes the feasible region.",
      topic: "optimization",
      cell_count: 15,
    },
    {
      name: "01_wiggly.py",
      path: "tools/01_wiggly.py",
      slug: "tools--01-wiggly",
      title: "wigglystuff Widgets",
      description: "Shows Slider2D, Matrix, HoverZoom, and TextCompare widgets inside marimo.",
      topic: "tools",
      cell_count: 14,
    },
    {
      name: "02_formative.py",
      path: "tools/02_formative.py",
      slug: "tools--02-formative",
      title: "Formative Assessment Widgets",
      description: "Presents concept maps, flashcards, labeling questions, and matching questions.",
      topic: "tools",
      cell_count: 28,
    },
  ],
};
