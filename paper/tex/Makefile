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
	@grep -qE '^\\documentclass\[anon\]\{nesy2026\}' $(TEX) \
	  || (echo "ABORT: $(TEX) is not anonymous — submission requires \\documentclass[anon]{nesy2026}"; exit 1)

check-real:
	@grep -qE '^\\documentclass\[final\]\{nesy2026\}' $(TEX) \
	  || (echo "ABORT: $(TEX) is anonymous — camera-ready requires \\documentclass[final]{nesy2026}"; exit 1)

clean:
	rm -f *.aux *.bbl *.blg *.log *.out *.toc *.fls *.fdb_latexmk *.synctex.gz
