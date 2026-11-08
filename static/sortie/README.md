A mission-briefing interface for tools you operate rather than browse: dark ground, translucent slabs, hairlines that overrun their corners, uppercase labels with a square bullet, one amber action. Built from the garage and briefing screens of Armored Core VI as the visual reference; the components here are original work in that idiom, for dashboards, experiment browsers, parameter sheets and anything else that wants to feel like a cockpit.

## Principles

1. **Everything is a readout.** A screen is a set of labelled values. Every block starts with an Eyebrow that says what it is; the content under it is the value.
2. **Hairlines, not boxes.** Depth comes from translucency (`panel` over `bg-100` with the ground texture showing through) and 1px rules, never from shadows or radius. Nothing is rounded.
3. **One action colour.** `accent` amber marks the single thing the user can do next. Gold, magenta, blue and red each have one meaning (below); none of them is decoration.
4. **Quiet texture.** Microtext, grids and scanlines are allowed as texture at `ink-faint`, and never carry information.

## Voice

Sentence case for everything a person reads (`Repeat briefing`, `Attack the Watchpoint`); CSS uppercases labels and titles, so write them in sentence case too. Verbs first on controls (`Sortie`, `Abort mission`). Terse, declarative, no exclamation marks, no emoji. Numbers are right-aligned with tabular figures, units in the label (`Weight, kg`), thousands separated (`380,000`). A chapter, path or section is prefixed `//` in a Tag (`//CHAPTER 1`, `//RUN 0042`).

## Colour

Two themes: `hangar` (dark, the default and the reference) and `daylight`, for documents and print. Text pairs below meet 4.5:1 in both.

- Ground: `bg-000` (void) under `bg-100` (screen) under `bg-200` (wells and alternate rows). Give the screen body `srt-ground` for the grid-and-scanline texture.
- Surfaces: `panel` (translucent slab), `panel-solid` over media, `panel-raised` for rows and buttons, `panel-selected` for the current item.
- Rules: `line` for every hairline; `line-strong` only for crop-mark corners, focused fields and frames.
- Text: `ink` on any surface; `ink-muted` for Eyebrows and secondary lines; `ink-faint` for texture and placeholders only.
- Meaning: `accent` amber = act now (one primary Button, the `Concludes Chapter` note, an active gauge). `gold` = the one callout on a screen (`PRIMARY TARGET`). `target` magenta = hostile, critical, destructive. `corp` blue = identity, who sent it. `lamp` red = a fault lamp or error text, never a fill. `ok` green = done. Success and danger differ in lightness as well as hue; each also carries a word.
- `focus` is a solid 1px ring at 2px offset on everything interactive.

## Type

Three faces from Google Fonts, no files to ship:

- **Michroma** (`label`) for Eyebrows and prompts at 10–13px with wide tracking. It is only ever uppercase and only ever small.
- **Chakra Petch** (`display`) for screen titles, panel titles, headings and every numeral. Titles uppercase with tracking; headings sentence case.
- **Saira** (`body`) for running text, NavList items and table labels at 13–16px.
- **Share Tech Mono** (`mono`) for Tags, IDs, coordinates and microtext.

Load them with one link: `https://fonts.googleapis.com/css2?family=Michroma&family=Chakra+Petch:wght@400;500;600&family=Saira:wght@400;500&family=Share+Tech+Mono&display=swap`. Use the style classes from `tokens.css` (`.eyebrow`, `.screen-title`, `.heading`, `.numeral`, `.body`, `.readout`) or the component classes, which set them for you.

## Spacing, radius, borders

A 4px grid, `space-1` to `space-8`. Panels pad at `space-5` (20px), which is also `bracket-len`, the overrun of a Panel's rules and the length of a crop-mark corner. Panels in a screen sit `space-6` apart and `space-6` from the screen edge, so the overruns have room. `radius-0` everywhere except the 6px `srt-lamp` dot. All borders are `hairline` (1px). No shadows; the one glow is `glow-target` around a hostile TargetFrame.

## Layout

A screen is `srt-screen`: a `srt-screen-title` on its rule, a `srt-screen-body` grid (sidebar 240px, then flexible columns at `space-6` gaps), a `srt-prompts` bar on `bg-000`. See MissionScreen for the whole composition. Below 900px stack the columns; the NavList becomes a tab row.

## Components

Eyebrow, Panel, Tag, Button, NavList, StatTable, Gauge, TargetFrame, Comms, Field, PromptBar, and the MissionScreen reference page. Each README says what the consumer provides and the one or two rules that keep it in character. All classes are prefixed `srt-`; state is `is-*`; selection is `aria-current`.

## Iconography

There is no icon set. Glyphs are typographic: the square bullet (`■`) before labels, `»` between a label and its value, `›` boxed before a primary action, the five dots before a sender name, the triangle load marker. Key prompts use Unicode shapes (`✕ ○ △`) or key names. If a project needs pictorial icons, use a single-weight 1px line set (Lucide or Tabler at `stroke-width: 1`) in `ink-muted`, and never filled or coloured icons.

## Using it

Web: load `tokens.css` then `components/bundle.css`, put `class="srt srt-ground"` on `<body>` (`data-theme="daylight"` on `<html>` for the light theme), and compose with the classes above.

Python plots (matplotlib, the JAX/scientific side): set the figure and axes facecolor to `bg-100`, spines and ticks to `line-strong`, text to `ink-muted`, the headline series in `ink` and the series you are talking about in `accent`; secondary series in `corp`; the thing that is wrong in `target`. Titles in Chakra Petch uppercase, axis labels in Saira, tick labels in Share Tech Mono. Turn on a 48px-equivalent grid at `line` with alpha 0.35 and drop the top and right spines. One accent series per figure, as one primary Button per screen.
