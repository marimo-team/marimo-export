---
version: alpha
name: marimo
description: Design system for marimo, a reactive Python notebook that is reproducible, git-friendly, and deployable as scripts or apps.
colors:
  primary: "#0880EA"
  primary-dark: "#28879F"
  on-primary: "#F8FAFC"
  on-primary-dark: "#B6ECF7"
  secondary: "#F1F5F9"
  secondary-dark: "#ECEEED"
  on-secondary: "#0F172A"
  on-secondary-dark: "#252927"
  background: "#FFFFFF"
  background-dark: "#181C1A"
  foreground: "#0F172A"
  foreground-dark: "#ECEEED"
  surface: "#FFFFFF"
  surface-dark: "#252927"
  surface-muted: "#F1F5F9"
  surface-muted-dark: "#020303"
  muted-foreground: "#64748B"
  muted-foreground-dark: "#AAB2AF"
  card: "#FFFFFF"
  card-dark: "#252927"
  card-foreground: "#0F172A"
  card-foreground-dark: "#C0C6C3"
  popover: "#FFFFFF"
  popover-dark: "#252927"
  popover-foreground: "#0F172A"
  popover-foreground-dark: "#AAB2AF"
  border: "#E2E8F0"
  border-dark: "#3B403E"
  input: "#A3A3A3"
  input-dark: "#474C4A"
  accent: "#EDF6FF"
  accent-dark: "#1D5B6A"
  on-accent: "#0B68CB"
  on-accent-dark: "#B6ECF7"
  ring: "#94A3B8"
  action: "#FEF2A5"
  action-hover: "#FFF8BB"
  on-action: "#946800"
  destructive: "#FF6666"
  on-destructive: "#F8FAFC"
  error: "#EA5D5D"
  on-error: "#F8FAFC"
  success: "#66FF7F"
  on-success: "#F8FAFC"
  stale: "#AF8931"
  link: "#0B68CB"
  link-dark: "#479BF5"
  link-visited: "#8E4EC6"
  link-visited-dark: "#BF9BDF"
  code-background-dark: "#282C34"
  code-comment: "#708090"
  code-comment-dark: "#6B7280"
  data-grid-dark: "#18181B"
  data-grid-dark-alt: "#27272A"
  data-grid-accent: "#7C3AED"
  chart-selection: "#669EFF"
  reactive-reference: "#005F87"
typography:
  body-md:
    fontFamily: "PT Sans"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
  body-sm:
    fontFamily: "PT Sans"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.25
    letterSpacing: "0em"
  body-xs:
    fontFamily: "PT Sans"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.25
    letterSpacing: "0em"
  label-md:
    fontFamily: "PT Sans"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "0em"
  label-xs:
    fontFamily: "PT Sans"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.025em"
  ui-h1:
    fontFamily: "PT Sans"
    fontSize: "36px"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "0em"
  ui-h2:
    fontFamily: "PT Sans"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0em"
  ui-h3:
    fontFamily: "PT Sans"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0em"
  ui-h4:
    fontFamily: "PT Sans"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0em"
  markdown-heading:
    fontFamily: "Lora"
    fontSize: "32px"
    fontWeight: 400
    lineHeight: 1.25
    letterSpacing: "0em"
  code-editor:
    fontFamily: "Fira Mono"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
  code-inline:
    fontFamily: "Fira Mono"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "0em"
  console:
    fontFamily: "Fira Mono"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0em"
  slide-h1:
    fontFamily: "PT Sans"
    fontSize: "70px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0em"
  slide-body:
    fontFamily: "PT Sans"
    fontSize: "24px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
rounded:
  none: "0px"
  sm: "4px"
  md: "6px"
  lg: "8px"
  cell: "10px"
  xl: "12px"
  error-output: "20px"
  full: "9999px"
