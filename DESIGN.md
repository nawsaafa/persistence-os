# persistence-os — Design System

> Phase 1 of `~/Projects/conductor/tracks/content-operator-stack_20260502/`. Locked 2026-05-05.
> Sibling to `voice.md` — voice governs the words, this doc governs the visual surface. When a generated artifact (page, deck, IG carousel, OG card, slide) needs visual choices, this doc is canon.
> Source: the production Mimir landing at `/Users/nawfalsaadi/Projects/mimir-os/landing/index.html` is the canonical realization. Tokens here are extracted from that page's settled CSS, not invented fresh.
>
> Format: drafted in a hybrid of Google Stitch `DESIGN.md` shape (token tables, semantic naming) and awesome-design-md component-vocabulary patterns. The format settles by use across 5 brands; if format-divergence emerges by vertical 3, fold the differences into a `## Format` amendment here.

---

## 1. Brand identity (one sentence)

**Norse-canopy aesthetic over a regulated-grade substrate** — a paper-tier visual language that signals memo / paper / changelog more than SaaS / startup-bro / tech-creator, anchored in Yggdrasil iconography (eye-in-the-well, runes, branches, canopy under stars) without crossing into fantasy-game register.

The aesthetic must reinforce the voice: substrate-first, anti-marketing, customer-and-operator-credential credibility (per `voice.md` §3 Rule 4). Typography and palette do most of the work — gravity comes from the literary serif and the warm-parchment-on-void contrast, not from any visual flourish.

## 2. Color palette

| Token | Hex | Use |
|---|---|---|
| `void` | `#08080C` | Page background; the ground-zero of the visual hierarchy. Mimir landing body. |
| `stone` | `#14141C` | Card / section-divider surfaces one step above void. |
| `slate` | `#1F1F26` | Elevated container, code-block background, subtle layering. |
| `parch` | `#EDE8DD` | Primary text on void (off-white parchment, not pure white — the warm cast is load-bearing). |
| `mute` | `#8E8C85` | Secondary text, metadata, navigation labels. |
| `moon` | `#5C7290` | Cool accent — moonlight bloom under the canopy, navigation hover, secondary metadata. |
| `rune` | `#B8924B` | Primary brand accent — eye-in-the-well stroke, brand mark, headline highlights. The single most distinctive color. |
| `ember` | `#D9A14B` | Warm accent — schematic hover-stroke, CTA hover, focus-visible state. Slightly warmer than rune for hover/active states. |

**Rules:**
- Default to `void` background + `parch` text. Light-mode is **not** part of the palette; persistence-os surfaces are intentionally dark by default.
- `rune` is the brand. Use it for one anchor element per surface (the brand mark, the section-divider hairline, the schematic stroke). More than two `rune`-colored elements per viewport is over-strong.
- `ember` only on hover / focus / active states. Do not use as a default decoration color.
- `moon` is the cool counter to `rune`'s warm — pair them on the same surface only when there's a deliberate moonlight + ember dual-anchor (e.g. moonlight bloom centered on Yggdrasil trunk in hero).
- No Tailwind color escapes. If a generated UI introduces a hex outside this 8-token palette, the generation is wrong.

## 3. Typography

| Token | Family | Weights | Use |
|---|---|---|---|
| `display` | Cinzel | 400 / 500 / 600 / 700 | All H1, H2, brand mark, hero headline, section number. The literary serif anchor — signals paper / memo / inscription, not SaaS. |
| `body` | Inter | 300 / 400 / 500 / 600 | All body text, navigation, captions, form fields, footer. |
| `mono` | JetBrains Mono | 400 / 500 | Code, hashes, version strings, datoms, AGPL-3 license refs, command snippets. |

**Letter-spacing tokens:**
- `tracking-rune` = `0.18em` — applied to uppercase metadata strings ("THE REGULATED-GRADE SUBSTRATE FOR AI AGENTS", section labels, button labels). Pairs with Cinzel display when used as decorative metadata strip.

**Rules:**
- Cinzel weight 400 is the default for display. Reach for 500/600 only when emphasizing within a Cinzel block.
- Inter 300 is permitted for very large hero subtext (24-26pt+) where the lighter weight reads as gravitas, not as anemic.
- Inter 600 is the heaviest weight permitted in body text. Cinzel is where weight goes for visual emphasis; bolding Inter past 600 collides with display register.
- JetBrains Mono is the *only* mono. No Courier, Menlo, or fallback declarations should be visible in any rendered surface.
- No third display font, no second body font. The 3-family stack is the discipline.

## 4. Spacing & layout primitives

Page rhythm: section padding `py-24` to `py-32`, container max-width `max-w-7xl` or narrower (`max-w-5xl`, `max-w-3xl` for prose-heavy surfaces). Hero sections use `min-h-[100dvh]` with `min-height: 100vh` fallback for iOS-safe viewport.

