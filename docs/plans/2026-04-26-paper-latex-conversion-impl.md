# Paper LaTeX Conversion Implementation Plan

> **For Claude:** REQUIRED EXECUTION: single-session **Subagent-Driven Development**.
> Dispatch a fresh subagent per task; review between tasks. No agent teams (paper conversion is sequential — every task touches the same .tex file).
>
> **Implementer subagent** — REQUIRED SKILLS:
> - superpowers:executing-plans
> - superpowers:verification-before-completion
>
> **Reviewer subagent (between tasks)** — REQUIRED SKILLS:
> - superpowers:requesting-code-review (adapted for paper conversion: spec compliance vs verbatim translation)
>
> Coderabbit + context7 not applicable (no production code, no library API surface).

**Goal:** Convert `paper/persistence-nesy-2026-draft.md` (Markdown v0.7, 521 lines) → `paper/tex/persistence-nesy-2026.tex` (LaTeX v0.8) using the official NeSy 2026 PMLR template, anonymized for double-blind review with `\ifanonymous` toggle.

**Architecture:** Hybrid Pandoc-plus-hand-port. Pandoc on prose-heavy sections (§1, §3, §6, §7); hand-port on structured/math sections (abstract, §2.6 capability matrix, §4 propositions, §5.8 ASCII diagram, revision history). Single output `.tex` file in new `paper/tex/` subdirectory; original `.md` archived to `paper/archive/`.

**Tech Stack:** Tectonic (single-binary LaTeX engine, ~50 MB), Pandoc (markdown→tex translator), GNU Make (build interface), `amsthm` + `cleveref` + `natbib` (LaTeX packages), official NeSy 2026 template (`jmlr.cls`-based, downloaded as ZIP).

**Branch strategy:** Continue on `feat/v0.4-substrate-primitives` (where v0.7 markdown landed) or create `feat/paper-v0.8-latex-conversion`. Recommendation: new branch off current HEAD (`0e8b88f`) so the conversion is reviewable as a coherent unit. First task creates the branch.

**Verification model:** Each task ends with a grep / build / page-count / pdftotext check that must pass before commit. The "failing test" pattern adapts to: (1) state the expected check, (2) confirm the check fails before the change, (3) make the change, (4) confirm the check passes. Some tasks (file moves, package installs) skip the "fails first" step where it doesn't apply; those tasks document the post-condition only.

**Time budget:** ~6-8h sequential. Pre-flight (Phase 1) ~30min; restructure (Phase 2) ~30min; preamble (Phase 3) ~45min; bibliography (Phase 4) ~1.5-2h; hand-port (Phase 5) ~2h; Pandoc-prose (Phase 6) ~1.5h; anonymization (Phase 7) ~30min; verification (Phase 8) ~30min; persistence (Phase 9) ~15min.

---

## Phase 1: Pre-flight tooling

### Task 1: Create conversion branch

**Files:** none (git operation only)

**Step 1: Verify working tree is clean and on the right base commit**

Run:
```bash
cd /Users/nawfalsaadi/Projects/persistence-os
git status --short
git rev-parse --abbrev-ref HEAD
git log --oneline -1
```

Expected:
- `git status --short` returns no output (clean tree)
- Branch is `feat/v0.4-substrate-primitives` or compatible
- HEAD is `25ba75d` (the design-doc commit) or later

**Step 2: Create the conversion branch**

Run:
```bash
git checkout -b feat/paper-v0.8-latex-conversion
```

Expected: switched to new branch.

**Step 3: Verify**

Run: `git rev-parse --abbrev-ref HEAD`
Expected output: `feat/paper-v0.8-latex-conversion`

**Step 4: No commit yet** — the branch is established; first commit lands in Task 4.

---

### Task 2: Install Tectonic + verify Pandoc

**Files:** none (host system install)

**Step 1: Check whether Tectonic is already installed**

Run: `which tectonic && tectonic --version`
Expected if installed: prints path (e.g. `/opt/homebrew/bin/tectonic`) and version.
Expected if not installed: `tectonic not found`.

**Step 2: Install if missing**

Run: `brew install tectonic`
Expected: ~30s download, exits 0.

**Step 3: Verify Tectonic works on a hello-world**

Run:
```bash
mkdir -p /tmp/tectonic-smoke && cd /tmp/tectonic-smoke
cat > hello.tex << 'EOF'
\documentclass{article}
\begin{document}
Hello, Tectonic.
\end{document}
EOF
tectonic hello.tex
ls -la hello.pdf
cd - >/dev/null
rm -rf /tmp/tectonic-smoke
```

Expected: `hello.pdf` produced, ~5-15 KB. First build downloads packages into `~/Library/Caches/Tectonic/`; subsequent builds are fast.

**Step 4: Check Pandoc**

Run: `which pandoc && pandoc --version | head -1`
Expected: `pandoc 2.x` or `3.x`. If missing, `brew install pandoc`.

**Step 5: No commit** — host tooling install only.

---

### Task 3: Download + inspect NeSy 2026 template

**Files:**
- Create: `/tmp/nesy-template/` (extraction directory, deleted after inspection)

**Step 1: Download the official template ZIP**

Run:
```bash
mkdir -p /tmp/nesy-template && cd /tmp/nesy-template
curl -L -o NeSy_2026_template.zip https://2026.nesyconf.org/assets/downloadables/NeSy_2026_template.zip
ls -la NeSy_2026_template.zip
```

Expected: ZIP file ~50-500 KB.

**Step 2: Extract and list contents**

Run:
```bash
cd /tmp/nesy-template
unzip -o NeSy_2026_template.zip
ls -la
find . -maxdepth 3 -type f | head -30
```

Expected: a directory tree with `.cls`, `.sty`, `.tex` (sample), `.bst`, possibly `.pdf` rendering of the sample.

**Step 3: Identify the document class file and the sample file**

Run:
```bash
find /tmp/nesy-template -name '*.cls' -o -name '*.sty' | head
find /tmp/nesy-template -name '*.tex' | head
```

Expected: typically one `.cls` (e.g. `nesy2026.cls` or `jmlr.cls`) and one or two sample `.tex`. Note the exact filename of the class file — needed for `\documentclass{...}` in Task 8.

**Step 4: Inspect the sample `.tex` for the right `\documentclass` invocation + anonymization syntax**

Run:
```bash
grep -n 'documentclass\|jmlrproceedings\|anonymous\|\\author' /tmp/nesy-template/*.tex | head -20
cat /tmp/nesy-template/*.tex 2>/dev/null | head -80
```

Record (for Task 8):
- Exact `\documentclass[...]{<classname>}` line
- Whether the class supports an `[anonymous]` or `[anon]` option (PMLR/jmlr templates often do)
- The author-block syntax (`\author{}` vs `\jmlrauthor{}` etc.)
- Any `\jmlrproceedings{...}` or `\editors{...}` lines required

**Step 5: Sanity-build the sample**

Run:
```bash
cd /tmp/nesy-template
SAMPLE=$(find . -name 'sample*.tex' -o -name 'example*.tex' -o -name 'main*.tex' | head -1)
echo "Sample: $SAMPLE"
tectonic --keep-intermediates "$SAMPLE" 2>&1 | tail -20
ls -la "${SAMPLE%.tex}.pdf"
```

Expected: clean build of the template's sample doc → reference PDF. If this fails, the `.tex` in Task 8 won't build either; debug now.

**Step 6: No commit** — `/tmp/nesy-template/` is throwaway. Notes for Task 8 captured in your scratch buffer or paste them into a `paper/tex/TEMPLATE_NOTES.md` if helpful (delete before commit).

---

## Phase 2: Repo restructure

### Task 4: Create `paper/tex/` and `paper/archive/` directories

**Files:**
- Create: `paper/tex/.gitkeep`
- Create: `paper/archive/.gitkeep`

**Step 1: Verify parent directory exists**

Run: `ls -la paper/`
Expected: `persistence-nesy-2026-draft.md` listed.

**Step 2: Create the subdirectories**

Run:
```bash
mkdir -p paper/tex paper/archive
touch paper/tex/.gitkeep paper/archive/.gitkeep
```

**Step 3: Verify**

Run: `ls -la paper/tex paper/archive`
Expected: both directories exist with `.gitkeep` files (so `git add` records them even when otherwise empty).

**Step 4: Commit**

```bash
git add paper/tex/.gitkeep paper/archive/.gitkeep
git commit -m "paper(v0.8): scaffold paper/tex/ and paper/archive/ directories"
```

---

### Task 5: Move `.md` to `paper/archive/`

**Files:**
- Move: `paper/persistence-nesy-2026-draft.md` → `paper/archive/persistence-nesy-2026-v0.7.md`

**Step 1: Verify the source file is at the expected path**