spacing:
  none: "0px"
  hairline: "1px"
  xxs: "2px"
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  2xl: "24px"
  3xl: "32px"
  4xl: "48px"
  cell-output-padding: "16px"
  card-padding: "24px"
  page-padding-sm: "16px"
  page-padding-md: "80px"
  page-padding-xl: "96px"
  content-compact: "740px"
  content-medium: "1110px"
  content-wide: "1400px"
  markdown-max-width: "80ch"
  grid-row-height: "20px"
  grid-columns: "24"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "12px"
  button-action:
    backgroundColor: "{colors.action}"
    textColor: "{colors.on-action}"
    typography: "{typography.label-md}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "12px"
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.on-secondary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "12px"
  button-icon:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.label-md}"
    rounded: "{rounded.md}"
    height: "24px"
    width: "24px"
    padding: "0px"
  input:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.code-editor}"
    rounded: "{rounded.sm}"
    height: "24px"
    padding: "6px"
  select:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    height: "24px"
    padding: "8px"
  cell:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body-md}"
    rounded: "{rounded.cell}"
    width: "100%"
    padding: "0px"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.card-foreground}"
    typography: "{typography.body-md}"
    rounded: "{rounded.xl}"
    padding: "{spacing.card-padding}"
  popover:
    backgroundColor: "{colors.popover}"
    textColor: "{colors.popover-foreground}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    width: "288px"
    padding: "16px"
  tooltip:
    backgroundColor: "{colors.popover}"
    textColor: "{colors.popover-foreground}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: "12px"
  badge:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-xs}"
    rounded: "{rounded.full}"
    padding: "8px"
  tabs-list:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.muted-foreground}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: "4px"
  tabs-trigger-active:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    padding: "12px"
  dialog:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body-md}"
    rounded: "{rounded.lg}"
    width: "672px"
    padding: "24px"
  alert-info:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.lg}"
    padding: "16px"
  progress:
    backgroundColor: "{colors.secondary}"
    rounded: "{rounded.full}"
    height: "8px"
    width: "100%"
  slider-thumb:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.full}"
    height: "16px"
    width: "16px"
  callout:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body-md}"
    rounded: "{rounded.lg}"
    padding: "48px"
---

## Overview

marimo should feel like a modern programming notebook, not a marketing page or a generic dashboard. The product surface is dense, precise, and calm: code, outputs, data tables, controls, panels, and runtime state should stay readable for long working sessions. The design supports two related jobs:

- authoring notebooks in an editor with cells, side panels, package/runtime tools, AI affordances, and fast code interaction
- publishing notebooks as readable apps, reports, dashboards, grids, or slides without exposing unnecessary editor chrome

The main source files for this design system are `frontend/src/css/globals.css`, `frontend/tailwind.config.cjs`, `frontend/src/css/app/*.css`, `frontend/src/components/ui/*`, and the editor renderers under `frontend/src/components/editor/renderers/*`.

The visual language is compact, software-native, and output-first. It uses white or near-black work surfaces, slate borders, a clear blue primary color, yellow action/stale affordances, Radix color scales for semantic states, and small solid shadows to make active controls feel tactile without making the page feel heavy.

## Colors

The color model is CSS-variable first. Tailwind utilities map to variables such as `--background`, `--foreground`, `--primary`, `--accent`, `--action`, `--border`, and `--ring`. These variables are scoped under `:root` and `.marimo`, with dark mode enabled by a `.dark` class and `color-scheme: dark`.

