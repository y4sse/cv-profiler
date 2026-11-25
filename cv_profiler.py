#!/usr/bin/env python3
"""cv-profiler v2: generate tailored, honest, ATS-friendly CVs from structured evidence + JDs."""

import argparse
from datetime import date, datetime, timezone
import json
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

VERSION = "2.0.0"
TEMPLATE = BASE_DIR / "templates" / "cv.tex"
ENGINE = (
    os.environ.get("CV_PROFILER_ENGINE")
    or shutil.which("tectonic")
    or str(Path.home() / ".local" / "bin" / "tectonic")
)
PROFILE_PATH = BASE_DIR / "profile.yaml"
PROFILE_EXAMPLE_PATH = BASE_DIR / "profile.example.yaml"
EVIDENCE_DIR = BASE_DIR / "evidence"
EVIDENCE_EXAMPLE_DIR = BASE_DIR / "evidence.example"
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
    "qa_testes": "QA e Testes de Software",
    "microsoft_365": "Microsoft 365 e Google Workspace",
    "excel_planilhas": "Excel e Google Sheets",
    "active_directory": "Active Directory",
    "redes": "Redes (TCP/IP, DNS, DHCP)",
    "monitoramento": "Monitoramento de Sistemas e Redes",
    "itil": "ITIL v3/v4",
    "python": "Python",
    "documentacao_treinamento": "Documentação (POPs) e Treinamento",
    "rotinas_administrativas_financeiras": "Rotinas Administrativas e Orçamento",
    "engenharia_software": "Engenharia de Software",
    "sap": "ERP SAP (PM, FICO, MM, QM)",
    "azure": "Microsoft Azure",
    "ingles": "Inglês C1 Advanced (EF SET)",
    "financas_fiscal": "Análise Financeira e Escrita Fiscal",
    "windows": "Windows e Windows Server",
    "linux": "Linux",
    "sql": "SQL e Bancos de Dados",
    "automacao": "Automação de Processos",
}

