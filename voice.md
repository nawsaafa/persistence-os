# persistence-os — Brand Voice Doc

> Phase 1 of `content-operator-stack_20260502`. Locked 2026-05-05.
> Source artifacts: this repo's `README.md` + `CLAUDE.md`, the `mimir-os-com` landing copy at `/Users/nawfalsaadi/Projects/mimir-os/landing/index.html`, the hero rewrite at `mimir-os/landing/HERO-REWRITE-SUGGESTION-2026-05-04.md`, and the post-2026-04-30 phase-shipped commit messages on `feat/v0.9-persistence-coder`.
>
> When this doc and any external piece of copy disagree, **this doc wins** — fix the copy, not the doc. The exception: a future ARIS-reviewed reframing (e.g. ADR-style positioning shift) can amend this doc with an explicit `## Amendments` section at the bottom.

---

## 1. Mission

`persistence-os` is the bitemporal, audit-grade substrate underneath Mimir — and underneath any AI-agent runtime that wants to be **branchable, replayable, and tamper-evident**. We exist for the buyers, regulators, and engineering leads who got the answer "we logged it" and then asked "is that enough?" — and realized it wasn't.

The product is the substrate. Every fact is immutable. Every action is an effect. Every plan is an AST. Every shared state change is a transaction. Every LLM boundary has a spec. Everything is REPL-live. Six invariants, seven modules, one substrate.

## 2. Audience (rank-ordered)

1. **Pre-seed B2B buyer at a regulated AI deployment.** Insurance, banking, healthcare, energy compliance, public-sector AI procurement. They need EU AI Act Article 12 logging and they've already discovered their LangChain/CrewAI stack can't satisfy it.
2. **AI engineering lead** building a multi-agent runtime in-house. Knows what STM and content-addressed storage are. Has read the Karpathy "cognitive core" thread and the Howie Liu HyperAgent thread. Has tried mem0/Letta and found the audit story missing.
3. **Compliance officer / GRC lead** who has been told "AI agents are too non-deterministic to audit." We exist to falsify that claim.
4. **Open-source skill-systems community** — Anthropic Skills, Simon Scrapes' compositional pattern. They want a memory layer that survives across runs, not a tutorial wrapper.
5. **Founders / operators** evaluating whether to buy or build the substrate. They need the math, not the marketing.

We are **not** writing for: creator-economy buyers, AI-influencer buyers, viral-marketing audiences, or anyone whose first question is "can it post to TikTok?" If a piece of copy reads like it could ship from a creator-factory landing page, it's wrong.

## 3. Voice & tone

Ten rules, in priority order:

1. **Measured, factual, no marketing.** This is the CLAUDE.md rule and it carries to every piece of public copy. If a sentence would feel out of place in a NeSy paper or a serious changelog, it's correct. If it would feel at home in a SaaS landing page from 2018, rewrite it.
2. **Substrate-first framing.** We ship primitives *below* frameworks (effects, plans, txn, replay, spec). We do not ship "an AI agent." We do not ship "a memory layer with vibes." A framework can sit on top; the substrate is still the substrate.
3. **Honest about what it is NOT.** Anti-pattern declarations are part of the voice — see the `## What persistence-os is NOT` section in `CLAUDE.md`. Any new external copy should pre-empt the predictable misread before the reader can fall into it.
4. **Customer-and-operator-credential credibility, not engineering-tier credibility.** Until persistence-os has battle-tested integrator references and Mimir has a paying-customer count to cite publicly, the proof anchors are: (a) **named third-party frameworks** (EU AI Act Article 12, AGPL-3 license, EBRD ESP 2024, IFC PS 1-8 — third-party-validated, the validation carries the weight); (b) **operator credentials** (Director of BD MENA at Scatec ASA, multi-billion-dollar megaproject, EBRD/Norfund at the table, nine months self-taught from 9pm-4am Casablanca); (c) **date-anchored public commitments** (public on GitHub since 3 May 2026, AGPL-3, alpha targets July 2026, Mimir landing live at mimir-os.com); (d) **specific shipped deliverables a real user can see** (Mimir Docker image installs via one-line provisioning, GuestFlow routes guest messages at a Sofitel today, AI Box runs offline in Sevilla). **Engineering-tier numbers (test counts, commit shas, ARIS scores, phase IDs, module names) belong in CHANGELOG / paper drafts / STATUS logs / commit messages — internal-credibility surfaces, NOT external buyer-facing copy.** They prove competence to engineers reading GitHub; they prove nothing to a DFI investment officer or a regulated-tier buyer. When the customer count / integrator references / audit-trail volume reach a public-citation threshold, this rule re-anchors — not before.
5. **Norse / Yggdrasil aesthetic in branded surfaces, plain in technical surfaces.** Marketing copy can lean into Mimir / Yggdrasil / runes (BRANCH / REPLAY / EXPORT). README, ADRs, paper, and changelogs stay measured and plain.
6. **Wedge-line cadence: action-not-positioning.** "Branch any audit point. Recover any client to a known-good state. No detail is ever overwritten." Three short verbs, three concrete outcomes. Use this rhythm when a hero or a section opener needs a punch.
7. **Cite the convergence.** Three independent April-2026 analyses (Chase / Howie Liu / Simon Scrapes) converged on this product — when relevant, name them. We are not a hot take; we are the conclusion of three different research passes.
8. **Founder duality is load-bearing — used sparingly.** The Director-of-BD-Scatec-by-day / substrate-builder-from-9-PM-to-4-AM duality is the moat. Reach for it when the buyer needs to trust that the operator-AND-builder shape is real. Do not lead with it on every post; it loses force from repetition.
9. **Pre-empt the OpenClaw / Hermes-agent / Letta comparison.** Buyers will look at OpenClaw (368K stars) first and ask why this isn't that. Name the competitor honestly, name the missing primitive (Ed25519 audit chain / bitemporal model / fork-with-byte-identity), close the gap. Do not pretend OpenClaw doesn't exist.
10. **No first-person plural in technical claims.** "We built X" is weak. "X is shipped at commit Y, 2138 tests pass" is the voice. First-person plural is reserved for product-narrative paragraphs (mission, philosophy) — not feature descriptions.