- **Primary blue (`#0880EA` light, `#28879F` dark):** the main interactive color for primary buttons, selected borders, active slider/progress fills, and high-confidence focus indicators.
- **Surface whites and greens/blacks:** light mode is mostly white with slate text. Dark mode is a green-tinted charcoal (`#181C1A`) with muted green-gray foregrounds.
- **Muted slate (`#F1F5F9`, `#64748B`):** secondary controls, inactive text, panel metadata, low-priority chrome, and grouped tab backgrounds.
- **Accent blue wash (`#EDF6FF` light, `#1D5B6A` dark):** hover, selection, active menus, and contextual emphasis that should not read as a primary action.
- **Action yellow (`#FEF2A5`):** special action buttons and stale/needs-run affordances. It should read as "attention or manual action", not as warning.
- **Error/destructive red (`#EA5D5D`, `#FF6666`):** cell errors, destructive buttons, error banners, and deleted generated cells.
- **Success green (`#66FF7F`):** success badges, pass states, and accepted statuses.
- **Links:** use blue links and purple visited links, with hover underline. Markdown links inherit these tokens.
- **Radix scales:** component details use Radix CSS variables such as `--slate-*`, `--blue-*`, `--sky-*`, `--yellow-*`, `--amber-*`, `--red-*`, and `--grass-*`. Use those scale variables for local semantic variants instead of hard-coding one-off hex values.

Keep the palette functional. Most screens should be dominated by background, foreground, border, muted, and one interactive color. Bright semantic colors should appear only where they carry state.

## Typography

marimo uses three local font families:

- **PT Sans:** default UI and prose font. It is the font for body text, labels, buttons, panel chrome, and most application UI.
- **Lora:** markdown and authored-notebook headings. It gives reports and notebook prose a more literary, document-like voice without affecting dense controls.
- **Fira Mono:** code editor, inline code, inputs that edit code-like values, console output, JSON output, and traceback text.

The app globally sets `body` and `:host` to `var(--text-font)`, smooths text rendering, and sets `code` to `var(--monospace-font)`. Markdown headings use `var(--heading-font)`.

Controls are intentionally small. Buttons and form controls generally use `text-sm`; compact labels and panel tabs often use `text-xs`, uppercase, semibold/bold, and modest tracking. Code editor size comes from `--marimo-code-editor-font-size`, defaulting to 14px in config and 0.9rem in CSS.

On shorter screens the root HTML font size is 90%; on screens at least 1000px tall it returns to 100%. Do not add extra viewport-based type scaling beyond the existing app-level rule.

Slides are a separate presentation surface. Their prose plugin approximates Google Slides sizing: 70px h1, 48px h2, 37px h3, 33px h4, and 24px body/code text.

## Layout

The main app container is `#App`: a full-height flex column with `bg-background`, `text-foreground`, vertical scrolling, and width behavior controlled by app config.

Default notebook widths:

- **compact:** max width 740px
- **medium:** max width 1110px, the default config
- **full:** full available width
- **columns:** horizontally scrollable, multi-column notebook authoring

Vertical layout wraps cells in responsive horizontal padding: 4px at the smallest width, then 64px, 80px, and 96px at larger breakpoints. It also keeps generous bottom padding for mobile browser chrome. Cells stack with 20px gaps in standard vertical columns, and read mode adds gaps when source code is shown.

Editor chrome uses resizable side and bottom panels:

- left sidebar/helper panel for files, variables, dependencies, packages, outline, docs, snippets, AI, errors, scratchpad, tracing, secrets, logs, terminal, and cache
- bottom developer panel with draggable tabs and runtime status items
- resize handles are 4px and turn slate on hover or active drag

Grid layout is explicit and mechanical: 24 columns, 20px row height, max width 1400px, bordered by default, and backed by `react-grid-layout`. In edit mode, the grid displays a subtle slate cell grid and drag handles; in read mode, transitions are disabled and content gets 20px container padding.

Slides layout is output-only. In read mode it uses a padded, near-full-height reveal deck. In edit/present workflows it pairs a slide minimap with the deck and caps height so slides fit without page scrolling.

Markdown content is document-oriented. Keep paragraphs, lists, tables, tabs, admonitions, and callouts readable inside notebook outputs without forcing all UI widgets into the markdown max width. The configured markdown max width is 80ch.

## Elevation & Depth

Depth is tactile but restrained. The base elevation system is a mix of soft shadows and "solid" offset shadows defined as CSS theme tokens:

