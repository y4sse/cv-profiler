#!/usr/bin/env python3
"""cv-profiler: generate tailored CVs from honest claims + job descriptions."""

import argparse
from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Literal, Protocol
import unicodedata

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

VERSION = "1.0.0"
TEMPLATE = BASE_DIR / "templates" / "cv.tex"
ENGINE = (
    os.environ.get("CV_PROFILER_ENGINE")
    or shutil.which("tectonic")
    or str(Path.home() / ".local" / "bin" / "tectonic")
)
PROFILE_PATH = BASE_DIR / "profile.yaml"
PROFILE_EXAMPLE_PATH = BASE_DIR / "profile.example.yaml"
CLAIMS_EXAMPLE = BASE_DIR / "claims" / "example.yaml"
ALIASES = BASE_DIR / "aliases.yaml"
JOBS_DIR = BASE_DIR / "jobs"
SESSIONS_DIR = BASE_DIR / "sessions"
OUT_DIR = BASE_DIR / "out"
WORK = Path(os.environ.get("CV_PROFILER_WORK") or (Path(tempfile.gettempdir()) / "cv-profiler-build"))

DEFAULT_NAME = "Seu Nome Completo"
DEFAULT_LOCATION = "Cidade – UF"
DEFAULT_EMAIL = "voce@exemplo.com"
DEFAULT_PHONE = "(00) 00000-0000"
DEFAULT_LINKEDIN = "linkedin.com/in/seu-perfil"
DEFAULT_GITHUB = "github.com/seu-usuario"
DEFAULT_ROLE = "Engenharia de Software | Suporte Técnico e TI"
DEFAULT_RESUMO = (
    "Graduando em Engenharia de Software com experiência prática em suporte técnico, "
    "resolução de incidentes e automação de rotinas."
)
DEFAULT_FORMACAO = "Bacharelado em Engenharia de Software — Universidade Exemplo."
DEFAULT_EXPERIENCIA = "Empresa Exemplo — Estágio em Suporte Técnico de TI."

SKILL_LABELS: dict[str, str] = {
    "service_desk": "Service Desk e Suporte Técnico",
    "qa_testes": "Qualidade de Software (QA e Testes)",
    "microsoft_365": "Microsoft 365 e Google Workspace",
    "excel_planilhas": "Excel, Planilhas e Automação de Fluxos",
    "active_directory": "Active Directory",
    "redes": "Redes e Infraestrutura de TI",
    "monitoramento": "Monitoramento de Sistemas e Redes",
    "itil": "ITIL (Gestão de Serviços de TI)",
    "python": "Python e Automação",
    "documentacao_treinamento": "Documentação (POPs), Treinamento e Segurança",
    "rotinas_administrativas_financeiras": "Rotinas Administrativas, Contratos e Orçamento",
    "engenharia_software": "Engenharia de Software",
    "sap": "ERP SAP (PM / FICO / MM / QM)",
    "azure": "Microsoft Azure",
    "ingles": "Inglês (EF SET C1 Advanced)",
    "financas_fiscal": "Análise Financeira e Escrita Fiscal",
    "windows": "Windows e Windows Server",
    "linux": "Linux",
    "sql": "SQL e Bancos de Dados",
}
LEVEL_LABELS: dict[str, str] = {
    "nocoes": "Noções",
    "pratica": "Prática",
    "solido": "Sólido",
}
CATEGORY_LABELS: dict[str, str] = {
    "profissional": "Profissional",
    "academico": "Acadêmico",
    "voluntariado": "Voluntariado",
}

CATEGORIES = ("profissional", "academico", "voluntariado")
LEVELS = ("nocoes", "pratica", "solido")
LEVEL_RANK = {level: idx for idx, level in enumerate(LEVELS)}
STATUSES = ("verified", "absent")
EVIDENCE_KINDS = (
    "estudo",
    "projeto",
    "producao_supervisionada",
    "producao_autonoma",
)
MAX_LEVEL_BY_EVIDENCE_KIND: dict[str, str] = {
    "estudo": "nocoes",
    "projeto": "pratica",
    "producao_supervisionada": "pratica",
    "producao_autonoma": "solido",
}

MAX_BULLET_LEN = 140
MIN_CONTEXT_LEN = 5
MIN_CONCRETE_LEN = 15

SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

BANNED_BUZZWORDS = frozenset(
    {
        "proativo",
        "proativa",
        "dinamico",
        "dinamica",
        "apaixonado",
        "apaixonada",
        "visao_360",
        "fora_da_caixa",
        "rockstar",
        "ninja",
        "guru",
        "sinergia",
        "disruptivo",
        "disruptiva",
        "excelencia_operacional",
    }
)
SOLIDO_ONLY_TERMS = frozenset(
    {
        "especialista",
        "arquiteto",
        "arquiteta",
        "senior",
        "lideranca_tecnica",
    }
)

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in _LATEX_ESCAPES))
_METRIC_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?")