## 4. Words and phrases to NEVER use

(Hard list. If a generated piece of copy contains any of these, the generation is wrong and gets rewritten — not nudged.)

- "personal agent operating system" *(retired 2026-05-04 after OpenClaw audit; collides with 368K-star incumbent)*
- "AI-powered"
- "revolutionizing" / "disrupting" / "game-changing" / "next-generation"
- "creator factory" / "content factory" / "AI avatar"
- "viral" / "10x productivity" / "no-code"
- "synergize" / "leverage" *(as a verb)* / "unlock value"
- "vibes" *(per CLAUDE.md: "NOT a memory library wrapping mem0/Pinecone with vibes")*
- "magic" / "intelligence-as-a-service" / "AI that just works"
- "we believe" *(ship the claim, not the belief)* / "we built X to help you Y" *(weak)*
- "enterprise-grade" *(say what makes it enterprise-grade — Ed25519, bitemporal, replay byte-identity — or say nothing)*
- "production-ready" *(ship the test count and the shas instead)*
- "drop-in replacement" / "the easiest way to X"
- **Engineering-tier numbers as buyer headline** — test counts (`2138 → 2301 passed`), commit shas (`9376361`), ARIS scores (`R1.1 PASS mean 8.22/min 7.8`), phase IDs (`Phase 2.1c.6`), module names (`feat/v0.9-2.1c-context-substrate`) as the *headline* of any external buyer-facing copy. These belong in CHANGELOG / paper / STATUS / commit messages, not on a buyer-readable surface. Once we have battle-tested customer references to cite, math-and-numbers shifts to customer / retention / audit-trail-volume tier — not before.
- Any phrase that fits on a HeyGen / Higgsfield / IG-creator landing page

## 5. On-brand vocabulary (preferred phrasing)

Reach for these. They each carry a load-bearing technical claim that distinguishes us from the field.

- **regulated-grade substrate** *(canonical positioning, post-OpenClaw)*
- **tamper-evident audit chain** *(not "secure logs")*
- **bitemporal** + **content-addressed datom** *(not "database record")*
- **replay byte-identical** *(not "reproducible" — we mean byte-for-byte)*
- **fork any audit point** *(not "rollback" — we mean speculative branch with rollback)*
- **for when "we logged it" isn't enough** *(buyer-side hook line)*
- **EU AI Act Article 12** *(name the regulation, not "compliance")*
- **Ed25519-signed** *(name the primitive)*
- **the substrate is the substrate** *(self-referential rebuttal of "wrap a framework around it")*
- **every fact / every effect / every plan / every transaction** *(invariants framing — anaphora is part of the voice)*
- **team knowledge work** *(not "the enterprise" — too vague)*
- **durability and steerability** *(Howie Liu axis)*
- **open-core, AGPL-3** *(name the license up front)*
- **derived properties of one substrate** *(rebuttal of "five engineered features" framing)*

## 6. Platform notes

The voice shifts in cadence per platform; the substance does not.

### LinkedIn (primary B2B channel — Nawfal-as-operator-builder)

