# cv-profiler

Personal CLI (`cv` / `cv-profiler`) that generates tailored, honest, ATS-friendly CVs from
a source-of-truth claims repository + job descriptions.

## Project layout

```text
cv-profiler/
├── README.md               # this file
├── LICENSE                 # MIT License
├── pyproject.toml          # package & console script definitions (cv, cv-profiler)
├── cv_profiler.py          # CLI entry + deterministic matching/validation/build
├── llm_grill.py            # structured LLM Questioner/Synthesizer + permissions boundary
├── aliases.yaml            # PT-BR -> canonical slug alias map (token-boundary matcher)
├── profile.example.yaml    # template for personal identity & defaults (copy to profile.yaml)
├── claims/
│   └── example.yaml        # sample claims store (copy to your own gitignored claims file)
├── jobs/                   # ingested JDs (<slug>/jd.pdf, jd.txt, job.yaml) — gitignored
├── sessions/               # append-only interview audit logs (<date>_<slug>.yaml) — gitignored
├── templates/
│   └── cv.tex              # single-column ATS-friendly XeLaTeX CV template
├── tests/                  # unit & integration tests (Waves 1-3 + CLI)
└── out/                    # generated CVs (<slug>.pdf) — gitignored
```

## Setup

Prerequisites:
- Python 3.10+ (`pydantic`, `PyYAML`)
- `tectonic` (XeLaTeX engine)
- `poppler` (`pdftotext`, `pdfinfo`)
- `Liberation Sans` font (or adjust `\setmainfont` in `templates/cv.tex`)

```bash
# 1. Create your local profile and claims store (both are gitignored)
cp profile.example.yaml profile.yaml
cp claims/example.yaml claims/my-claims.yaml
# Edit profile.yaml (name, contact, defaults, and set claims_file: claims/my-claims.yaml)

# 2. Install editable CLI into your virtualenv
pip install -e .
```

## Commands

```bash
# List verified claims & validate schema/honesty rules
cv claims
cv claims --all

# List ingested jobs and any loose PDFs dropped in jobs/
cv jobs

# 1. Ingest a browser-printed PDF or text JD into jobs/<slug>/ (--slug optional)
cv ingest /path/to/jd.pdf --slug 2026-11_empresa_suporte --company Empresa --title "Suporte Técnico"

# 2. Check deterministic gaps (and optionally scan for unmapped JD terms via LLM, read-only)
cv gap 2026-11_empresa_suporte
cv gap 2026-11_empresa_suporte --discover-aliases

# 3. Run gap-driven grill interview (auto-uses LLM if API key set, or --mode manual|llm)
cv grill 2026-11_empresa_suporte
cv grill 2026-11_empresa_suporte --skill windows --mode llm

# 4. Deterministically build tailored CV + run ATS gate -> out/<slug>.pdf
cv build 2026-11_empresa_suporte
```

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