def norm(text: str) -> str:
    s = (text or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def norm_slug(text: str) -> str:
    s = (text or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-_")


def contains_token(haystack_norm: str, needle_norm: str) -> bool:
    if not haystack_norm or not needle_norm:
        return False
    return f"_{needle_norm}_" in f"_{haystack_norm}_"


def escape_latex(text: str) -> str:
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPES[m.group(0)], text or "")


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "estudo",
        "projeto",
        "producao_supervisionada",
        "producao_autonoma",
    ]
    context: str = Field(..., min_length=MIN_CONTEXT_LEN)
    concrete: str = Field(..., min_length=MIN_CONCRETE_LEN)
    artifact: str | None = None

    @field_validator("context", "concrete")
    @classmethod
    def _strip_required(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        min_len = MIN_CONTEXT_LEN if info.field_name == "context" else MIN_CONCRETE_LEN
        if len(cleaned) < min_len:
            raise ValueError(f"{info.field_name} must be at least {min_len} non-whitespace characters")
        return cleaned

    @field_validator("artifact")
    @classmethod
    def _clean_artifact(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    status: Literal["verified", "absent"] = "verified"
    category: Literal["profissional", "academico", "voluntariado"]
    level: Literal["nocoes", "pratica", "solido"] | None = None
    evidence: Evidence | None = None
    tags: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    source_session: str | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: list[str]) -> list[str]:
        cleaned: list[str] = []
        for t in tags:
            slug = norm(t)
            if not slug:
                raise ValueError(f"invalid empty tag: {t!r}")
            if slug not in cleaned:
                cleaned.append(slug)
        return cleaned

    @field_validator("bullets")
    @classmethod
    def _clean_bullets(cls, bullets: list[str]) -> list[str]:
        cleaned: list[str] = []
        for b in bullets:
            s = b.strip()
            if not s:
                raise ValueError("bullet cannot be empty")
            if len(s) > MAX_BULLET_LEN:
                raise ValueError(f"bullet exceeds {MAX_BULLET_LEN} chars ({len(s)})")
            cleaned.append(s)
        return cleaned

    @model_validator(mode="after")
    def _check_status_and_level_cap(self) -> "Claim":
        if self.status == "absent":
            if self.level is not None:
                raise ValueError("absent claim must have level=None")
            if self.evidence is not None:
                raise ValueError("absent claim must have evidence=None")
            if self.bullets:
                raise ValueError("absent claim must have no bullets")
            return self

        if self.level is None:
            raise ValueError("verified claim requires level")
        if self.evidence is None:
            raise ValueError("verified claim requires structured evidence")
        if not self.tags:
            raise ValueError("verified claim requires at least one tag")

        max_level = MAX_LEVEL_BY_EVIDENCE_KIND[self.evidence.kind]
        if LEVEL_RANK[self.level] > LEVEL_RANK[max_level]:
            raise ValueError(
                f"level '{self.level}' exceeds max allowed '{max_level}' "
                f"for evidence.kind '{self.evidence.kind}'"
            )
        return self


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = DEFAULT_NAME
    location: str = DEFAULT_LOCATION
    email: str = DEFAULT_EMAIL
    phone: str = DEFAULT_PHONE
    linkedin: str = DEFAULT_LINKEDIN
    github: str = DEFAULT_GITHUB
    claims_file: str = "claims/example.yaml"
    role: str = DEFAULT_ROLE
    resumo: str = DEFAULT_RESUMO
    formacao: str = DEFAULT_FORMACAO
    experiencia: str = DEFAULT_EXPERIENCIA


def load_profile(path: Path | None = None) -> Profile:
    target = path
    if target is None:
        if PROFILE_PATH.exists():
            target = PROFILE_PATH
        elif PROFILE_EXAMPLE_PATH.exists():
            target = PROFILE_EXAMPLE_PATH
    if target is None or not target.exists():
        return Profile()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(raw)


def resolve_claims_path(profile: Profile | None = None) -> Path:
    prof = profile or load_profile()
    p = Path(prof.claims_file)
    resolved = p if p.is_absolute() else (BASE_DIR / p)
    if resolved.exists():
        return resolved
    return CLAIMS_EXAMPLE


CLAIMS = resolve_claims_path()


class JobMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    title: str | None = None
    company: str | None = None
    url: str | None = None
    source_file: str | None = None
    ingested_at: str | None = None
    role: str | None = None
    resumo: str | None = None
    formacao: str | None = None
    experiencia: str | None = None
    category_filter: list[Literal["profissional", "academico", "voluntariado"]] | None = None
    allow_2_pages: bool = False


class GapReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_slug: str
    covered: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    known_absent: list[str] = Field(default_factory=list)
    actionable_gaps: list[str] = Field(default_factory=list)


class QAItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(..., min_length=1)
    a: str = Field(..., min_length=1)


class ClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "upgrade", "enrich", "mark_absent"]
    claim: Claim


class SessionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(..., pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    existing_level: Literal["nocoes", "pratica", "solido"] | None = None
    qa: list[QAItem] = Field(default_factory=list)
    proposal: ClaimProposal | None = None
    validator_result: ValidationResult | None = None
    user_decision: Literal["accepted", "edited", "rejected", "absent", "skipped"]


class InterviewSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    job_slug: str = Field(..., min_length=1)
    created_at: str = Field(..., min_length=1)
    backend: str = "manual"
    gaps_input: list[str] = Field(default_factory=list)
    items: list[SessionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic Validation
# ---------------------------------------------------------------------------


def _extract_metrics(text: str) -> set[str]:
    return {m.group(0) for m in _METRIC_RE.finditer(text or "")}


def validate_claim(
    raw: Claim | dict[str, Any],
    *,
    qa_answers: list[str] | None = None,
    existing_claim: Claim | None = None,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        claim = raw if isinstance(raw, Claim) else Claim.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "claim"
            errors.append(f"schema:{loc}: {err['msg']}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    if claim.status == "absent":
        return ValidationResult(ok=True, errors=[], warnings=[])

    assert claim.evidence is not None and claim.level is not None

    # Evidence specificity: must not just repeat id/tags or context, and needs substance.
    concrete_norm = norm(claim.evidence.concrete)
    context_norm = norm(claim.evidence.context)
    id_and_tags = {norm(claim.id)} | {norm(t) for t in claim.tags}
    concrete_tokens = [tok for tok in concrete_norm.split("_") if len(tok) > 1]

    if concrete_norm in id_and_tags or concrete_norm == context_norm:
        errors.append("evidence: concrete repeats skill id/tag or context without detail")
    if len(set(concrete_tokens)) < 3:
        errors.append("evidence: concrete must describe a tangible action (>= 3 distinct terms)")

    # Buzzword and level-gated wording checks across bullets and evidence.
    texts_to_check = [("evidence.concrete", claim.evidence.concrete)] + [
        (f"bullets[{i}]", b) for i, b in enumerate(claim.bullets)
    ]
    for field_label, text in texts_to_check:
        text_norm = norm(text)
        for bw in sorted(BANNED_BUZZWORDS):
            if contains_token(text_norm, bw):
                errors.append(f"buzzword:{field_label}: banned term '{bw}'")
        if claim.level != "solido":
            for term in sorted(SOLIDO_ONLY_TERMS):
                if contains_token(text_norm, term):
                    errors.append(
                        f"oversell:{field_label}: '{term}' requires level 'solido' (got '{claim.level}')"
                    )

    # Upgrade guard: raising level requires updated evidence.
    if existing_claim is not None and existing_claim.status == "verified":
        assert existing_claim.level is not None and existing_claim.evidence is not None
        if LEVEL_RANK[claim.level] > LEVEL_RANK[existing_claim.level]:
            if claim.evidence == existing_claim.evidence:
                errors.append(
                    f"upgrade: cannot raise level from '{existing_claim.level}' to "
                    f"'{claim.level}' without new evidence"
                )

    # Metric grounding: when Q&A answers exist, every numeric metric in bullets/evidence
    # must trace back to user answers, claim id/tags, or prior verified claim.
    if qa_answers is not None:
        allowed_metrics: set[str] = set()
        for ans in qa_answers:
            allowed_metrics |= _extract_metrics(ans)
        allowed_metrics |= _extract_metrics(claim.id)
        for tag in claim.tags:
            allowed_metrics |= _extract_metrics(tag)
        if existing_claim is not None and existing_claim.status == "verified":
            if existing_claim.evidence is not None:
                allowed_metrics |= _extract_metrics(existing_claim.evidence.context)
                allowed_metrics |= _extract_metrics(existing_claim.evidence.concrete)
            for b in existing_claim.bullets:
                allowed_metrics |= _extract_metrics(b)

        for field_label, text in texts_to_check:
            for metric in sorted(_extract_metrics(text)):
                if metric not in allowed_metrics:
                    errors.append(
                        f"ungrounded_metric:{field_label}: '{metric}' not found in user answers"
                    )

    if not claim.bullets:
        warnings.append("bullets: verified claim has no CV bullets yet")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# YAML Persistence & Storage
# ---------------------------------------------------------------------------


def write_yaml_atomic(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return path


def load_claims(path: Path = CLAIMS) -> list[Claim]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("claims")
    if raw is None:
        raw = data.get("skills") or []
    claims: list[Claim] = []
    seen_ids: set[str] = set()
    for r in raw:
        claim = Claim.model_validate(r)
        if claim.id in seen_ids:
            raise ValueError(f"duplicate claim id: '{claim.id}'")
        seen_ids.add(claim.id)
        claims.append(claim)
    return claims


def save_claims(claims: list[Claim], path: Path = CLAIMS) -> Path:
    seen_ids: set[str] = set()
    serialized: list[dict[str, Any]] = []
    for c in claims:
        if c.id in seen_ids:
            raise ValueError(f"duplicate claim id: '{c.id}'")
        seen_ids.add(c.id)
        serialized.append(c.model_dump(mode="python"))
    return write_yaml_atomic(path, {"claims": serialized})


def upsert_claim(claims: list[Claim], updated: Claim) -> list[Claim]:
    result = list(claims)
    for idx, existing in enumerate(result):
        if existing.id == updated.id:
            result[idx] = updated
            return result
    result.append(updated)
    return result


def save_session(session: InterviewSession, sessions_dir: Path = SESSIONS_DIR) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    base_name = session.session_id
    target = sessions_dir / f"{base_name}.yaml"
    counter = 2
    while target.exists():
        target = sessions_dir / f"{base_name}_{counter}.yaml"
        counter += 1
    if target.stem != session.session_id:
        session = session.model_copy(update={"session_id": target.stem})
    return write_yaml_atomic(target, session.model_dump(mode="python"))


def load_aliases(path: Path = ALIASES) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aliases: dict[str, list[str]] = {}
    for k, v in (data or {}).items():
        key = norm(str(k))
        vals = [norm(str(x)) for x in (v or []) if str(x).strip()]
        aliases[key] = vals
    return aliases


# ---------------------------------------------------------------------------
# Jobs Storage & Ingestion
# ---------------------------------------------------------------------------


def extract_pdf_text(pdf_path: Path, txt_path: Path) -> str:
    res = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if res.returncode != 0:
        raise RuntimeError(f"pdftotext failed on {pdf_path}: {res.stderr.strip()}")
    text = txt_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"extracted text from {pdf_path} is empty (scanned image PDF?)")
    return text


def ingest_job(
    source_path: Path,
    slug: str,
    *,
    title: str | None = None,
    company: str | None = None,
    url: str | None = None,
    jobs_dir: Path = JOBS_DIR,
) -> tuple[Path, JobMeta, str]:
    clean_slug = norm_slug(slug)
    if not clean_slug or not SLUG_RE.match(clean_slug):
        raise ValueError(f"invalid job slug: {slug!r}")
    if not source_path.exists():
        raise FileNotFoundError(f"job source file not found: {source_path}")

    job_dir = jobs_dir / clean_slug
    job_dir.mkdir(parents=True, exist_ok=True)
    txt_dst = job_dir / "jd.txt"

    if source_path.suffix.lower() == ".pdf":
        pdf_dst = job_dir / "jd.pdf"
        if source_path.resolve() != pdf_dst.resolve():
            shutil.copy2(source_path, pdf_dst)
        text = extract_pdf_text(pdf_dst, txt_dst)
    else:
        text = source_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"job text file is empty: {source_path}")
        txt_dst.write_text(text, encoding="utf-8")

    meta_path = job_dir / "job.yaml"
    existing_meta: dict[str, Any] = {}
    if meta_path.exists():
        existing_meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}

    meta = JobMeta.model_validate(
        {
            **existing_meta,
            "slug": clean_slug,
            "title": title if title is not None else existing_meta.get("title"),
            "company": company if company is not None else existing_meta.get("company"),
            "url": url if url is not None else existing_meta.get("url"),
            "source_file": source_path.name,
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    write_yaml_atomic(meta_path, meta.model_dump(mode="python"))
    return job_dir, meta, text


def load_job(job_ref: str, *, jobs_dir: Path = JOBS_DIR) -> tuple[str, str, JobMeta | None]:
    """Resolve a job slug in jobs/ or a direct file/dir path -> (slug, jd_text, meta)."""
    candidate_dir = jobs_dir / job_ref
    direct_path = Path(job_ref)

    job_dir: Path | None = None
    if candidate_dir.is_dir():
        job_dir = candidate_dir
    elif direct_path.is_dir():
        job_dir = direct_path

    if job_dir is not None:
        pdf_path = job_dir / "jd.pdf"
        txt_path = job_dir / "jd.txt"
        meta_path = job_dir / "job.yaml"

        if pdf_path.exists() and (
            not txt_path.exists() or pdf_path.stat().st_mtime > txt_path.stat().st_mtime
        ):
            jd_text = extract_pdf_text(pdf_path, txt_path)
        elif txt_path.exists():
            jd_text = txt_path.read_text(encoding="utf-8")
        else:
            raise FileNotFoundError(f"neither jd.txt nor jd.pdf found in {job_dir}")

        meta: JobMeta | None = None
        if meta_path.exists():
            raw_meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            meta = JobMeta.model_validate(raw_meta)
        slug = meta.slug if meta else norm_slug(job_dir.name)
        return slug, jd_text, meta

    if direct_path.is_file():
        slug = norm_slug(direct_path.stem)
        if direct_path.suffix.lower() == ".pdf":
            with tempfile.TemporaryDirectory() as td:
                jd_text = extract_pdf_text(direct_path, Path(td) / "jd.txt")
        else:
            jd_text = direct_path.read_text(encoding="utf-8")
        return slug, jd_text, None

    raise FileNotFoundError(f"job not found as slug in {jobs_dir} or path: {job_ref}")


# ---------------------------------------------------------------------------
# Deterministic Matching & Gap Analysis
# ---------------------------------------------------------------------------


def claim_covers(claim: Claim, jd_norm: str, aliases: dict[str, list[str]]) -> bool:
    if claim.status != "verified":
        return False
    keys = [norm(claim.id)] + [norm(t) for t in claim.tags]
    for key in keys:
        if not key:
            continue
        if contains_token(jd_norm, key):
            return True
        for a in aliases.get(key, []):
            if contains_token(jd_norm, a):
                return True
    return False


def select_claims(
    jd_text: str,
    claims: list[Claim],
    aliases: dict[str, list[str]],
    *,
    categories: list[str] | None = None,
) -> list[Claim]:
    jd_norm = norm(jd_text)
    allowed_cats = set(categories) if categories else None
    selected: list[Claim] = []
    for c in claims:
        if allowed_cats is not None and c.category not in allowed_cats:
            continue
        if claim_covers(c, jd_norm, aliases):
            selected.append(c)
    return selected


def compute_gaps(jd_text: str, claims: list[Claim], aliases: dict[str, list[str]]) -> list[str]:
    jd_norm = norm(jd_text)
    selected = select_claims(jd_text, claims, aliases)
    mentioned: set[str] = set()
    for canon, syns in aliases.items():
        for s in [canon] + syns:
            if contains_token(jd_norm, s):
                mentioned.add(canon)
                break
    covered: set[str] = set()
    for c in selected:
        covered.add(norm(c.id))
        for t in c.tags:
            covered.add(norm(t))
    return sorted(t for t in mentioned if t not in covered)


def compute_gap_report(
    jd_text: str,
    claims: list[Claim],
    aliases: dict[str, list[str]],
    *,
    job_slug: str = "ad-hoc",
) -> GapReport:
    selected = select_claims(jd_text, claims, aliases)
    gaps = compute_gaps(jd_text, claims, aliases)
    absent_ids = {c.id for c in claims if c.status == "absent"}
    known_absent = [g for g in gaps if g in absent_ids]
    actionable = [g for g in gaps if g not in absent_ids]
    return GapReport(
        job_slug=job_slug,
        covered=[c.id for c in selected],
        gaps=gaps,
        known_absent=known_absent,
        actionable_gaps=actionable,
    )


def extract_jd_snippets(
    jd_text: str,
    skill_id: str,
    aliases: dict[str, list[str]],
    *,
    max_lines: int = 3,
) -> list[str]:
    key = norm(skill_id)
    terms = [key] + aliases.get(key, [])
    hits: list[str] = []
    for line in jd_text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        line_norm = norm(cleaned)
        if any(contains_token(line_norm, t) for t in terms if t):
            hits.append(cleaned)
            if len(hits) >= max_lines:
                break
    return hits


# ---------------------------------------------------------------------------
# Grill Interview Loop (Wave 2 Manual Backend; Wave 3 Plugs LLM Backend Here)
# ---------------------------------------------------------------------------


class GrillBackend(Protocol):
    def generate_questions(
        self,
        skill_id: str,
        jd_snippets: list[str],
        existing_claim: Claim | None,
    ) -> list[str]: ...

    def build_proposal(
        self,
        skill_id: str,
        existing_claim: Claim | None,
        qa: list[QAItem],
        input_fn: Callable[[str], str],
        output_fn: Callable[[str], None],
    ) -> ClaimProposal | None: ...


class ManualGrillBackend:
    """Deterministic local grill backend (Wave 2); Wave 3 adds LLMGrillBackend."""

    name: str = "manual"
    last_validation: ValidationResult | None = None

    def generate_questions(
        self,
        skill_id: str,
        jd_snippets: list[str],
        existing_claim: Claim | None,
    ) -> list[str]:
        if existing_claim and existing_claim.status == "verified":
            return [
                f"Qual experiencia nova ou contexto adicional voce teve com '{skill_id}'?",
                f"Qual tarefa concreta ou entrega voce executou em '{skill_id}'?",
                f"O que voce NAO fez / qual o limite real do seu uso de '{skill_id}'?",
            ]
        return [
            f"Onde e quando voce usou '{skill_id}' na pratica (empresa, faculdade, projeto)?",
            f"Qual tarefa concreta voce executou sozinho ou supervisionado em '{skill_id}'?",
            f"Qual o limite real da sua experiencia em '{skill_id}' (o que voce NAO faz ainda)?",
        ]

    def build_proposal(
        self,
        skill_id: str,
        existing_claim: Claim | None,
        qa: list[QAItem],
        input_fn: Callable[[str], str],
        output_fn: Callable[[str], None],
    ) -> ClaimProposal | None:
        default_cat = existing_claim.category if existing_claim else "profissional"
        output_fn(f"  Categoria ({'/'.join(CATEGORIES)}) [{default_cat}]:")
        category = (input_fn("  > ").strip() or default_cat)

        output_fn(f"  Tipo de evidencia ({'/'.join(EVIDENCE_KINDS)}) [producao_supervisionada]:")
        kind = (input_fn("  > ").strip() or "producao_supervisionada")

        max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND.get(kind, "pratica")
        output_fn(f"  Nivel ({'/'.join(LEVELS)}; max para '{kind}' = '{max_lvl}') [{max_lvl}]:")
        level = (input_fn("  > ").strip() or max_lvl)

        default_ctx = qa[0].a if qa else ""
        output_fn(f"  Contexto (onde/quando) [{default_ctx}]:")
        context = (input_fn("  > ").strip() or default_ctx)

        default_conc = qa[1].a if len(qa) > 1 else ""
        output_fn(f"  Evidencia concreta (o que fez) [{default_conc}]:")
        concrete = (input_fn("  > ").strip() or default_conc)

        output_fn("  Artefato (URL/repo/cert opcional, Enter para vazio):")
        artifact = input_fn("  > ").strip() or None

        output_fn("  Bullet para CV (1 linha objetiva, <= 140 chars):")
        bullet = input_fn("  > ").strip()
        bullets = [bullet] if bullet else []

        action: Literal["create", "upgrade", "enrich", "mark_absent"] = "create"
        if existing_claim is not None and existing_claim.status == "verified":
            assert existing_claim.level is not None
            if level in LEVEL_RANK and LEVEL_RANK[level] > LEVEL_RANK[existing_claim.level]:
                action = "upgrade"
            else:
                action = "enrich"

        raw_claim = {
            "id": skill_id,
            "status": "verified",
            "category": category,
            "level": level,
            "evidence": {
                "kind": kind,
                "context": context,
                "concrete": concrete,
                "artifact": artifact,
            },
            "tags": sorted(set((existing_claim.tags if existing_claim else []) + [skill_id])),
            "bullets": bullets,
        }
        res = validate_claim(
            raw_claim,
            qa_answers=[item.a for item in qa],
            existing_claim=existing_claim,
        )
        self.last_validation = res
        if not res.ok:
            for err in res.errors:
                output_fn(f"  [REJEITADO] {err}")
            return None

        claim = Claim.model_validate(raw_claim)
        return ClaimProposal(action=action, claim=claim)


def _interactive_edit_proposal(
    proposal: ClaimProposal,
    *,
    qa_answers: list[str],
    existing_claim: Claim | None,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> tuple[ClaimProposal | None, ValidationResult]:
    """Allow user to edit a validated proposal; re-validates deterministically before accepting."""
    c = proposal.claim
    if c.status != "verified" or c.evidence is None or c.level is None:
        return None, ValidationResult(ok=False, errors=["cannot edit absent claim"])

    max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND[c.evidence.kind]
    output_fn(f"  Novo nivel ({'/'.join(LEVELS)}; max='{max_lvl}') [{c.level}]:")
    new_level = input_fn("  > ").strip() or c.level

    output_fn(f"  Contexto [{c.evidence.context}]:")
    new_ctx = input_fn("  > ").strip() or c.evidence.context

    output_fn(f"  Evidencia concreta [{c.evidence.concrete}]:")
    new_conc = input_fn("  > ").strip() or c.evidence.concrete

    cur_bullet = c.bullets[0] if c.bullets else ""
    output_fn(f"  Bullet principal [{cur_bullet}]:")
    new_bullet = input_fn("  > ").strip() or cur_bullet
    new_bullets = [new_bullet] + c.bullets[1:] if new_bullet else []

    raw_edited = {
        "id": c.id,
        "status": "verified",
        "category": c.category,
        "level": new_level,
        "evidence": {
            "kind": c.evidence.kind,
            "context": new_ctx,
            "concrete": new_conc,
            "artifact": c.evidence.artifact,
        },
        "tags": list(c.tags),
        "bullets": new_bullets,
    }
    res = validate_claim(
        raw_edited,
        qa_answers=qa_answers,
        existing_claim=existing_claim,
    )
    if not res.ok:
        for err in res.errors:
            output_fn(f"  [EDICAO REJEITADA] {err}")
        return None, res

    edited_claim = Claim.model_validate(raw_edited)
    action = proposal.action
    if existing_claim is not None and existing_claim.status == "verified" and existing_claim.level:
        assert edited_claim.level is not None
        action = (
            "upgrade"
            if LEVEL_RANK[edited_claim.level] > LEVEL_RANK[existing_claim.level]
            else "enrich"
        )
    return ClaimProposal(action=action, claim=edited_claim), res


def _downgrade_proposal(
    proposal: ClaimProposal,
    *,
    qa_answers: list[str],
    existing_claim: Claim | None,
) -> tuple[ClaimProposal | None, ValidationResult]:
    c = proposal.claim
    if c.status != "verified" or c.level is None:
        return None, ValidationResult(ok=False, errors=["cannot downgrade absent claim"])
    rank = LEVEL_RANK[c.level]
    if rank == 0:
        return None, ValidationResult(ok=False, errors=["claim is already at lowest level 'nocoes'"])
    lower_level = LEVELS[rank - 1]
    downgraded = c.model_copy(update={"level": lower_level})
    res = validate_claim(
        downgraded,
        qa_answers=qa_answers,
        existing_claim=existing_claim,
    )
    if not res.ok:
        return None, res
    action = proposal.action
    if existing_claim is not None and existing_claim.status == "verified" and existing_claim.level:
        action = (
            "upgrade"
            if LEVEL_RANK[lower_level] > LEVEL_RANK[existing_claim.level]
            else "enrich"
        )
    return ClaimProposal(action=action, claim=downgraded), res


def run_grill_session(
    job_ref: str,
    *,
    skill_filter: str | None = None,
    recheck: bool = False,
    claims_path: Path = CLAIMS,
    aliases_path: Path = ALIASES,
    jobs_dir: Path = JOBS_DIR,
    sessions_dir: Path = SESSIONS_DIR,
    backend: GrillBackend | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[InterviewSession, Path]:
    slug, jd_text, _ = load_job(job_ref, jobs_dir=jobs_dir)
    claims = load_claims(claims_path)
    aliases = load_aliases(aliases_path)
    report = compute_gap_report(jd_text, claims, aliases, job_slug=slug)

    if skill_filter:
        skill_slug = norm(skill_filter)
        if not ID_RE.match(skill_slug):
            raise ValueError(f"invalid skill id: {skill_filter!r}")
        targets = [skill_slug]
    else:
        targets = list(report.gaps if recheck else report.actionable_gaps)

    today = date.today().isoformat()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session_id = f"{today}_{slug}"
    # Reserve final filename deterministically for source_session reference.
    sessions_dir.mkdir(parents=True, exist_ok=True)
    planned_path = sessions_dir / f"{session_id}.yaml"
    counter = 2
    while planned_path.exists():
        planned_path = sessions_dir / f"{session_id}_{counter}.yaml"
        counter += 1
    session_id = planned_path.stem
    session_rel = f"sessions/{planned_path.name}"

    active_backend: GrillBackend = backend or ManualGrillBackend()
    backend_name = getattr(active_backend, "name", type(active_backend).__name__)
    claims_by_id = {c.id: c for c in claims}
    items: list[SessionItem] = []

    if not targets:
        output_fn(f"[{slug}] Nenhum gap acionavel encontrado.")
        session = InterviewSession(
            session_id=session_id,
            job_slug=slug,
            created_at=created_at,
            backend=backend_name,
            gaps_input=[],
            items=[],
        )
        saved = save_session(session, sessions_dir=sessions_dir)
        return session, saved

    output_fn(f"=== Grill Session: {slug} | backend: {backend_name} | alvos: {', '.join(targets)} ===")
    claims_modified = False

    for skill_id in targets:
        existing = claims_by_id.get(skill_id)
        existing_level = existing.level if (existing and existing.status == "verified") else None
        snippets = extract_jd_snippets(jd_text, skill_id, aliases)

        output_fn(f"\n--- Skill: {skill_id} (atual: {existing.status if existing else 'novo'}"
                  f"{f'/{existing_level}' if existing_level else ''}) ---")
        for sn in snippets:
            output_fn(f"  JD: {sn}")

        output_fn("  Voce tem experiencia real com esta skill? [s]im / [n]ao (marcar absent) / [p]ular:")
        initial = input_fn("  > ").strip().lower()
        if initial in ("p", "pular", "skip"):
            items.append(
                SessionItem(
                    skill_id=skill_id,
                    existing_level=existing_level,
                    qa=[],
                    proposal=None,
                    validator_result=None,
                    user_decision="skipped",
                )
            )
            continue

        if initial in ("n", "nao", "não", "no", "absent"):
            absent_claim = Claim(
                id=skill_id,
                status="absent",
                category=existing.category if existing else "profissional",
                level=None,
                evidence=None,
                tags=sorted(set((existing.tags if existing else []) + [skill_id])),
                bullets=[],
                updated_at=today,
                source_session=session_rel,
            )
            proposal = ClaimProposal(action="mark_absent", claim=absent_claim)
            val = validate_claim(absent_claim)
            claims = upsert_claim(claims, absent_claim)
            claims_by_id[skill_id] = absent_claim
            claims_modified = True
            items.append(
                SessionItem(
                    skill_id=skill_id,
                    existing_level=existing_level,
                    qa=[],
                    proposal=proposal,
                    validator_result=val,
                    user_decision="absent",
                )
            )
            output_fn(f"  -> '{skill_id}' marcado como absent.")
            continue

        questions = active_backend.generate_questions(skill_id, snippets, existing)
        qa: list[QAItem] = []
        for q in questions:
            output_fn(f"  Q: {q}")
            ans = input_fn("  A: ").strip()
            if ans:
                qa.append(QAItem(q=q, a=ans))

        proposal = active_backend.build_proposal(skill_id, existing, qa, input_fn, output_fn)
        if proposal is None:
            backend_val = getattr(active_backend, "last_validation", None)
            val_res = (
                backend_val
                if isinstance(backend_val, ValidationResult) and not backend_val.ok
                else ValidationResult(
                    ok=False,
                    errors=["proposal rejected by validator or cancelled"],
                )
            )
            items.append(
                SessionItem(
                    skill_id=skill_id,
                    existing_level=existing_level,
                    qa=qa,
                    proposal=None,
                    validator_result=val_res,
                    user_decision="rejected",
                )
            )
            continue

        qa_answers = [x.a for x in qa]
        val = validate_claim(
            proposal.claim,
            qa_answers=qa_answers,
            existing_claim=existing,
        )
        if not val.ok:
            for err in val.errors:
                output_fn(f"  [REJEITADO] {err}")
            items.append(
                SessionItem(
                    skill_id=skill_id,
                    existing_level=existing_level,
                    qa=qa,
                    proposal=proposal,
                    validator_result=val,
                    user_decision="rejected",
                )
            )
            continue

        if proposal.claim.status == "absent":
            output_fn(f"  Proposta ({proposal.action}): marcar '{proposal.claim.id}' como absent.")
            output_fn("  Confirmar marcacao como absent? [a]ceitar / [r]ejeitar (padrao: rejeitar):")
            decision_raw = input_fn("  > ").strip().lower()
            if decision_raw in ("a", "aceitar", "accept", "s", "sim", "y"):
                final_absent = proposal.claim.model_copy(
                    update={"updated_at": today, "source_session": session_rel}
                )
                claims = upsert_claim(claims, final_absent)
                claims_by_id[skill_id] = final_absent
                claims_modified = True
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=ClaimProposal(action="mark_absent", claim=final_absent),
                        validator_result=val,
                        user_decision="absent",
                    )
                )
                output_fn(f"  -> '{skill_id}' marcado como absent.")
            else:
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=proposal,
                        validator_result=val,
                        user_decision="rejected",
                    )
                )
                output_fn(f"  -> '{skill_id}' descartado (nao gravado).")
            continue

        # Explicit user approval boundary (default-deny: blank/unknown never persists).
        edited_by_user = False
        while True:
            output_fn(
                f"  Proposta ({proposal.action}): {proposal.claim.id} "
                f"[{proposal.claim.category} | {proposal.claim.level} | "
                f"{proposal.claim.evidence.kind if proposal.claim.evidence else '-'}]"
            )
            if proposal.claim.evidence:
                output_fn(f"    contexto: {proposal.claim.evidence.context}")
                output_fn(f"    concreto: {proposal.claim.evidence.concrete}")
            for b in proposal.claim.bullets:
                output_fn(f"    * {b}")

            output_fn(
                "  Confirmar gravacao em claims? "
                "[a]ceitar / [e]ditar / [d]owngrade / [r]ejeitar / [n]ao-tenho (padrao: rejeitar):"
            )
            decision_raw = input_fn("  > ").strip().lower()

            if decision_raw in ("e", "editar", "edit"):
                edited_prop, edit_val = _interactive_edit_proposal(
                    proposal,
                    qa_answers=qa_answers,
                    existing_claim=existing,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if edited_prop is not None and edit_val.ok:
                    proposal = edited_prop
                    val = edit_val
                    edited_by_user = True
                continue

            if decision_raw in ("d", "downgrade"):
                down_prop, down_val = _downgrade_proposal(
                    proposal,
                    qa_answers=qa_answers,
                    existing_claim=existing,
                )
                if down_prop is not None and down_val.ok:
                    proposal = down_prop
                    val = down_val
                    edited_by_user = True
                    output_fn(f"  -> Nivel reduzido para '{proposal.claim.level}'.")
                else:
                    for err in down_val.errors:
                        output_fn(f"  [NAO REDUZIDO] {err}")
                continue

            if decision_raw in ("a", "aceitar", "accept", "y", "s", "sim"):
                final_claim = proposal.claim.model_copy(
                    update={"updated_at": today, "source_session": session_rel}
                )
                claims = upsert_claim(claims, final_claim)
                claims_by_id[skill_id] = final_claim
                claims_modified = True
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=ClaimProposal(action=proposal.action, claim=final_claim),
                        validator_result=val,
                        user_decision="edited" if edited_by_user else "accepted",
                    )
                )
                output_fn(f"  -> '{skill_id}' salvo em claims.")
                break

            if decision_raw in ("n", "absent", "nao", "não"):
                absent_claim = Claim(
                    id=skill_id,
                    status="absent",
                    category=existing.category if existing else "profissional",
                    level=None,
                    evidence=None,
                    tags=sorted(set((existing.tags if existing else []) + [skill_id])),
                    bullets=[],
                    updated_at=today,
                    source_session=session_rel,
                )
                claims = upsert_claim(claims, absent_claim)
                claims_by_id[skill_id] = absent_claim
                claims_modified = True
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=ClaimProposal(action="mark_absent", claim=absent_claim),
                        validator_result=validate_claim(absent_claim),
                        user_decision="absent",
                    )
                )
                output_fn(f"  -> '{skill_id}' marcado como absent.")
                break

            # Default-deny: any other input (including empty Enter or 'r') discards safely.
            items.append(
                SessionItem(
                    skill_id=skill_id,
                    existing_level=existing_level,
                    qa=qa,
                    proposal=proposal,
                    validator_result=val,
                    user_decision="rejected",
                )
            )
            output_fn(f"  -> '{skill_id}' descartado (nao gravado).")
            break

    if claims_modified:
        save_claims(claims, path=claims_path)

    session = InterviewSession(
        session_id=session_id,
        job_slug=slug,
        created_at=created_at,
        backend=backend_name,
        gaps_input=targets,
        items=items,
    )
    saved_path = save_session(session, sessions_dir=sessions_dir)
    output_fn(f"\nSessao persistida em: {saved_path}")
    return session, saved_path