- 200–500 words, three to six paragraphs, one image or quote-card.
- Lead with a buyer-relevant trigger (a regulator question, an OpenClaw thread, an EU AI Act Article 12 deadline).
- Mid-post: introduce the substrate framing + one technical claim (Ed25519 / bitemporal / fork-byte-identity).
- Close with one CTA: GitHub link, Mimir landing, or a question to invite buyer-side reply.
- Founder-duality is permitted here — buyers respond to the Scatec-by-day / substrate-by-night shape — but no more than 1 in 4 posts should lead with it.

### X / Twitter (primary technical-credibility channel)

- Either a one-line technical zinger ("Replay byte-identity at 1.2k datom/s. Six invariants. AGPL-3. github.com/...") OR a thread of 5–9 posts.
- Threads must cite shas, test counts, or paper sections. Speculation without a measurement is off-voice.
- Tag @AnthropicAI / @huggingface / @karpathy when the post intersects their work; never tag for tag's sake.
- No emoji except the rune-glyph eye 𓂀 in a hero post — and even that is once-per-launch, not per-post.

### GitHub README + paper + changelogs (technical canon)

- Paper-tier rigor. Anaphora ("every fact, every effect…") is welcome. Marketing language is not.
- Sentences are dense. Numbers and module names are not abbreviated.
- Always preserve the six-invariants frame and the seven-modules table.
- The CLAUDE.md "what it is NOT" anti-pattern section is the template — every new public surface gets one.

### Long-form blog / Show HN

- Open with the convergence narrative (three April-2026 analyses) when the post is positioning-heavy; open with the specific phase-shipped result when the post is technical-deep.
- Pre-empt the OpenClaw / Hermes-agent / Letta comparison early, ideally in the first three paragraphs.
- Close with the EU AI Act Article 12 timing and the AGPL-3 license stance — these are the load-bearing differentiators.

### Conference talk / pitch deck

- Voice loosens *slightly* — the operator-builder duality is leadable here.
- Six-invariants slide and seven-modules slide are mandatory.
- One slide must show the audit chain visualization (Mimir landing has the schematic).
- Never include a "Future of AI" framing slide. The framing is the present, not the future.

## 7. Example posts (template gallery)

### Example 1 — README hero (canonical wedge)

> **Branch any audit point. Recover any client to a known-good state. No detail is ever overwritten.**
>
> The bitemporal substrate underneath Mimir — the always-on personal agent operating system. Open-core. AGPL-3. Used in production via the Mimir Docker image; available standalone for teams building their own multi-agent runtimes.

*Why it's on-voice:* three-line action wedge, named technical claim (bitemporal), license stated up front, no marketing adjectives.

### Example 2 — Mimir landing meta description

> Cryptographic audit. Bitemporal replay. Forkable history. The substrate underneath your AI agents — for when "we logged it" isn't enough.

*Why it's on-voice:* names three primitives (audit / replay / fork), buyer-hook line ("when we logged it isn't enough"), one sentence.

### Example 3 — Date-anchored public commitment (buyer-facing announcement)

> Persistence-os has been public on GitHub since 3 May 2026, under AGPL-3 — the bitemporal substrate underneath Mimir. Mimir public alpha targets July 2026. The audit-export the EU AI Act Article 12 will require from accountable AI systems is built into the substrate by default; we did not bolt it on. Operator-first, builder-second: shipped over nine months from 9pm to 4am Casablanca by someone who runs a multi-billion-dollar megaproject by day. The duality is the moat.

*Why on-voice:* date-anchored (public-since-3-May-2026, alpha-targets-July-2026), operator credential (Casablanca / nine-month build / megaproject day-job), named third-party frameworks (AGPL-3 license, EU AI Act Article 12), customer-visible deliverable (Mimir alpha). Zero engineering-tier numbers; proof comes from sources a non-engineer can verify.

### Example 4 — LinkedIn anti-pattern declaration (paraphrased from CLAUDE.md)

> Things `persistence-os` is NOT, in the order buyers most often misread it:
>
> 1. NOT a memory library wrapping mem0 or Pinecone with vibes. The six invariants are the contract; everything else is implementation detail.
> 2. NOT an agent framework replacing LangChain or CrewAI. We ship primitives *below* frameworks.
> 3. NOT a creator-economy product. This is a B2B team-knowledge-work substrate — durability and steerability axis, not output volume.
> 4. NOT pip-installable yet. v0.9.0a1 alpha targets June 2026.
> 5. NOT a real-OS-sandbox. The v0.9 sandbox is capability-denial-not-detection.
>
> The negative space is the positioning.

*Why it's on-voice:* anaphora, math-and-names credibility, pre-empts five misreads, closes with a one-line reframe.

### Example 5 — X thread opener (technical credibility)

