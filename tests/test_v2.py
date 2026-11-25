"""Unit and integration tests for cv-profiler v2 (evidence store, auto-ingest, layout)."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from pydantic import ValidationError

import cv_profiler as cp


def _copy_example_evidence(dst: Path) -> Path:
    shutil.copytree(cp.EVIDENCE_EXAMPLE_DIR, dst)
    return dst


class TestEvidenceStoreAndHonesty(unittest.TestCase):
    def test_load_and_validate_example_evidence(self):
        store = cp.load_evidence_store(cp.EVIDENCE_EXAMPLE_DIR)
        self.assertEqual(len(store.experiences), 1)
        self.assertEqual(len(store.education), 1)
        self.assertEqual(len(store.projects), 1)
        self.assertEqual(len(store.certificates), 1)
        self.assertEqual([a.id for a in store.absent], ["linux"])
        res = cp.validate_evidence_store(store)
        self.assertTrue(res.ok, res.errors)

    def test_skill_level_capped_by_evidence_kind(self):
        with self.assertRaises(ValidationError):
            cp.EducationEvidence.model_validate(
                {
                    "id": "uni",
                    "institution": "Uni",
                    "degree": "Bacharelado",
                    "start": "2024-01",
                    "evidence_kind": "estudo",
                    "skills": ["python"],
                    "skill_levels": {"python": "solido"},
                }
            )

    def test_buzzword_and_oversell_rejected_in_evidence_bullets(self):
        store = cp.EvidenceStore(
            experiences=[
                cp.ExperienceEvidence(
                    id="exp1",
                    org="Acme",
                    role="Suporte",
                    start="2025-01",
                    evidence_kind="producao_supervisionada",
                    bullets=[
                        cp.BulletItem(
                            text="Atendimento proativo como especialista de suporte N1.",
                            skills=["service_desk"],
                        )
                    ],
                )
            ]
        )
        res = cp.validate_evidence_store(store)
        self.assertFalse(res.ok)
        joined = "\n".join(res.errors)
        self.assertIn("buzzword:", joined)
        self.assertIn("oversell:", joined)

    def test_unanchored_and_missing_pdf_artifact_emit_warnings(self):
        with tempfile.TemporaryDirectory() as td:
            ev = _copy_example_evidence(Path(td) / "evidence")
            cp.write_yaml_atomic(
                ev / "certificates" / "cert-pdf.yaml",
                {
                    "id": "cert-pdf",
                    "title": "Cert PDF",
                    "issuer": "Org",
                    "date": "2026-01",
                    "artifact": "artifacts/missing.pdf",
                    "evidence_kind": "estudo",
                    "skills": ["itil"],
                },
            )
            cp.save_unanchored_entries(
                [
                    cp.UnanchoredEntry(
                        id="redes",
                        evidence_kind="producao_supervisionada",
                        level="pratica",
                        context="Ambiente corporativo",
                        concrete="Suporte a redes TCP/IP, DNS e DHCP no dia a dia",
                        tags=["redes"],
                    )
                ],
                ev,
            )
            store = cp.load_evidence_store(ev)
            res = cp.validate_evidence_store(store)
            self.assertTrue(res.ok, res.errors)
            joined = "\n".join(res.warnings)
            self.assertIn("artifact:certificate:cert-pdf", joined)
            self.assertIn("unanchored:redes", joined)


class TestAutoIngestionAndPartialSlugs(unittest.TestCase):
    def test_ingest_rejects_directory_without_creating_empty_folder(self):
        with tempfile.TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            jobs_dir.mkdir()
            with self.assertRaises(ValueError):
                cp.ingest_job(jobs_dir, jobs_dir=jobs_dir)
            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_partial_slug_resolution_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            jobs_dir = Path(td) / "jobs"
            jobs_dir.mkdir()
            f1 = jobs_dir / "lanlink_suporte.txt"
            f1.write_text("Suporte tecnico N2 ITIL\n", encoding="utf-8")
            f2 = jobs_dir / "orizon_suporte.txt"
            f2.write_text("Analista suporte N1 Windows\n", encoding="utf-8")

            slug, jd_text, _ = cp.load_job("lanlink", jobs_dir=jobs_dir, auto_sync=True)
            self.assertIn("lanlink", slug)
            self.assertIn("ITIL", jd_text)
            # Both loose files auto-synced into jobs/ingested/
            self.assertFalse(f1.exists())
            self.assertFalse(f2.exists())

            with self.assertRaises(ValueError) as ctx:
                cp.load_job("suporte", jobs_dir=jobs_dir, auto_sync=False)
            self.assertIn("ambiguo", str(ctx.exception))


class TestV2CompositionAndBuild(unittest.TestCase):
    def test_habilidades_omits_proficiency_levels(self):
        store = cp.load_evidence_store(cp.EVIDENCE_EXAMPLE_DIR)
        claims = cp.derive_claims_from_evidence(store)
        tex = cp.compose_habilidades(claims, {"service_desk", "python"}, cp.load_aliases())
        self.assertIn("Service Desk", tex)
        self.assertIn("Python", tex)
        for forbidden in ("Sólido", "Prática", "Noções", "solido", "pratica", "nocoes"):
            self.assertNotIn(forbidden, tex)

    def test_projects_auto_included_only_when_adding_jd_coverage(self):
        store = cp.load_evidence_store(cp.EVIDENCE_EXAMPLE_DIR)
        aliases = cp.load_aliases()
        # Experience already covers service_desk & python -> projects not needed
        projs_none = cp.select_projects_for_cv(
            store.projects, store.experiences, {"service_desk", "python"}, aliases, mode="auto"
        )
        self.assertEqual(projs_none, [])
        # Project covers excel_planilhas which experience does NOT cover -> auto-included!
        projs_needed = cp.select_projects_for_cv(
            store.projects, store.experiences, {"excel_planilhas"}, aliases, mode="auto"
        )
        self.assertEqual([p.id for p in projs_needed], ["automacao-planilhas"])

    def test_v2_grill_attaches_verified_bullet_to_experience(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ev = _copy_example_evidence(root / "evidence")
            jobs_dir = root / "jobs"
            sessions_dir = root / "sessions"
            jobs_dir.mkdir()
            (jobs_dir / "vaga_itil.txt").write_text("Requisitos: ITIL v4 e Service Desk.\n", encoding="utf-8")

            answers = iter(
                [
                    "s",  # has real experience with itil
                    "Empresa Exemplo em 2025",
                    "Aplicacao de praticas ITIL v4 na classificacao de incidentes e mudancas",
                    "Nao fiz gestao formal de problemas",
                    "profissional",
                    "producao_supervisionada",
                    "pratica",
                    "",
                    "",
                    "",
                    "Aplicacao de praticas ITIL v4 na triagem e registro de incidentes.",
                    "a",  # accept proposal
                    "1",  # attach to first option: Experiência Empresa Exemplo
                ]
            )
            logs: list[str] = []
            session, saved = cp.run_grill_session(
                "vaga_itil",
                evidence_dir=ev,
                jobs_dir=jobs_dir,
                sessions_dir=sessions_dir,
                input_fn=lambda _: next(answers),
                output_fn=logs.append,
            )
            self.assertTrue(saved.exists())
            self.assertEqual(session.items[0].user_decision, "accepted")
            self.assertEqual(session.items[0].target_evidence, "experiences/empresa-exemplo-suporte.yaml")

            reloaded = cp.load_evidence_store(ev)
            self.assertIn("itil", reloaded.experiences[0].all_skills())
            self.assertEqual(reloaded.experiences[0].skill_levels["itil"], "pratica")

    def test_v2_build_produces_1page_ats_clean_pdf_with_expected_section_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ev = _copy_example_evidence(root / "evidence")
            jobs_dir = root / "jobs"
            out_dir = root / "out"
            jobs_dir.mkdir()
            (jobs_dir / "vaga_suporte.txt").write_text(
                "Suporte tecnico N1, Excel e Python.\n", encoding="utf-8"
            )
            res = cp.build_cv(
                "vaga_suporte",
                evidence_dir=ev,
                jobs_dir=jobs_dir,
                out_dir=out_dir,
                profile_path=cp.PROFILE_EXAMPLE_PATH,
            )
            self.assertEqual(res["ats_failures"], [])
            self.assertTrue(res["pdf"].exists())
            self.assertEqual(res["selected_projects"], ["automacao-planilhas"])

            plain = subprocess.run(
                ["pdftotext", str(res["pdf"]), "-"], capture_output=True, text=True, check=True
            ).stdout
            # Verify exact section order in linear ATS text stream
            idx_resumo = plain.index("RESUMO PROFISSIONAL")
            idx_exp = plain.index("EXPERIÊNCIA PROFISSIONAL")
            idx_form = plain.index("FORMAÇÃO ACADÊMICA")
            idx_proj = plain.index("PROJETOS")
            idx_cert = plain.index("CERTIFICADOS")
            idx_hab = plain.index("HABILIDADES")
            self.assertTrue(idx_resumo < idx_exp < idx_form < idx_proj < idx_cert < idx_hab)
            # Habilidades must not display proficiency levels
            hab_tail = plain[idx_hab:]
            for forbidden in ("Sólido", "Prática", "Noções"):
                self.assertNotIn(forbidden, hab_tail)


if __name__ == "__main__":
    unittest.main()