- `shadow-xs` through `shadow-2xl` use low-opacity, blurred gray shadows.
- `shadow-xs-solid` through `shadow-2xl-solid` use offset, sharper shadows that make controls feel pressable.
- Focused interactive cells use `shadow-md-solid` with the shade color.
- Error outlines and callouts can use colored solid shadows, such as red, blue, amber, sky, or grass.

Use borders first for hierarchy. Panels, cells, cards, popovers, menus, tabs, and inputs all use borders. Shadows should emphasize focus, hover, dragged surfaces, popovers, menus, dialogs, and explicit callouts.

Avoid large decorative shadows and floating section cards. marimo's surfaces should feel like application tools and document outputs, not stacked marketing tiles.

## Shapes

The base radius token is 8px. Tailwind maps this to:

- `rounded-sm`: 4px
- `rounded-md`: 6px
- `rounded-lg`: 8px

Common local shapes:

- cells use 10px outer radius, with 9px inner edge handling for first/last child sections
- cards use `rounded-xl`, approximately 12px
- inputs and selects use 4px
- menu items and active rows use 4px
- dialogs, alerts, popovers, and tooltips use 6-8px
- sliders, switches, badges, progress bars, and pills use full radius
- JSON/error output may be more rounded, up to 20px, when it needs to read as a contained output block

Do not mix sharp and highly rounded shapes in the same local control group. Dense editor chrome should stay closer to 4-8px. Notebook content callouts may use 8-12px.

## Components

**Buttons.** Buttons use `inline-flex`, centered content, `text-sm`, medium weight, `rounded-md`, focus-visible ring, disabled opacity 50%, and compact heights. The default button is primary blue with white text, border, and `shadow-xs`; active state removes the shadow. The `action` variant is yellow and is reserved for important notebook actions. Ghost/text/link variants are low chrome and rely on opacity, accent hover, or underline.

**Icon buttons.** Icon buttons are usually 24px square. Prefer existing Lucide/Radix iconography inside buttons and use tooltips for unclear actions. Avoid text-only controls when a standard icon already communicates the action.

**Inputs and textareas.** Inputs are compact: 24px high, 4px radius, code font, background surface, input border, solid shadow, hover shadow, and stronger solid focus shadow. Textareas use the same visual grammar with more padding and min height. Place icons as absolute adornments and keep placeholders muted.

**Selects and menus.** Select triggers share the compact input shape but use prose font. Menu and select content use popover background, popover foreground, border, 6px radius, 4px internal padding, `shadow-md`, and 32px minimum width. Menu items are 14px, 6px vertical padding, 8px horizontal padding, 4px radius, and use accent background/text on focus or selection.

**Tabs.** Tabs sit on a muted rounded background with 4px padding. Active triggers use background surface, foreground text, and a small shadow. Markdown tabbed sets use bottom borders and active primary underline rather than pill shadows.

**Cards.** Cards exist for genuinely framed repeated content or modal-like groupings. They use card background, card foreground, border, `rounded-xl`, shadow, and 24px padding. Do not put cards inside cards.

**Notebook cells.** Cells are the central surface. In edit mode they are 100% width, 10px radius, one-pixel gray border, divided internal sections, and background surface. Hover darkens the border. Focus lifts the cell with a solid shadow. Error cells use red outlines/shadows. Needs-run cells use stale/action yellow outlines. Published cells remove border and shadow so reports read as documents.

**Code editor.** CodeMirror uses Fira Mono, a transparent border, 3px padding, 24px right padding, and subtle active-line highlights. Light editor background is white; dark editor background is `#282C34`. Reactive references use a blue underline and become thicker on hover.

**Outputs.** Output areas use 16px padding, flow-root layout, and overflow auto. Standard output and tracebacks use Fira Mono. Error output is bold, red, rounded, lightly tinted, and preserves whitespace. Stale outputs fade and desaturate with a short delayed transition.