Run: `ls -la paper/persistence-nesy-2026-draft.md`
Expected: file exists, ~80-100 KB.

**Step 2: Use `git mv` to preserve history**

Run:
```bash
git mv paper/persistence-nesy-2026-draft.md paper/archive/persistence-nesy-2026-v0.7.md
```

**Step 3: Add an "ARCHIVED" header to the moved file**

Edit `paper/archive/persistence-nesy-2026-v0.7.md`:

Insert as new line 1 (above the `# Toward Accountable...` title):
```markdown
> **ARCHIVED.** This is the frozen Markdown source of paper v0.7 (2026-04-26). The live source as of v0.8 is `paper/tex/persistence-nesy-2026.tex`. Future edits go to the LaTeX file, not here. This file is preserved for diff reference and ARIS-review history.

---

```

**Step 4: Verify**

Run:
```bash
ls -la paper/archive/persistence-nesy-2026-v0.7.md
ls paper/persistence-nesy-2026-draft.md 2>&1 | grep -c "No such file"
head -1 paper/archive/persistence-nesy-2026-v0.7.md
```

Expected:
- Archive file exists
- Original path returns "No such file" (count = 1)
- First line is the ARCHIVED notice

**Step 5: Commit**

```bash
git add paper/archive/persistence-nesy-2026-v0.7.md
git commit -m "paper(v0.8): archive v0.7 markdown to paper/archive/

Frozen reference for v0.7 → v0.8 conversion diff. Live source moves
to paper/tex/persistence-nesy-2026.tex in subsequent commits."
```

---

### Task 6: Create `.gitignore` for build artifacts

**Files:**
- Create: `paper/tex/.gitignore`

**Step 1: Write the .gitignore**

Create `paper/tex/.gitignore` with exact content:
```
# LaTeX build artifacts
*.aux
*.bbl
*.blg
*.log
*.out
*.toc
*.fls
*.fdb_latexmk
*.synctex.gz
*.synctex(busy)

# Tectonic / latexmk caches
.tectonic-cache/

# PDF outputs (committed manually if needed; default-ignored to keep repo clean)
*.pdf

# Editor + OS detritus
.DS_Store
*.swp
```

**Step 2: Verify**

Run: `wc -l paper/tex/.gitignore`
Expected: ~16 lines.

**Step 3: Commit**

```bash
git add paper/tex/.gitignore
git rm paper/tex/.gitkeep
git commit -m "paper(v0.8): .gitignore for LaTeX build artifacts"
```

---

### Task 7: Create the Makefile

**Files:**
- Create: `paper/tex/Makefile`

**Step 1: Write the Makefile**

Create `paper/tex/Makefile` with exact content (note: tabs, not spaces, for recipe lines):
```make
.PHONY: default submission cameraready clean check-anon check-real

TEX := persistence-nesy-2026.tex
PDF := persistence-nesy-2026.pdf

default: $(PDF)

$(PDF): $(TEX) references.bib
	tectonic --keep-intermediates $(TEX)

submission: check-anon $(PDF)
	cp $(PDF) submission-anonymized.pdf
	@echo "→ submission-anonymized.pdf ready"

cameraready: check-real $(PDF)
	cp $(PDF) cameraready-final.pdf
	@echo "→ cameraready-final.pdf ready"

check-anon:
	@grep -qE '^\\anonymoustrue' $(TEX) \
	  || (echo "ABORT: $(TEX) is not anonymous — submission requires \\anonymoustrue"; exit 1)

check-real:
	@grep -qE '^\\anonymousfalse' $(TEX) \
	  || (echo "ABORT: $(TEX) is anonymous — camera-ready requires \\anonymousfalse"; exit 1)

clean:
	rm -f *.aux *.bbl *.blg *.log *.out *.toc *.fls *.fdb_latexmk *.synctex.gz
```

**Step 2: Verify (will fail until Task 8)**

Run:
```bash
cd paper/tex && make 2>&1 | tail -3 && cd - >/dev/null
```

Expected: error like `make: *** No rule to make target 'persistence-nesy-2026.tex'` (file not yet created — that's Task 8). The Makefile is *syntactically valid* if `make` reaches that error rather than parse-failing on the recipe.

**Step 3: Commit**

```bash
git add paper/tex/Makefile
git commit -m "paper(v0.8): Makefile with submission/cameraready anonymization gates"
```

---

## Phase 3: Preamble + skeleton

### Task 8: Copy NeSy class file and create skeleton `.tex`

**Files:**
- Create: `paper/tex/<classname>.cls` (and any required `.sty`/`.bst` files)
- Create: `paper/tex/persistence-nesy-2026.tex` (skeleton only — title + empty body)

**Step 1: Copy the class file from the extracted template**

Run:
```bash
ls /tmp/nesy-template/*.cls /tmp/nesy-template/*.sty /tmp/nesy-template/*.bst 2>/dev/null
cp /tmp/nesy-template/*.cls paper/tex/ 2>/dev/null
cp /tmp/nesy-template/*.sty paper/tex/ 2>/dev/null
cp /tmp/nesy-template/*.bst paper/tex/ 2>/dev/null
ls paper/tex/
```

Expected: at least one `.cls` file copied; possibly `.sty` / `.bst` companions. If the template ZIP put files in a subdirectory, adjust the source path.

**Step 2: Write the skeleton `.tex`**

Create `paper/tex/persistence-nesy-2026.tex`. Use the template's exact `\documentclass` line (recorded in Task 3 Step 4). Below is a TARGET shape — the *actual* `\documentclass` and `\author` macros come from the template:

```latex
% !TEX program = tectonic
\documentclass[anon]{nesy2026}   % REPLACE with exact line from template sample

% --- math + theorems ---
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsthm}

% --- cross-references (must load after hyperref if hyperref is present) ---
\usepackage[capitalise]{cleveref}

% --- citations (template may already load natbib/biblatex; comment out if so) ---
% \usepackage{natbib}

% --- theorem environments ---
\newtheorem{proposition}{Proposition}
\crefname{proposition}{Proposition}{Propositions}

% --- inline code-span macro ---
\newcommand{\C}[1]{\texttt{#1}}

% --- anonymization toggle ---
\newif\ifanonymous
\anonymoustrue   % <-- flip to \anonymousfalse for camera-ready

% --- title + author ---
\title{Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate}

\ifanonymous
  \author{Anonymous}
\else
  \author{% TODO: real author block — set before camera-ready
    Nawfal Saadi%
  }
\fi

\begin{document}
\maketitle

\begin{abstract}
% Abstract goes here — Task 13.
\end{abstract}

% Body sections go here — Tasks 14-22.

\bibliographystyle{plainnat}   % adjust per template
\bibliography{references}

\end{document}
```

**Step 3: Verify the skeleton builds (empty body OK)**

Run:
```bash
cd paper/tex
touch references.bib   # empty bib so \bibliography{references} resolves
tectonic --keep-intermediates persistence-nesy-2026.tex 2>&1 | tail -10
ls persistence-nesy-2026.pdf
cd - >/dev/null
```

Expected: PDF produced (1-2 pages, just title + Anonymous author + empty abstract). If the build fails, debug `\documentclass` options against the template sample before continuing.

**Step 4: Verify the make target works**

Run: `cd paper/tex && make clean && make 2>&1 | tail -3 && cd - >/dev/null`
Expected: PDF rebuilt cleanly.

**Step 5: Verify anonymization gate**

Run: `cd paper/tex && make submission 2>&1 | tail -3 && cd - >/dev/null`
Expected: succeeds (skeleton has `\anonymoustrue`); produces `submission-anonymized.pdf`.

Run: `cd paper/tex && make cameraready 2>&1 | tail -3 && cd - >/dev/null`
Expected: ABORTs with "anonymous — camera-ready requires \\anonymousfalse" (and exits non-zero).

**Step 6: Commit**

```bash
git add paper/tex/*.cls paper/tex/*.sty paper/tex/*.bst paper/tex/persistence-nesy-2026.tex paper/tex/references.bib 2>&1 | grep -v "did not match" | head
git status --short
git commit -m "paper(v0.8): skeleton .tex + NeSy class file + empty references.bib

Template files copied from official NeSy_2026_template.zip. Skeleton
builds clean PDF with title + anonymous author. \\ifanonymous toggle
gates submission vs cameraready Makefile targets."
```

---

### Task 9: Add labels for §-cross-references

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (preamble only — section labels added in Task 14+)

**Step 1: Add `\crefname` declarations for sections, equations, tables, figures**

Insert in preamble (above `\begin{document}`):
```latex
\crefname{section}{\S}{\S\S}
\crefname{equation}{Eq.}{Eqs.}
\crefname{table}{Table}{Tables}
\crefname{figure}{Figure}{Figures}
```

**Step 2: Verify build still passes**

Run: `cd paper/tex && make 2>&1 | tail -3 && cd - >/dev/null`
Expected: clean rebuild.

**Step 3: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): cleveref names for section/equation/table/figure refs"
```

---

### Task 10: Document conversion-progress markers in skeleton

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex`

