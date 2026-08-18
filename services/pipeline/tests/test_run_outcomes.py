"""CLI outcome semantics: absent work must never look like a successful reuse."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_engine_pipeline import cli
from memory_engine_pipeline.runner import RunReport
from memory_engine_pipeline.stages.base import StageResult, StageStatus


class CliOutcomeSemantics(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-cli-source-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-cli-work-"))
        self.addCleanup(self._remove_tree, self.root)
        self.addCleanup(self._remove_tree, self.workdir)

    @staticmethod
    def _remove_tree(path: Path) -> None:
        import shutil

        shutil.rmtree(path, ignore_errors=True)

    def _report(self, *results: StageResult) -> RunReport:
        return RunReport(
            run_id="run",
            workdir=self.workdir,
            source_roots=(str(self.root),),
            results=results,
            reclaimed_jobs=(),
        )

    def test_disabled_render_flags_cannot_exit_zero(self):
        report = self._report(
            StageResult(
                stage="render-print",
                status=StageStatus.SKIPPED,
                detail="disabled by --no-render-print",
            ),
            StageResult(
                stage="render-video",
                status=StageStatus.SKIPPED,
                detail="disabled by --no-render-video",
            ),
        )
        with patch.object(cli, "run_pipeline", return_value=report) as run:
            code = cli.main(
                [
                    str(self.root),
                    "--workdir",
                    str(self.workdir),
                    "--no-render-print",
                    "--no-render-video",
                    "--quiet",
                ]
            )

        self.assertEqual(1, code)
        settings = run.call_args.kwargs["settings"]
        self.assertFalse(settings.render_print)
        self.assertFalse(settings.render_video)

    def test_empty_output_skip_is_incomplete_even_if_it_names_an_output(self):
        missing = self.workdir / "book.pdf"
        report = self._report(
            StageResult(
                stage="render-print",
                status=StageStatus.SKIPPED,
                detail="the album stage produced no AlbumSpec to render",
                outputs=(str(missing),),
            )
        )
        with patch.object(cli, "run_pipeline", return_value=report):
            code = cli.main(
                [str(self.root), "--workdir", str(self.workdir), "--quiet"]
            )

        self.assertEqual(1, code, "SKIPPED must never inherit cache-hit semantics")
        self.assertFalse(report.ok)

    def test_verified_cached_artifact_is_a_successful_reuse(self):
        output = self.workdir / "book.pdf"
        output.write_bytes(b"%PDF-verified")
        report = self._report(
            StageResult(
                stage="render-print",
                status=StageStatus.COMPLETED,
                detail="this album has already been rendered",
                outputs=(str(output),),
            )
        )
        with patch.object(cli, "run_pipeline", return_value=report):
            code = cli.main(
                [str(self.root), "--workdir", str(self.workdir), "--quiet"]
            )

        self.assertEqual(0, code)
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
