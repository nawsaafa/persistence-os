# Paper LaTeX conversion — design doc

**Date:** 2026-04-26
**Author:** Stream C — paper drafting kickoff (post-v0.7 substrate refresh)
**Target:** convert `paper/persistence-nesy-2026-draft.md` (Markdown v0.7, 521 lines) to NeSy 2026 PMLR-format LaTeX (v0.8). Anonymized for double-blind review with toggle to swap in real author info at camera-ready.
**Scope:** structural/format conversion only — no proposition changes, no new content. Prop 1-5 wording untouched.
**Time budget:** ~6-8h single-session, single-worker.

---

## Why this exists

Paper is currently maintained as Markdown (`paper/persistence-nesy-2026-draft.md`). NeSy 2026 submission requires LaTeX. Converting now (rather than under deadline pressure in early June) gives:

- Stable submission-format source from day 1 — every subsequent edit lands directly in `.tex`
- Robust cross-references (`\cref{prop:branch}` / `\eqref{eq:datom}`) that survive section reorderings — markdown's prose references rot silently
- Proper `.bib` bibliography rendering with auto-numbered `\cite{}` markers
- Theorem environments (`\begin{proposition}...\end{proposition}` with auto-numbering) — what KR/NeSy reviewers expect
- Anonymization toggle baked in — single-line flip from submission to camera-ready

## Target venue (CFP-confirmed)

- **NeSy 2026** — 20th Intl Conference on Neurosymbolic Learning and Reasoning, Lisbon (FCUL), 1-4 September 2026
- **Phase 2 deadlines (our target):** abstract 9 June 2026 / paper 16 June 2026 / notification 8 July / camera-ready 20 July
- **Proceedings:** PMLR (Proceedings of Machine Learning Research)
- **LaTeX template:** official NeSy 2026 template ZIP at `https://2026.nesyconf.org/assets/downloadables/NeSy_2026_template.zip` (PMLR/jmlr.cls-based)
- **Review:** double-blind. Anonymization mandatory: "All submissions must be anonymized and must not include any information that could intentionally or unintentionally compromise the double-blind review process."
- **Page limit:** 10 pages excl. references and supplementary materials (full paper)

## Decisions (locked from brainstorming)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| Q1 | Source of truth going forward | LaTeX-only after one-shot conversion | Submission format = working format; archive markdown as v0.7 frozen artifact |
| Q2 | Conversion approach | Hybrid: Pandoc for prose, hand-port for structured | Pandoc unreliable on theorem-style numbering, capability-matrix cells, ASCII diagrams |
| Q3 | Bibliography | Build `.bib` upfront (~25-30 entries) | Cheaper now than under deadline pressure; canonical from day one |
| Q4 | Theorem environments | `amsthm` + `cleveref` + `\label{prop:...}` | KR/NeSy reviewers expect proper theorem rendering; auto-refs survive reorderings |
| Q5 | §5.8 system diagram | Verbatim ASCII for now; v0.8 TikZ follow-up task | Bounded conversion scope; visual upgrade is its own focused task |
| Q6 | Repo structure | `paper/tex/` subdirectory; single `.tex`; archive `.md` | Keeps build artifacts contained; one-author paper doesn't need split-file |
| Q7a | Anonymization mechanism | `\newif\ifanonymous` toggle with `\anonymoustrue` default | Standard PMLR/JMLR pattern; one-line flip for camera-ready |
| Q7b | "Adaptive Trader v2" case study | Anonymize for submission; restore at camera-ready | Distinctive enough to Google; safest path under deadline |
| — | Tooling | Tectonic (single-binary, ~50 MB) + Pandoc + GNU Make | Avoids 7 GB MacTeX install; auto-fetches packages, reproducible builds |

## Repo structure (post-conversion)

```
paper/
├── tex/
│   ├── persistence-nesy-2026.tex   ← single-file source, ~600 lines after conversion
│   ├── references.bib              ← ~25-30 BibTeX entries
│   ├── nesy2026.cls (or jmlr.cls)  ← from official NeSy_2026_template.zip
│   ├── Makefile                    ← `make`, `make submission`, `make cameraready`, `make clean`
│   └── .gitignore                  ← *.aux *.bbl *.blg *.log *.out *.pdf
├── archive/
│   └── persistence-nesy-2026-v0.7.md  ← frozen markdown, the input to this conversion
└── (later: figures/, supplemental/)
```

## LaTeX preamble (target shape)

```latex
\documentclass[anon,...]{nesy2026}   % final \documentclass set by template inspection (Task 1)

\usepackage{amsmath,amssymb,amsthm}
\usepackage[capitalise]{cleveref}
\usepackage{natbib}                  % or biblatex per template

\newtheorem{proposition}{Proposition}
\crefname{proposition}{Proposition}{Propositions}

\newcommand{\C}[1]{\texttt{#1}}      % short code spans

\newif\ifanonymous \anonymoustrue    % flip to \anonymousfalse for camera-ready

\title{Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate}

\ifanonymous
  \author{Anonymous}
\else
  \author{Nawfal Saadi \\ <affiliation> \\ <email>}
\fi
```