**Step 1: Add section stub markers**

Insert section stubs with `% TODO Task N` markers between `\maketitle` and `\bibliography`. This lets you track progress and gives Pandoc/hand-port tasks a clear insertion point:

```latex
\maketitle

% Revision history — Task 18
% (draft-only material; deleted before submission)

\begin{abstract}
% Task 13
\end{abstract}

\section{Introduction}\label{sec:intro}
% Task 19 (Pandoc prose)

\section{Related Work}\label{sec:related}
% Tasks 20a-20f (six §2.x subsections — mostly Pandoc, §2.6 hand-port)

\section{The Persistence Thesis}\label{sec:thesis}
% Task 19 (Pandoc prose)

\section{Formalization}\label{sec:formal}
% Tasks 15 (propositions hand-port across §4.1-§4.7)

\section{Implementation}\label{sec:impl}
% Tasks 16-17 (§5.1-§5.7 mostly Pandoc; §5.8 hand-port)

\section{Evaluation --- Reproduction Plan}\label{sec:eval}
% Task 21 (Pandoc prose with [TBD] cells preserved)

\section{Discussion}\label{sec:discussion}
% Task 22 (Pandoc prose)

\section{Conclusion}\label{sec:conclusion}
% Task 22 (short, Pandoc)

\ifanonymous\else
\section*{Acknowledgements}
% Task 25 (camera-ready only)
\fi
```

**Step 2: Verify build**

Run: `cd paper/tex && make 2>&1 | tail -3 && cd - >/dev/null`
Expected: clean build, PDF now shows section headings (with empty bodies).

**Step 3: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): section stubs + \\label markers for cleveref"
```

---

## Phase 4: Bibliography

### Task 11: Build `references.bib` from existing References section

**Files:**
- Modify: `paper/tex/references.bib`
- Reference (read-only): `paper/archive/persistence-nesy-2026-v0.7.md` lines 487-517

**Step 1: Identify the source list**

Read lines 491-517 of `paper/archive/persistence-nesy-2026-v0.7.md` — the existing References section has ~26 entries. Each becomes one BibTeX entry.

**Step 2: Write `references.bib`**

Replace the empty `paper/tex/references.bib` with entries below. Keys follow `lastname<year>` convention (e.g. `tonsky2023`, `kambhampati2024`). Hand-fill every entry; do NOT skip ones whose venue is uncertain — mark uncertain venues with `% VERIFY: ...` comments rather than leaving them out.

Full entry list (write each as a `@inproceedings`, `@article`, `@misc`, or `@book` as appropriate):

```bibtex
@inproceedings{andrychowicz2017her,
  author = {Andrychowicz, Marcin and others},
  title = {Hindsight Experience Replay},
  booktitle = {NeurIPS},
  year = {2017},
}

