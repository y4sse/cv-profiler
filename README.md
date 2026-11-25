# cv-profiler

Personal CLI (`cv` / `cv-profiler`) that generates tailored, honest, ATS-friendly 1-page CVs from
a structured `evidence/` directory + job descriptions.

## Project layout

```text
cv-profiler/
├── README.md                 # this file
├── LICENSE                   # MIT License
├── pyproject.toml            # package & console script definitions (cv, cv-profiler)
├── cv_profiler.py            # CLI entry + evidence validation, auto-ingest, matching & build
├── llm_grill.py              # structured LLM Questioner/Synthesizer + deterministic honesty gate
├── aliases.yaml              # PT-BR -> canonical skill slug map (token-boundary matcher)
├── profile.example.yaml      # template for Dados Pessoais + Resumo (copy to profile.yaml)
├── evidence.example/         # sample structured evidence store (copy to evidence/)
├── evidence/                 # your real evidence store (gitignored)
│   ├── experiences/*.yaml    # Experiência Profissional (verbatim bullets tagged with skills)
│   ├── education/*.yaml      # Formação Acadêmica
│   ├── projects/*.yaml       # Projetos (included automatically only when relevant)
│   ├── certificates/*.yaml   # Certificados (referencing URLs or .pdf files in artifacts/)
│   ├── artifacts/            # Raw proofs (certificate .pdf files, POPs, etc.)
│   ├── unanchored.yaml       # Optional temporary/migrated skills awaiting an experience anchor
│   └── absent.yaml           # Skills verified as absent (prevents repeated grill prompts)
├── jobs/                     # Drop raw .pdf or .txt JDs here -> auto-ingested!
│   └── ingested/<slug>/      # Auto-created folder per ingested job (jd.pdf, jd.txt, job.yaml)
├── sessions/                 # Append-only grill interview audit logs — gitignored
├── templates/
│   └── cv.tex                # Single-column, table-free ATS XeLaTeX template
├── tests/                    # Unit & integration tests
└── out/                      # Generated CVs (<slug>.pdf) — gitignored
```

## What is a `slug`?

Every job you drop into `jobs/` gets a short, URL-safe identifier called a **slug** — for example
`2026-11_lanlink_suporte-2n` or `2026-11_orizon-analista-de-suporte-ti`.

The slug is the single key linking everything for that position:
- `jobs/ingested/<slug>/` — the stored `jd.pdf`, extracted `jd.txt`, and `job.yaml` metadata/overrides
- `sessions/<date>_<slug>.yaml` — any interview logs run for that job
- `out/<slug>.pdf` — the compiled CV tailored for that job

**You don't need to create or type full slugs manually:**
- Dropping any `.pdf` or `.txt` into `jobs/` and running `cv status`, `cv gap`, `cv build`, or `cv run`
  **automatically ingests** the file into `jobs/ingested/<slug>/` with a cleaned slug.
- Commands accept **unambiguous partial slugs**: e.g., `cv build lanlink` automatically resolves
  `2026-11_lanlink_suporte-2n`.

## Setup

Prerequisites:
- Python 3.10+ (`pydantic`, `PyYAML`)
- `tectonic` (XeLaTeX engine)
- `poppler` (`pdftotext`, `pdfinfo`)
- `Liberation Sans` font (or adjust `\setmainfont` in `templates/cv.tex`)

```bash
# 1. Create your local profile and evidence store (both are gitignored)
cp profile.example.yaml profile.yaml
cp -r evidence.example/* evidence/
# Edit profile.yaml (Dados Pessoais, role, resumo) and your YAML files under evidence/

# 2. Install editable CLI into your virtualenv
pip install -e .
```

## Everyday workflow

```bash
# 0. Drop any browser-printed JD (.pdf or .txt) directly into jobs/

# 1. Auto-ingest new JDs, validate evidence/, and see slugs + warnings
cv status

# 2. All-in-one shortcut for a job (gap check -> optional grill -> build CV)
cv run lanlink

# Or run individual steps anytime:
cv gap lanlink                         # covered skills vs. gaps (--discover-aliases optional)
cv grill lanlink                       # gap interview -> attaches verified bullet into evidence/
cv build lanlink                       # compiles tailored CV -> out/<slug>.pdf + ATS checks
```

Useful build flags:
- `cv build lanlink --projects` / `--no-projects` — force include or exclude `Projetos` (default: `auto`, included only when a project covers JD skills not already covered by `Experiência`)
- `cv build lanlink --allow-2-pages` — permit a 2-page CV (default ATS limit is 1 page)
- `cv ingest /external/path/vaga.pdf --slug custom-slug --company Acme --title "Suporte"` — manual ingest when importing from outside `jobs/` or setting custom metadata upfront

## CV layout & honesty design

Generated CVs follow a single-column, **table-free** ATS-friendly order:
1. **Dados Pessoais**
2. **Resumo Profissional**
3. **Experiência Profissional** — bullets come verbatim from `evidence/experiences/*.yaml` (prioritized by JD match without rewriting your history)
4. **Formação Acadêmica**
5. **Projetos** *(included automatically only when needed)*
6. **Certificados**
7. **Habilidades** — compact grouped keywords derived from `evidence/`, **without** level labels (`Noções`/`Prática`/`Sólido` stay internal honesty guardrails and descriptive context in `Resumo`/`Experiência`)

## LLM configuration (optional, for `grill --mode llm` / `gap --discover-aliases`)

Uses Python stdlib HTTP (`urllib.request`, no extra packages). Set any one of:
- `OPENAI_API_KEY` (or `CV_PROFILER_LLM_BASE_URL` for OpenAI-compatible / Ollama endpoints)
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)

Optional overrides: `CV_PROFILER_LLM_PROVIDER`, `CV_PROFILER_LLM_MODEL`, or CLI flags `--provider`, `--model`, `--base-url`.

## Tests

```bash
python -m unittest discover -s tests -v
```