SKILL_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Suporte e Infraestrutura",
        ("service_desk", "itil", "active_directory", "redes", "monitoramento", "windows", "linux", "azure"),
    ),
    (
        "Qualidade, Dados e Automação",
        ("qa_testes", "python", "sql", "excel_planilhas", "automacao", "engenharia_software"),
    ),
    (
        "Sistemas e Operações",
        ("microsoft_365", "sap", "documentacao_treinamento", "rotinas_administrativas_financeiras", "financas_fiscal"),
    ),
    (
        "Idiomas",
        ("ingles",),
    ),
]

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
MONTH_NAMES_PT: dict[int, str] = {
    1: "jan",
    2: "fev",
    3: "mar",
    4: "abr",
    5: "mai",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "set",
    10: "out",
    11: "nov",
    12: "dez",
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
EVIDENCE_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
YEAR_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")

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

_JOB_BOARD_NOISE_RE = re.compile(
    r"(?:^|[.\-_])(?:inhire\.app|gupy\.io|linkedin\.com|indeed\.com|catho\.com\.br|"
    r"vagas\.com\.br|glassdoor\.com(?:\.br)?|greenhouse\.io|lever\.co|workday\.com|"
    r"breezy\.hr|solides\.com\.br|infojobs\.com\.br|www|https?)(?=[.\-_\s]|$)",
    re.IGNORECASE,
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


def generate_auto_slug(source_path: Path, *, today: date | None = None, max_body_len: int = 40) -> str:
    """Create a clean YYYY-MM_<name> slug from a raw JD filename, stripping job-board noise."""
    stem = _JOB_BOARD_NOISE_RE.sub("-", source_path.stem)
    body = norm_slug(stem) or "vaga"
    if len(body) > max_body_len:
        cut = body[:max_body_len]
        if "-" in cut and len(cut.rsplit("-", 1)[0]) >= 12:
            cut = cut.rsplit("-", 1)[0]
        body = cut.strip("-_") or body[:max_body_len]
    prefix = (today or date.today()).strftime("%Y-%m")
    if re.match(r"^\d{4}-\d{2}[_-]", body):
        return body
    return f"{prefix}_{body}"


def format_ym(ym: str | None) -> str:
    if not ym:
        return "atual"
    if YEAR_MONTH_RE.match(ym):
        y, m = ym.split("-")
        return f"{MONTH_NAMES_PT[int(m)]}/{y}"
    return ym


def format_date_range(start: str, end: str | None) -> str:
    return f"{format_ym(start)} – {format_ym(end)}"


def contains_token(haystack_norm: str, needle_norm: str) -> bool:
    if not haystack_norm or not needle_norm:
        return False
    return f"_{needle_norm}_" in f"_{haystack_norm}_"


def escape_latex(text: str) -> str:
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPES[m.group(0)], text or "")


def _is_url(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip().lower()
    return v.startswith(("http://", "https://")) or (
        "." in v and not v.endswith((".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md", ".yaml"))
        and "/" in v
        and not v.startswith(("evidence/", "artifacts/", "./", "../"))
    )


def _normalize_url(url: str) -> str:
    u = url.strip()
    if u.startswith(("http://", "https://")):
        return u
    return f"https://{u}"


def _display_url(url: str) -> str:
    return re.sub(r"^https?://", "", url.strip()).rstrip("/")


# ---------------------------------------------------------------------------
# Pydantic Models — Core Claims & Honesty (v1 compatible + v2 EvidenceStore)
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


# --- v2 Evidence Directory Models ---


def _clean_skill_list(skills: list[str]) -> list[str]:
    cleaned: list[str] = []
    for s in skills:
        slug = norm(s)
        if not slug:
            raise ValueError(f"invalid empty skill slug: {s!r}")
        if slug not in cleaned:
            cleaned.append(slug)
    return cleaned


def _clean_skill_levels(
    skill_levels: dict[str, Literal["nocoes", "pratica", "solido"]],
    evidence_kind: str,
) -> dict[str, Literal["nocoes", "pratica", "solido"]]:
    max_level = MAX_LEVEL_BY_EVIDENCE_KIND[evidence_kind]
    out: dict[str, Literal["nocoes", "pratica", "solido"]] = {}
    for raw_k, lvl in skill_levels.items():
        k = norm(raw_k)
        if not k or not ID_RE.match(k):
            raise ValueError(f"invalid skill id in skill_levels: {raw_k!r}")
        if LEVEL_RANK[lvl] > LEVEL_RANK[max_level]:
            raise ValueError(
                f"skill_levels['{k}']='{lvl}' exceeds max '{max_level}' for evidence_kind='{evidence_kind}'"
            )
        out[k] = lvl
    return out


class BulletItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    skills: list[str] = Field(default_factory=list)
    core: bool = False

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        s = value.strip()
        if not s:
            raise ValueError("bullet text cannot be empty")
        if len(s) > MAX_BULLET_LEN:
            raise ValueError(f"bullet exceeds {MAX_BULLET_LEN} chars ({len(s)})")
        return s

    @field_validator("skills")
    @classmethod
    def _clean_skills(cls, value: list[str]) -> list[str]:
        return _clean_skill_list(value)


class ExperienceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    org: str = Field(..., min_length=2)
    role: str = Field(..., min_length=2)
    location: str | None = None
    start: str = Field(..., pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    end: str | None = Field(default=None, pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    evidence_kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"] = (
        "producao_autonoma"
    )
    artifacts: list[str] = Field(default_factory=list)
    skill_levels: dict[str, Literal["nocoes", "pratica", "solido"]] = Field(default_factory=dict)
    bullets: list[BulletItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_levels(self) -> "ExperienceEvidence":
        self.skill_levels = _clean_skill_levels(self.skill_levels, self.evidence_kind)
        return self

    def all_skills(self) -> set[str]:
        skills = set(self.skill_levels.keys())
        for b in self.bullets:
            skills.update(b.skills)
        return skills

    def effective_level(self, skill_id: str) -> Literal["nocoes", "pratica", "solido"]:
        if skill_id in self.skill_levels:
            return self.skill_levels[skill_id]
        return MAX_LEVEL_BY_EVIDENCE_KIND[self.evidence_kind]  # type: ignore[return-value]


class EducationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    institution: str = Field(..., min_length=2)
    degree: str = Field(..., min_length=2)
    location: str | None = None
    start: str = Field(..., pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    end: str | None = Field(default=None, pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    evidence_kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"] = "estudo"
    skills: list[str] = Field(default_factory=list)
    skill_levels: dict[str, Literal["nocoes", "pratica", "solido"]] = Field(default_factory=dict)
    highlights: list[BulletItem] = Field(default_factory=list)

    @field_validator("skills")
    @classmethod
    def _clean_skills(cls, value: list[str]) -> list[str]:
        return _clean_skill_list(value)

    @model_validator(mode="after")
    def _check_levels(self) -> "EducationEvidence":
        self.skill_levels = _clean_skill_levels(self.skill_levels, self.evidence_kind)
        return self

    def all_skills(self) -> set[str]:
        skills = set(self.skills) | set(self.skill_levels.keys())
        for h in self.highlights:
            skills.update(h.skills)
        return skills

    def effective_level(self, skill_id: str) -> Literal["nocoes", "pratica", "solido"]:
        if skill_id in self.skill_levels:
            return self.skill_levels[skill_id]
        return MAX_LEVEL_BY_EVIDENCE_KIND[self.evidence_kind]  # type: ignore[return-value]


class ProjectEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    name: str = Field(..., min_length=2)
    role: str | None = None
    date: str = Field(..., pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    url: str | None = None
    evidence_kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"] = "projeto"
    artifacts: list[str] = Field(default_factory=list)
    skill_levels: dict[str, Literal["nocoes", "pratica", "solido"]] = Field(default_factory=dict)
    bullets: list[BulletItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_levels(self) -> "ProjectEvidence":
        self.skill_levels = _clean_skill_levels(self.skill_levels, self.evidence_kind)
        return self

    def all_skills(self) -> set[str]:
        skills = set(self.skill_levels.keys())
        for b in self.bullets:
            skills.update(b.skills)
        return skills

    def effective_level(self, skill_id: str) -> Literal["nocoes", "pratica", "solido"]:
        if skill_id in self.skill_levels:
            return self.skill_levels[skill_id]
        return MAX_LEVEL_BY_EVIDENCE_KIND[self.evidence_kind]  # type: ignore[return-value]


class CertificateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    title: str = Field(..., min_length=2)
    issuer: str = Field(..., min_length=2)
    date: str = Field(..., pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$")
    detail: str | None = None
    url: str | None = None
    artifact: str | None = None
    evidence_kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"] = "estudo"
    skills: list[str] = Field(default_factory=list)
    skill_levels: dict[str, Literal["nocoes", "pratica", "solido"]] = Field(default_factory=dict)

    @field_validator("skills")
    @classmethod
    def _clean_skills(cls, value: list[str]) -> list[str]:
        return _clean_skill_list(value)

    @model_validator(mode="after")
    def _check_levels(self) -> "CertificateEvidence":
        self.skill_levels = _clean_skill_levels(self.skill_levels, self.evidence_kind)
        return self

    def all_skills(self) -> set[str]:
        return set(self.skills) | set(self.skill_levels.keys())

    def effective_level(self, skill_id: str) -> Literal["nocoes", "pratica", "solido"]:
        if skill_id in self.skill_levels:
            return self.skill_levels[skill_id]
        return MAX_LEVEL_BY_EVIDENCE_KIND[self.evidence_kind]  # type: ignore[return-value]


class UnanchoredEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    evidence_kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"]
    level: Literal["nocoes", "pratica", "solido"]
    context: str = Field(..., min_length=MIN_CONTEXT_LEN)
    concrete: str = Field(..., min_length=MIN_CONCRETE_LEN)
    tags: list[str] = Field(default_factory=list)
    artifact: str | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return _clean_skill_list(value)

    @model_validator(mode="after")
    def _check_level_cap(self) -> "UnanchoredEntry":
        max_level = MAX_LEVEL_BY_EVIDENCE_KIND[self.evidence_kind]
        if LEVEL_RANK[self.level] > LEVEL_RANK[max_level]:
            raise ValueError(
                f"unanchored '{self.id}': level '{self.level}' exceeds max '{max_level}' "
                f"for evidence_kind '{self.evidence_kind}'"
            )
        return self


class AbsentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    updated_at: str | None = None
    source_session: str | None = None


class EvidenceStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiences: list[ExperienceEvidence] = Field(default_factory=list)
    education: list[EducationEvidence] = Field(default_factory=list)
    projects: list[ProjectEvidence] = Field(default_factory=list)
    certificates: list[CertificateEvidence] = Field(default_factory=list)
    unanchored: list[UnanchoredEntry] = Field(default_factory=list)
    absent: list[AbsentEntry] = Field(default_factory=list)
    root_dir: str | None = None


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = DEFAULT_NAME
    location: str = DEFAULT_LOCATION
    email: str = DEFAULT_EMAIL
    phone: str = DEFAULT_PHONE
    linkedin: str = DEFAULT_LINKEDIN
    github: str = DEFAULT_GITHUB
    evidence_dir: str = "evidence"
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


def resolve_evidence_dir(profile: Profile | None = None) -> Path:
    prof = profile or load_profile()
    p = Path(prof.evidence_dir)
    resolved = p if p.is_absolute() else (BASE_DIR / p)
    if resolved.is_dir() and any(
        (resolved / sub).is_dir() for sub in ("experiences", "education", "projects", "certificates")
    ):
        return resolved
    if EVIDENCE_EXAMPLE_DIR.is_dir():
        return EVIDENCE_EXAMPLE_DIR
    return resolved


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
    include_projects: Literal["auto", "include", "exclude"] = "auto"
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
    target_evidence: str | None = None


class InterviewSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    job_slug: str = Field(..., min_length=1)
    created_at: str = Field(..., min_length=1)
    backend: str = "manual"
    gaps_input: list[str] = Field(default_factory=list)
    items: list[SessionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic Validation (Claims & EvidenceStore)
# ---------------------------------------------------------------------------


def _extract_metrics(text: str) -> set[str]:
    return {m.group(0) for m in _METRIC_RE.finditer(text or "")}


def _check_text_honesty(
    field_label: str,
    text: str,
    effective_level: str,
    errors: list[str],
) -> None:
    text_norm = norm(text)
    for bw in sorted(BANNED_BUZZWORDS):
        if contains_token(text_norm, bw):
            errors.append(f"buzzword:{field_label}: banned term '{bw}'")
    if effective_level != "solido":
        for term in sorted(SOLIDO_ONLY_TERMS):
            if contains_token(text_norm, term):
                errors.append(
                    f"oversell:{field_label}: '{term}' requires level 'solido' (got '{effective_level}')"
                )


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

    concrete_norm = norm(claim.evidence.concrete)
    context_norm = norm(claim.evidence.context)
    id_and_tags = {norm(claim.id)} | {norm(t) for t in claim.tags}
    concrete_tokens = [tok for tok in concrete_norm.split("_") if len(tok) > 1]

    if concrete_norm in id_and_tags or concrete_norm == context_norm:
        errors.append("evidence: concrete repeats skill id/tag or context without detail")
    if len(set(concrete_tokens)) < 3:
        errors.append("evidence: concrete must describe a tangible action (>= 3 distinct terms)")

    texts_to_check = [("evidence.concrete", claim.evidence.concrete)] + [
        (f"bullets[{i}]", b) for i, b in enumerate(claim.bullets)
    ]
    for field_label, text in texts_to_check:
        _check_text_honesty(field_label, text, claim.level, errors)

    if existing_claim is not None and existing_claim.status == "verified":
        assert existing_claim.level is not None and existing_claim.evidence is not None
        if LEVEL_RANK[claim.level] > LEVEL_RANK[existing_claim.level]:
            if claim.evidence == existing_claim.evidence:
                errors.append(
                    f"upgrade: cannot raise level from '{existing_claim.level}' to "
                    f"'{claim.level}' without new evidence"
                )

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


def _check_artifact_ref(
    owner_label: str,
    artifact: str | None,
    root_dir: Path | None,
    warnings: list[str],
) -> None:
    if not artifact or _is_url(artifact) or root_dir is None:
        return
    p = Path(artifact)
    candidates = [p] if p.is_absolute() else [root_dir / p, BASE_DIR / p]
    if not any(c.exists() for c in candidates):
        warnings.append(f"artifact:{owner_label}: arquivo local nao encontrado ({artifact})")


def validate_evidence_store(store: EvidenceStore) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    root = Path(store.root_dir) if store.root_dir else None

    for exp in store.experiences:
        max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND[exp.evidence_kind]
        for idx, b in enumerate(exp.bullets):
            eff_lvl = max(
                (exp.effective_level(s) for s in b.skills),
                key=lambda lv: LEVEL_RANK[lv],
                default=max_lvl,
            )
            _check_text_honesty(f"experience:{exp.id}.bullets[{idx}]", b.text, eff_lvl, errors)
        for art in exp.artifacts:
            _check_artifact_ref(f"experience:{exp.id}", art, root, warnings)

    for edu in store.education:
        max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND[edu.evidence_kind]
        for idx, h in enumerate(edu.highlights):
            eff_lvl = max(
                (edu.effective_level(s) for s in h.skills),
                key=lambda lv: LEVEL_RANK[lv],
                default=max_lvl,
            )
            _check_text_honesty(f"education:{edu.id}.highlights[{idx}]", h.text, eff_lvl, errors)

    for proj in store.projects:
        max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND[proj.evidence_kind]
        for idx, b in enumerate(proj.bullets):
            eff_lvl = max(
                (proj.effective_level(s) for s in b.skills),
                key=lambda lv: LEVEL_RANK[lv],
                default=max_lvl,
            )
            _check_text_honesty(f"project:{proj.id}.bullets[{idx}]", b.text, eff_lvl, errors)
        for art in proj.artifacts:
            _check_artifact_ref(f"project:{proj.id}", art, root, warnings)

    for cert in store.certificates:
        max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND[cert.evidence_kind]
        if cert.detail:
            _check_text_honesty(f"certificate:{cert.id}.detail", cert.detail, max_lvl, errors)
        _check_artifact_ref(f"certificate:{cert.id}", cert.artifact, root, warnings)

    for item in store.unanchored:
        _check_text_honesty(f"unanchored:{item.id}.concrete", item.concrete, item.level, errors)
        _check_artifact_ref(f"unanchored:{item.id}", item.artifact, root, warnings)
        warnings.append(
            f"unanchored:{item.id}: skill ainda sem vinculo a uma experiencia, projeto ou certificado "
            f"(mova de unanchored.yaml para evidence/experiences/, projects/ ou certificates/)"
        )

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# YAML Persistence & Evidence Store I/O
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


def _load_yaml_dir(subdir: Path, model_cls: type[BaseModel]) -> list[Any]:
    if not subdir.is_dir():
        return []
    items: list[Any] = []
    seen: set[str] = set()
    for p in sorted(subdir.glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not raw:
            continue
        obj = model_cls.model_validate(raw)
        obj_id = getattr(obj, "id")
        if obj_id in seen:
            raise ValueError(f"duplicate id '{obj_id}' in {subdir}")
        seen.add(obj_id)
        items.append(obj)
    return items


def load_evidence_store(evidence_dir: Path | None = None) -> EvidenceStore:
    root = evidence_dir or resolve_evidence_dir()
    if not root.is_dir():
        raise FileNotFoundError(f"evidence directory not found: {root}")

    experiences = _load_yaml_dir(root / "experiences", ExperienceEvidence)
    education = _load_yaml_dir(root / "education", EducationEvidence)
    projects = _load_yaml_dir(root / "projects", ProjectEvidence)
    certificates = _load_yaml_dir(root / "certificates", CertificateEvidence)

    unanchored: list[UnanchoredEntry] = []
    unanchored_path = root / "unanchored.yaml"
    if unanchored_path.exists():
        raw_u = yaml.safe_load(unanchored_path.read_text(encoding="utf-8")) or {}
        seen_u: set[str] = set()
        for entry in raw_u.get("unanchored") or []:
            obj = UnanchoredEntry.model_validate(entry)
            if obj.id in seen_u:
                raise ValueError(f"duplicate unanchored id '{obj.id}'")
            seen_u.add(obj.id)
            unanchored.append(obj)

    absent: list[AbsentEntry] = []
    absent_path = root / "absent.yaml"
    if absent_path.exists():
        raw_a = yaml.safe_load(absent_path.read_text(encoding="utf-8")) or {}
        seen_a: set[str] = set()
        for entry in raw_a.get("absent") or []:
            obj = AbsentEntry.model_validate(entry)
            if obj.id in seen_a:
                raise ValueError(f"duplicate absent id '{obj.id}'")
            seen_a.add(obj.id)
            absent.append(obj)

    return EvidenceStore(
        experiences=experiences,
        education=education,
        projects=projects,
        certificates=certificates,
        unanchored=unanchored,
        absent=absent,
        root_dir=str(root),
    )


def save_absent_entries(entries: list[AbsentEntry], evidence_dir: Path) -> Path:
    seen: set[str] = set()
    serialized: list[dict[str, Any]] = []
    for e in entries:
        if e.id in seen:
            continue
        seen.add(e.id)
        serialized.append(e.model_dump(mode="python"))
    return write_yaml_atomic(evidence_dir / "absent.yaml", {"absent": serialized})


def save_unanchored_entries(entries: list[UnanchoredEntry], evidence_dir: Path) -> Path:
    seen: set[str] = set()
    serialized: list[dict[str, Any]] = []
    for e in entries:
        if e.id in seen:
            continue
        seen.add(e.id)
        serialized.append(e.model_dump(mode="python"))
    return write_yaml_atomic(evidence_dir / "unanchored.yaml", {"unanchored": serialized})


def derive_claims_from_evidence(store: EvidenceStore) -> list[Claim]:
    """Deterministically derive normalized Claim views from EvidenceStore for matching & gap analysis."""
    best: dict[str, dict[str, Any]] = {}

    def _record(
        skill_id: str,
        *,
        category: Literal["profissional", "academico", "voluntariado"],
        kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"],
        level: Literal["nocoes", "pratica", "solido"],
        context: str,
        concrete: str,
        bullet: str | None = None,
        artifact: str | None = None,
        extra_tags: list[str] | None = None,
    ) -> None:
        sid = norm(skill_id)
        if not sid or not ID_RE.match(sid):
            return
        tags = sorted({sid} | {norm(t) for t in (extra_tags or []) if norm(t)})
        cur = best.get(sid)
        if cur is None:
            best[sid] = {
                "id": sid,
                "category": category,
                "kind": kind,
                "level": level,
                "context": context,
                "concrete": concrete,
                "artifact": artifact,
                "tags": set(tags),
                "bullets": [bullet] if bullet else [],
            }
            return
        cur["tags"].update(tags)
        if bullet and bullet not in cur["bullets"]:
            cur["bullets"].append(bullet)
        if LEVEL_RANK[level] > LEVEL_RANK[cur["level"]]:
            cur["level"] = level
            cur["kind"] = kind
            cur["context"] = context
            cur["concrete"] = concrete
            cur["category"] = category
            if artifact:
                cur["artifact"] = artifact
        elif not cur["artifact"] and artifact:
            cur["artifact"] = artifact

    for exp in store.experiences:
        ctx = f"{exp.org} — {exp.role} ({format_date_range(exp.start, exp.end)})"
        art = exp.artifacts[0] if exp.artifacts else None
        for b in exp.bullets:
            for sid in b.skills:
                _record(
                    sid,
                    category="profissional",
                    kind=exp.evidence_kind,
                    level=exp.effective_level(sid),
                    context=ctx,
                    concrete=b.text if len(b.text) >= MIN_CONCRETE_LEN else f"{exp.role}: {b.text}",
                    bullet=b.text,
                    artifact=art,
                )
        for sid, lvl in exp.skill_levels.items():
            if sid not in best:
                fallback_text = exp.bullets[0].text
                _record(
                    sid,
                    category="profissional",
                    kind=exp.evidence_kind,
                    level=lvl,
                    context=ctx,
                    concrete=fallback_text,
                    bullet=fallback_text,
                    artifact=art,
                )

    for proj in store.projects:
        ctx = f"{proj.name} ({format_ym(proj.date)})"
        art = proj.url or (proj.artifacts[0] if proj.artifacts else None)
        for b in proj.bullets:
            for sid in b.skills:
                _record(
                    sid,
                    category="profissional",
                    kind=proj.evidence_kind,
                    level=proj.effective_level(sid),
                    context=ctx,
                    concrete=b.text if len(b.text) >= MIN_CONCRETE_LEN else f"{proj.name}: {b.text}",
                    bullet=b.text,
                    artifact=art,
                )
        for sid, lvl in proj.skill_levels.items():
            if sid not in best:
                fallback_text = proj.bullets[0].text
                _record(
                    sid,
                    category="profissional",
                    kind=proj.evidence_kind,
                    level=lvl,
                    context=ctx,
                    concrete=fallback_text,
                    bullet=fallback_text,
                    artifact=art,
                )

    for edu in store.education:
        ctx = f"{edu.institution} — {edu.degree} ({format_date_range(edu.start, edu.end)})"
        default_conc = edu.highlights[0].text if edu.highlights else f"Formacao em {edu.degree} ({edu.institution})"
        for h in edu.highlights:
            for sid in h.skills:
                _record(
                    sid,
                    category="academico",
                    kind=edu.evidence_kind,
                    level=edu.effective_level(sid),
                    context=ctx,
                    concrete=h.text if len(h.text) >= MIN_CONCRETE_LEN else default_conc,
                    bullet=h.text,
                )
        for sid in edu.all_skills():
            _record(
                sid,
                category="academico",
                kind=edu.evidence_kind,
                level=edu.effective_level(sid),
                context=ctx,
                concrete=default_conc,
            )

    for cert in store.certificates:
        ctx = f"{cert.issuer} — {cert.title} ({format_ym(cert.date)})"
        conc = f"{cert.title}: {cert.detail}" if cert.detail else f"Certificacao {cert.title} ({cert.issuer})"
        if len(conc) < MIN_CONCRETE_LEN:
            conc = f"Certificacao concluida: {conc}"
        art = cert.url or cert.artifact
        for sid in cert.all_skills():
            _record(
                sid,
                category="profissional" if cert.evidence_kind != "estudo" else "academico",
                kind=cert.evidence_kind,
                level=cert.effective_level(sid),
                context=ctx,
                concrete=conc,
                artifact=art,
            )

    for u in store.unanchored:
        _record(
            u.id,
            category="profissional" if u.evidence_kind != "estudo" else "academico",
            kind=u.evidence_kind,
            level=u.level,
            context=u.context,
            concrete=u.concrete,
            artifact=u.artifact,
            extra_tags=u.tags,
        )

    claims: list[Claim] = []
    for sid in sorted(best.keys()):
        entry = best[sid]
        claims.append(
            Claim(
                id=sid,
                status="verified",
                category=entry["category"],
                level=entry["level"],
                evidence=Evidence(
                    kind=entry["kind"],
                    context=entry["context"],
                    concrete=entry["concrete"],
                    artifact=entry["artifact"],
                ),
                tags=sorted(entry["tags"]),
                bullets=entry["bullets"][:3],
            )
        )

    verified_ids = {c.id for c in claims}
    for ab in store.absent:
        if ab.id in verified_ids:
            continue
        claims.append(
            Claim(
                id=ab.id,
                status="absent",
                category="profissional",
                level=None,
                evidence=None,
                tags=[ab.id],
                bullets=[],
                updated_at=ab.updated_at,
                source_session=ab.source_session,
            )
        )
    return claims


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
# Jobs Storage & Automatic Ingestion (jobs/ inbox -> jobs/ingested/<slug>/)
# ---------------------------------------------------------------------------


def ingested_jobs_dir(jobs_dir: Path = JOBS_DIR) -> Path:
    return jobs_dir / "ingested"


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


def _ensure_unique_slug(base_slug: str, target_root: Path) -> str:
    slug = base_slug
    counter = 2
    while (target_root / slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def ingest_job(
    source_path: Path,
    slug: str | None = None,
    *,
    title: str | None = None,
    company: str | None = None,
    url: str | None = None,
    jobs_dir: Path = JOBS_DIR,
    move_source: bool = False,
) -> tuple[Path, JobMeta, str]:
    if not source_path.exists():
        raise FileNotFoundError(f"job source file not found: {source_path}")
    if not source_path.is_file():
        raise ValueError(
            f"job source must be a .pdf or .txt file, got directory: {source_path} "
            "(drop files inside jobs/ for automatic ingestion)"
        )

    ingested_root = ingested_jobs_dir(jobs_dir)
    if slug is None:
        base_slug = generate_auto_slug(source_path)
        clean_slug = _ensure_unique_slug(base_slug, ingested_root)
    else:
        clean_slug = norm_slug(slug)
        if not clean_slug or not SLUG_RE.match(clean_slug):
            raise ValueError(f"invalid job slug: {slug!r}")

    job_dir = ingested_root / clean_slug
    job_dir.mkdir(parents=True, exist_ok=True)
    txt_dst = job_dir / "jd.txt"
    orig_name = source_path.name

    if source_path.suffix.lower() == ".pdf":
        pdf_dst = job_dir / "jd.pdf"
        if source_path.resolve() != pdf_dst.resolve():
            if move_source:
                shutil.move(str(source_path), str(pdf_dst))
            else:
                shutil.copy2(source_path, pdf_dst)
        text = extract_pdf_text(pdf_dst, txt_dst)
    else:
        text = source_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"job text file is empty: {source_path}")
        txt_dst.write_text(text, encoding="utf-8")
        if move_source and source_path.resolve() != txt_dst.resolve():
            source_path.unlink()

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
            "source_file": orig_name,
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    write_yaml_atomic(meta_path, meta.model_dump(mode="python"))
    return job_dir, meta, text


def sync_jobs(jobs_dir: Path = JOBS_DIR) -> list[tuple[Path, JobMeta]]:
    """Automatically ingest any loose .pdf/.txt files dropped in jobs/ into jobs/ingested/<slug>/."""
    ingested_now: list[tuple[Path, JobMeta]] = []
    if not jobs_dir.exists():
        return ingested_now

    for entry in sorted(jobs_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix.lower() in (".pdf", ".txt"):
            job_dir, meta, _ = ingest_job(entry, slug=None, jobs_dir=jobs_dir, move_source=True)
            ingested_now.append((job_dir, meta))
    return ingested_now


def _iter_ingested_dirs(jobs_dir: Path) -> list[Path]:
    dirs: list[Path] = []
    if not jobs_dir.exists():
        return dirs
    ing_root = ingested_jobs_dir(jobs_dir)
    if ing_root.is_dir():
        for entry in sorted(ing_root.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                dirs.append(entry)
    # Backward compatibility: also detect legacy ingested job dirs directly inside jobs_dir
    for entry in sorted(jobs_dir.iterdir()):
        if entry.name.startswith(".") or entry.name == "ingested":
            continue
        if entry.is_dir() and any((entry / f).exists() for f in ("job.yaml", "jd.txt", "jd.pdf")):
            dirs.append(entry)
    return dirs


def _read_job_dir(job_dir: Path) -> tuple[str, str, JobMeta | None]:
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


def load_job(
    job_ref: str,
    *,
    jobs_dir: Path = JOBS_DIR,
    auto_sync: bool = True,
) -> tuple[str, str, JobMeta | None]:
    """Resolve an exact or unambiguous partial job slug in jobs/ingested/, or a direct path."""
    if auto_sync:
        sync_jobs(jobs_dir=jobs_dir)

    ing_dir = ingested_jobs_dir(jobs_dir) / job_ref
    legacy_dir = jobs_dir / job_ref
    direct_path = Path(job_ref)

    for candidate in (ing_dir, legacy_dir, direct_path):
        if candidate.is_dir() and candidate.name != "ingested":
            if any((candidate / f).exists() for f in ("jd.txt", "jd.pdf", "job.yaml")):
                return _read_job_dir(candidate)

    if direct_path.is_file():
        slug = norm_slug(direct_path.stem)
        if direct_path.suffix.lower() == ".pdf":
            with tempfile.TemporaryDirectory() as td:
                jd_text = extract_pdf_text(direct_path, Path(td) / "jd.txt")
        else:
            jd_text = direct_path.read_text(encoding="utf-8")
        return slug, jd_text, None

    # Unambiguous partial slug match (e.g., 'lanlink' -> '2026-11_lanlink_suporte-2n')
    needle = norm_slug(job_ref)
    if needle:
        matches: list[Path] = []
        for d in _iter_ingested_dirs(jobs_dir):
            if needle in norm_slug(d.name):
                matches.append(d)
        if len(matches) == 1:
            return _read_job_dir(matches[0])
        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            raise ValueError(f"slug parcial '{job_ref}' e ambiguo; corresponde a: {names}")

    raise FileNotFoundError(
        f"job nao encontrado como slug em {ingested_jobs_dir(jobs_dir)} ou caminho: {job_ref}"
    )


def list_ingested_jobs(
    *,
    jobs_dir: Path = JOBS_DIR,
    out_dir: Path = OUT_DIR,
    auto_sync: bool = False,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Return (ingested_job_entries, uningested_loose_files) inside jobs_dir."""
    if auto_sync:
        sync_jobs(jobs_dir=jobs_dir)

    ingested: list[dict[str, Any]] = []
    loose: list[Path] = []
    if not jobs_dir.exists():
        return ingested, loose

    for entry in _iter_ingested_dirs(jobs_dir):
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
                "path": str(entry),
                "company": meta.company if meta else None,
                "title": meta.title if meta else None,
                "has_pdf": (entry / "jd.pdf").exists(),
                "has_txt": (entry / "jd.txt").exists(),
                "built_cv": pdf_out if pdf_out.exists() else None,
            }
        )

    for entry in sorted(jobs_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_file() and entry.suffix.lower() in (".pdf", ".txt"):
            loose.append(entry)
    return ingested, loose


# ---------------------------------------------------------------------------
# Deterministic Matching & Gap Analysis
# ---------------------------------------------------------------------------


def extract_jd_skills(jd_text: str, aliases: dict[str, list[str]]) -> set[str]:
    jd_norm = norm(jd_text)
    mentioned: set[str] = set()
    for canon, syns in aliases.items():
        for s in [canon] + syns:
            if contains_token(jd_norm, s):
                mentioned.add(canon)
                break
    return mentioned


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
    mentioned = extract_jd_skills(jd_text, aliases)
    selected = select_claims(jd_text, claims, aliases)
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
# Grill Interview Loop (v2 EvidenceStore + Legacy Claims Compatibility)
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
    """Deterministic local grill backend."""

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
        category = input_fn("  > ").strip() or default_cat

        output_fn(f"  Tipo de evidencia ({'/'.join(EVIDENCE_KINDS)}) [producao_supervisionada]:")
        kind = input_fn("  > ").strip() or "producao_supervisionada"

        max_lvl = MAX_LEVEL_BY_EVIDENCE_KIND.get(kind, "pratica")
        output_fn(f"  Nivel ({'/'.join(LEVELS)}; max para '{kind}' = '{max_lvl}') [{max_lvl}]:")
        level = input_fn("  > ").strip() or max_lvl

        default_ctx = qa[0].a if qa else ""
        output_fn(f"  Contexto (onde/quando) [{default_ctx}]:")
        context = input_fn("  > ").strip() or default_ctx

        default_conc = qa[1].a if len(qa) > 1 else ""
        output_fn(f"  Evidencia concreta (o que fez) [{default_conc}]:")
        concrete = input_fn("  > ").strip() or default_conc

        output_fn("  Artefato (URL/repo/cert.pdf opcional, Enter para vazio):")
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


def _persist_claim_to_evidence(
    claim: Claim,
    store: EvidenceStore,
    evidence_dir: Path,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> tuple[EvidenceStore, str]:
    """Attach a verified claim from grill to an existing or new evidence YAML file."""
    assert claim.evidence is not None and claim.level is not None
    options: list[tuple[str, str, Any]] = []
    for exp in store.experiences:
        options.append(("experience", f"Experiência: {exp.org} — {exp.role} ({exp.id})", exp))
    for proj in store.projects:
        options.append(("project", f"Projeto: {proj.name} ({proj.id})", proj))
    for edu in store.education:
        options.append(("education", f"Formação: {edu.degree} — {edu.institution} ({edu.id})", edu))
    for cert in store.certificates:
        options.append(("certificate", f"Certificado: {cert.title} ({cert.id})", cert))

    output_fn("  Onde ancorar esta evidencia em evidence/?")
    for idx, (_, label, _) in enumerate(options, start=1):
        output_fn(f"    [{idx}] {label}")
    output_fn("    [p] Criar novo projeto em evidence/projects/")
    output_fn("    [c] Criar novo certificado em evidence/certificates/")
    output_fn("    [u] Salvar temporariamente em evidence/unanchored.yaml")

    choice = input_fn("  > ").strip().lower()
    bullet_text = claim.bullets[0] if claim.bullets else claim.evidence.concrete[:MAX_BULLET_LEN]
    tags = sorted(set([claim.id] + claim.tags))

    if choice.isdigit() and 1 <= int(choice) <= len(options):
        kind, _, obj = options[int(choice) - 1]
        cap_lvl = MAX_LEVEL_BY_EVIDENCE_KIND[obj.evidence_kind]
        eff_lvl: Literal["nocoes", "pratica", "solido"] = (
            claim.level if LEVEL_RANK[claim.level] <= LEVEL_RANK[cap_lvl] else cap_lvl  # type: ignore[assignment]
        )
        if kind == "experience":
            updated_bullets = list(obj.bullets) + [BulletItem(text=bullet_text, skills=tags, core=False)]
            updated_levels = dict(obj.skill_levels)
            updated_levels[claim.id] = eff_lvl
            updated = obj.model_copy(update={"bullets": updated_bullets, "skill_levels": updated_levels})
            target_path = evidence_dir / "experiences" / f"{updated.id}.yaml"
            write_yaml_atomic(target_path, updated.model_dump(mode="python"))
            return load_evidence_store(evidence_dir), f"experiences/{updated.id}.yaml"

        if kind == "project":
            updated_bullets = list(obj.bullets) + [BulletItem(text=bullet_text, skills=tags, core=False)]
            updated_levels = dict(obj.skill_levels)
            updated_levels[claim.id] = eff_lvl
            updated = obj.model_copy(update={"bullets": updated_bullets, "skill_levels": updated_levels})
            target_path = evidence_dir / "projects" / f"{updated.id}.yaml"
            write_yaml_atomic(target_path, updated.model_dump(mode="python"))
            return load_evidence_store(evidence_dir), f"projects/{updated.id}.yaml"

        if kind == "education":
            updated_hl = list(obj.highlights) + [BulletItem(text=bullet_text, skills=tags, core=False)]
            updated_skills = sorted(set(obj.skills) | set(tags))
            updated_levels = dict(obj.skill_levels)
            updated_levels[claim.id] = eff_lvl
            updated = obj.model_copy(
                update={"highlights": updated_hl, "skills": updated_skills, "skill_levels": updated_levels}
            )
            target_path = evidence_dir / "education" / f"{updated.id}.yaml"
            write_yaml_atomic(target_path, updated.model_dump(mode="python"))
            return load_evidence_store(evidence_dir), f"education/{updated.id}.yaml"

        # certificate
        updated_skills = sorted(set(obj.skills) | set(tags))
        updated_levels = dict(obj.skill_levels)
        updated_levels[claim.id] = eff_lvl
        updated = obj.model_copy(update={"skills": updated_skills, "skill_levels": updated_levels})
        target_path = evidence_dir / "certificates" / f"{updated.id}.yaml"
        write_yaml_atomic(target_path, updated.model_dump(mode="python"))
        return load_evidence_store(evidence_dir), f"certificates/{updated.id}.yaml"

    if choice in ("p", "projeto", "project"):
        output_fn(f"  Nome do projeto [{claim.evidence.context}]:")
        proj_name = input_fn("  > ").strip() or claim.evidence.context
        proj_id = norm_slug(proj_name)[:36] or f"proj-{claim.id}"
        today_ym = date.today().strftime("%Y-%m")
        output_fn(f"  Data (YYYY-MM) [{today_ym}]:")
        proj_date = input_fn("  > ").strip() or today_ym
        cap_lvl = MAX_LEVEL_BY_EVIDENCE_KIND["projeto"]
        eff_lvl = claim.level if LEVEL_RANK[claim.level] <= LEVEL_RANK[cap_lvl] else cap_lvl  # type: ignore[assignment]
        proj = ProjectEvidence(
            id=proj_id,
            name=proj_name,
            date=proj_date,
            url=claim.evidence.artifact if _is_url(claim.evidence.artifact) else None,
            evidence_kind="projeto",
            artifacts=[claim.evidence.artifact] if claim.evidence.artifact and not _is_url(claim.evidence.artifact) else [],
            skill_levels={claim.id: eff_lvl},
            bullets=[BulletItem(text=bullet_text, skills=tags, core=True)],
        )
        target_path = evidence_dir / "projects" / f"{proj.id}.yaml"
        write_yaml_atomic(target_path, proj.model_dump(mode="python"))
        return load_evidence_store(evidence_dir), f"projects/{proj.id}.yaml"

    if choice in ("c", "cert", "certificado", "certificate"):
        output_fn(f"  Titulo do certificado [{claim.evidence.context}]:")
        cert_title = input_fn("  > ").strip() or claim.evidence.context
        output_fn("  Instituicao emissora:")
        cert_issuer = input_fn("  > ").strip() or "Instituicao"
        today_ym = date.today().strftime("%Y-%m")
        output_fn(f"  Data (YYYY-MM) [{today_ym}]:")
        cert_date = input_fn("  > ").strip() or today_ym
        cert_id = norm_slug(f"{cert_issuer}-{cert_title}")[:36] or f"cert-{claim.id}"
        cert_kind: Literal["estudo", "projeto", "producao_supervisionada", "producao_autonoma"] = (
            claim.evidence.kind
        )
        cert = CertificateEvidence(
            id=cert_id,
            title=cert_title,
            issuer=cert_issuer,
            date=cert_date,
            detail=bullet_text,
            url=claim.evidence.artifact if _is_url(claim.evidence.artifact) else None,
            artifact=claim.evidence.artifact if claim.evidence.artifact and not _is_url(claim.evidence.artifact) else None,
            evidence_kind=cert_kind,
            skills=tags,
            skill_levels={claim.id: claim.level},
        )
        target_path = evidence_dir / "certificates" / f"{cert.id}.yaml"
        write_yaml_atomic(target_path, cert.model_dump(mode="python"))
        return load_evidence_store(evidence_dir), f"certificates/{cert.id}.yaml"

    # Default / 'u': store in unanchored.yaml
    entry = UnanchoredEntry(
        id=claim.id,
        evidence_kind=claim.evidence.kind,
        level=claim.level,
        context=claim.evidence.context,
        concrete=claim.evidence.concrete,
        tags=tags,
        artifact=claim.evidence.artifact,
    )
    updated_unanchored = [u for u in store.unanchored if u.id != entry.id] + [entry]
    save_unanchored_entries(updated_unanchored, evidence_dir)
    return load_evidence_store(evidence_dir), "unanchored.yaml"


def run_grill_session(
    job_ref: str,
    *,
    skill_filter: str | None = None,
    recheck: bool = False,
    evidence_dir: Path | None = None,
    claims_path: Path | None = None,
    aliases_path: Path = ALIASES,
    jobs_dir: Path = JOBS_DIR,
    sessions_dir: Path = SESSIONS_DIR,
    backend: GrillBackend | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[InterviewSession, Path]:
    use_evidence_mode = claims_path is None
    eff_evidence_dir = evidence_dir or resolve_evidence_dir()

    slug, jd_text, _ = load_job(job_ref, jobs_dir=jobs_dir, auto_sync=use_evidence_mode)
    aliases = load_aliases(aliases_path)

    store: EvidenceStore | None = None
    if use_evidence_mode:
        store = load_evidence_store(eff_evidence_dir)
        claims = derive_claims_from_evidence(store)
    else:
        assert claims_path is not None
        claims = load_claims(claims_path)

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

    def _record_absent(skill_id: str, absent_claim: Claim) -> None:
        nonlocal claims, store, claims_modified
        if use_evidence_mode:
            assert store is not None
            updated_absent = [a for a in store.absent if a.id != skill_id] + [
                AbsentEntry(id=skill_id, updated_at=today, source_session=session_rel)
            ]
            save_absent_entries(updated_absent, eff_evidence_dir)
            store = load_evidence_store(eff_evidence_dir)
            claims = derive_claims_from_evidence(store)
        else:
            claims = upsert_claim(claims, absent_claim)
            claims_modified = True
        claims_by_id[skill_id] = absent_claim

    for skill_id in targets:
        existing = claims_by_id.get(skill_id)
        existing_level = existing.level if (existing and existing.status == "verified") else None
        snippets = extract_jd_snippets(jd_text, skill_id, aliases)

        output_fn(
            f"\n--- Skill: {skill_id} (atual: {existing.status if existing else 'novo'}"
            f"{f'/{existing_level}' if existing_level else ''}) ---"
        )
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
            _record_absent(skill_id, absent_claim)
            items.append(
                SessionItem(
                    skill_id=skill_id,
                    existing_level=existing_level,
                    qa=[],
                    proposal=proposal,
                    validator_result=val,
                    user_decision="absent",
                    target_evidence="absent.yaml" if use_evidence_mode else None,
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
                _record_absent(skill_id, final_absent)
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=ClaimProposal(action="mark_absent", claim=final_absent),
                        validator_result=val,
                        user_decision="absent",
                        target_evidence="absent.yaml" if use_evidence_mode else None,
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
                "  Confirmar gravacao? "
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
                target_ev: str | None = None
                if use_evidence_mode:
                    assert store is not None
                    store, target_ev = _persist_claim_to_evidence(
                        final_claim,
                        store,
                        eff_evidence_dir,
                        input_fn=input_fn,
                        output_fn=output_fn,
                    )
                    claims = derive_claims_from_evidence(store)
                    claims_by_id = {c.id: c for c in claims}
                    output_fn(f"  -> '{skill_id}' salvo em evidence/{target_ev}.")
                else:
                    claims = upsert_claim(claims, final_claim)
                    claims_by_id[skill_id] = final_claim
                    claims_modified = True
                    output_fn(f"  -> '{skill_id}' salvo em claims.")
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=ClaimProposal(action=proposal.action, claim=final_claim),
                        validator_result=val,
                        user_decision="edited" if edited_by_user else "accepted",
                        target_evidence=target_ev,
                    )
                )
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
                _record_absent(skill_id, absent_claim)
                items.append(
                    SessionItem(
                        skill_id=skill_id,
                        existing_level=existing_level,
                        qa=qa,
                        proposal=ClaimProposal(action="mark_absent", claim=absent_claim),
                        validator_result=validate_claim(absent_claim),
                        user_decision="absent",
                        target_evidence="absent.yaml" if use_evidence_mode else None,
                    )
                )
                output_fn(f"  -> '{skill_id}' marcado como absent.")
                break

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

    if not use_evidence_mode and claims_modified:
        assert claims_path is not None
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
# Composition, LaTeX Rendering & ATS Gate (v2 Single-Column, No Tables)
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


def _skill_matches_jd(skill_id: str, jd_skills: set[str], aliases: dict[str, list[str]]) -> bool:
    if skill_id in jd_skills:
        return True
    # Also match if any alias group covers this skill
    for jd_s in jd_skills:
        if skill_id in aliases.get(jd_s, []):
            return True
    return False


def _bullet_jd_score(bullet: BulletItem, jd_skills: set[str], aliases: dict[str, list[str]]) -> int:
    return sum(1 for s in bullet.skills if _skill_matches_jd(s, jd_skills, aliases))


def compose_experiencia(
    experiences: list[ExperienceEvidence],
    jd_skills: set[str],
    aliases: dict[str, list[str]],
    *,
    max_total_bullets: int = 10,
) -> str:
    """Render chronological experiences with verbatim bullets (faithful, never rewritten)."""
    if not experiences:
        return "Experiência profissional em construção."

    ordered_exps = sorted(
        experiences,
        key=lambda e: (1 if e.end is None else 0, e.start),
        reverse=True,
    )

    # First pass: keep all core bullets + JD-matching bullets; include extra non-core bullets while within budget
    selected_per_exp: list[tuple[ExperienceEvidence, list[BulletItem]]] = []
    must_keep_count = 0
    for exp in ordered_exps:
        scored = [
            (
                1 if b.core else 0,
                _bullet_jd_score(b, jd_skills, aliases),
                idx,
                b,
            )
            for idx, b in enumerate(exp.bullets)
        ]
        # Keep core bullets in original order, then JD-matching bullets, then remaining bullets
        core_bullets = [b for b in exp.bullets if b.core]
        non_core = [
            (score, idx, b)
            for is_core, score, idx, b in scored
            if not is_core
        ]
        non_core_matched = [b for score, _, b in sorted(non_core, key=lambda x: (-x[0], x[1])) if score > 0]
        non_core_other = [b for score, _, b in non_core if score == 0]
        must_keep_count += len(core_bullets) + len(non_core_matched)
        selected_per_exp.append((exp, core_bullets + non_core_matched + non_core_other))

    extra_budget = max(0, max_total_bullets - must_keep_count)

    blocks: list[str] = []
    for exp, candidates in selected_per_exp:
        kept: list[BulletItem] = []
        for b in candidates:
            if b.core or _bullet_jd_score(b, jd_skills, aliases) > 0:
                kept.append(b)
            elif extra_budget > 0:
                kept.append(b)
                extra_budget -= 1
        if not kept:
            kept = [exp.bullets[0]]

        left = f"{exp.role} — {exp.org}"
        if exp.location:
            left = f"{left} ({exp.location})"
        right = format_date_range(exp.start, exp.end)
        lines = [rf"\cvitemheader{{{escape_latex(left)}}}{{{escape_latex(right)}}}"]
        lines.append(r"\begin{cvlist}")
        for b in kept:
            lines.append(rf"  \item {escape_latex(b.text)}")
        lines.append(r"\end{cvlist}")
        blocks.append("\n".join(lines))

    return "\n".join(blocks)


def compose_formacao(
    education: list[EducationEvidence],
    jd_skills: set[str],
    aliases: dict[str, list[str]],
) -> str:
    if not education:
        return ""
    ordered = sorted(education, key=lambda e: e.start, reverse=True)
    blocks: list[str] = []
    for edu in ordered:
        left = f"{edu.degree} — {edu.institution}"
        if edu.location:
            left = f"{left} ({edu.location})"
        right = format_date_range(edu.start, edu.end)
        lines = [rf"\cvitemheader{{{escape_latex(left)}}}{{{escape_latex(right)}}}"]
        if edu.highlights:
            ordered_hl = sorted(
                edu.highlights,
                key=lambda h: (-_bullet_jd_score(h, jd_skills, aliases), 0),
            )
            lines.append(r"\begin{cvlist}")
            for h in ordered_hl:
                lines.append(rf"  \item {escape_latex(h.text)}")
            lines.append(r"\end{cvlist}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def select_projects_for_cv(
    projects: list[ProjectEvidence],
    experiences: list[ExperienceEvidence],
    jd_skills: set[str],
    aliases: dict[str, list[str]],
    *,
    mode: Literal["auto", "include", "exclude"] = "auto",
) -> list[ProjectEvidence]:
    if not projects or mode == "exclude":
        return []

    exp_skills: set[str] = set()
    for exp in experiences:
        exp_skills.update(exp.all_skills())

    def _proj_relevance(p: ProjectEvidence) -> tuple[int, int, str]:
        p_skills = p.all_skills()
        matched = {s for s in p_skills if _skill_matches_jd(s, jd_skills, aliases)}
        uncovered_by_exp = matched - exp_skills
        return (len(uncovered_by_exp), len(matched), p.date)

    ranked = sorted(projects, key=_proj_relevance, reverse=True)
    if mode == "include":
        return ranked

    # Auto mode: include only when a project adds JD skill coverage not already in Experiência
    # (or when the user has no experiences yet).
    if not experiences:
        return ranked[:2]
    needed = [p for p in ranked if _proj_relevance(p)[0] > 0]
    return needed[:2]


def compose_projetos_section(
    selected_projects: list[ProjectEvidence],
    jd_skills: set[str],
    aliases: dict[str, list[str]],
) -> str:
    if not selected_projects:
        return ""
    blocks: list[str] = [r"\cvsection{Projetos}"]
    for proj in selected_projects:
        left = f"{proj.name} — {proj.role}" if proj.role else proj.name
        right = format_ym(proj.date)
        lines = [rf"\cvitemheader{{{escape_latex(left)}}}{{{escape_latex(right)}}}"]
        if proj.url:
            raw_url = _normalize_url(proj.url)
            disp_url = escape_latex(_display_url(proj.url))
            lines.append(rf"{{\small \href{{{raw_url}}}{{{disp_url}}}}}\par\vspace{{-2pt}}")
        ordered_bullets = sorted(
            proj.bullets,
            key=lambda b: (0 if b.core else 1, -_bullet_jd_score(b, jd_skills, aliases)),
        )
        lines.append(r"\begin{cvlist}")
        for b in ordered_bullets:
            lines.append(rf"  \item {escape_latex(b.text)}")
        lines.append(r"\end{cvlist}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def compose_certificados_section(
    certificates: list[CertificateEvidence],
    jd_skills: set[str],
    aliases: dict[str, list[str]],
) -> str:
    if not certificates:
        return ""

    def _cert_rank(c: CertificateEvidence) -> tuple[int, str]:
        hits = sum(1 for s in c.all_skills() if _skill_matches_jd(s, jd_skills, aliases))
        return (hits, c.date)

    ordered = sorted(certificates, key=_cert_rank, reverse=True)
    lines = [r"\cvsection{Certificados}", r"\begin{cvlist}"]
    for cert in ordered:
        entry = rf"\textbf{{{escape_latex(cert.title)}}} — {escape_latex(cert.issuer)} ({escape_latex(format_ym(cert.date))})"
        if cert.detail:
            entry += f": {escape_latex(cert.detail)}"
        if cert.url:
            raw_url = _normalize_url(cert.url)
            disp_url = escape_latex(_display_url(cert.url))
            entry += rf" (\href{{{raw_url}}}{{{disp_url}}})"
        lines.append(rf"  \item {entry}.")
    lines.append(r"\end{cvlist}")
    return "\n".join(lines)


def compose_habilidades(
    claims: list[Claim],
    jd_skills: set[str],
    aliases: dict[str, list[str]],
) -> str:
    """Compose compact grouped Habilidades WITHOUT proficiency level labels."""
    verified_ids = [c.id for c in claims if c.status == "verified"]
    if not verified_ids:
        return "Habilidades técnicas em desenvolvimento."

    verified_set = set(verified_ids)
    assigned: set[str] = set()
    grouped: list[tuple[str, list[str]]] = []

    for group_name, members in SKILL_GROUPS:
        present = [sid for sid in members if sid in verified_set]
        if present:
            # Order JD-matched skills first within each group
            present_ordered = sorted(
                present,
                key=lambda s: (0 if _skill_matches_jd(s, jd_skills, aliases) else 1, members.index(s)),
            )
            grouped.append((group_name, present_ordered))
            assigned.update(present)

    leftovers = sorted(verified_set - assigned)
    if leftovers:
        leftovers_ordered = sorted(
            leftovers,
            key=lambda s: (0 if _skill_matches_jd(s, jd_skills, aliases) else 1, s),
        )
        grouped.append(("Outras Competências", leftovers_ordered))

    # Order groups so those with more JD hits appear first (keeping Idiomas last unless only match)
    def _group_rank(item: tuple[str, list[str]]) -> tuple[int, int]:
        gname, members = item
        hits = sum(1 for s in members if _skill_matches_jd(s, jd_skills, aliases))
        is_lang = 1 if gname == "Idiomas" else 0
        return (is_lang, -hits)

    grouped.sort(key=_group_rank)

    lines: list[str] = []
    for gname, members in grouped:
        labels = ", ".join(escape_latex(skill_display_name(s)) for s in members)
        lines.append(rf"\textbf{{{escape_latex(gname)}:}} {labels}.\par")
    return "\n".join(lines)


def compose_competencias(selected: list[Claim]) -> str:
    """Legacy helper retained for Wave 1 tests; renders clean skill summary without levels."""
    verified = [c for c in selected if c.status == "verified" and c.evidence is not None]
    if not verified:
        return "Habilidades gerais em desenvolvimento."
    return compose_habilidades(verified, set(), {})


def render(
    resumo: str,
    experiencia: str,
    formacao: str,
    habilidades: str,
    *,
    projetos_section: str = "",
    certificados_section: str = "",
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
    src = src.replace("%EXPERIENCIA%", experiencia)
    src = src.replace("%FORMACAO%", formacao)
    src = src.replace("%PROJETOS_SECTION%", projetos_section)
    src = src.replace("%CERTIFICADOS_SECTION%", certificados_section)
    src = src.replace("%HABILIDADES%", habilidades)
    tex_src = WORK / "cv.tex"
    tex_src.write_text(src, encoding="utf-8")
    out = subprocess.run([ENGINE, "-X", "compile", str(tex_src)], capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        sys.stderr.write(out.stdout + out.stderr)
        raise RuntimeError("LaTeX compilation failed")
    return WORK / "cv.pdf"


def ats_checks(pdf: Path, allow_two_pages: bool = False) -> list[str]:
    failures = []
    # Check both linear plain text (how most ATS parsers extract) and -layout mode.
    plain_txt = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
    layout_txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    for mode_label, txt in (("linear", plain_txt), ("layout", layout_txt)):
        if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", txt):
            failures.append(f"contact({mode_label}): email missing from extracted text")
        if "\ufffd" in txt:
            failures.append(f"accent({mode_label}): replacement char in extracted text (encoding garble)")
        ligatures = sorted({ch for ch in txt if 0xFB00 <= ord(ch) <= 0xFB06})
        if ligatures:
            failures.append(f"accent({mode_label}): ligature garble present: {''.join(ligatures)}")
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
    include_projects: Literal["auto", "include", "exclude"] | None = None,
    allow_two_pages: bool | None = None,
    out_path: Path | None = None,
    evidence_dir: Path | None = None,
    claims_path: Path | None = None,
    aliases_path: Path = ALIASES,
    jobs_dir: Path = JOBS_DIR,
    out_dir: Path = OUT_DIR,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    prof = load_profile(profile_path)
    aliases = load_aliases(aliases_path)
    use_evidence_mode = claims_path is None

    meta: JobMeta | None = None
    if job_ref:
        slug, jd_text, meta = load_job(job_ref, jobs_dir=jobs_dir, auto_sync=use_evidence_mode)
    else:
        slug, jd_text = "default", ""

    eff_role = role or (meta.role if meta and meta.role else prof.role)
    eff_resumo = resumo or (meta.resumo if meta and meta.resumo else prof.resumo)
    eff_categories = categories or (meta.category_filter if meta else None)
    eff_proj_mode = include_projects or (meta.include_projects if meta else "auto")
    eff_allow_2 = (
        allow_two_pages
        if allow_two_pages is not None
        else (meta.allow_2_pages if meta else False)
    )

    jd_skills = extract_jd_skills(jd_text, aliases)
    warnings: list[str] = []

    if use_evidence_mode:
        store = load_evidence_store(evidence_dir or resolve_evidence_dir(prof))
        val = validate_evidence_store(store)
        if not val.ok:
            raise ValueError(f"evidence validation failed: {val.errors}")
        warnings = val.warnings
        claims = derive_claims_from_evidence(store)
        selected = select_claims(jd_text, claims, aliases, categories=eff_categories)

        eff_exp_tex = (
            format_latex_block(experiencia)
            if experiencia
            else (
                format_latex_block(meta.experiencia)
                if meta and meta.experiencia
                else compose_experiencia(store.experiences, jd_skills, aliases)
            )
        )
        eff_form_tex = (
            format_latex_block(formacao)
            if formacao
            else (
                format_latex_block(meta.formacao)
                if meta and meta.formacao
                else compose_formacao(store.education, jd_skills, aliases)
            )
        )
        selected_projects = select_projects_for_cv(
            store.projects,
            store.experiences,
            jd_skills,
            aliases,
            mode=eff_proj_mode,
        )
        proj_tex = compose_projetos_section(selected_projects, jd_skills, aliases)
        cert_tex = compose_certificados_section(store.certificates, jd_skills, aliases)
        hab_claims = [
            c
            for c in claims
            if c.status == "verified" and (eff_categories is None or c.category in eff_categories)
        ]
        hab_tex = compose_habilidades(hab_claims, jd_skills, aliases)
    else:
        assert claims_path is not None
        claims = load_claims(claims_path)
        for claim in claims:
            res = validate_claim(claim)
            if not res.ok:
                raise ValueError(f"claim '{claim.id}' failed validation: {res.errors}")
        selected = select_claims(jd_text, claims, aliases, categories=eff_categories)
        eff_exp_tex = format_latex_block(
            experiencia or (meta.experiencia if meta and meta.experiencia else prof.experiencia)
        )
        eff_form_tex = format_latex_block(
            formacao or (meta.formacao if meta and meta.formacao else prof.formacao)
        )
        selected_projects = []
        proj_tex = ""
        cert_tex = ""
        hab_tex = compose_habilidades(selected or claims, jd_skills, aliases)

    report = compute_gap_report(jd_text, claims, aliases, job_slug=slug)
    built_pdf = render(
        eff_resumo,
        eff_exp_tex,
        eff_form_tex,
        hab_tex,
        projetos_section=proj_tex,
        certificados_section=cert_tex,
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
        "selected_projects": [p.id for p in selected_projects],
        "habilidades": hab_tex,
        "competencias": hab_tex,
        "pdf": final_pdf,
        "ats_failures": failures,
        "warnings": warnings,
        "report": report,
    }


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cv",
        description="cv-profiler v2: generate tailored, honest, ATS-friendly CVs from evidence/ + JDs.",
    )
    ap.add_argument("-V", "--version", action="version", version=f"cv-profiler {VERSION}")
    ap.add_argument("--debug", action="store_true", help="Show full Python tracebacks on errors")
    sub = ap.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser(
        "status",
        help="Auto-ingest loose JDs in jobs/, validate evidence/, and show overview",
    )
    p_status.add_argument("--json", action="store_true", dest="as_json", help="Print JSON summary")

    p_jobs = sub.add_parser("jobs", help="Auto-ingest loose JDs and list ingested jobs")
    p_jobs.add_argument("--json", action="store_true", dest="as_json", help="Print JSON summary")

    p_claims = sub.add_parser("claims", help="Validate evidence/ and list derived verified skills")
    p_claims.add_argument("--category", choices=CATEGORIES, default=None)
    p_claims.add_argument("--all", action="store_true", dest="include_absent")

    p_ingest = sub.add_parser("ingest", help="Manually ingest an external JD (.pdf or .txt) with custom metadata")
    p_ingest.add_argument("source", help="Path to a .pdf or .txt JD file")
    p_ingest.add_argument("--slug", default=None, help="Custom job slug (default: YYYY-MM_<filename>)")
    p_ingest.add_argument("--title", default=None)
    p_ingest.add_argument("--company", default=None)
    p_ingest.add_argument("--url", default=None)

    p_gap = sub.add_parser("gap", help="Check covered skills and gaps against a job slug (partial match supported)")
    p_gap.add_argument("job", help="Job slug (or partial slug, e.g. 'lanlink') or file path")
    p_gap.add_argument(
        "--discover-aliases",
        action="store_true",
        help="Use LLM (read-only) to suggest JD technical terms missing from aliases.yaml",
    )
    p_gap.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default=None)
    p_gap.add_argument("--model", default=None)
    p_gap.add_argument("--base-url", default=None)

    p_grill = sub.add_parser("grill", help="Run gap-driven interview and anchor verified bullets into evidence/")
    p_grill.add_argument("job", help="Job slug (or partial slug) or file path")
    p_grill.add_argument("--skill", default=None, help="Target a single skill id")
    p_grill.add_argument("--recheck", action="store_true", help="Include skills previously marked absent")
    p_grill.add_argument("--mode", choices=("auto", "llm", "manual"), default="auto")
    p_grill.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default=None)
    p_grill.add_argument("--model", default=None)
    p_grill.add_argument("--base-url", default=None)

    p_build = sub.add_parser("build", help="Compile tailored ATS-friendly CV -> out/<slug>.pdf")
    p_build.add_argument("job", nargs="?", default=None, help="Job slug (or partial slug) or file path")
    p_build.add_argument("--role", default=None)
    p_build.add_argument("--resumo", default=None)
    p_build.add_argument("--projects", dest="projects_mode", action="store_const", const="include", default=None)
    p_build.add_argument("--no-projects", dest="projects_mode", action="store_const", const="exclude")
    p_build.add_argument("--category", action="append", choices=CATEGORIES, default=None)
    p_build.add_argument("--allow-2-pages", action="store_true", default=None)
    p_build.add_argument("--out", default=None, help="Custom output PDF path")

    p_run = sub.add_parser("run", help="All-in-one: auto-ingest -> gap -> optional grill -> build CV")
    p_run.add_argument("job", help="Job slug (or partial slug) or file path")
    p_run.add_argument("--no-grill", action="store_true", help="Skip interactive grill even if gaps exist")
    p_run.add_argument("--mode", choices=("auto", "llm", "manual"), default="auto")
    p_run.add_argument("--projects", dest="projects_mode", action="store_const", const="include", default=None)
    p_run.add_argument("--no-projects", dest="projects_mode", action="store_const", const="exclude")
    p_run.add_argument("--allow-2-pages", action="store_true", default=None)
    p_run.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default=None)
    p_run.add_argument("--model", default=None)
    p_run.add_argument("--base-url", default=None)

    return ap


def _select_grill_backend(args: argparse.Namespace) -> GrillBackend | None:
    if args.mode in ("auto", "llm"):
        import llm_grill

        cfg = llm_grill.LLMConfig.from_env(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
        )
        if cfg is not None:
            return llm_grill.LLMGrillBackend.from_config(cfg)
        if args.mode == "llm":
            raise SystemExit(
                "Modo --mode llm exige OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "GEMINI_API_KEY ou CV_PROFILER_LLM_BASE_URL."
            )
    return None


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "status":
        newly_ingested = sync_jobs()
        ingested, _ = list_ingested_jobs(auto_sync=False)
        store = load_evidence_store()
        val = validate_evidence_store(store)
        if not val.ok:
            raise ValueError(f"evidence validation failed: {val.errors}")
        claims = derive_claims_from_evidence(store)
        verified_count = sum(1 for c in claims if c.status == "verified")
        absent_count = sum(1 for c in claims if c.status == "absent")

        if args.as_json:
            print(
                json.dumps(
                    {
                        "newly_ingested": [m.slug for _, m in newly_ingested],
                        "evidence": {
                            "root": store.root_dir,
                            "experiences": len(store.experiences),
                            "education": len(store.education),
                            "projects": len(store.projects),
                            "certificates": len(store.certificates),
                            "unanchored": len(store.unanchored),
                            "verified_skills": verified_count,
                            "absent_skills": absent_count,
                            "warnings": val.warnings,
                        },
                        "jobs": [
                            {**j, "built_cv": str(j["built_cv"]) if j["built_cv"] else None}
                            for j in ingested
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if newly_ingested:
            print(f"Auto-ingeridos ({len(newly_ingested)}):")
            for job_dir, meta in newly_ingested:
                print(f"  + {meta.slug}  ({meta.source_file} -> {job_dir})")

        print(
            f"Evidence ({store.root_dir}): "
            f"{len(store.experiences)} exp, {len(store.education)} formacao, "
            f"{len(store.projects)} projetos, {len(store.certificates)} certs | "
            f"{verified_count} skills verificadas, {absent_count} absent"
        )
        if val.warnings:
            print(f"Avisos ({len(val.warnings)}):")
            for w in val.warnings:
                print(f"  ! {w}")

        if not ingested:
            print("\nNenhuma vaga ingerida em jobs/ingested/. Solte um .pdf/.txt em jobs/ e rode 'cv status'.")
        else:
            print(f"\nVagas ingeridas ({len(ingested)}):")
            for j in ingested:
                label = " — ".join(x for x in (j["company"], j["title"]) if x)
                label_str = f" ({label})" if label else ""
                cv_tag = f" [CV: {j['built_cv'].name}]" if j["built_cv"] else " [sem CV]"
                print(f"  - {j['slug']}{label_str}{cv_tag}")
        return

    if args.command == "jobs":
        newly_ingested = sync_jobs()
        ingested, loose = list_ingested_jobs(auto_sync=False)
        if args.as_json:
            print(
                json.dumps(
                    {
                        "newly_ingested": [m.slug for _, m in newly_ingested],
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
        if newly_ingested:
            for _, meta in newly_ingested:
                print(f"auto-ingested: {meta.slug} (de {meta.source_file})")
        if not ingested:
            print("Nenhuma vaga ingerida. Solte um .pdf ou .txt em jobs/ e rode 'cv status'.")
        else:
            print(f"Vagas ingeridas ({len(ingested)}):")
            for j in ingested:
                label = " — ".join(x for x in (j["company"], j["title"]) if x) or "(sem metadados)"
                cv_tag = f" [CV: {j['built_cv'].name}]" if j["built_cv"] else " [sem CV]"
                print(f"  - {j['slug']}  ({label}){cv_tag}")
        return

    if args.command == "claims":
        store = load_evidence_store()
        val = validate_evidence_store(store)
        if not val.ok:
            raise ValueError(f"evidence validation failed: {val.errors}")
        claims = derive_claims_from_evidence(store)
        verified_count = sum(1 for c in claims if c.status == "verified")
        absent_count = sum(1 for c in claims if c.status == "absent")
        filtered = [
            c
            for c in claims
            if (args.category is None or c.category == args.category)
            and (args.include_absent or c.status == "verified")
        ]
        print(f"Claims derivados de {store.root_dir}: {verified_count} verified, {absent_count} absent")
        for c in filtered:
            if c.status == "absent":
                print(f"  - {c.id:<28} [absent]")
            else:
                assert c.evidence is not None and c.level is not None
                print(f"  - {c.id:<28} [{c.level:<7} | {c.evidence.kind:<23}] ({c.category})")
        if val.warnings:
            print(f"\nAvisos ({len(val.warnings)}):")
            for w in val.warnings:
                print(f"  ! {w}")
        return

    if args.command == "ingest":
        src = Path(args.source)
        job_dir, meta, text = ingest_job(
            src,
            args.slug,
            title=args.title,
            company=args.company,
            url=args.url,
            move_source=False,
        )
        store = load_evidence_store()
        claims = derive_claims_from_evidence(store)
        aliases = load_aliases()
        report = compute_gap_report(text, claims, aliases, job_slug=meta.slug)
        print(f"ingested: {job_dir}")
        print(f"slug: {meta.slug}")
        print(f"chars: {len(text)}")
        print(f"covered_skills: {report.covered if report.covered else 'NONE'}")
        print(f"actionable_gaps: {report.actionable_gaps if report.actionable_gaps else 'NONE'}")
        print(f"known_absent: {report.known_absent if report.known_absent else 'NONE'}")
        return

    if args.command == "gap":
        slug, jd_text, _ = load_job(args.job)
        store = load_evidence_store()
        claims = derive_claims_from_evidence(store)
        aliases = load_aliases()
        report = compute_gap_report(jd_text, claims, aliases, job_slug=slug)
        print(f"job: {report.job_slug}")
        print(f"covered_skills: {report.covered if report.covered else 'NONE'}")
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
        selected_backend = _select_grill_backend(args)
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
            categories=args.category,
            include_projects=args.projects_mode,
            allow_two_pages=args.allow_2_pages,
            out_path=Path(args.out) if args.out else None,
        )
        report: GapReport = res["report"]
        print(f"job: {res['slug']}")
        print(f"matched_skills: {len(res['selected'])}")
        print(f"projects_included: {res['selected_projects'] if res['selected_projects'] else 'NONE'}")
        print(f"pdf: {res['pdf']}")
        print(f"ats_failures: {res['ats_failures'] if res['ats_failures'] else 'NONE'}")
        print(f"jd_mentioned_uncovered: {report.gaps if report.gaps else 'NONE'}")
        print(f"known_absent: {report.known_absent if report.known_absent else 'NONE'}")
        return

    if args.command == "run":
        slug, jd_text, _ = load_job(args.job)
        store = load_evidence_store()
        claims = derive_claims_from_evidence(store)
        aliases = load_aliases()
        report = compute_gap_report(jd_text, claims, aliases, job_slug=slug)
        print(f"=== Run: {slug} ===")
        print(f"covered_skills: {report.covered if report.covered else 'NONE'}")
        print(f"actionable_gaps: {report.actionable_gaps if report.actionable_gaps else 'NONE'}")
        print(f"known_absent: {report.known_absent if report.known_absent else 'NONE'}")

        if report.actionable_gaps and not args.no_grill:
            ans = input("Deseja rodar grill para os gaps acionaveis antes de gerar o CV? [s/N]: ").strip().lower()
            if ans in ("s", "sim", "y", "yes"):
                selected_backend = _select_grill_backend(args)
                run_grill_session(slug, backend=selected_backend)

        res = build_cv(
            slug,
            include_projects=args.projects_mode,
            allow_two_pages=args.allow_2_pages,
        )
        print(f"\npdf: {res['pdf']}")
        print(f"ats_failures: {res['ats_failures'] if res['ats_failures'] else 'NONE'}")
        return


def main(argv: list[str] | None = None) -> None:
    args_list = list(sys.argv[1:] if argv is None else argv)
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
