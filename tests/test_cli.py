"""CLI interface tests for cv / cv-profiler v2."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import cv_profiler as cp


class TestCLIInterface(unittest.TestCase):
    def test_status_subcommand_outputs_validated_summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cp.main(["status"])
        out = buf.getvalue()
        self.assertIn("Evidence (", out)
        self.assertIn("skills verificadas", out)

    def test_claims_subcommand_outputs_derived_summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cp.main(["claims"])
        out = buf.getvalue()
        self.assertIn("Claims derivados de", out)
        self.assertIn("service_desk", out)

    def test_auto_sync_moves_loose_job_into_ingested(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            out_dir = root / "out"
            jobs_dir.mkdir()
            loose = jobs_dir / "acme.inhire.app-Suporte Tecnico N1.txt"
            loose.write_text("Vaga Suporte Tecnico N1 com ITIL e Active Directory\n", encoding="utf-8")

            ingested_now = cp.sync_jobs(jobs_dir=jobs_dir)
            self.assertEqual(len(ingested_now), 1)
            job_dir, meta = ingested_now[0]
            self.assertFalse(loose.exists())
            self.assertTrue((job_dir / "jd.txt").exists())
            self.assertTrue((job_dir / "job.yaml").exists())
            self.assertIn("acme", meta.slug)
            self.assertNotIn("inhire", meta.slug)

            ingested, remaining_loose = cp.list_ingested_jobs(jobs_dir=jobs_dir, out_dir=out_dir)
            self.assertEqual([j["slug"] for j in ingested], [meta.slug])
            self.assertEqual(remaining_loose, [])

    def test_clean_error_exit_without_traceback(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cp.main(["gap", "nonexistent-slug-xyz"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("erro:", err.getvalue())

    def test_base_dir_resolves_symlinks(self):
        self.assertTrue((cp.BASE_DIR / "templates" / "cv.tex").is_file())
        self.assertTrue((cp.BASE_DIR / "evidence.example" / "experiences" / "empresa-exemplo-suporte.yaml").is_file())
        self.assertTrue((cp.BASE_DIR / "profile.example.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
