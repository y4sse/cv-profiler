"""Unit and integration tests for Wave 1 of cv-profiler."""

from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

import cv_profiler as cp


def _valid_claim_dict(**overrides):
    base = {
        "id": "sql",
        "status": "verified",
        "category": "profissional",
        "level": "pratica",
        "evidence": {
            "kind": "producao_supervisionada",
            "context": "Empresa Exemplo - Estagio TI",
            "concrete": "Consultas SELECT com WHERE e JOIN para conferencia de registros",
            "artifact": None,
        },
        "tags": ["sql"],
        "bullets": [
            "Consultas SQL (SELECT, WHERE, JOIN) para conferencia e auditoria de registros."
        ],
    }
    base.update(overrides)
    return base


class TestClaimSchemaAndLevelCaps(unittest.TestCase):
    def test_valid_verified_claim(self):
        c = cp.Claim.model_validate(_valid_claim_dict())
        self.assertEqual(c.id, "sql")
        self.assertEqual(c.status, "verified")
        self.assertEqual(c.evidence.kind, "producao_supervisionada")

    def test_valid_absent_claim(self):
        c = cp.Claim.model_validate(
            {
                "id": "linux",
                "status": "absent",
                "category": "profissional",
                "level": None,
                "evidence": None,
                "tags": ["linux"],
                "bullets": [],
            }
        )
        self.assertEqual(c.status, "absent")
        self.assertIsNone(c.level)

    def test_extra_fields_forbidden(self):
        with self.assertRaises(ValidationError):
            cp.Claim.model_validate(_valid_claim_dict(extra_key="nope"))
        bad_ev = _valid_claim_dict()
        bad_ev["evidence"]["extra"] = "nope"
        with self.assertRaises(ValidationError):
            cp.Claim.model_validate(bad_ev)

    def test_absent_claim_rejects_level_evidence_or_bullets(self):
        with self.assertRaises(ValidationError):
            cp.Claim.model_validate(
                {
                    "id": "linux",
                    "status": "absent",
                    "category": "profissional",
                    "level": "nocoes",
                    "evidence": None,
                    "tags": ["linux"],
                    "bullets": [],
                }
            )
        with self.assertRaises(ValidationError):
            cp.Claim.model_validate(
                {
                    "id": "linux",
                    "status": "absent",
                    "category": "profissional",
                    "level": None,
                    "evidence": None,
                    "tags": ["linux"],
                    "bullets": ["Alguma coisa"],
                }
            )

    def test_max_level_by_evidence_kind(self):
        cases = [
            ("estudo", "nocoes", True),
            ("estudo", "pratica", False),
            ("estudo", "solido", False),
            ("projeto", "pratica", True),
            ("projeto", "solido", False),
            ("producao_supervisionada", "pratica", True),
            ("producao_supervisionada", "solido", False),
            ("producao_autonoma", "solido", True),
        ]
        for kind, level, should_pass in cases:
            data = _valid_claim_dict(level=level)
            data["evidence"]["kind"] = kind
            if should_pass:
                c = cp.Claim.model_validate(data)
                self.assertEqual(c.level, level)
            else:
                with self.assertRaises(ValidationError, msg=f"{kind}/{level} should fail"):
                    cp.Claim.model_validate(data)

    def test_duplicate_id_rejected_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dup.yaml"
            p.write_text(
                "claims:\n"
                "  - id: sql\n"
                "    status: absent\n"
                "    category: profissional\n"
                "    tags: [sql]\n"
                "  - id: sql\n"
                "    status: absent\n"
                "    category: profissional\n"
                "    tags: [sql]\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                cp.load_claims(p)


class TestRejectionValidator(unittest.TestCase):
    def test_accepts_grounded_claim(self):
        res = cp.validate_claim(
            _valid_claim_dict(),
            qa_answers=["Usei SQL na empresa com SELECT, WHERE e JOIN para conferir registros."],
        )
        self.assertTrue(res.ok, res.errors)

    def test_rejects_level_above_evidence_cap(self):
        data = _valid_claim_dict(level="solido")
        data["evidence"]["kind"] = "estudo"
        res = cp.validate_claim(data)
        self.assertFalse(res.ok)
        self.assertTrue(any("exceeds max allowed" in e for e in res.errors))

    def test_rejects_buzzwords_and_overselling(self):
        data = _valid_claim_dict(
            bullets=["Profissional proativo com visao 360 em consultas SQL."]
        )
        res = cp.validate_claim(data)
        self.assertFalse(res.ok)
        self.assertTrue(any("buzzword" in e for e in res.errors))

        oversell = _valid_claim_dict(
            level="pratica",
            bullets=["Especialista em otimizacao de bancos SQL."],
        )
        res2 = cp.validate_claim(oversell)
        self.assertFalse(res2.ok)
        self.assertTrue(any("oversell" in e for e in res2.errors))

    def test_rejects_ungrounded_metrics(self):
        data = _valid_claim_dict(
            bullets=["Reduziu tempo de consulta SQL em 45% na operacao diaria."]
        )
        res = cp.validate_claim(
            data,
            qa_answers=["Fiz consultas SQL simples para conferir registros no estagio."],
        )
        self.assertFalse(res.ok)
        self.assertTrue(any("ungrounded_metric" in e and "45%" in e for e in res.errors))

        res_ok = cp.validate_claim(
            data,
            qa_answers=["Otimizei relatorio e reduzi o tempo de consulta em 45%."],
        )
        self.assertTrue(res_ok.ok, res_ok.errors)

    def test_rejects_upgrade_without_new_evidence(self):
        existing = cp.Claim.model_validate(_valid_claim_dict(level="nocoes"))
        upgraded = _valid_claim_dict(level="pratica")
        res = cp.validate_claim(upgraded, existing_claim=existing)
        self.assertFalse(res.ok)
        self.assertTrue(any("upgrade:" in e for e in res.errors))

    def test_rejects_generic_or_short_concrete_evidence(self):
        data = _valid_claim_dict()
        data["evidence"]["concrete"] = "SQL SQL SQL SQL SQL"
        res = cp.validate_claim(data)
        self.assertFalse(res.ok)
        self.assertTrue(any("evidence:" in e for e in res.errors))


class TestTokenBoundaryMatcher(unittest.TestCase):
    def test_ad_does_not_match_administracao_or_dados(self):
        aliases = cp.load_aliases()
        ad_claim = cp.Claim.model_validate(
            {
                "id": "active_directory",
                "status": "verified",
                "category": "profissional",
                "level": "pratica",
                "evidence": {
                    "kind": "producao_supervisionada",
                    "context": "Ambiente corporativo TI",
                    "concrete": "Gestao de contas e grupos no Active Directory",
                },
                "tags": ["active_directory"],
                "bullets": ["Administracao de contas via AD."],
            }
        )
        jd_false = "Graduacao em administracao, analise de dados e atendimento adequado."
        self.assertEqual(cp.select_claims(jd_false, [ad_claim], aliases), [])
        self.assertEqual(cp.compute_gaps(jd_false, [], aliases), [])

        jd_true = "Experiencia com gestao de AD/GPO em ambiente corporativo."
        self.assertEqual(len(cp.select_claims(jd_true, [ad_claim], aliases)), 1)
        self.assertEqual(cp.compute_gaps(jd_true, [], aliases), ["active_directory"])

    def test_absent_claim_not_selected_and_remains_gap(self):
        aliases = cp.load_aliases()
        absent_linux = cp.Claim.model_validate(
            {
                "id": "linux",
                "status": "absent",
                "category": "profissional",
                "tags": ["linux"],
            }
        )
        jd = "Conhecimento em Linux Server e shell."
        self.assertEqual(cp.select_claims(jd, [absent_linux], aliases), [])
        self.assertEqual(cp.compute_gaps(jd, [absent_linux], aliases), ["linux"])

    def test_multi_tag_claim_covers_all_tags(self):
        aliases = {"python": ["python"], "automacao": ["automacao"]}
        py_claim = cp.Claim.model_validate(
            {
                "id": "python",
                "status": "verified",
                "category": "profissional",
                "level": "pratica",
                "evidence": {
                    "kind": "producao_supervisionada",
                    "context": "Empresa Exemplo",
                    "concrete": "Scripts Python para automacao de conciliacao",
                },
                "tags": ["python", "automacao"],
                "bullets": ["Automacao com Python."],
            }
        )
        self.assertEqual(cp.compute_gaps("Vaga com foco em automacao.", [py_claim], aliases), [])


class TestLatexEscapingAndEndToEnd(unittest.TestCase):
    def test_escape_latex_all_special_chars_no_double_escape(self):
        raw = r"C:\Users\demo 100% R&D $HOME #1 user_id {a} ~ ^ ação"
        escaped = cp.escape_latex(raw)
        self.assertIn(r"\textbackslash{}", escaped)
        self.assertIn(r"\%", escaped)
        self.assertIn(r"\&", escaped)
        self.assertIn(r"\$", escaped)
        self.assertIn(r"\#", escaped)
        self.assertIn(r"\_", escaped)
        self.assertIn(r"\{a\}", escaped)
        self.assertIn(r"\textasciitilde{}", escaped)
        self.assertIn(r"\textasciicircum{}", escaped)
        self.assertIn("ação", escaped)
        # Ensure backslash replacement braces were NOT double-escaped
        self.assertNotIn(r"\textbackslash\{\}", escaped)

    def test_render_with_special_chars_in_bullets_passes_tectonic_and_ats(self):
        tricky = cp.Claim.model_validate(
            _valid_claim_dict(
                bullets=[
                    r"Scripts SQL & Python para 100% de auditoria em C:\logs ($ENV, #1, user_id, {ok}, ~, ^)."
                ]
            )
        )
        comp = cp.compose_competencias([tricky])
        pdf = cp.render(
            resumo="Suporte N1 & N2 (100% remoto) com foco em QA_TI.",
            competencias=comp,
            formacao="Engenharia de Software (Universidade Exemplo) #1",
            experiencia="Estagio TI & Suporte",
        )
        self.assertTrue(pdf.exists())
        self.assertEqual(cp.ats_checks(pdf, allow_two_pages=False), [])
        # Verify human-readable formatted output (accents, structured headings, no raw YAML debug text)
        import subprocess
        extracted = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("COMPETÊNCIAS TÉCNICAS", extracted)
        self.assertIn("FORMAÇÃO ACADÊMICA", extracted)
        self.assertIn("EXPERIÊNCIA PROFISSIONAL", extracted)
        self.assertIn("SQL e Bancos de Dados", extracted)
        self.assertIn("Prática", extracted)
        self.assertNotIn("| nivel ", extracted)
        self.assertNotIn("| evidencia:", extracted)

    def test_format_latex_block_paragraphs_and_bullets(self):
        raw = "Empresa X — Cargo\n- Item 100% & ok\n- Item #2\n\nSegundo parágrafo."
        tex = cp.format_latex_block(raw)
        self.assertIn(r"\begin{cvlist}", tex)
        self.assertIn(r"\item Item 100\% \& ok", tex)
        self.assertIn(r"\item Item \#2", tex)
        self.assertIn(r"\end{cvlist}", tex)
        self.assertIn(r"Segundo parágrafo.\par", tex)

    def test_example_and_active_claims_valid_e2e(self):
        for path in (cp.CLAIMS_EXAMPLE, cp.CLAIMS):
            claims = cp.load_claims(path)
            self.assertGreaterEqual(len(claims), 1)
            for c in claims:
                res = cp.validate_claim(c)
                self.assertTrue(res.ok, f"{path.name}:{c.id}: {res.errors}")

        aliases = cp.load_aliases()
        example_claims = cp.load_claims(cp.CLAIMS_EXAMPLE)
        jd_text = "Vaga de Suporte Tecnico (N1) com automacao em Python."
        selected = cp.select_claims(jd_text, example_claims, aliases)
        self.assertEqual({c.id for c in selected}, {"service_desk", "python"})
        self.assertEqual(cp.compute_gaps(jd_text, example_claims, aliases), [])


if __name__ == "__main__":
    unittest.main()