# ---------------------------------------------------------------------------
# Composition, LaTeX Rendering & ATS Gate
# ---------------------------------------------------------------------------


def skill_display_name(skill_id: str) -> str:
    return SKILL_LABELS.get(skill_id, skill_id.replace("_", " ").title())


def format_latex_block(text: str) -> str:
    """Format plain text (paragraphs, bold subheadings before bullets, '- ' bullets) into LaTeX."""
    lines = (text or "").strip().splitlines()
    if not lines:
        return ""
    out: list[str] = []
    in_list = False
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            if in_list:
                out.append(r"\end{cvlist}")
                in_list = False
            continue
        if s.startswith("- ") or s.startswith("* "):
            if not in_list:
                out.append(r"\begin{cvlist}")
                in_list = True
            out.append(rf"  \item {escape_latex(s[2:].strip())}")
        else:
            if in_list:
                out.append(r"\end{cvlist}")
                in_list = False
            next_nonempty = ""
            for nxt in lines[idx + 1 :]:
                if nxt.strip():
                    next_nonempty = nxt.strip()
                    break
            is_heading = next_nonempty.startswith("- ") or next_nonempty.startswith("* ")
            if is_heading:
                prefix = r"\vspace{4pt}" if out else ""
                out.append(rf"{prefix}\textbf{{{escape_latex(s)}}}\par\vspace{{-2pt}}")
            else:
                out.append(escape_latex(s) + r"\par")
    if in_list:
        out.append(r"\end{cvlist}")
    return "\n".join(out)


