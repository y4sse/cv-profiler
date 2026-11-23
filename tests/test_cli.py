"""CLI interface tests for cv / cv-profiler."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import cv_profiler as cp


class TestCLIInterface(unittest.TestCase):
    def test_claims_subcommand_outputs_validated_summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cp.main(["claims"])
        out = buf.getvalue()
        total_verified = sum(1 for c in cp.load_claims() if c.status == "verified")
        self.assertIn(f"Claims validados: {total_verified} verified", out)
        self.assertIn("service_desk", out)

    def test_jobs_subcommand_lists_ingested_and_loose(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs_dir = root / "jobs"
            out_dir = root / "out"
            jobs_dir.mkdir()
            loose = jobs_dir / "nova_vaga.txt"
            loose.write_text("Vaga Suporte N1\n", encoding="utf-8")
            cp.ingest_job(loose, "2026-11_demo", company="Demo", title="Suporte", jobs_dir=jobs_dir)

            ingested, loose_files = cp.list_ingested_jobs(jobs_dir=jobs_dir, out_dir=out_dir)
            self.assertEqual([j["slug"] for j in ingested], ["2026-11_demo"])
            self.assertEqual([p.name for p in loose_files], ["nova_vaga.txt"])

    def test_clean_error_exit_without_traceback(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cp.main(["gap", "nonexistent-slug-xyz"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("erro:", err.getvalue())

    def test_base_dir_resolves_symlinks(self):
        self.assertTrue((cp.BASE_DIR / "templates" / "cv.tex").is_file())
        self.assertTrue((cp.BASE_DIR / "claims" / "example.yaml").is_file())
        self.assertTrue((cp.BASE_DIR / "profile.example.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
