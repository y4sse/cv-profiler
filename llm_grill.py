"""Structured LLM Questioner/Synthesizer backend with strict permissions boundary (Wave 3)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable, Literal
import urllib.error
import urllib.request

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import cv_profiler as cp

ProviderName = Literal["openai", "anthropic", "gemini"]

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-2.5-flash",
}

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """Raised when an LLM transport or structured response fails."""


# ---------------------------------------------------------------------------
# Strict Structured Output Schemas (extra="forbid")
# ---------------------------------------------------------------------------


class QuestionerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[str] = Field(..., min_length=1, max_length=3)

    @field_validator("questions")
    @classmethod
    def _clean_questions(cls, questions: list[str]) -> list[str]:
        cleaned = [q.strip() for q in questions if q and q.strip()]
        if not (1 <= len(cleaned) <= 3):
            raise ValueError("questioner must return 1 to 3 non-empty questions")
        return cleaned


class SynthesizerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "upgrade", "enrich", "mark_absent"]
    category: Literal["profissional", "academico", "voluntariado"]
    level: Literal["nocoes", "pratica", "solido"] | None = None
    evidence: cp.Evidence | None = None
    tags: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    rationale: str = Field(..., min_length=5)


class AliasCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    synonyms: list[str] = Field(..., min_length=1)
    jd_quote: str = Field(..., min_length=3)


class AliasDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[AliasCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider-Agnostic HTTP Transport (stdlib urllib only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMConfig:
    provider: ProviderName
    model: str
    api_key: str
    base_url: str | None = None
    timeout_s: int = 45

    @classmethod
    def from_env(
        cls,
        *,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_s: int = 45,
    ) -> "LLMConfig | None":
        eff_base = base_url or os.environ.get("CV_PROFILER_LLM_BASE_URL")
        eff_provider = (provider or os.environ.get("CV_PROFILER_LLM_PROVIDER") or "").strip().lower()

        if not eff_provider:
            if eff_base or os.environ.get("OPENAI_API_KEY"):
                eff_provider = "openai"
            elif os.environ.get("ANTHROPIC_API_KEY"):
                eff_provider = "anthropic"
            elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                eff_provider = "gemini"
            else:
                return None

        if eff_provider not in ("openai", "anthropic", "gemini"):
            raise LLMError(f"unsupported LLM provider: {eff_provider!r}")

        if eff_provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY") or ("local" if eff_base else "")
        elif eff_provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
        else:
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

        if not api_key:
            return None

        eff_model = (
            model
            or os.environ.get("CV_PROFILER_LLM_MODEL")
            or DEFAULT_MODELS[eff_provider]
        )
        return cls(
            provider=eff_provider,  # type: ignore[arg-type]
            model=eff_model,
            api_key=api_key,
            base_url=eff_base,
            timeout_s=timeout_s,
        )


def parse_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    m = _CODE_FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Fallback: extract outermost JSON object if model wrapped prose around it.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise LLMError(f"invalid JSON from LLM: {exc}") from exc
        else:
            raise LLMError(f"invalid JSON from LLM: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"expected JSON object from LLM, got {type(parsed).__name__}")
    return parsed


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM network error: {exc.reason}") from exc
    return parse_json_object(raw)


class HTTPJSONTransport:
    """Callable transport executing structured JSON completions via HTTP."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = self.config
        schema_note = (
            "\nResponda APENAS com um objeto JSON valido aderente ao JSON Schema:\n"
            + json.dumps(json_schema, ensure_ascii=False)
        )
        full_system = system_prompt + schema_note

        if cfg.provider == "openai":
            base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            payload = {
                "model": cfg.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user_prompt},
                ],
            }
            data = _http_post_json(
                url,
                payload,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                timeout_s=cfg.timeout_s,
            )
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"unexpected OpenAI response shape: {data}") from exc
            return parse_json_object(content)

        if cfg.provider == "anthropic":
            base = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
            url = f"{base}/v1/messages"
            payload = {
                "model": cfg.model,
                "max_tokens": 1024,
                "temperature": 0.1,
                "system": full_system,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            data = _http_post_json(
                url,
                payload,
                headers={
                    "x-api-key": cfg.api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout_s=cfg.timeout_s,
            )
            try:
                text_parts = [
                    blk["text"]
                    for blk in data["content"]
                    if isinstance(blk, dict) and blk.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            except (KeyError, TypeError) as exc:
                raise LLMError(f"unexpected Anthropic response shape: {data}") from exc
            return parse_json_object(content)

        # Gemini REST API
        base = (cfg.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = f"{base}/models/{cfg.model}:generateContent?key={cfg.api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": full_system}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        data = _http_post_json(url, payload, headers={}, timeout_s=cfg.timeout_s)
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected Gemini response shape: {data}") from exc
        return parse_json_object(content)


JSONTransport = Callable[[str, str, dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# Permissions Boundary: LLM Output -> Deterministic ClaimProposal
# ---------------------------------------------------------------------------


def enforce_proposal_boundary(
    skill_id: str,
    raw_output: dict[str, Any],
    *,
    qa_answers: list[str],
    existing_claim: cp.Claim | None = None,
) -> tuple[cp.ClaimProposal | None, cp.ValidationResult, str | None]:
    """
    Convert untrusted LLM JSON into a validated ClaimProposal.

    Security & honesty invariants enforced deterministically:
    - LLM cannot set or override `id`, `status`, `updated_at`, or `source_session`.
    - `action` must match existing_claim state (`create` only for new/absent;
      `upgrade`/`enrich` only for existing verified claims).
    - Full `validate_claim()` honesty gate runs (schema, MAX_LEVEL_BY_EVIDENCE_KIND,
      buzzwords, solido-only oversell, bullet length, ungrounded metrics, upgrade guard).
    - Never writes to disk; returns `(proposal, validation_result, rationale)`.
    """
    try:
        synth = SynthesizerOutput.model_validate(raw_output)
    except ValidationError as exc:
        errors = [
            f"llm_schema:{'.'.join(str(p) for p in e['loc']) or 'output'}: {e['msg']}"
            for e in exc.errors()
        ]
        return None, cp.ValidationResult(ok=False, errors=errors), None

    is_existing_verified = existing_claim is not None and existing_claim.status == "verified"
    action = synth.action
    boundary_errors: list[str] = []

    if action == "create" and is_existing_verified:
        boundary_errors.append(
            "boundary:action: cannot 'create' for already verified claim (use 'upgrade' or 'enrich')"
        )
    if action in ("upgrade", "enrich") and not is_existing_verified:
        boundary_errors.append(
            f"boundary:action: cannot '{action}' when no verified claim exists for '{skill_id}'"
        )

    existing_tags = existing_claim.tags if existing_claim else []
    merged_tags = sorted({cp.norm(t) for t in ([skill_id] + existing_tags + synth.tags) if cp.norm(t)})

    if action == "mark_absent":
        raw_claim: dict[str, Any] = {
            "id": skill_id,  # locked deterministically
            "status": "absent",
            "category": existing_claim.category if existing_claim else synth.category,
            "level": None,
            "evidence": None,
            "tags": sorted({cp.norm(t) for t in ([skill_id] + existing_tags) if cp.norm(t)}),
            "bullets": [],
        }
    else:
        raw_claim = {
            "id": skill_id,  # locked deterministically
            "status": "verified",
            "category": synth.category,
            "level": synth.level,
            "evidence": synth.evidence.model_dump(mode="python") if synth.evidence else None,
            "tags": merged_tags,
            "bullets": synth.bullets,
        }

    val = cp.validate_claim(
        raw_claim,
        qa_answers=qa_answers,
        existing_claim=existing_claim,
    )
    if boundary_errors:
        val = cp.ValidationResult(
            ok=False,
            errors=boundary_errors + val.errors,
            warnings=val.warnings,
        )

    if not val.ok:
        return None, val, synth.rationale

    claim = cp.Claim.model_validate(raw_claim)
    if action == "upgrade" and existing_claim is not None and existing_claim.level is not None:
        assert claim.level is not None
        if cp.LEVEL_RANK[claim.level] <= cp.LEVEL_RANK[existing_claim.level]:
            val = cp.ValidationResult(
                ok=False,
                errors=[
                    f"boundary:upgrade: proposed level '{claim.level}' is not higher "
                    f"than existing '{existing_claim.level}'"
                ],
                warnings=val.warnings,
            )
            return None, val, synth.rationale

    return cp.ClaimProposal(action=action, claim=claim), val, synth.rationale


# ---------------------------------------------------------------------------
# Prompts & LLMGrillBackend
# ---------------------------------------------------------------------------

QUESTIONER_SYSTEM_PROMPT = (
    "Voce e um entrevistador tecnico rigoroso para construcao de curriculo honesto (PT-BR). "
    "Objetivo: verificar experiencia REAL do candidato sem incentivar exagero ou buzzwords. "
    "Gere de 2 a 3 perguntas curtas, objetivas e factuais em PT-BR cobrindo: "
    "(1) onde/quando usou a habilidade na pratica, "
    "(2) qual tarefa concreta ou entregavel executou pessoalmente, "
    "(3) qual o limite real da experiencia (o que NAO faz ou nunca operou sozinho)."
)

SYNTHESIZER_SYSTEM_PROMPT = (
    "Voce sintetiza respostas de entrevista em um rascunho de Claim honesto para curriculo ATS (PT-BR). "
    "REGRAS ESTRITAS (violacao causa rejeicao automatica):\n"
    "1. NUNCA invente empresas, projetos, tarefas ou numeros/percentuais que nao estejam nas respostas.\n"
    "2. Classifique evidence.kind entre: estudo, projeto, producao_supervisionada, producao_autonoma.\n"
    "3. Respeite o teto de nivel por tipo de evidencia:\n"
    "   - estudo -> max 'nocoes'\n"
    "   - projeto -> max 'pratica'\n"
    "   - producao_supervisionada -> max 'pratica'\n"
    "   - producao_autonoma -> max 'solido'\n"
    "4. Proibido usar buzzwords de LinkedIn (proativo, dinamico, visao 360, apaixonado, etc.) "
    "e termos como 'especialista'/'arquiteto'/'senior' se level != 'solido'.\n"
    "5. Escreva 1 ou 2 bullets objetivos em PT-BR, cada um com no maximo 140 caracteres.\n"
    "6. Se as respostas mostrarem ausencia de experiencia real, retorne action='mark_absent', "
    "level=null, evidence=null e bullets=[]."
)

ALIAS_DISCOVERY_SYSTEM_PROMPT = (
    "Extraia habilidades tecnicas ou ferramentas explicitamente exigidas ou desejadas "
    "na descricao de vaga (PT-BR/EN) que ainda NAO estejam na lista de aliases conhecidos. "
    "Nao inclua soft skills genericas nem beneficios. "
    "Cada slug deve ser snake_case ASCII (ex: powershell, glpi, intune)."
)


class LLMGrillBackend:
    """
    Structured LLM backend implementing `cv_profiler.GrillBackend`.

    - Questioner: generates 2-3 targeted factual questions from JD snippets + existing claim.
    - Synthesizer: maps Q&A answers to `SynthesizerOutput`, enforces the deterministic
      permissions boundary (`enforce_proposal_boundary`), and allows at most
      `max_repair_attempts` (default 1) repair retry when the deterministic honesty gate
      rejects an oversell/schema violation.
    - Never writes to disk; persistence happens solely in `run_grill_session()` after
      explicit user confirmation.
    """

    def __init__(
        self,
        transport: JSONTransport,
        *,
        name: str = "llm",
        max_repair_attempts: int = 1,
    ) -> None:
        self.transport = transport
        self.name = name
        self.max_repair_attempts = max(0, max_repair_attempts)
        self.last_validation: cp.ValidationResult | None = None
        self.last_rationale: str | None = None

    @classmethod
    def from_config(cls, config: LLMConfig, *, max_repair_attempts: int = 1) -> "LLMGrillBackend":
        return cls(
            HTTPJSONTransport(config),
            name=f"llm:{config.provider}/{config.model}",
            max_repair_attempts=max_repair_attempts,
        )

    def generate_questions(
        self,
        skill_id: str,
        jd_snippets: list[str],
        existing_claim: cp.Claim | None,
    ) -> list[str]:
        existing_summary = (
            existing_claim.model_dump(mode="python") if existing_claim is not None else None
        )
        user_prompt = json.dumps(
            {
                "skill_id": skill_id,
                "jd_snippets": jd_snippets,
                "existing_claim": existing_summary,
            },
            ensure_ascii=False,
        )
        raw = self.transport(
            QUESTIONER_SYSTEM_PROMPT,
            user_prompt,
            QuestionerOutput.model_json_schema(),
        )
        parsed = QuestionerOutput.model_validate(raw)
        return parsed.questions

    def build_proposal(
        self,
        skill_id: str,
        existing_claim: cp.Claim | None,
        qa: list[cp.QAItem],
        input_fn: Callable[[str], str],
        output_fn: Callable[[str], None],
    ) -> cp.ClaimProposal | None:
        self.last_validation = None
        self.last_rationale = None

        if not qa:
            self.last_validation = cp.ValidationResult(
                ok=False,
                errors=["qa: cannot synthesize claim without user answers"],
            )
            return None

        qa_answers = [item.a for item in qa]
        base_payload: dict[str, Any] = {
            "skill_id": skill_id,
            "existing_claim": (
                existing_claim.model_dump(mode="python") if existing_claim is not None else None
            ),
            "qa": [item.model_dump(mode="python") for item in qa],
        }
        schema = SynthesizerOutput.model_json_schema()

        attempts = 1 + self.max_repair_attempts
        feedback_errors: list[str] = []

        for attempt in range(attempts):
            payload = dict(base_payload)
            if feedback_errors:
                payload["previous_rejection_errors"] = feedback_errors
                payload["repair_instruction"] = (
                    "Corrija APENAS os erros listados acima sem inventar fatos novos "
                    "(reduza o nivel para respeitar MAX_LEVEL_BY_EVIDENCE_KIND, remova "
                    "metricas nao citadas pelo usuario ou retorne mark_absent)."
                )
            try:
                raw = self.transport(
                    SYNTHESIZER_SYSTEM_PROMPT,
                    json.dumps(payload, ensure_ascii=False),
                    schema,
                )
            except LLMError as exc:
                self.last_validation = cp.ValidationResult(
                    ok=False,
                    errors=[f"llm_transport: {exc}"],
                )
                output_fn(f"  [ERRO LLM] {exc}")
                return None

            proposal, val, rationale = enforce_proposal_boundary(
                skill_id,
                raw,
                qa_answers=qa_answers,
                existing_claim=existing_claim,
            )
            self.last_validation = val
            self.last_rationale = rationale

            if val.ok and proposal is not None:
                if rationale:
                    output_fn(f"  [LLM rationale] {rationale}")
                return proposal

            feedback_errors = list(val.errors)
            if attempt + 1 < attempts:
                output_fn(
                    f"  [GATE] Proposta LLM rejeitada ({len(val.errors)} erro(s)); "
                    "solicitando correcao estrita ao LLM..."
                )
            else:
                for err in val.errors:
                    output_fn(f"  [REJEITADO PELO GATE] {err}")

        return None


def discover_unmapped_aliases(
    jd_text: str,
    existing_aliases: dict[str, list[str]],
    transport: JSONTransport,
) -> list[AliasCandidate]:
    """Read-only helper proposing technical JD terms missing from aliases.yaml."""
    payload = {
        "known_alias_slugs": sorted(existing_aliases.keys()),
        "jd_text": jd_text[:6000],
    }
    raw = transport(
        ALIAS_DISCOVERY_SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False),
        AliasDiscoveryOutput.model_json_schema(),
    )
    parsed = AliasDiscoveryOutput.model_validate(raw)
    known = set(existing_aliases.keys())
    return [c for c in parsed.candidates if c.slug not in known]