def compose_competencias(selected: list[Claim]) -> str:
    verified = [c for c in selected if c.status == "verified" and c.evidence is not None]
    if not verified:
        return "Habilidades gerais em desenvolvimento."
    blocks: list[str] = []
    for c in verified:
        assert c.evidence is not None and c.level is not None
        title = escape_latex(skill_display_name(c.id))
        lvl = escape_latex(LEVEL_LABELS.get(c.level, c.level))
        ctx = escape_latex(c.evidence.context)
        lines = [rf"\cvskill{{{title}}}{{{lvl}}}{{{ctx}}}"]
        bullet_items = c.bullets if c.bullets else [c.evidence.concrete]
        lines.append(r"\begin{cvlist}")
        for b in bullet_items:
            lines.append(rf"  \item {escape_latex(b)}")
        lines.append(r"\end{cvlist}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def render(
    resumo: str,
    competencias: str,
    formacao: str,
    experiencia: str,
    *,
    role: str | None = None,
    profile: Profile | None = None,
) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    prof = profile or load_profile()
    eff_role = role or prof.role
    src = TEMPLATE.read_text(encoding="utf-8")
    src = src.replace("%NAME%", escape_latex(prof.name))
    src = src.replace("%ROLE%", escape_latex(eff_role))
    src = src.replace("%LOCATION%", escape_latex(prof.location))
    src = src.replace("%EMAIL_RAW%", prof.email)
    src = src.replace("%EMAIL%", escape_latex(prof.email))
    src = src.replace("%PHONE%", escape_latex(prof.phone))
    src = src.replace("%LINKEDIN_RAW%", prof.linkedin)
    src = src.replace("%LINKEDIN%", escape_latex(prof.linkedin))
    src = src.replace("%GITHUB_RAW%", prof.github)
    src = src.replace("%GITHUB%", escape_latex(prof.github))
    src = src.replace("%RESUMO%", format_latex_block(resumo))
    src = src.replace("%COMPETENCIAS%", competencias)
    src = src.replace("%FORMACAO%", format_latex_block(formacao))
    src = src.replace("%EXPERIENCIA%", format_latex_block(experiencia))
    tex_src = WORK / "cv.tex"
    tex_src.write_text(src, encoding="utf-8")
    out = subprocess.run([ENGINE, "-X", "compile", str(tex_src)], capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        sys.stderr.write(out.stdout + out.stderr)
        raise RuntimeError("LaTeX compilation failed")
    return WORK / "cv.pdf"


def ats_checks(pdf: Path, allow_two_pages: bool = False) -> list[str]:
    failures = []
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", txt):
        failures.append("contact: email missing from extracted text")
    if "\ufffd" in txt:
        failures.append("accent: replacement char in extracted text (encoding garble)")
    ligatures = sorted({ch for ch in txt if 0xFB00 <= ord(ch) <= 0xFB06})
    if ligatures:
        failures.append(f"accent: ligature garble present: {''.join(ligatures)}")
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    pages = int(m.group(1)) if m else 1
    if pages > (2 if allow_two_pages else 1):
        failures.append(f"pages: {pages} exceeds limit ({2 if allow_two_pages else 1})")
    return failures


def build_cv(
    job_ref: str | None = None,
    *,
    role: str | None = None,
    resumo: str | None = None,
    formacao: str | None = None,
    experiencia: str | None = None,
    categories: list[str] | None = None,
    allow_two_pages: bool | None = None,
    out_path: Path | None = None,
    claims_path: Path = CLAIMS,
    aliases_path: Path = ALIASES,
    jobs_dir: Path = JOBS_DIR,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    claims = load_claims(claims_path)
    for claim in claims:
        res = validate_claim(claim)
        if not res.ok:
            raise ValueError(f"claim '{claim.id}' failed validation: {res.errors}")

    aliases = load_aliases(aliases_path)
    meta: JobMeta | None = None
    if job_ref:
        slug, jd_text, meta = load_job(job_ref, jobs_dir=jobs_dir)
    else:
        slug, jd_text = "default", ""

    prof = load_profile()
    eff_role = role or (meta.role if meta and meta.role else prof.role)
    eff_resumo = resumo or (meta.resumo if meta and meta.resumo else prof.resumo)
    eff_formacao = formacao or (meta.formacao if meta and meta.formacao else prof.formacao)
    eff_experiencia = experiencia or (
        meta.experiencia if meta and meta.experiencia else prof.experiencia
    )
    eff_categories = categories or (meta.category_filter if meta else None)
    eff_allow_2 = (
        allow_two_pages
        if allow_two_pages is not None
        else (meta.allow_2_pages if meta else False)
    )

    selected = select_claims(jd_text, claims, aliases, categories=eff_categories)
    competencias = compose_competencias(selected)
    report = compute_gap_report(jd_text, claims, aliases, job_slug=slug)

    built_pdf = render(
        eff_resumo,
        competencias,
        eff_formacao,
        eff_experiencia,
        role=eff_role,
        profile=prof,
    )
    failures = ats_checks(built_pdf, allow_two_pages=eff_allow_2)

    if out_path is not None:
        final_pdf = out_path
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        final_pdf = out_dir / f"{slug}.pdf"
    final_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built_pdf, final_pdf)

    return {
        "slug": slug,
        "selected": selected,
        "competencias": competencias,
        "pdf": final_pdf,
        "ats_failures": failures,
        "report": report,
    }


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def list_ingested_jobs(
    *,
    jobs_dir: Path = JOBS_DIR,
    out_dir: Path = OUT_DIR,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Return (ingested_job_entries, uningested_loose_files) inside jobs_dir."""
    ingested: list[dict[str, Any]] = []
    loose: list[Path] = []
    if not jobs_dir.exists():
        return ingested, loose

    for entry in sorted(jobs_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            meta_path = entry / "job.yaml"
            meta: JobMeta | None = None
            if meta_path.exists():
                raw_meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                meta = JobMeta.model_validate(raw_meta)
            slug = meta.slug if meta else norm_slug(entry.name)
            pdf_out = out_dir / f"{slug}.pdf"
            ingested.append(
                {
                    "slug": slug,
                    "company": meta.company if meta else None,
                    "title": meta.title if meta else None,
                    "has_pdf": (entry / "jd.pdf").exists(),
                    "has_txt": (entry / "jd.txt").exists(),
                    "built_cv": pdf_out if pdf_out.exists() else None,
                }
            )
        elif entry.is_file() and entry.suffix.lower() in (".pdf", ".txt"):
            loose.append(entry)
    return ingested, loose


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cv",
        description="cv-profiler: generate tailored, honest, ATS-friendly CVs from verified claims + JDs.",
    )
    ap.add_argument("-V", "--version", action="version", version=f"cv-profiler {VERSION}")
    ap.add_argument("--debug", action="store_true", help="Show full Python tracebacks on errors")
    sub = ap.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Import a JD (.pdf or .txt) into jobs/<slug>/")
    p_ingest.add_argument("source", help="Path to browser-printed JD .pdf or .txt file")
    p_ingest.add_argument(
        "--slug",
        default=None,
        help="Job folder slug (default: YYYY-MM_<filename>)",
    )
    p_ingest.add_argument("--title", default=None)
    p_ingest.add_argument("--company", default=None)
    p_ingest.add_argument("--url", default=None)

    p_jobs = sub.add_parser("jobs", help="List ingested jobs and any loose JD files in jobs/")
    p_jobs.add_argument("--json", action="store_true", dest="as_json", help="Print JSON summary")

    p_claims = sub.add_parser("claims", help="Validate and list claims from the active claims file")
    p_claims.add_argument(
        "--category",
        choices=CATEGORIES,
        default=None,
        help="Filter claims by category",
    )
    p_claims.add_argument(
        "--all",
        action="store_true",
        dest="include_absent",
        help="Include claims marked status=absent",
    )

    p_gap = sub.add_parser("gap", help="Run deterministic gap diff against a job")
    p_gap.add_argument("job", help="Job slug in jobs/ or path to JD file")
    p_gap.add_argument(
        "--discover-aliases",
        action="store_true",
        help="Use LLM (read-only) to suggest JD technical terms missing from aliases.yaml",
    )
    p_gap.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default=None)
    p_gap.add_argument("--model", default=None)
    p_gap.add_argument("--base-url", default=None)

    p_grill = sub.add_parser("grill", help="Run gap-driven interview and persist session YAML")
    p_grill.add_argument("job", help="Job slug in jobs/ or path to JD file")
    p_grill.add_argument("--skill", default=None, help="Target a single skill id")
    p_grill.add_argument(
        "--recheck",
        action="store_true",
        help="Include skills previously marked status=absent",
    )
    p_grill.add_argument(
        "--mode",
        choices=("auto", "llm", "manual"),
        default="auto",
        help="Interview backend: 'auto' uses LLM if configured else manual",
    )
    p_grill.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default=None)
    p_grill.add_argument("--model", default=None)
    p_grill.add_argument("--base-url", default=None)

    p_build = sub.add_parser("build", help="Deterministically compile tailored CV + ATS gate")
    p_build.add_argument("job", nargs="?", default=None, help="Job slug in jobs/ or path to JD")
    p_build.add_argument("--role", default=None)
    p_build.add_argument("--resumo", default=None)
    p_build.add_argument("--formacao", default=None)
    p_build.add_argument("--experiencia", default=None)
    p_build.add_argument(
        "--category",
        action="append",
        choices=CATEGORIES,
        default=None,
        help="Restrict included claims to category (repeatable)",
    )
    p_build.add_argument("--allow-2-pages", action="store_true", default=None)
    p_build.add_argument("--out", default=None, help="Custom output PDF path")

    return ap


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "ingest":
        src = Path(args.source)
        slug = args.slug or f"{date.today().strftime('%Y-%m')}_{norm_slug(src.stem)}"
        job_dir, meta, text = ingest_job(
            src,
            slug,
            title=args.title,
            company=args.company,
            url=args.url,
        )
        claims = load_claims()
        aliases = load_aliases()
        report = compute_gap_report(text, claims, aliases, job_slug=meta.slug)
        print(f"ingested: {job_dir}")
        print(f"slug: {meta.slug}")
        print(f"chars: {len(text)}")
        print(f"covered_claims: {report.covered if report.covered else 'NONE'}")
        print(f"actionable_gaps: {report.actionable_gaps if report.actionable_gaps else 'NONE'}")
        print(f"known_absent: {report.known_absent if report.known_absent else 'NONE'}")
        return

    if args.command == "jobs":
        ingested, loose = list_ingested_jobs()
        if args.as_json:
            import json

            print(
                json.dumps(
                    {
                        "ingested": [
                            {**j, "built_cv": str(j["built_cv"]) if j["built_cv"] else None}
                            for j in ingested
                        ],
                        "loose_files": [str(p) for p in loose],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if not ingested:
            print("Nenhum job ingerido em jobs/. Use: cv ingest <arquivo.pdf>")
        else:
            print(f"Jobs ingeridos ({len(ingested)}):")
            for j in ingested:
                label = " — ".join(x for x in (j["company"], j["title"]) if x) or "(sem metadados)"
                cv_tag = f" [CV: {j['built_cv'].name}]" if j["built_cv"] else " [sem CV]"
                print(f"  - {j['slug']}  ({label}){cv_tag}")
        if loose:
            print(f"\nArquivos soltos em jobs/ aguardando ingest ({len(loose)}):")
            for p in loose:
                print(f"  - {p.name}  ->  cv ingest \"{p}\"")
        return

    if args.command == "claims":
        claims = load_claims()
        verified_count = 0
        absent_count = 0
        for c in claims:
            val = validate_claim(c)
            if not val.ok:
                raise ValueError(f"claim '{c.id}' failed validation: {val.errors}")
            if c.status == "absent":
                absent_count += 1
            else:
                verified_count += 1
        filtered = [
            c
            for c in claims
            if (args.category is None or c.category == args.category)
            and (args.include_absent or c.status == "verified")
        ]
        print(f"Claims validados: {verified_count} verified, {absent_count} absent")
        for c in filtered:
            if c.status == "absent":
                print(f"  - {c.id:<20} [absent]  ({c.category})")
            else:
                assert c.evidence is not None and c.level is not None
                print(
                    f"  - {c.id:<20} [{c.level:<7} | {c.evidence.kind:<23}] "
                    f"({c.category})  bullets={len(c.bullets)}"
                )
        return

    if args.command == "gap":
        slug, jd_text, _ = load_job(args.job)
        claims = load_claims()
        aliases = load_aliases()
        report = compute_gap_report(jd_text, claims, aliases, job_slug=slug)
        print(f"job: {report.job_slug}")
        print(f"covered_claims: {report.covered if report.covered else 'NONE'}")
        print(f"actionable_gaps: {report.actionable_gaps if report.actionable_gaps else 'NONE'}")
        print(f"known_absent: {report.known_absent if report.known_absent else 'NONE'}")
        if args.discover_aliases:
            import llm_grill

            cfg = llm_grill.LLMConfig.from_env(
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
            )
            if cfg is None:
                raise SystemExit(
                    "LLM nao configurado para --discover-aliases. Defina OPENAI_API_KEY, "
                    "ANTHROPIC_API_KEY, GEMINI_API_KEY ou CV_PROFILER_LLM_BASE_URL."
                )
            candidates = llm_grill.discover_unmapped_aliases(
                jd_text, aliases, llm_grill.HTTPJSONTransport(cfg)
            )
            print("alias_candidates (read-only; revise antes de adicionar em aliases.yaml):")
            if not candidates:
                print("  NONE")
            for cand in candidates:
                print(f"  - {cand.slug}: {cand.synonyms} (JD: {cand.jd_quote!r})")
        return

    if args.command == "grill":
        selected_backend: GrillBackend | None = None
        if args.mode in ("auto", "llm"):
            import llm_grill

            cfg = llm_grill.LLMConfig.from_env(
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
            )
            if cfg is not None:
                selected_backend = llm_grill.LLMGrillBackend.from_config(cfg)
            elif args.mode == "llm":
                raise SystemExit(
                    "Modo --mode llm exige OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                    "GEMINI_API_KEY ou CV_PROFILER_LLM_BASE_URL."
                )
        run_grill_session(
            args.job,
            skill_filter=args.skill,
            recheck=args.recheck,
            backend=selected_backend,
        )
        return

    if args.command == "build":
        res = build_cv(
            args.job,
            role=args.role,
            resumo=args.resumo,
            formacao=args.formacao,
            experiencia=args.experiencia,
            categories=args.category,
            allow_two_pages=args.allow_2_pages,
            out_path=Path(args.out) if args.out else None,
        )
        report: GapReport = res["report"]
        print(f"job: {res['slug']}")
        print(f"selected_claims: {len(res['selected'])}")
        print(f"pdf: {res['pdf']}")
        print(f"ats_failures: {res['ats_failures'] if res['ats_failures'] else 'NONE'}")
        print(f"jd_mentioned_uncovered: {report.gaps if report.gaps else 'NONE'}")
        print(f"known_absent: {report.known_absent if report.known_absent else 'NONE'}")
        return


def main(argv: list[str] | None = None) -> None:
    args_list = list(sys.argv[1:] if argv is None else argv)
    # Preserve Wave 1 compatibility if invoked directly with --job without a subcommand.
    if args_list and args_list[0].startswith("-") and args_list[0] not in (
        "-h",
        "--help",
        "-V",
        "--version",
        "--debug",
    ):
        if "--job" in args_list:
            idx = args_list.index("--job")
            job_val = args_list[idx + 1] if idx + 1 < len(args_list) else None
            rest = args_list[:idx] + args_list[idx + 2 :]
            args_list = ["build"] + ([job_val] if job_val else []) + rest
        else:
            args_list = ["build"] + args_list

    ap = build_parser()
    args = ap.parse_args(args_list)

    try:
        _dispatch(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nOperacao cancelada pelo usuario.\n")
        raise SystemExit(130)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        if args.debug:
            raise
        sys.stderr.write(f"erro: {exc}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