@article{badreddine2022ltn,
  author = {Badreddine, Samy and {d'Avila Garcez}, Artur and Serafini, Luciano and Spranger, Michael},
  title = {Logic Tensor Networks},
  journal = {Artificial Intelligence},
  volume = {303},
  pages = {103649},
  year = {2022},
}

@misc{hannecke2026ssgm,
  author = {Hannecke, M. and others},
  title = {Governing Evolving Memory in {LLM} Agents: The {SSGM} Framework},
  year = {2026},
  note = {arXiv:2603.11768},
}

@article{kambhampati2024canllm,
  author = {Kambhampati, Subbarao},
  title = {Can Large Language Models Reason and Plan?},
  journal = {Annals of the New York Academy of Sciences},
  volume = {1534},
  number = {1},
  pages = {15--18},
  year = {2024},
}

@misc{kurtic2025zep,
  author = {Kurtic, E. and others},
  title = {Zep: A Temporal Knowledge Graph Architecture for Agent Memory},
  year = {2025},
  note = {arXiv:2501.13956},
}

@misc{khattab2023dspy,
  author = {Khattab, Omar and others},
  title = {{DSPy}: Compiling Declarative Language Model Calls into Self-Improving Pipelines},
  year = {2023},
  note = {Ongoing},
}

@misc{liu2023llmp,
  author = {Liu, B. and Jiang, Y. and Zhang, X. and Liu, Q. and Zhang, S. and Biswas, J. and Stone, P.},
  title = {{LLM+P}: Empowering Large Language Models with Optimal Planning Proficiency},
  year = {2023},
  note = {arXiv:2304.11477},
}

@inproceedings{manhaeve2018deepproblog,
  author = {Manhaeve, R. and Duman{\v{c}}i{\'c}, S. and Kimmig, A. and Demeester, T. and {De Raedt}, L.},
  title = {{DeepProbLog}: Neural Probabilistic Logic Programming},
  booktitle = {NeurIPS},
  year = {2018},
}

@misc{n1n2026memento,
  author = {{n1n.ai}},
  title = {Building a Bitemporal Knowledge Graph for {LLM} Agent Memory --- Memento Case Study},
  year = {2026},
  note = {Industry report},
}

@misc{memento2026skills,
  author = {{Memento-Teams}},
  title = {{Memento-Skills}: Framework for Self-Designing Agents},
  year = {2026},
  note = {arXiv:2603.18743},
}

@inproceedings{pryor2023neupsl,
  author = {Pryor, Connor and Dickens, Charles and Augustine, Eriq and Albalak, Alon and Wang, William Yang and Getoor, Lise},
  title = {{NeuPSL}: Neural Probabilistic Soft Logic},
  booktitle = {IJCAI},
  year = {2023},
}

@inproceedings{cheng2025pangolin,
  author = {Cheng, Shangyi and others},
  title = {Pangolin: Programming Large Language Models with Algebraic Effects},
  booktitle = {LMPL},
  year = {2025},
  % VERIFY: venue acronym
}

@inproceedings{valmeekam2022plansurvey,
  author = {Valmeekam, K. and Sreedharan, S. and Marquez, M. and Olmo, A. and Kambhampati, S.},
  title = {Large Language Models Still Can't Plan (A Benchmark for {LLMs} on Planning and Reasoning about Change)},
  booktitle = {NeurIPS Foundation Models for Decision Making Workshop},
  year = {2022},
}

@inproceedings{valmeekam2023planbench,
  author = {Valmeekam, K. and Marquez, M. and Sreedharan, S. and Kambhampati, S.},
  title = {{PlanBench}: An Extensible Benchmark for Evaluating Large Language Models on Planning and Reasoning about Change},
  booktitle = {NeurIPS Datasets and Benchmarks Track},
  year = {2023},
}

@misc{wang2025composable,
  author = {Wang, D. and others},
  title = {Composable Effect Handling for Programming {LLM}-integrated Scripts},
  year = {2025},
  note = {arXiv:2507.22048},
}

@misc{wang2023voyager,
  author = {Wang, Guanzhi and others},
  title = {Voyager: An Open-Ended Embodied Agent with Large Language Models},
  year = {2023},
  note = {arXiv:2305.16291},
}

@misc{anon2026camo,
  author = {{Anonymous}},
  title = {{CAMO}: Causal Analysis via Matched Outcomes for {LLM} Agent Simulations},
  year = {2026},
  note = {arXiv:2604.14691},
}

@misc{anon2026agenther,
  author = {{Anonymous}},
  title = {{AgentHER}: Hindsight Experience Replay for {LLM} Agent Trajectory Relabeling},
  year = {2026},
  note = {arXiv:2603.21357},
}

@misc{anon2025agentracer,
  author = {{Anonymous}},
  title = {{AgenTracer}: Counterfactual Fault Injection for Multi-Agent Failures},
  year = {2025},
  note = {OpenReview},
}

@misc{anon2025aap,
  author = {{Anonymous}},
  title = {Abduct-Act-Predict: Scaffolding Causal Inference for {LLM} Agents},
  year = {2025},
  note = {arXiv:2509.10401},
}

@misc{fan2024proofofthought,
  author = {Fan, Z. and others},
  title = {Proof of Thought: Neurosymbolic Program Synthesis},
  year = {2024},
  note = {arXiv:2409.17270},
}

@misc{meta2026kernelevolve,
  author = {{Meta Engineering}},
  title = {{KernelEvolve}: Ranking Engineer Agent for {AI} Infrastructure},
  year = {2026},
  note = {Industry report},
}

@misc{tonsky2023datomic,
  author = {Tonsky, Nikita},
  title = {Unofficial Guide to Datomic Internals},
  year = {2023},
  note = {Online: tonsky.me},
}

@misc{leijen_koka,
  author = {Leijen, Daan},
  title = {Koka: A Functional Language with Effect Types and Handlers},
  note = {Microsoft Research, ongoing},
}

@misc{bieniusa2026multiverse,
  author = {Bieniusa, A. and others},
  title = {Multiverse: Transactional Memory with Dynamic Multiversioning},
  year = {2026},
  note = {arXiv:2601.09735},
}

@misc{hickey2012database,
  author = {Hickey, Rich},
  title = {The Database as a Value},
  year = {2012},
  note = {Talk transcript},
}
```

**Step 3: Verify count**

Run: `grep -cE '^@(article|inproceedings|misc|book)' paper/tex/references.bib`
Expected: 26.

**Step 4: Verify build with empty `\cite{}` is still clean**

Run: `cd paper/tex && make 2>&1 | tail -5 && cd - >/dev/null`
Expected: clean (no `\cite` markers in body yet, so no warnings about undefined refs).

**Step 5: Commit**

```bash
git add paper/tex/references.bib
git commit -m "paper(v0.8): references.bib — 26 BibTeX entries from v0.7 References section

Mined from paper/archive/persistence-nesy-2026-v0.7.md lines 491-517.
Three uncertain venues marked with % VERIFY comments. Anonymous-authored
entries kept under stable keys (anon2026camo etc.) so \\cite stays valid
even when authors get de-anonymized in subsequent arXiv versions."
```

---

### Task 12: First `\cite{}` smoke-test

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex`

**Step 1: Insert one cite to verify the bibliography pipeline end-to-end**

In §1 stub, replace `% Task 19 (Pandoc prose)` with a placeholder line:
```latex
This paper draws on Datomic's bitemporal datom model~\citep{tonsky2023datomic} and \\
Pangolin's algebraic-effect handlers~\citep{cheng2025pangolin}.
% Smoke test — replaced by Task 19 Pandoc output.
```

(If the template uses `\cite` instead of `\citep`, switch accordingly — the template sample from Task 3 will tell you which.)

**Step 2: Verify build resolves both cites**

Run:
```bash
cd paper/tex && make 2>&1 | grep -E 'Warning|Error|undefined' | head
make
pdftotext persistence-nesy-2026.pdf - | grep -A 2 "draws on Datomic"
cd - >/dev/null
```

Expected: no `undefined references` warning. Body text shows `[24]` and `[12]` (or similar numbers) for the two cites; References list at end of PDF shows both entries.

**Step 3: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): smoke-test bibliography pipeline with two \\citep calls"
```

---

## Phase 5: Hand-port structured sections

### Task 13: Abstract

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (replace abstract body)
- Reference (read-only): `paper/archive/persistence-nesy-2026-v0.7.md` line 25

**Step 1: Read the source abstract**

Read line 25 of the archive file. The abstract is one long paragraph (~600 words).

**Step 2: Hand-port the abstract**

Replace the empty `\begin{abstract}...\end{abstract}` block with the full abstract text. Conversion rules:
- Markdown `**bold**` → `\textbf{...}`
- Markdown `*italic*` → `\emph{...}`
- Markdown `` `code` `` → `\C{...}` (use the macro defined in preamble)
- Inline parenthetical citations like "(Zep, Graphiti)" → `~\citep{kurtic2025zep}` placed at the end of the noun phrase
- Em-dashes `—` stay as-is (LaTeX renders them via inputenc/utf8)

Specific cite mapping for the abstract:
- "Zep, Graphiti" → `\citep{kurtic2025zep}` (Graphiti is a sister project, single citation)
- "Pangolin" → `\citep{cheng2025pangolin}`
- "DSPy" → `\citep{khattab2023dspy}`
- "Voyager, Memento-Skills" → `\citep{wang2023voyager,memento2026skills}`
- "CAMO, AgentHER" → `\citep{anon2026camo,anon2026agenther}`

**Step 3: Verify build**

Run: `cd paper/tex && make 2>&1 | grep -E 'Warning|Error|undefined' && pdftotext persistence-nesy-2026.pdf - | head -40 && cd - >/dev/null`
Expected: no undefined refs; abstract text appears in PDF; cites resolve to numbers.

**Step 4: Verify against source**

Run:
```bash
diff <(grep -oE '\\citep\{[a-z0-9,]+\}' paper/tex/persistence-nesy-2026.tex | sort -u) <(echo "expected")
# spot-check: do the 5 expected citations all appear?
grep -c '\\citep' paper/tex/persistence-nesy-2026.tex
```
Expected: at least 5 `\citep` occurrences (the 5 cite groups above) plus the 2 from the smoke test = 7+ total.

**Step 5: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port abstract from v0.7 markdown"
```

---

### Task 14: Hand-port §4 propositions (5 theorem environments)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§4 body)
- Reference: `paper/archive/persistence-nesy-2026-v0.7.md` lines 129-244

**Step 1: Read §4 source**

Read lines 129-244 of the archive — covers §4.1 (datoms + Prop 1), §4.2 (effects + Prop 2), §4.3 (audit + Prop 3?), §4.4 (plans + Prop 5), §4.5 (replay + Prop 3 NO-OP), §4.6 (transactions), §4.7 (specs).

Note: Prop numbering in v0.7 is:
- Prop 1: §4.1 — Branch is logical operation
- Prop 2: §4.2 — Effect well-formedness
- Prop 3: §4.5 — NO-OP byte-identity
- Prop 4: §4.3 — Tamper-evidence
- Prop 5: §4.4 — Plan content-addressing

**Step 2: Hand-port §4.1 (Datoms + Prop 1)**

Insert under `\section{Formalization}`:
```latex
\section{Formalization}\label{sec:formal}

\subsection{Datoms and bitemporal queries}\label{sec:datoms}

% (port the §4.1 prose verbatim — all italic emphasis, all $...$ math, all \C{} code spans)
% The display-math block at line 137 stays as $$...$$ but you may convert to:
\begin{equation}\label{eq:datom}
d = \langle e,\ a,\ v,\ \tau,\ \tau_{sys},\ \nu_{from},\ \nu_{to},\ \omega \rangle
\end{equation}

% then the surrounding prose with inline math $e$, $a$, $v$, etc. — verbatim from source

\begin{proposition}[Branch is a logical operation over the shipped store]\label{prop:branch}
\C{branch(D, t, \(\Delta\))} returns a new \C{DB} value backed by a fresh in-memory store seeded with \C{asOf(D, t)} and extended with $\Delta$; writes to the branched value cannot leak back into the parent store. \emph{Complexity:} on the Phase 1 \C{InMemoryStore} reference implementation (\C{src/persistence/fact/db.py}), materialization is $O(|D|)$ in the seed snapshot plus $O(|\Delta|)$ in the hypothetical additions. \emph{Phase 2 upgrade:} under a persistent hash-array-mapped-trie (HAMT) backing store, the seed step reduces to $O(|\Delta| \log |D|)$ via structural path-copy; the \C{Store} Protocol boundary makes this a drop-in replacement requiring no change to \C{branch}'s interface.
\end{proposition}
```

**Step 3: Hand-port §4.2 (Effects + Prop 2)**

Same pattern. The catalog $K = \{...\}$ display math (lines 158-160) → `equation*` (or `align*` if it spans multiple lines):
```latex
\begin{align*}
K = \{ & \C{llm/call},\ \C{tool/call},\ \C{mem/read},\ \C{mem/write},\ \C{decide},\ \C{ask-user}, \\
       & \C{emit-artifact},\ \C{sleep},\ \C{random},\ \C{env/read},\ \C{net/fetch},\ \C{secret/use}, \\
       & \C{cost/charge},\ \C{clock/now},\ \C{audit/emit} \}
\end{align*}
```

Handler signature:
```latex
\begin{equation}\label{eq:handler}
h : (\kappa,\ \sigma_{in},\ k : \sigma_{out} \to \alpha,\ \mathrm{ctx}) \to \alpha
\end{equation}
```

```latex
\begin{proposition}[Well-formedness; machine-checkable on the shipped runtime]\label{prop:wellformed}
A stack $H$ over catalog $K$ is well-formed iff for every $\kappa \in K$, at least one handler above the raw base handles $\kappa$. The shipped \C{Runtime.is\_well\_formed(catalog)} (\C{src/persistence/effect/runtime.py}) decides this property in $O(|H| \cdot |K|)$ time; \C{Runtime.uncovered\_ops(catalog)} returns the witness set. At runtime, \C{Runtime.perform(op, \dots)} raises \C{Unhandled} when no handler covers $\kappa$ --- the property is not merely asserted but \emph{enforced on every call}.
\end{proposition}
```

**Step 4: Hand-port §4.3 (Audit + Prop 4)**

```latex
\subsection{The Merkle-hashed audit chain}\label{sec:audit}

% (port §4.3 prose; bullet list naming make_audit_handler / verify_chain / audit_entry_to_datom becomes itemize)

\begin{itemize}
\item \C{make\_audit\_handler} ...
\item \C{verify\_chain} ...
\item \C{audit\_entry\_to\_datom} ...
\item \emph{(v0.4.0a1)} \C{audit\_entry\_to\_datom} additionally writes a \C{parent\_provenance\_hash} alias alongside the existing \C{:prev-hash} keyword --- both keys point to the same chain hash. This bridges audit-entry datoms into the typed \C{Provenance} schema (\cref{sec:datoms}) so that \C{DB.causal\_history(e)} walks audit chains and ordinary fact-log derivation chains under one query primitive.
\end{itemize}

\begin{proposition}[Tamper-evidence]\label{prop:tamper}
% (port verbatim from line 184)
\end{proposition}
```

**Step 5: Hand-port §4.4 (Plans + Prop 5)**

Largest math/logic block — 4-5 paragraphs prose + Prop 5 (~250 words). Port verbatim, with `\C{}` for all the inline code spans (`Node.id`, `parse(unparse(n))`, `:id`, etc.). Replace bullet-style citations with `\citep{}`.

**Step 6: Hand-port §4.5 (Replay + Prop 3 NO-OP byte-identity)**

```latex
\subsection{Trajectories and replay}\label{sec:replay}

% (port §4.5 prose)

\begin{equation}\label{eq:replay}
\mathrm{replay}(T, I) = T' \text{ where ...}
\end{equation}

\begin{proposition}[NO-OP byte-identity replay]\label{prop:noop}
% (port verbatim; this is the strongest empirical claim)
\end{proposition>
```

**Step 7: Hand-port §4.6 + §4.7 (Transactions, Specs)**

Shorter sections, mostly prose. Port verbatim with `\C{}` macros and `\citep{}` for cited works.

**Step 8: Verify all 5 propositions render**

Run:
```bash
cd paper/tex && make 2>&1 | grep -E 'Warning|Error|undefined' && pdftotext persistence-nesy-2026.pdf - | grep -E '^Proposition [1-5]' && cd - >/dev/null
```
Expected:
- No undefined refs
- 5 lines: `Proposition 1`, `Proposition 2`, `Proposition 3`, `Proposition 4`, `Proposition 5` (numbered automatically)

**Step 9: Verify cleveref**

Insert one test cross-reference somewhere in §4.5: `As shown in \cref{prop:branch} ...`
Run: `cd paper/tex && make && pdftotext persistence-nesy-2026.pdf - | grep -i "as shown in proposition" && cd - >/dev/null`
Expected: PDF text shows "As shown in Proposition 1" — `\cref` resolved.
Then remove the test sentence (it was just a smoke test).

**Step 10: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §4 Formalization with 5 \\begin{proposition} environments

Five propositions ported as theorem environments with \\label{prop:branch},
prop:wellformed, prop:tamper, prop:noop, prop:plan. Display math wrapped
in equation environments. Inline code spans use \\C{} macro."
```

---

### Task 15: §2.6 capability matrix table

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§2.6 body)
- Reference: `paper/archive/persistence-nesy-2026-v0.7.md` lines 92-112

**Step 1: Read source**

Lines 92-112 contain the §2.6 Positioning subsection with a 9-row × 7-column markdown table.

**Step 2: Hand-port as `tabular`**

Insert in §2.6 (under the Positioning prose):
```latex
\subsection{Positioning}\label{sec:positioning}

% (port §2.6 intro prose)

\begin{table}[ht]
\centering
\small
\begin{tabular}{lccccccc}
\toprule
Capability & Zep/Graphiti & Pangolin & DSPy & Voyager & CAMO & \makecell{Persistence \\ (Phase 1 shipped)} & \makecell{Persistence \\ (Phase 2 designed)} \\
\midrule
Bitemporal memory                              & $\bullet$ &           &           &           &           & $\bullet$ &            \\
Effect handler stack                           &           & $\bullet$ &           &           &           & $\bullet$ &            \\
Merkle-hashed audit chain                      & partial   &           &           &           &           & $\bullet$ &            \\
Counterfactual replay (byte-identical NO-OP)   &           &           &           &           & partial   & $\bullet^*$ &          \\
Boundary specs + LLM self-healing hints        &           &           & partial   &           &           & $\bullet$ &            \\
Declarative plan AST                           &           &           & $\bullet$ &           &           &           & $\circ$    \\
Plan-AST optimization                          &           &           & partial   &           &           &           & $\circ$    \\
Skill library (4-gate promotion)               &           &           &           & $\bullet$ &           &           & $\circ$    \\
Multi-agent STM                                &           &           &           &           &           &           & $\circ$    \\
Live production REPL                           &           &           &           &           &           &           & $\circ$    \\
Regulator-replay fidelity                      &           &           &           &           &           & \multicolumn{2}{c}{[designed --- see \cref{sec:eval}]} \\
\bottomrule
\end{tabular}
\caption{Capability coverage across related systems vs.\ Persistence (Phase 1 shipped, Phase 2 designed). $\bullet$ = supported; $\circ$ = designed-but-not-shipped; ``partial'' = covered with caveats. The asterisk on Persistence Phase-1 NO-OP replay denotes the toy-agent-vs-LLM-leaf footnote (see \cref{sec:replay}).}
\label{tab:capability-matrix}
\end{table>
```

Note: `\makecell` requires `\usepackage{makecell}` in the preamble. Add it now if not already present.

**Step 3: Verify build**

Run:
```bash
cd paper/tex && make 2>&1 | grep -E 'Warning|Error|Overfull' | head && cd - >/dev/null
```
Expected: no errors; possibly an `Overfull \hbox` warning if the table is too wide — if so, switch to `\scriptsize` or use `tabularx` with explicit column widths.

**Step 4: Verify table renders**

Run: `cd paper/tex && make && pdftotext persistence-nesy-2026.pdf - | grep -A 1 "Capability coverage" && cd - >/dev/null`
Expected: capability-matrix caption text appears in PDF text extraction.

**Step 5: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §2.6 capability matrix as tabular environment"
```

---

### Task 16: §5.8 system diagram (verbatim ASCII)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex`
- Reference: `paper/archive/persistence-nesy-2026-v0.7.md` lines 295-323

**Step 1: Read source diagram**

Lines 297-324 contain the ASCII box-drawing diagram.

**Step 2: Hand-port as `verbatim`**

Insert in §5.8:
```latex
\subsection{System diagram}\label{sec:diagram}

\begin{figure}[ht]
\centering
\begin{verbatim}
  ┌─────────────────────────────────────────────────────────┐
  │  AGENT (business logic, domain-specific)                │
  └───────────────────────┬─────────────────────────────────┘
                          │ (perform :op args)
                          ▼
  ┌─── Effect ─── Spec ──┤ Txn [Phase 2] ├─────────────────┐
  │   handler stack, boundary contracts, STM commits        │
  └───────────────────────┬─────────────────────────────────┘
                          │ (emit datoms)
                          ▼
  ┌─── Fact ────────────────────────────────────────────────┐
  │   InMemory / SQLite log  ·  DictProjection              │
  │   [Phase 2: Postgres + Kuzu + mem0]                     │
  └─── ▲ ────────────────────────────────── ▲ ──────────────┘
       │ as-of / branch / history           │
       │                                    │
  ┌─── Replay ──────┐             ┌─── Plan [Phase 2] ──────┐
  │  trajectories   │             │  EDN AST + skills       │
  │  counterfactual │             │  MIPROv2 / MCTS / evo   │
  │  NO-OP identity │ ──evidence→ │  4-gate promotion       │
  └─────────────────┘             └─────────────────────────┘
                          ▲
                          │
  ┌─── REPL [Phase 2] ─────┴────────────────────────────────┐
  │   inspect / edit / rewind / branch (capability-gated)   │
  └─────────────────────────────────────────────────────────┘
\end{verbatim}
\caption{Persistence runtime stack. Solid arrows are runtime data flows; dashed boundaries are module Protocol surfaces. Phase 1 ships Fact / Effect / Spec / Replay; Phase 2 ships Plan execution / Txn / REPL.}
\label{fig:system-diagram}
\end{figure>
```

**Step 3: Verify Unicode box-drawing chars survive**

Tectonic/XeLaTeX-style engines handle UTF-8 well; pdfLaTeX may not. The NeSy template's class file should specify the engine. If you get errors like "Package inputenc Error: Unicode character", add to preamble:
```latex
\usepackage[utf8]{inputenc}   % usually already in template
% If still failing, force utf8x or fontspec:
% \usepackage{fontspec}  % requires XeLaTeX/LuaLaTeX
```

Tectonic uses XeLaTeX-compatible mode by default for unicode handling, so this should Just Work.

**Step 4: Build + verify**

Run:
```bash
cd paper/tex && make 2>&1 | grep -E 'Error|Unicode|inputenc' | head && cd - >/dev/null
```
Expected: no Unicode errors. If errors, fall back to ASCII alternatives (`+--+` instead of `┌─┐`) and file the TikZ upgrade task with higher priority.

**Step 5: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §5.8 system diagram as verbatim ASCII

Follow-up task scheduled to upgrade to TikZ before 9 June abstract deadline."
```

---

### Task 17: Revision history block

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex`
- Reference: `paper/archive/persistence-nesy-2026-v0.7.md` lines 10-19

**Step 1: Decide whether the revision history goes in the submission**

The revision history is internal-development context — NOT something a NeSy reviewer needs in a 10-page submission. Two options:

- **Option A:** Drop it from the submission `.tex` entirely. The history lives in git + the archived markdown. Simplest, saves page budget.
- **Option B:** Keep it in `\ifanonymous\else ... \fi` (camera-ready only) as an appendix — gives reviewers context post-acceptance.

**Recommendation:** Option A. The 10-page limit is tight; 9 versions of revision-history bullets cost ~1 page. Keep the history in git/markdown archive only.

**Step 2: Add a deferred-section comment in `.tex`**

In the section between `\maketitle` and `\begin{abstract}`, insert:
```latex
% --- Revision history ---
% Intentionally NOT included in submission .tex. The v0.1-v0.7 history is
% preserved in paper/archive/persistence-nesy-2026-v0.7.md (lines 10-19).
% If a future reviewer requests it, it can be re-added under \ifanonymous\else.
```

**Step 3: No PDF change** — skip build verification. Comment-only change.

**Step 4: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): drop revision history from submission .tex (preserved in archive)"
```

---

### Task 18: §1 *What this paper reports, honestly* paragraph

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§1 stub)
- Reference: `paper/archive/persistence-nesy-2026-v0.7.md` line 45

**Step 1: Read source**

Line 45 is one long paragraph. Hand-port as a single paragraph (Pandoc would mangle the dense `\C{}` spans).

**Step 2: Port verbatim**

Replace the smoke-test placeholder in §1 with the actual `What this paper reports, honestly` paragraph from line 45. Same conversion rules as Task 13:
- `**bold**` → `\textbf{}`
- `*italic*` → `\emph{}`
- `` `code` `` → `\C{}`
- `→` (Unicode arrow) → `$\to$` or keep verbatim
- v0.4.0a1 etc. stay as-is

Cite mappings (the paragraph mentions specific test counts + commits — these are subject to the anonymization scrub in Task 23, leave them in for now).

**Step 3: Verify build + cite count**

Run:
```bash
cd paper/tex && make 2>&1 | grep -E 'Warning|Error' && grep -c '\\citep\|\\cite{' paper/tex/persistence-nesy-2026.tex && cd - >/dev/null
```
Expected: cite count grew from previous tasks; no errors.

**Step 4: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §1 'What this paper reports, honestly' paragraph"
```

---

## Phase 6: Pandoc-then-clean prose sections

### Task 19: Pandoc §1 (Introduction)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§1 body)
- Temp: `/tmp/section-01-intro.tex` (Pandoc output, scratch only)

**Step 1: Extract §1 from archive .md**

Run:
```bash
sed -n '29,59p' paper/archive/persistence-nesy-2026-v0.7.md > /tmp/section-01-intro.md
wc -l /tmp/section-01-intro.md
```

Expected: ~31 lines.

**Step 2: Run Pandoc**

Run:
```bash
pandoc -f markdown -t latex --wrap=preserve /tmp/section-01-intro.md -o /tmp/section-01-intro.tex
head -20 /tmp/section-01-intro.tex
```

Expected: LaTeX output with `\section`, `\textbf`, `\texttt`, etc.

**Step 3: Clean Pandoc output**

Pandoc-produced output typically needs these manual fixes:
- `\section{1. Introduction}` → `\section{Introduction}` (drop the manual `1.` numbering — LaTeX auto-numbers)
- `\texttt{Foo}` → `\C{Foo}` (use the macro for short identifiers)
- Replace inline `(Tonsky 2023)` with `\citep{tonsky2023}`
- Markdown footnote markers (rare in this paper)
- Pandoc may emit `\hfill\break` for soft breaks — usually safe to delete

Use a focused scratch buffer; do NOT just paste raw Pandoc output.

**Step 4: Insert cleaned content into the main .tex**

Replace the §1 stub with the cleaned content. Preserve `\label{sec:intro}` if Pandoc didn't generate it.

The `What this paper reports, honestly` paragraph from Task 18 should NOT be overwritten — it's already verbatim. Ensure the Pandoc output for §1 covers the *rest* of §1 and slots in the hand-ported paragraph at the right place.

**Step 5: Verify build**

Run: `cd paper/tex && make 2>&1 | grep -E 'Warning|Error' | head && cd - >/dev/null`
Expected: no errors. Possibly some `Reference X undefined` warnings if any Pandoc-generated `\cite{...}` keys don't match `references.bib` — fix those before commit.

**Step 6: Verify all expected cites resolve**

Run:
```bash
cd paper/tex && pdftotext persistence-nesy-2026.pdf - | head -100 | grep -c "\[" && cd - >/dev/null
```
Expected: multiple bracketed cite numbers.

**Step 7: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §1 Introduction (Pandoc + cleanup)"
```

---

### Task 20: Pandoc §2 (Related Work, six subsections)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§2 body)
- Temp: `/tmp/section-02-related.{md,tex}`

**Step 1: Extract §2 from archive**

Run:
```bash
sed -n '60,113p' paper/archive/persistence-nesy-2026-v0.7.md > /tmp/section-02-related.md
```

**Step 2: Run Pandoc**

```bash
pandoc -f markdown -t latex --wrap=preserve /tmp/section-02-related.md -o /tmp/section-02-related.tex
```

**Step 3: Clean**

Same rules as Task 19. Special attention:
- §2.6 (Positioning) was already hand-ported in Task 15 — when inserting Pandoc output, **skip** §2.6 prose body that overlaps with the Task-15 prose; only insert prose Pandoc generated for §2.1-§2.5.
- All ~10-15 `\citep{}` insertions: map inline parentheticals (Zep, Pangolin, DSPy, Voyager, CAMO, AgentHER, Liu 2023, Kambhampati 2024, Valmeekam 2022/2023, Badreddine 2022, Manhaeve 2018, Pryor 2023, Hannecke 2026, Andrychowicz 2017) to the matching `references.bib` keys.

**Step 4: Insert + verify**

Replace §2 stub. Run: `cd paper/tex && make 2>&1 | grep -E 'undefined|Error' && cd - >/dev/null`
Expected: no undefined refs. If any, the Pandoc output produced a cite key not in `references.bib` — either add the entry to .bib or fix the cite key.

**Step 5: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §2 Related Work (Pandoc + cleanup; §2.6 unchanged)"
```

---

### Task 21: Pandoc §3 (Persistence Thesis)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§3 body)

**Step 1: Extract + Pandoc**

```bash
sed -n '114,128p' paper/archive/persistence-nesy-2026-v0.7.md > /tmp/section-03-thesis.md
pandoc -f markdown -t latex --wrap=preserve /tmp/section-03-thesis.md -o /tmp/section-03-thesis.tex
```

**Step 2: Clean + insert**

Same conversion rules. §3 is short (~14 source lines).

**Step 3: Build + commit**

```bash
cd paper/tex && make 2>&1 | grep -E 'Error|undefined' && cd - >/dev/null
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §3 Persistence Thesis"
```

---

### Task 22: Pandoc §5 (Implementation, 8 subsections, except §5.8)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§5 body)

**Step 1: Extract §5.1–§5.7 (skip §5.8 — Task 16 handled it)**

Source lines: §5.1 = 249-258, §5.2 = 259-268, §5.3 = 269-272, §5.4 = 273-276, §5.5 = 277-286, §5.6 = 287-290, §5.7 = 291-294. §5.8 was 295-326 (already done).

```bash
sed -n '245,294p' paper/archive/persistence-nesy-2026-v0.7.md > /tmp/section-05-impl.md
pandoc -f markdown -t latex --wrap=preserve /tmp/section-05-impl.md -o /tmp/section-05-impl.tex
```

**Step 2: Clean + insert**

Same rules. §5.5 has the longest hardening-track-status paragraph — ensure all `\C{}` spans land correctly.

**Step 3: Build + commit**

```bash
cd paper/tex && make && cd - >/dev/null
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §5 Implementation (§5.1–§5.7; §5.8 unchanged)"
```

---

### Task 23: Pandoc §6 (Evaluation — Reproduction Plan)

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§6 body)

**Step 1: Extract**

```bash
sed -n '328,422p' paper/archive/persistence-nesy-2026-v0.7.md > /tmp/section-06-eval.md
pandoc -f markdown -t latex --wrap=preserve /tmp/section-06-eval.md -o /tmp/section-06-eval.tex
```

**Step 2: Clean + insert**

Special items in §6:
- The §6.1 LongMemEval table (lines 338-344) is small (5 rows × 3 cols, mostly `[per primary source]` placeholder). Hand-port as `tabular`:

```latex
\begin{table}[ht]
\centering
\small
\begin{tabular}{lcc}
\toprule
System & Accuracy & p95 latency \\
\midrule
MemGPT          & [per primary source] & [per primary source] \\
Mem0            & [per primary source] & [per primary source] \\
Mem0g           & [per primary source] & [per primary source] \\
Zep / Graphiti  & [per primary source] & [per primary source] \\
Memento         & [per primary source] & [per primary source] \\
\bottomrule
\end{tabular}
\caption{LongMemEval baselines (camera-ready: numbers re-verified against primary sources).}
\label{tab:longmemeval}
\end{table>
```

- §6.4 is `*(Removed from this paper.)*` — port that line verbatim as `\subsection{(Removed from this paper.)}` or just `\subsection*{}` with explanatory paragraph.

- §6.5 contains "Adaptive Trader v2" — leave it in for now; Task 26 anonymization scrub will redact.

**Step 3: Build + commit**

```bash
cd paper/tex && make && cd - >/dev/null
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §6 Evaluation — Reproduction Plan"
```

---

### Task 24: Pandoc §7 + §8 + Acknowledgements

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex` (§7, §8, acks)

**Step 1: Extract**

```bash
sed -n '423,486p' paper/archive/persistence-nesy-2026-v0.7.md > /tmp/section-07-discussion.md
pandoc -f markdown -t latex --wrap=preserve /tmp/section-07-discussion.md -o /tmp/section-07-discussion.tex
```

**Step 2: Clean + insert**

§7 covers Limitations, Privacy architecture, Adoption path, NeSy framing. §8 is Conclusion (short). Acknowledgements wrap in `\ifanonymous\else ... \fi`.

**Step 3: Build + commit**

```bash
cd paper/tex && make && cd - >/dev/null
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): port §7 Discussion + §8 Conclusion + Acknowledgements (anon-gated)"
```

---

## Phase 7: Anonymization scrub

### Task 25: Scrub commit SHAs and branch names

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex`

**Step 1: Identify all commit-SHA mentions**

Run:
```bash
grep -nE '[a-f0-9]{6,7}' paper/tex/persistence-nesy-2026.tex | head -30
```

Expected: ~10-12 lines mentioning SHAs like `b459fe5`, `7bac436`, `be7e37f`, `b9cbf37`, `bce93da`, `0e8b88f`, etc.

**Step 2: Replace each with redaction marker + restore comment**

For each match, replace verbatim. Example:

Before:
```latex
The Plan module v0.2.0a1 (merged to \C{main} at commit \C{b459fe5}, tagged 2026-04-24) ...
```

After:
```latex
The Plan module v0.2.0a1 (merged to \C{main} at commit \C{[redacted]}, tagged 2026-04-24) ... % RESTORE: b459fe5
```

Repeat for every SHA. The `% RESTORE: <sha>` trailing comment is the camera-ready cookie — Task 28 (post-July-8) restoration will grep for it.

**Step 3: Strip branch names**

Run: `grep -n 'feat/v0\|feat/paper' paper/tex/persistence-nesy-2026.tex`
Expected: 1-2 mentions. Replace with `[branch redacted]` and add `% RESTORE: feat/v0.4-substrate-primitives` (or whatever the branch was).

**Step 4: Verify**

Run:
```bash
grep -cE 'RESTORE:' paper/tex/persistence-nesy-2026.tex
```
Expected: matches the count of redactions you made (10-15).

Run: `cd paper/tex && make 2>&1 | grep -E 'Error' && cd - >/dev/null`
Expected: clean build.

**Step 5: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): anonymization scrub — redact commit SHAs and branch names

Each redaction tagged with % RESTORE: <original> for the camera-ready
restoration pass scheduled between July 8 notification and July 20 deadline."
```

---

### Task 26: Scrub "Adaptive Trader v2" + license-intent line

**Files:**
- Modify: `paper/tex/persistence-nesy-2026.tex`

**Step 1: Find all "Adaptive Trader v2" mentions**

Run:
```bash
grep -nE 'Adaptive Trader|adaptive[_ -]trader' paper/tex/persistence-nesy-2026.tex
```

Expected: ~3-5 mentions in §6.5 + abstract.

**Step 2: Replace**

Replace each with `Case-A: a production algorithmic-trading deployment` or `Case~A` (depending on the sentence shape). Tag: `% RESTORE: Adaptive Trader v2`.

Section heading "Case studies — one named deployment, three anonymized vignettes" → "Case studies — one production deployment, three anonymized vignettes". Tag: `% RESTORE: 'one named deployment'`.

**Step 3: Drop the license-intent metadata if it was ported**

Run: `grep -n 'AGPL\|License intent' paper/tex/persistence-nesy-2026.tex`

If any matches: delete those lines (they were front-matter metadata in the markdown, not part of the paper proper). The license is a post-acceptance concern; not in the submission.

**Step 4: Search for any remaining identifying strings**

Run:
```bash
grep -niE 'saadi|aibox|ai-?box|nawfal|persistence-?os.git|github\.com/[a-z]' paper/tex/persistence-nesy-2026.tex
```

Expected: no matches. Any matches found → replace + tag.

**Step 5: Verify build**

Run: `cd paper/tex && make 2>&1 | grep -E 'Error' && cd - >/dev/null`
Expected: clean.

**Step 6: Commit**

```bash
git add paper/tex/persistence-nesy-2026.tex
git commit -m "paper(v0.8): anonymization scrub — Adaptive Trader v2, license metadata, identity strings"
```

---

## Phase 8: Verification gates

### Task 27: Clean build + page count

**Files:** none (read-only checks)

**Step 1: Clean build**

Run:
```bash
cd paper/tex && make clean && make 2>&1 | tee /tmp/last-build.log | tail -10 && cd - >/dev/null
grep -E 'Error|Undefined' /tmp/last-build.log | head
```

Expected:
- Build succeeds, exits 0
- No `Error` lines
- No `Reference X undefined` warnings (warnings about float placement / overfull hboxes acceptable but note them)

**Step 2: Page count check**

Run:
```bash
cd paper/tex
PAGES=$(pdfinfo persistence-nesy-2026.pdf 2>/dev/null | grep '^Pages:' | awk '{print $2}')
echo "Pages: $PAGES"
[ "$PAGES" -le 14 ] && echo "OK: ≤ 14 (10 body + ~4 refs)" || echo "OVER LIMIT"
cd - >/dev/null
```

Expected: ≤ 14 pages total (NeSy limit is 10 excl. references; refs typically add 2-4 pages).

If over limit: identify the longest section, file a follow-up trim task. Don't trim during conversion — this is structural conversion, not editorial.

**Step 3: No commit** — read-only.

---

### Task 28: Anonymization grep-check

**Files:** none

**Step 1: Build the latest PDF**

Run: `cd paper/tex && make 2>&1 | tail -2 && cd - >/dev/null`

**Step 2: Extract text and grep for de-anonymization risks**

Run:
```bash
cd paper/tex
pdftotext persistence-nesy-2026.pdf - > /tmp/paper-text.txt
echo "=== Identity strings ==="
grep -niE 'saadi|aibox|nawfal|adaptive trader|/Users/' /tmp/paper-text.txt | head
echo "=== Commit SHAs ==="
grep -nE '\b[a-f0-9]{7}\b' /tmp/paper-text.txt | head
echo "=== Branch names ==="
grep -nE 'feat/(v[0-9]|paper)' /tmp/paper-text.txt | head
echo "=== Repo URLs ==="
grep -niE 'github\.com|gitlab\.com|persistence-os\.git' /tmp/paper-text.txt | head
cd - >/dev/null
```

Expected: ALL FOUR sections empty (no matches).

**Step 3: If any matches found**

Each match is a de-anonymization defect. Add to a new `paper/tex/ANON_AUDIT.md` (gitignored or committed) listing the leaks, fix each one, re-run Step 2.

Repeat until all four sections are empty.

**Step 4: Confirm Make-target gating works**

Run:
```bash
cd paper/tex
make submission 2>&1 | tail -2     # should succeed
make cameraready 2>&1 | tail -2    # should ABORT
cd - >/dev/null
```

Expected:
- `make submission` → "→ submission-anonymized.pdf ready"
- `make cameraready` → "ABORT: ... requires \\anonymousfalse" (exit non-zero)

**Step 5: No commit** — read-only.

---

### Task 29: Verify all 5 propositions render with cleveref resolution

**Files:** none

**Step 1: Confirm 5 numbered theorem boxes appear**

Run:
```bash
cd paper/tex
pdftotext persistence-nesy-2026.pdf - | grep -E '^Proposition [1-5][^0-9]'
cd - >/dev/null
```

Expected: 5 lines:
```
Proposition 1 (Branch is a logical operation over the shipped store).
Proposition 2 (Well-formedness; machine-checkable on the shipped runtime).
Proposition 3 (NO-OP byte-identity replay).
Proposition 4 (Tamper-evidence).
Proposition 5 (Plan content-addressing with descendant propagation).
```

**Step 2: Confirm `\cref{prop:...}` references resolve in body text**

Run:
```bash
cd paper/tex && grep -c '\\cref{prop:' persistence-nesy-2026.tex && cd - >/dev/null
```

Expected: ≥ 1 (probably 3-5 cross-references).

Run:
```bash
cd paper/tex && pdftotext persistence-nesy-2026.pdf - | grep -E '(see|under|by)\s+Proposition\s+[1-5]' | head && cd - >/dev/null
```
Expected: matches — meaning `\cref{prop:branch}` rendered as "Proposition 1" in body text.

**Step 3: No commit** — read-only.

---

### Task 30: Side-by-side spot-check vs archived markdown

**Files:** none

**Step 1: Pick 5 spot-check anchors**

For each anchor, read both the markdown source and the rendered PDF text — they should convey the same claim:

- Abstract: §0 line 25 (markdown) vs PDF page 1
- Prop 5 statement: line 193 (md) vs PDF §4.4
- §2.6 capability matrix: line 96-108 (md) vs PDF Table 1
- §5.8 system diagram: line 297-323 (md) vs PDF Figure 1
- §6.5 case-study list: lines 380-414 (md) vs PDF §6.5

Run for each (example for Prop 5):
```bash
sed -n '193p' paper/archive/persistence-nesy-2026-v0.7.md | head -c 200
echo
echo "---"
pdftotext paper/tex/persistence-nesy-2026.pdf - | grep -A 2 "Plan content-addressing" | head -c 400
```

Expected: claims align (modulo `\C{}` macros vs backticks; modulo `[redacted]` vs commit SHAs).

**Step 2: Document any divergences**

If you find a divergence, add a comment in the .tex line referencing the archive line: `% v0.7 line N: <reason for divergence>`.

**Step 3: No commit** — read-only check.

---

## Phase 9: Persistence + handoff

### Task 31: Write conductor STATUS update

**Files:**
- Modify: `~/Projects/ai-box/conductor/tracks/persistence-os-foundation_20260420/STATUS.md`

**Step 1: Append a v0.8 LaTeX-conversion shipping section**

Append to STATUS.md (use Edit tool with `\n\n## Paper v0.8 LaTeX conversion SHIPPED ...` block). Include:
- Date (2026-04-26 or whenever the conversion completes)
- Branch name + final commit SHA
- File count + line count diff
- Anonymization summary (number of redactions, RESTORE-tag count)
- Page count of resulting PDF
- Pointers to the design + impl plan docs
- Outstanding follow-ups (TikZ §5.8 upgrade, full ARIS R3 on cumulative diff, restoration pass)

**Step 2: Verify STATUS file was updated**

Run: `tail -20 ~/Projects/ai-box/conductor/tracks/persistence-os-foundation_20260420/STATUS.md`

**Step 3: Commit (in the ai-box repo, NOT persistence-os)**

```bash
cd ~/Projects/ai-box
git add conductor/tracks/persistence-os-foundation_20260420/STATUS.md
git commit -m "conductor(persistence-os): paper v0.8 LaTeX conversion shipped"
cd - >/dev/null
```

---

### Task 32: Persist to vault, Serena memory, MEMORY.md

**Files:**
- Modify: `~/.claude-nawfal-2/projects/-Users-nawfalsaadi-Projects/memory/MEMORY.md` (insert v0.8 bullet)
- Vault entry (curl or `vault remember`)
- Serena memory `paper-v0.8-latex-conversion-shipped`

**Step 1: Vault entry**

Run:
```bash
vault remember "Persistence OS — Paper v0.8 LaTeX conversion SHIPPED <date>. Branch <branch> final commit <sha>. Single-file paper/tex/persistence-nesy-2026.tex (~600 lines) using official NeSy 2026 PMLR template (jmlr.cls-based). 26-entry references.bib. 5 amsthm propositions with \\cref cross-refs. \\ifanonymous toggle gates Makefile submission/cameraready. Anonymization scrub redacted N commit SHAs / 1 branch name / 'Adaptive Trader v2' (all tagged with % RESTORE: for July 8-20 restoration pass). PDF page count: <pages> (≤ 14 limit). v0.7 markdown archived to paper/archive/. Follow-ups: §5.8 TikZ upgrade before 9 June abstract deadline; full ARIS R1+R2+R3 on v0.6→v0.8 cumulative diff. Vault memory_id: <id>." --user-id=nawfal-dev --tier=L1
```

**Step 2: Serena memory**

Use `mcp__plugin_serena_serena__write_memory` to write a memory at name `paper-v0.8-latex-conversion-shipped` with the same gist as the vault entry plus pointers to the design + impl plan files.

**Step 3: MEMORY.md auto-memory bullet**

Edit `~/.claude-nawfal-2/projects/-Users-nawfalsaadi-Projects/memory/MEMORY.md` to add a new bullet under the Persistence OS section:

```markdown
- **Paper v0.8 LaTeX conversion SHIPPED <date>**. Branch `<branch>` final `<sha>`. Single-file `paper/tex/persistence-nesy-2026.tex` from official NeSy 2026 PMLR template (jmlr.cls-based, double-blind, anonymized). 26-entry `references.bib`. 5 `amsthm` propositions with `\cref`. `\ifanonymous` toggle gates Makefile `submission`/`cameraready`. Anonymization redacted commit SHAs / branch / "Adaptive Trader v2" (all tagged `% RESTORE:`). PDF <N> pages (≤ 14 limit; NeSy 10-page body excl. refs). v0.7 markdown archived to `paper/archive/`. Follow-ups: §5.8 TikZ before 9 June abstract; full ARIS R1+R2+R3 on v0.6→v0.8 cumulative diff. Vault tx=<n>.
```

**Step 4: Verify**

Run: `head -5 ~/.claude-nawfal-2/projects/-Users-nawfalsaadi-Projects/memory/MEMORY.md`
Expected: new bullet near the top of the Persistence OS section.

**Step 5: No commit needed for MEMORY.md** (it lives outside the repo). Conductor STATUS commit happened in Task 31. Persistence-os repo commit history already captures the conversion.

---

## Final state

After all 32 tasks:

```
persistence-os/
├── docs/plans/
│   ├── 2026-04-26-paper-latex-conversion-design.md      ✓ committed
│   └── 2026-04-26-paper-latex-conversion-impl.md        ✓ this file
├── paper/
│   ├── archive/
│   │   └── persistence-nesy-2026-v0.7.md                ✓ committed (frozen)
│   └── tex/
│       ├── persistence-nesy-2026.tex                    ✓ committed (~600 lines, 5 props, 26 cites)
│       ├── references.bib                               ✓ committed
│       ├── nesy2026.cls (or jmlr.cls)                   ✓ committed (from template ZIP)
│       ├── Makefile                                     ✓ committed
│       └── .gitignore                                   ✓ committed
└── (rest unchanged)
```

Outside the repo:
- Conductor track STATUS.md updated
- Vault entry written
- Serena memory `paper-v0.8-latex-conversion-shipped` written
- MEMORY.md auto-memory updated

Branch ready to merge to `main` (or stay on the feat branch — user choice).

Three named follow-up tasks queued:
1. **§5.8 TikZ upgrade** — before 9 June 2026 abstract deadline
2. **Full ARIS R1+R2+R3 on v0.6→v0.8 diff** — open-ended, recommended hygiene
3. **Camera-ready restoration pass** — between 8 July 2026 notification and 20 July 2026 camera-ready: flip `\anonymoustrue` → `\anonymousfalse`, restore every `% RESTORE:` cookie, restore "Adaptive Trader v2", populate real author block, populate Acknowledgements section.