Inline rhythm: copy `leading-relaxed` (1.625) by default; display headers `leading-tight` (1.1-1.25). Vertical spacing between paragraphs `mt-6` to `mt-8`.

Hairline divider: 1px, `linear-gradient(90deg, transparent, rgba(237,232,221,0.18), transparent)` — a fade-in/fade-out parchment line. Used between sections, never solid-line dividers.

**Rules:**
- Three-column grid is the maximum density for any persistence-os surface. Four-column or denser reads as dashboard / app, off-register.
- White-space is the load-bearing element. When in doubt, more padding, not less.
- No drop-shadows on cards by default; `border` (1px parch / 0.08 alpha) is the elevation language. Drop-shadows belong to ember-glow effects only.

## 5. Iconography & visual motifs

**Eye-in-the-well** (the brand mark): SVG circle stroke `rune` 1.2-1.5 width + ellipse stroke `parch` 0.9-1.0 + central pupil `parch` filled with optional `eye-pulse` animation. The mark scales from favicon (32×32) to hero (256×256+). Always rendered SVG, never bitmap.

**Three runes** (BRANCH / REPLAY / EXPORT): hand-drawn-feel inline SVG glyphs in `rune` stroke. Each rune corresponds to a substrate primitive. Used on landing §III; can be reused on decks or carousels as anchor icons. Do not introduce a fourth rune.

**Six-branch schematic** (substrate modules): thin-line SVG showing fact / effect / spec / replay / txn / plan / repl as branches off Yggdrasil's trunk. `rune` stroke 1px on hover/focus → `ember`. Each branch hot-area carries `<title>` for desktop tooltip + tap-caption for touch.

**Stars + moonlight**: radial-gradient stars on the void background; moonlight bloom (radial-gradient ellipse) centered behind hero trunk. Subtle parchment grain SVG-noise overlay at 0.04-0.05 alpha. Together they are the canonical hero ground.

**No emoji in branded surfaces.** Exception: a single rune-glyph (e.g. 𓂀 eye-of-Horus, used once-per-launch on social) is permitted as ceremonial accent, not as decoration.

**No stock photography.** Never. If imagery is needed, prefer SVG/diagrammatic, hand-drawn-feel motifs, or photographs of the actual operator context (Casablanca desk at 2am, AI Box in Sevilla).

## 6. Motion language

Five named animations, all honoring `prefers-reduced-motion: reduce`.

| Animation | Duration | Easing | Use |
|---|---|---|---|
| `eye-pulse` | 6s loop | ease-in-out | The eye pupil breathes. Single-instance; never two on same viewport. |
| `ripple` (×3 staggered) | 6s loop, 0/2/4s delay | linear | The eye watches outward — three concentric rings expanding from the well. |
| `descend` | 2.4s loop | ease-in-out | Vertical-bob cue (8px) on scroll-down indicator. |
| `fade-up` | 1s | ease | Section reveal on intersection — opacity 0→1, translateY(24px→0). |
| `branch-glow` | static | — | `filter: drop-shadow(0 0 18px rgba(184,146,75,0.18)) drop-shadow(0 0 4px rgba(237,232,221,0.10))` — applied to brand-mark and section-anchors as ambient glow. |

**Rules:**
- All animations honor `prefers-reduced-motion`. The reduced-motion fallback strips animation entirely; surfaces remain fully usable.
- No bounce, no spring, no overshoot. Physics that suggests "playful" or "delightful" is off-register.
- No scroll-jacking, no parallax beyond the static moonlight bloom, no auto-playing video.
- Page-load transitions are fade only, never slide. Slide-in transitions read as marketing-funnel.

## 7. Component vocabulary

The persistence-os visual language uses these named components. Generated UI should reach for these before introducing new ones.