**Markdown.** Markdown links use link tokens and visited-link tokens. Markdown headings use Lora. Markdown tabs, critic markup, tables, admonitions, and inline code are styled explicitly so authored notebooks render as polished documents.

**Tables and dataframes.** Tables scroll horizontally when needed. Row stripes use a very light lime surface and card surface; hover uses a yellow surface. Cells are right-aligned by default with compact horizontal padding. Flush tables inside cells remove extra borders and inherit `--marimo-table-edge-padding`.

**Data grid editor.** The Glide data editor uses a 1.25 line height. Dark mode overrides use zinc-like surfaces (`#18181B`, `#27272A`, `#3F3F46`) with a purple accent (`#7C3AED`) and indigo search result. Keep data-grid styling utilitarian and high-density.

**Progress and sliders.** Tracks are 8px high, full radius, slate-200 in light mode and accent-tinted in dark mode. Filled ranges are blue in light mode and primary in dark mode. Slider thumbs are 16px circles with a solid shadow, blue border, white light background, and accent dark background.

**Alerts, callouts, and admonitions.** Alerts are 16px padded, bordered, 8px rounded blocks. Info uses sky/accent colors, warning uses yellow/amber, danger uses red, success/tip uses grass. Callouts are larger: 48px padding, 48px vertical margins, 8px radius, border, and a 4px solid colored offset shadow.

**Dialogs, popovers, and tooltips.** Dialog overlays use background at 80% opacity with a tiny backdrop blur. Dialog content is fixed, max 672px on desktop, 24px padded, bordered, and lightly shadowed. Popovers are 288px wide by default with 16px padding. Tooltips use 12px horizontal padding, 6px vertical padding, border, and a short 400ms provider delay.

**Chrome panels.** Side and bottom panels should favor scannable lists, compact controls, icons, and subtle borders. Panel headings use small uppercase semibold text in slate. Developer panel tabs use compact rounded rows with muted active background.

**Motion.** Motion is functional and short: accordion open/close at 200ms, delayed show at 200ms or 400ms, progress indeterminate at 1.5s, resize-handle color at 250ms, stale output fade at 300ms. Avoid decorative motion.

## Do's and Don'ts

- Do use the existing CSS variables and Tailwind semantic utilities before adding local hex colors.
- Do keep editor and panel controls compact; 24px inputs, 24px icon buttons, and 36px small buttons are normal.
- Do make notebook outputs readable as documents in run/read mode by removing unnecessary editor chrome.
- Do use borders, muted surfaces, and subtle solid shadows for hierarchy.
- Do preserve dark-mode behavior through `.dark`, `light-dark()`, and scoped `.marimo` variables.
- Do use Radix color scales for local semantic variants such as info, warning, danger, success, and admonitions.
- Do use Fira Mono for code, console output, JSON, tracebacks, and code-like inputs.
- Do use Lora for authored markdown headings and PT Sans for application UI.
- Do expose interaction state through hover, focus-visible rings, active shadows, disabled opacity, stale tinting, and error outlines.
- Do keep read-mode and published cells visually quieter than edit-mode cells.
- Do re-check WCAG contrast whenever changing primary, action, muted, or badge colors. Some exact source-derived pairs are intentionally subtle or compact and should not be copied into long body text without checking contrast.
- Don't create marketing-style hero sections, decorative cards, or large illustration-led layouts inside the product UI.
- Don't introduce one-off palettes for a feature when the semantic tokens or Radix scales already cover the state.
- Don't overuse primary blue; reserve it for primary actions, selection, progress, and clear focus.
- Don't use yellow as a generic warning color in notebook chrome; in marimo it often means action, stale, or needs-run.
- Don't make nested cards or card-like page sections. Use cards only for repeated items, modals, or genuinely framed tools.
- Don't hide important runtime state behind color alone; pair color with labels, icons, borders, or position.
- Don't add slow decorative animation. Motion should clarify state changes or loading.
- Don't let app chrome dominate published notebooks. In read mode, content should be the first visual priority.
