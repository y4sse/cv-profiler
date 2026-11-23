"""Unit and integration tests for Wave 2 of cv-profiler."""

from pathlib import Path
import tempfile
import unittest
import yaml

import cv_profiler as cp


def _write_claims(path: Path, claims: list[cp.Claim]) -> None:
    cp.save_claims(claims, path=path)


class TestJobStorageAndIngest(unittest.TestCase):
    def test_ingest_txt_and_load_by_slug(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "vaga.txt"
            src.write_text(
                "Vaga Analista de Suporte\nRequisitos: Active Directory, SQL e Linux.\n",
                encoding="utf-8",
            )
            jobs_dir = root / "jobs"
            job_dir, meta, text = cp.ingest_job(
                src,
                "2026-11_acme_suporte",
                title="Analista de Suporte",
                company="Acme",
                jobs_dir=jobs_dir,
            )
            self.assertEqual(job_dir, jobs_dir / "2026-11_acme_suporte")
            self.assertTrue((job_dir / "jd.txt").exists())
            self.assertTrue((job_dir / "job.yaml").exists())
            self.assertEqual(meta.company, "Acme")

            slug, loaded_text, loaded_meta = cp.load_job(
                "2026-11_acme_suporte", jobs_dir=jobs_dir
            )
            self.assertEqual(slug, "2026-11_acme_suporte")
            self.assertEqual(loaded_text, text)
            self.assertIsNotNone(loaded_meta)
            self.assertEqual(loaded_meta.title, "Analista de Suporte")

    def test_ingest_pdf_extracts_jd_txt_and_auto_refreshes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample_pdf = cp.render(
                resumo="Vaga de Suporte Tecnico N1 com Python.",
                competencias=cp.compose_competencias(cp.load_claims(cp.CLAIMS_EXAMPLE)),
                formacao="Bacharelado em Engenharia de Software.",
                experiencia="Estagio de Suporte TI.",
            )
            jobs_dir = root / "jobs"
            job_dir, meta, text = cp.ingest_job(
                sample_pdf,
                "2026-11_pdf_test",
                jobs_dir=jobs_dir,
            )
            self.assertTrue((job_dir / "jd.pdf").exists())
            self.assertTrue((job_dir / "jd.txt").exists())
            self.assertIn("COMPETÊNCIAS", text)

            # Delete jd.txt and confirm load_job re-extracts from jd.pdf automatically
            (job_dir / "jd.txt").unlink()
            slug, refreshed, _ = cp.load_job("2026-11_pdf_test", jobs_dir=jobs_dir)
            self.assertEqual(slug, "2026-11_pdf_test")
            self.assertIn("COMPETÊNCIAS", refreshed)
            self.assertTrue((job_dir / "jd.txt").exists())


class TestGapReportAndGrillSessionPersistence(unittest.TestCase):
    def test_grill_accept_absent_and_reject_persists_session_and_claims(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            sessions_dir = root / "sessions"
            claims_path = root / "claims.yaml"

            # Seed one existing verified claim; JD asks for sql, linux, windows.
            existing = [
                cp.Claim.model_validate(
                    {
                        "id": "active_directory",
                        "status": "verified",
                        "category": "profissional",
                        "level": "pratica",
                        "evidence": {
                            "kind": "producao_supervisionada",
                            "context": "Empresa Exemplo",
                            "concrete": "Gestao de contas e permissoes no Active Directory",
                        },
                        "tags": ["active_directory"],
                        "bullets": ["Administracao de contas via AD."],
                    }
                )
            ]
            _write_claims(claims_path, existing)

            jd_file = root / "vaga.txt"
            jd_file.write_text(
                "Requisitos: Active Directory, SQL para consultas, Linux Server e Windows Server.\n",
                encoding="utf-8",
            )
            cp.ingest_job(jd_file, "2026-11_test_vaga", jobs_dir=jobs_dir)

            # Gaps sorted: ['linux', 'sql', 'windows']
            # 1) linux -> mark absent immediately
            # 2) sql -> accept valid claim
            # 3) windows -> attempt invalid oversell (estudo + solido) -> rejected, not persisted
            answers = iter(
                [
                    # linux: no experience
                    "n",
                    # sql: yes -> 3 Q&A answers -> structured fields -> accept
                    "s",
                    "Estagio na Empresa Exemplo em 2025",
                    "Consultas SELECT com WHERE e JOIN para conferencia de registros",
                    "Nao administro banco nem crio procedures",
                    "profissional",
                    "producao_supervisionada",
                    "pratica",
                    "",  # default context from Q1
                    "",  # default concrete from Q2
                    "",  # no artifact
                    "Consultas SQL (SELECT, WHERE, JOIN) para conferencia de registros em producao.",
                    "a",
                    # windows: yes -> 3 Q&A -> invalid cap (estudo + solido) -> rejected
                    "s",
                    "Curso online de Windows Server",
                    "Subi uma VM Windows Server em laboratorio de estudo",
                    "Nunca operei Windows Server em producao",
                    "profissional",
                    "estudo",
                    "solido",
                    "",
                    "",
                    "",
                    "Administracao basica de Windows Server em laboratorio.",
                ]
            )
            logs: list[str] = []
            session, session_path = cp.run_grill_session(
                "2026-11_test_vaga",
                claims_path=claims_path,
                jobs_dir=jobs_dir,
                sessions_dir=sessions_dir,
                input_fn=lambda _prompt: next(answers),
                output_fn=logs.append,
            )

            # Session YAML persisted and validates cleanly on reload
            self.assertTrue(session_path.exists())
            reloaded_session = cp.InterviewSession.model_validate(
                yaml.safe_load(session_path.read_text(encoding="utf-8"))
            )
            self.assertEqual(reloaded_session.job_slug, "2026-11_test_vaga")
            self.assertEqual(reloaded_session.gaps_input, ["linux", "sql", "windows"])
            decisions = {it.skill_id: it.user_decision for it in reloaded_session.items}
            self.assertEqual(
                decisions,
                {"linux": "absent", "sql": "accepted", "windows": "rejected"},
            )

            # Claims store updated with linux=absent and sql=verified; windows NOT persisted
            reloaded_claims = cp.load_claims(claims_path)
            by_id = {c.id: c for c in reloaded_claims}
            self.assertIn("linux", by_id)
            self.assertEqual(by_id["linux"].status, "absent")
            self.assertIn("sql", by_id)
            self.assertEqual(by_id["sql"].status, "verified")
            self.assertEqual(by_id["sql"].level, "pratica")
            self.assertEqual(by_id["sql"].source_session, f"sessions/{session_path.name}")
            self.assertNotIn("windows", by_id)

            # Second gap check skips known_absent (linux) in actionable_gaps; only windows remains
            aliases = cp.load_aliases()
            _, jd_text, _ = cp.load_job("2026-11_test_vaga", jobs_dir=jobs_dir)
            report = cp.compute_gap_report(
                jd_text, reloaded_claims, aliases, job_slug="2026-11_test_vaga"
            )
            self.assertEqual(report.known_absent, ["linux"])
            self.assertEqual(report.actionable_gaps, ["windows"])
            self.assertIn("sql", report.covered)

            # Append-only session naming: running a second session same day creates _2 file
            answers2 = iter(["p"])  # skip windows
            _, session_path2 = cp.run_grill_session(
                "2026-11_test_vaga",
                claims_path=claims_path,
                jobs_dir=jobs_dir,
                sessions_dir=sessions_dir,
                input_fn=lambda _prompt: next(answers2),
                output_fn=logs.append,
            )
            self.assertNotEqual(session_path, session_path2)
            self.assertTrue(session_path.exists() and session_path2.exists())


class TestBuildSubcommandAndOverrides(unittest.TestCase):
    def test_build_from_ingested_job_with_meta_and_category_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            out_dir = root / "out"
            src = root / "demo_vaga.txt"
            src.write_text(
                "Vaga Analista de Suporte N1 com scripts Python e automacao.\n",
                encoding="utf-8",
            )
            job_dir, _, _ = cp.ingest_job(src, "2026-11_demo_n1", jobs_dir=jobs_dir)
            # Add custom resumo override into job.yaml
            meta_path = job_dir / "job.yaml"
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            meta["resumo"] = "Estudante focado em Suporte TI & N1 (100% comprometido)."
            meta["allow_2_pages"] = True
            cp.write_yaml_atomic(meta_path, meta)

            res = cp.build_cv(
                "2026-11_demo_n1",
                claims_path=cp.CLAIMS_EXAMPLE,
                jobs_dir=jobs_dir,
                out_dir=out_dir,
            )
            self.assertEqual(res["pdf"], out_dir / "2026-11_demo_n1.pdf")
            self.assertTrue(res["pdf"].exists())
            self.assertEqual(res["ats_failures"], [])
            self.assertEqual(len(res["selected"]), 2)


if __name__ == "__main__":
    unittest.main()