## Anonymization scrub list (separate plan task)

A reviewer who Googles unique phrases or grep'able commit hashes can de-anonymize. Conversion plan includes an explicit scrub pass over:

- **Specific git commit SHAs** — 11+ mentions in the current `.md` (e.g. `b459fe5`, `7bac436`, `be7e37f`, `b9cbf37`, `bce93da`). Replace with `[commit redacted for blind review]`. Comment-tag each line `% RESTORE: <sha>` for the camera-ready restoration pass.
- **Branch names** — `feat/v0.4-substrate-primitives`. Replace with `[branch redacted]`.
- **"Adaptive Trader v2"** (§6.5) — distinctive enough to Google. Replace with `Case-A: a production algorithmic-trading deployment`. Tag for restore.
- **GitHub repo URLs / paths revealing authorship** — verify via grep over the converted `.tex`.
- **Self-citations** — none currently exist; verify via grep before submission.
- **License intent line at top of `.md`** — currently mentions "AGPL-3 for the runtime" which combined with other context could de-anonymize. Move to acknowledgements (which are excluded under `\ifanonymous`).

## Build pipeline

```make
default: persistence-nesy-2026.pdf

persistence-nesy-2026.pdf: persistence-nesy-2026.tex references.bib
	tectonic --keep-intermediates persistence-nesy-2026.tex

submission:
	@grep -q '^\\anonymoustrue' persistence-nesy-2026.tex \
	  || (echo "ABORT: not anonymous — submission requires \anonymoustrue"; exit 1)
	$(MAKE)
	cp persistence-nesy-2026.pdf submission-anonymized.pdf

cameraready:
	@grep -q '^\\anonymousfalse' persistence-nesy-2026.tex \
	  || (echo "ABORT: anonymous — camera-ready requires \anonymousfalse"; exit 1)
	$(MAKE)
	cp persistence-nesy-2026.pdf cameraready-final.pdf

clean:
	rm -f *.aux *.bbl *.blg *.log *.out
```

The `submission` / `cameraready` targets enforce the anonymization invariant — no foot-gun where the toggle is forgotten.

## Verification gates (before commit)

1. `make` produces a clean PDF with no LaTeX errors (warnings besides float-placement nags acceptable)
2. PDF page count ≤ 10 (excl. references) — fits NeSy full-paper limit
3. **Anonymization grep-check:** `pdftotext persistence-nesy-2026.pdf - | grep -iE 'saadi|aibox|adaptive trader v2|persistence-os|nawfal|/Users/'` returns nothing
4. All 5 propositions render as numbered theorem boxes; `\cref{prop:branch}` resolves correctly in body text
5. Side-by-side spot-check vs `archive/persistence-nesy-2026-v0.7.md`: §4 math blocks, §2.6 capability matrix, §5.8 ASCII diagram render at parity
6. References list has all ~25-30 entries; every `\cite{}` resolves (no `(?)` in rendered PDF)
7. `make submission` succeeds (toggle is `\anonymoustrue`)

## Out of scope (follow-up tasks)

- **§5.8 TikZ diagram upgrade** — file v0.8 polish task before 9 June abstract deadline
- **Full ARIS R1+R2+R3 on v0.6→v0.8 cumulative diff** — separate task post-conversion
- **§6 [TBD] cells** (LongMemEval / CAMO / regulator-replay numbers) — Stream C continuation
- **§6.5 case-study expansion** (Adaptive Trader v2 + 3 anonymized vignettes) — Stream C continuation
- **Restoration pass** (`\anonymousfalse` + restore "Adaptive Trader v2" + restore commit SHAs) — between 8 July notification and 20 July camera-ready

## Files touched in this conversion task

- **Create**: `paper/tex/persistence-nesy-2026.tex`, `paper/tex/references.bib`, `paper/tex/Makefile`, `paper/tex/.gitignore`
- **Add (from template)**: `paper/tex/<class file>` and any required style files from the NeSy 2026 template ZIP
- **Move**: `paper/persistence-nesy-2026-draft.md` → `paper/archive/persistence-nesy-2026-v0.7.md`
- **Install (host)**: `tectonic` via `brew install tectonic`; `pandoc` if not already installed

## Commit cadence

Bite-sized TDD-shaped commits — see `docs/plans/2026-04-26-paper-latex-conversion-impl.md` (the implementation plan).

## ARIS posture

R1 + R2 + R3 on the converted `.tex` is **recommended hygiene** before the 9 June abstract deadline but is NOT a gate for this conversion task. The conversion makes no proposition change; ARIS R3 (paper fitness) on the v0.7→v0.8 cumulative diff is its own follow-up task.
