"""Unit and integration tests for Wave 3 (structured LLM Questioner/Synthesizer + permissions boundary)."""

import os
from pathlib import Path
import tempfile
import unittest
import yaml

import cv_profiler as cp
import llm_grill as lg


class FakeTransport:
    """Deterministic stub transport returning queued JSON objects."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, system_prompt: str, user_prompt: str, json_schema: dict) -> dict:
        self.calls.append((system_prompt, user_prompt, json_schema))
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class TestPermissionsBoundary(unittest.TestCase):
    def test_locks_id_and_status_deterministically(self):
        # Even if LLM omits or tries to inject extra fields, extra="forbid" rejects unknown keys;
        # valid synthesizer output always gets id locked to target skill_id.
        raw = {
            "action": "create",
            "category": "profissional",
            "level": "pratica",
            "evidence": {
                "kind": "producao_supervisionada",
                "context": "Empresa Exemplo - Estagio TI",
                "concrete": "Consultas SELECT com WHERE e JOIN para conferencia de registros",
                "artifact": None,
            },
            "tags": ["sql"],
            "bullets": ["Consultas SQL (SELECT, WHERE, JOIN) para conferencia de registros."],
            "rationale": "Candidato executou consultas em ambiente supervisionado.",
        }
        proposal, val, rationale = lg.enforce_proposal_boundary(
            "sql",
            raw,
            qa_answers=["Usei SQL na empresa com SELECT, WHERE e JOIN para conferir registros."],
        )
        self.assertTrue(val.ok, val.errors)
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.claim.id, "sql")
        self.assertEqual(proposal.claim.status, "verified")
        self.assertIsNone(proposal.claim.source_session)
        self.assertIn("supervisionado", rationale or "")

    def test_rejects_injected_extra_field_like_id_hijack(self):
        raw = {
            "id": "sap",  # LLM attempting to hijack another claim id
            "action": "create",
            "category": "profissional",
            "level": "nocoes",
            "evidence": {
                "kind": "estudo",
                "context": "Universidade Exemplo",
                "concrete": "Aulas praticas de consultas SQL basicas em laboratorio",
                "artifact": None,
            },
            "tags": ["sql"],
            "bullets": ["Conhecimentos basicos em SQL."],
            "rationale": "Uso academico.",
        }
        proposal, val, _ = lg.enforce_proposal_boundary(
            "sql",
            raw,
            qa_answers=["Estudei SQL na universidade em laboratorio."],
        )
        self.assertFalse(val.ok)
        self.assertIsNone(proposal)
        self.assertTrue(any("llm_schema" in e for e in val.errors))

    def test_rejects_create_on_existing_verified_and_upgrade_on_new(self):
        existing = cp.Claim.model_validate(
            {
                "id": "sql",
                "status": "verified",
                "category": "profissional",
                "level": "nocoes",
                "evidence": {
                    "kind": "estudo",
                    "context": "Graduacao Exemplo",
                    "concrete": "Disciplina de banco de dados com consultas SELECT simples",
                },
                "tags": ["sql"],
                "bullets": ["Conhecimentos basicos de SQL."],
            }
        )
        raw_create = {
            "action": "create",
            "category": "profissional",
            "level": "pratica",
            "evidence": {
                "kind": "producao_supervisionada",
                "context": "Estagio Empresa Exemplo",
                "concrete": "Consultas SELECT com JOIN para auditoria de chamados",
                "artifact": None,
            },
            "tags": ["sql"],
            "bullets": ["Consultas SQL com JOIN em auditoria de chamados."],
            "rationale": "Nova evidencia pratica.",
        }
        prop1, val1, _ = lg.enforce_proposal_boundary(
            "sql",
            raw_create,
            qa_answers=["Na empresa fiz SELECT com JOIN para auditar chamados."],
            existing_claim=existing,
        )
        self.assertFalse(val1.ok)
        self.assertIsNone(prop1)
        self.assertTrue(any("boundary:action" in e for e in val1.errors))

        raw_upgrade = dict(raw_create, action="upgrade")
        prop2, val2, _ = lg.enforce_proposal_boundary(
            "sql",
            raw_upgrade,
            qa_answers=["Na empresa fiz SELECT com JOIN para auditar chamados."],
            existing_claim=None,
        )
        self.assertFalse(val2.ok)
        self.assertIsNone(prop2)
        self.assertTrue(any("boundary:action" in e for e in val2.errors))


class TestLLMGrillBackendAndRepairGate(unittest.TestCase):
    def test_repair_retry_fixes_oversell_and_requires_explicit_user_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            sessions_dir = root / "sessions"
            claims_path = root / "claims.yaml"
            cp.save_claims([], path=claims_path)

            jd_file = root / "jd.txt"
            jd_file.write_text("Requisitos: SQL para consultas e Linux.\n", encoding="utf-8")
            cp.ingest_job(jd_file, "2026-11_llm_vaga", jobs_dir=jobs_dir)

            # Gaps: ['linux', 'sql']
            # 1) linux: LLM asks 2 questions -> proposes valid claim -> user presses Enter (blank)
            #    -> DEFAULT-DENY: must NOT persist to claims.yaml!
            # 2) sql: LLM asks 2 questions -> first proposal hallucinates '40%' + oversells 'solido' on 'estudo'
            #    -> deterministic gate rejects & triggers 1 repair retry -> repaired 'nocoes' passes gate
            #    -> user approves with 'a' -> ONLY 'sql' persisted to claims.yaml!
            transport = FakeTransport(
                [
                    # linux Questioner
                    {
                        "questions": [
                            "Onde voce usou Linux na pratica?",
                            "Qual tarefa concreta executou em Linux?",
                        ]
                    },
                    # linux Synthesizer (valid)
                    {
                        "action": "create",
                        "category": "academico",
                        "level": "nocoes",
                        "evidence": {
                            "kind": "estudo",
                            "context": "Laboratorio Universidade",
                            "concrete": "Uso de terminal Linux basico e navegacao de arquivos em aula",
                            "artifact": None,
                        },
                        "tags": ["linux"],
                        "bullets": ["Conhecimentos basicos em terminal Linux para tarefas academicas."],
                        "rationale": "Exposicao academica basica.",
                    },
                    # sql Questioner
                    {
                        "questions": [
                            "Onde voce usou SQL?",
                            "Quais consultas escreveu sem ajuda?",
                        ]
                    },
                    # sql Synthesizer attempt 1 (INVALID: study+solido + ungrounded 40%)
                    {
                        "action": "create",
                        "category": "academico",
                        "level": "solido",
                        "evidence": {
                            "kind": "estudo",
                            "context": "Graduacao Exemplo",
                            "concrete": "Consultas SELECT simples em disciplina de banco de dados",
                            "artifact": None,
                        },
                        "tags": ["sql"],
                        "bullets": ["Otimizou consultas SQL reduzindo latencia em 40%."],
                        "rationale": "Tentativa exagerada.",
                    },
                    # sql Synthesizer attempt 2 (REPAIRED: estudo + nocoes, grounded)
                    {
                        "action": "create",
                        "category": "academico",
                        "level": "nocoes",
                        "evidence": {
                            "kind": "estudo",
                            "context": "Graduacao Exemplo",
                            "concrete": "Consultas SELECT e WHERE em disciplina de banco de dados",
                            "artifact": None,
                        },
                        "tags": ["sql"],
                        "bullets": ["Conhecimentos basicos em SQL (SELECT, WHERE) em atividades academicas."],
                        "rationale": "Corrigido para nocoes conforme evidencia de estudo.",
                    },
                ]
            )
            backend = lg.LLMGrillBackend(transport, name="llm:test/stub", max_repair_attempts=1)

            answers = iter(
                [
                    # linux: yes -> 2 answers -> blank Enter on confirmation (default-deny!)
                    "s",
                    "Usei Linux no laboratorio da universidade.",
                    "Apenas comandos basicos de terminal e arquivos.",
                    "",
                    # sql: yes -> 2 answers -> accept repaired proposal
                    "s",
                    "Na graduacao em banco de dados.",
                    "Fiz consultas SELECT e WHERE simples.",
                    "a",
                ]
            )
            logs: list[str] = []
            session, session_path = cp.run_grill_session(
                "2026-11_llm_vaga",
                claims_path=claims_path,
                jobs_dir=jobs_dir,
                sessions_dir=sessions_dir,
                backend=backend,
                input_fn=lambda _p: next(answers),
                output_fn=logs.append,
            )

            self.assertEqual(session.backend, "llm:test/stub")
            self.assertTrue(session_path.exists())

            # Verify repair prompt actually carried deterministic rejection errors
            repair_call_payload = yaml.safe_load(transport.calls[-1][1])
            self.assertIn("previous_rejection_errors", repair_call_payload)

            # Verify permissions boundary: linux rejected by default-deny, sql accepted
            decisions = {it.skill_id: it.user_decision for it in session.items}
            self.assertEqual(decisions, {"linux": "rejected", "sql": "accepted"})

            reloaded = cp.load_claims(claims_path)
            self.assertEqual([c.id for c in reloaded], ["sql"])
            self.assertEqual(reloaded[0].level, "nocoes")
            self.assertEqual(reloaded[0].source_session, f"sessions/{session_path.name}")

    def test_hard_rejects_after_failed_repair_and_never_persists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            sessions_dir = root / "sessions"
            claims_path = root / "claims.yaml"
            cp.save_claims([], path=claims_path)

            jd_file = root / "jd.txt"
            jd_file.write_text("Requisitos: SQL.\n", encoding="utf-8")
            cp.ingest_job(jd_file, "2026-11_hard_reject", jobs_dir=jobs_dir)

            bad_proposal = {
                "action": "create",
                "category": "profissional",
                "level": "pratica",
                "evidence": {
                    "kind": "producao_supervisionada",
                    "context": "Estagio TI",
                    "concrete": "Profissional proativo com visao 360 em SQL",
                    "artifact": None,
                },
                "tags": ["sql"],
                "bullets": ["Especialista proativo em SQL."],
                "rationale": "Buzzwords.",
            }
            transport = FakeTransport(
                [
                    {"questions": ["Onde voce usou SQL?"]},
                    bad_proposal,
                    bad_proposal,  # still bad on retry -> hard reject
                ]
            )
            backend = lg.LLMGrillBackend(transport, name="llm:test/bad", max_repair_attempts=1)
            answers = iter(["s", "Usei SQL no estagio para consultas basicas."])
            logs: list[str] = []

            session, _ = cp.run_grill_session(
                "2026-11_hard_reject",
                claims_path=claims_path,
                jobs_dir=jobs_dir,
                sessions_dir=sessions_dir,
                backend=backend,
                input_fn=lambda _p: next(answers),
                output_fn=logs.append,
            )

            self.assertEqual(session.items[0].user_decision, "rejected")
            self.assertIsNotNone(session.items[0].validator_result)
            assert session.items[0].validator_result is not None
            self.assertFalse(session.items[0].validator_result.ok)
            self.assertEqual(cp.load_claims(claims_path), [])

    def test_user_can_downgrade_llm_proposal_before_saving(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            sessions_dir = root / "sessions"
            claims_path = root / "claims.yaml"
            cp.save_claims([], path=claims_path)

            jd_file = root / "jd.txt"
            jd_file.write_text("Requisitos: SQL.\n", encoding="utf-8")
            cp.ingest_job(jd_file, "2026-11_downgrade", jobs_dir=jobs_dir)

            transport = FakeTransport(
                [
                    {"questions": ["Onde voce usou SQL?"]},
                    {
                        "action": "create",
                        "category": "profissional",
                        "level": "pratica",
                        "evidence": {
                            "kind": "producao_supervisionada",
                            "context": "Empresa Exemplo",
                            "concrete": "Consultas SELECT simples para conferir chamados no estagio",
                            "artifact": None,
                        },
                        "tags": ["sql"],
                        "bullets": ["Consultas SQL basicas para verificacao de chamados."],
                        "rationale": "Uso supervisionado.",
                    },
                ]
            )
            backend = lg.LLMGrillBackend(transport, name="llm:test/down")
            # User answers -> hits 'd' (downgrade pratica -> nocoes) -> then 'a' (accept)
            answers = iter(["s", "Fiz poucas consultas SELECT na empresa.", "d", "a"])
            logs: list[str] = []

            session, _ = cp.run_grill_session(
                "2026-11_downgrade",
                claims_path=claims_path,
                jobs_dir=jobs_dir,
                sessions_dir=sessions_dir,
                backend=backend,
                input_fn=lambda _p: next(answers),
                output_fn=logs.append,
            )

            self.assertEqual(session.items[0].user_decision, "edited")
            reloaded = cp.load_claims(claims_path)
            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].level, "nocoes")


class TestConfigParsingAndAliasDiscovery(unittest.TestCase):
    def test_parse_json_object_handles_fences_and_rejects_garbage(self):
        obj = lg.parse_json_object('```json\n{"questions": ["Q1?"]}\n```')
        self.assertEqual(obj, {"questions": ["Q1?"]})
        with self.assertRaises(lg.LLMError):
            lg.parse_json_object("not json at all")

    def test_discover_unmapped_aliases_filters_known(self):
        transport = FakeTransport(
            [
                {
                    "candidates": [
                        {
                            "slug": "powershell",
                            "synonyms": ["powershell", "ps1"],
                            "jd_quote": "Diferencial: Conhecimentos em Powershell",
                        },
                        {
                            "slug": "itil",
                            "synonyms": ["itil"],
                            "jd_quote": "ITIL v3/v4",
                        },
                    ]
                }
            ]
        )
        aliases = cp.load_aliases()
        cands = lg.discover_unmapped_aliases("ITIL e Powershell", aliases, transport)
        self.assertEqual([c.slug for c in cands], ["powershell"])

    def test_llm_config_from_env_auto_detection(self):
        saved = {
            k: os.environ.pop(k, None)
            for k in (
                "CV_PROFILER_LLM_PROVIDER",
                "CV_PROFILER_LLM_MODEL",
                "CV_PROFILER_LLM_BASE_URL",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
            )
        }
        try:
            self.assertIsNone(lg.LLMConfig.from_env())
            os.environ["GEMINI_API_KEY"] = "test-key"
            cfg = lg.LLMConfig.from_env()
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.provider, "gemini")
            self.assertEqual(cfg.model, lg.DEFAULT_MODELS["gemini"])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
