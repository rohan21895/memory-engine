"""The command line, and its exit codes.

A run that finds videos and analyses none of them must NOT exit 0. That is the
same rule `services/pipeline` follows, and it is the reason both exist: a
zero-length success is indistinguishable from a real one to anything that only
checks the exit code.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401 - sets sys.path

from memory_engine_video_analysis import cli


class ExitCodes(unittest.TestCase):
    def test_an_empty_workdir_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(cli.main([directory, "--quiet"]), 1)

    def test_a_library_of_video_with_no_proxies_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records" / "records" / "ab" / "cd"
            root.mkdir(parents=True)
            (root / "record.json").write_text(
                json.dumps(
                    {"media_id": "a" * 64, "kind": "video", "proxies": []}
                ),
                encoding="utf-8",
            )
            self.assertEqual(cli.main([directory, "--quiet"]), 1)

    def test_a_real_proxy_produces_records_and_exits_zero(self):
        clip = _support.hard_cut()
        proxy = _support.make_proxy(clip)
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            root = workdir / "records" / "records" / "aa" / "aa"
            root.mkdir(parents=True)
            (root / f"{proxy.media_id}.json").write_text(
                json.dumps(
                    {
                        "media_id": proxy.media_id,
                        "kind": "video",
                        "sources": [{"original_filename": "hard_cut.mp4"}],
                        "proxies": [
                            {
                                "kind": "video_proxy_480p",
                                "proxy_id": proxy.proxy_id,
                                "path": str(clip),
                                "size": {"width": 854, "height": 480},
                                "frame_index": {"path": str(proxy.frame_index.path)},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = cli.main([str(workdir), "--quiet", "--at", "2026-08-17T00:00:00+00:00"])
            self.assertEqual(code, 0)
            written = workdir / "moments" / f"{proxy.media_id}.json"
            self.assertTrue(written.is_file())
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(payload["media_id"], proxy.media_id)
            self.assertTrue(payload["records"])
            self.assertFalse(payload["report"]["transcript_available"])
            self.assertIn("WEIGHTS_MISSING", payload["report"]["transnetv2_seam"])
            self.assertIn("face_presence", payload["report"]["not_measured"])


if __name__ == "__main__":
    unittest.main()