| Component | Where | What it does |
|---|---|---|
| **Hero canopy** | Landing top section | Stars + moonlight + Yggdrasil trunk + brand mark + wedge headline + CTAs. Min-height = full viewport. |
| **Wedge headline** | Hero, section openers | 3-line action-cadence headline (e.g. "Branch any audit point. Recover any client to a known-good state. No detail is ever overwritten."). Display Cinzel 500-600, line-height 1.15. |
| **Tracking-rune metadata strip** | Above headline / on cards | Uppercase Cinzel 400 with `tracking-rune` (0.18em). Examples: "THE REGULATED-GRADE SUBSTRATE FOR AI AGENTS". One per surface, max two. |
| **Three-rune row** | Landing §III | Three glyph + label pairs in a row: BRANCH, REPLAY, EXPORT. Glyphs `rune` stroke. Used on landing only; subset (one or two runes) permitted on carousels. |
| **Six-branch schematic** | Landing §IV | Yggdrasil-trunk SVG with six labeled branches. Hover/focus → `ember`. Tooltip on desktop, tap-caption on touch. |
| **Hairline divider** | Between sections | Parchment-fade 1px line. Replaces solid dividers and double-rules entirely. |
| **Compass card** (rare) | Pricing tiers | Slate-elevated card with rune-stroke 1px border, parchment text. Used only for tier comparison — not for general content cards. |
| **Wedge button** (CTA) | Hero, section CTAs | Border-1 rune, transparent background, Cinzel 500 label, hover → ember stroke + branch-glow filter. The CTA does not "shout" — it is a doorway, not a banner. |
| **Code block** | Technical surfaces | `slate` background, `parch` text, JetBrains Mono. No syntax highlighting on the landing; full highlighting permitted on documentation surfaces. |
| **Tag pill** | Tier labels, status badges | Tracking-rune Cinzel uppercase 11-12pt on rune-stroke 1px transparent background. "ALPHA · JULY 2026", "AGPL-3", "PUBLIC SINCE 3 MAY 2026". |

## 8. Voice ↔ design alignment

DESIGN.md does not stand alone. The visual choices encode the voice rules:

- **Cinzel + parchment-on-void** signals paper-tier / memo / inscription register. Reinforces voice §3 Rule 1 (*measured, factual, no marketing*) and Rule 5 (*Norse aesthetic on branded surfaces, plain on technical*) — branded surfaces use the full canopy; technical surfaces (paper, README, ADRs) use the same palette tokens but flatten the visual to plain serif on parchment.
- **No emoji + no stock photo + no marketing flourish** reinforces voice §4 (NEVER list — "AI-powered", "production-ready", emoji-checks).
- **Tag pills carry buyer-proof anchors** — "ALPHA · JULY 2026", "AGPL-3", "PUBLIC SINCE 3 MAY 2026" — the tag pill is the visual surface where date-anchored public commitments live (per voice §3 Rule 4 corrected, see `## Amendments` log in voice.md). Tag pills DO NOT carry engineering-tier numbers (test counts, shas, ARIS scores, phase IDs) — those belong in CHANGELOG, paper, STATUS surfaces, never on a buyer-readable tag pill.
- **Three-rune row + six-branch schematic** are the canonical visual proof of substrate-depth — they show the architecture without copy explaining it. Reinforces voice §3 Rule 2 (*substrate-first framing*).
- **Wedge button + hairline divider** are the anti-CTA-funnel discipline. The page does not hard-sell; the visual rhythm respects the buyer's seniority. Reinforces voice §3 Rule 1 (*measured*) and the buyer-respect posture across all five brands.

Cross-doc rule: if a generated surface introduces visual choices outside this token set OR voice choices outside `voice.md`, the generation is wrong — both docs must be honored together.

## 9. Maintenance

Token additions / typography swaps / new component vocabulary — direct edit, log in `## Amendments`. The threshold for an ARIS R0 review is the same as voice.md §3 voice-rule changes: any §1 (brand identity), §2 (palette structure), §3 (typography stack), or §8 (voice-design alignment) substantive rewrite gates behind ARIS R0 (mean ≥7.5 / min ≥7.0).

The Mimir landing at `/Users/nawfalsaadi/Projects/mimir-os/landing/index.html` is the canonical implementation. If the landing's visual diverges from this doc, fix the landing or fix the doc — not both. Currently they're aligned (this doc was extracted from the landing 2026-05-05).

This doc is **canon for the L4 Maya creative-director agent** (per `~/Projects/conductor/tracks/content-operator-stack_20260502/plan.md` Phase 3-4) and for any persistence-os / Mimir surface — landing pages, decks, IG carousels, OG cards, paper figure templates, conference slides.

## Format note (open question)

This doc is drafted in a hybrid of Google Stitch `DESIGN.md` shape (token tables, semantic naming, palette + typography sections) and awesome-design-md component-vocabulary patterns (named components + voice-design alignment). The format will settle by use across 5 brands. If format-divergence emerges by vertical 3 of the DESIGN.md sweep — i.e. the consumer brands (Sahbi, Nexus Assurance) need a materially different shape — fold the differences into a `## Format` amendment here, OR adopt a tight-template / full-template split mirroring voice.md's policy.

Default expectation: full-template B2B brands (persistence-os, InfraFlow, Keystone) get the full 9-section DESIGN.md; tight-template consumer brands (Sahbi, Nexus Assurance) get a ~150-line variant with §6 Motion compressed and §7 Component vocabulary reduced to platform-relevant components only.

## Amendments

(Append-only. Newest at top.)

- *(none yet — initial draft 2026-05-05.)*