> Six invariants, seven modules, one substrate.
>
> 1/ Every fact is immutable, content-addressed, bitemporal.
> 2/ Every action is an effect (Koka-style algebraic handlers).
> 3/ Every plan is an EDN AST.
> 4/ Every shared state change is a transaction (STM, SERIALIZABLE).
> 5/ Every LLM boundary has a spec (Malli-style, parse-don't-validate).
> 6/ Everything is REPL-live.
>
> github.com/nawsaafa/persistence-os — AGPL-3, v0.8.5a1.

*Why it's on-voice:* anaphora, named primitives, named license, named version. No adjectives.

### Example 6 — Show HN opening paragraph

> In April 2026, three independent research passes — Chase 7-levels, Howie Liu HyperAgent, Simon Scrapes' "skill systems" — converged on the same conclusion the same week. Agentic OS substrate, distributed via Anthropic Skills, positioned around durability + steerability, with a content-operator stack on top that respects voice-and-taste before scale-and-systems. We had been building toward all three for nine months. `persistence-os v0.8.5a1` is the substrate that came out the other side.

*Why it's on-voice:* convergence narrative as opening hook, named research passes, version-stated proof, founder-duality implicit ("nine months") not foregrounded.

### Example 7 — Buyer-side LinkedIn hook line

> Your AI compliance team asked: "if a regulator subpoenas the agent's reasoning trace from six months ago, can we replay it byte-identical?" You said "we logged it." That isn't enough.

*Why it's on-voice:* opens in the buyer's voice, names the gap, refuses the "we logged it" answer. No technical primitive yet — the substrate framing comes in paragraph two.

### Example 8 — Competitor mention (honest framing)

> OpenClaw (368K stars) ships event-sourced replay and snapshot persistence — but no Ed25519 audit chain, no bitemporal model, no fork-with-byte-identity. Hermes-agent (Nous Research, 129K stars) supports only `/undo`. Letta, Mem0, and AnythingLLM have memory but no audit chain. Cursor, Cline, and Aider have neither. The regulated-tier moat is what's missing across the field.

*Why it's on-voice:* names the leader honestly, names what each competitor lacks at the primitive level, no derogatory adjectives.

## 8. Voice consistency check

Generated copy is on-voice if **all eight** of the following hold:

1. Zero phrases from the §4 NEVER list.
2. ≥ 1 phrase from the §5 ON-BRAND vocabulary (in marketing copy) OR ≥ 1 named technical primitive (in technical copy).
3. No first-person plural inside a feature claim.
4. Either a sha / version / test count / phase ID is named, OR a regulation / license / standard is named.
5. The OpenClaw / Hermes / Letta / mem0 question is either pre-empted or irrelevant to the post (not ignored).
6. The substrate-first framing (it's a primitive layer, not an end-user agent) survives the post.
7. The Norse aesthetic appears only on branded surfaces; technical surfaces stay plain.
8. The piece would not be out of place quoted in a serious changelog or NeSy-track paper.

If any one of these fails, the generation is wrong. Don't nudge — rewrite.

## 9. Maintenance

This doc is **canon for the L4 Maya creative-director agent** (per `~/Projects/conductor/tracks/content-operator-stack_20260502/plan.md` Phase 3-4). Any update here propagates downstream into the L3 content-seed skill, the L4 Maya generation prompts, and the L5 cascade voice variants.

Update process:
- Spelling / phrasing nits — direct edit, no review needed.
- New on-brand phrase or new NEVER-phrase — direct edit, log in `## Amendments`.
- Audience reordering, mission rewrite, or §3 voice-rule change — gated behind a fresh ARIS R0 review (DESIGN gate, mean ≥7.5 / min ≥7.0). Land the amended doc as a single commit with the ARIS report attached.

## Amendments

(Append-only. Newest at top.)

- **2026-05-05 (later, same day as initial draft) — engineering-tier numbers retired as buyer-proof.** §3 Rule 4 rewritten: was *"Math-and-numbers credibility — test counts (2138 → 2301), commit shas (9376361), ARIS scores"*; now *"Customer-and-operator-credential credibility — customer evidence + operator credentials + named third-party frameworks + date-anchored public commitments. Engineering-tier numbers belong in CHANGELOG / paper / STATUS — not buyer-facing copy."* §4 NEVER list amended to forbid test-counts / shas / ARIS-scores / phase-IDs as external buyer-headline. §7 Example 3 replaced — was a phase-shipped commit message leading on test counts and ARIS scores; now a date-anchored public-commitment buyer-facing announcement. Trigger: user feedback after Phase 1 sweep — *"the math and numbers should come once we have a working product that is battle tested with real customers, that's what people will believe and that's what builds trust."* Re-anchor trigger: when persistence-os has battle-tested integrator references and Mimir has paying-customer count to cite, math-and-numbers shifts to customer / retention / audit-trail-volume tier. See feedback memory `feedback_no_engineering_numbers_as_buyer_proof.md` for canonical guidance.
- *(initial draft 2026-05-05.)*
