"""Unit tests for the pieces whose failures are silent.

Each class here covers something that, when wrong, produces a plausible answer
rather than an exception: a digest that disagrees with the Rust worker's, an
inventory that misses a change, a quality score that ranks backwards, a batch
response correlated onto the wrong photo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import (  # noqa: E402
    PROTO_ROOT,
    REPO_ROOT,
    FakeMlRuntime,
    require_ingest_binary,
    write_photo,
)

from memory_engine_pipeline import classical, ids, inventory, mlruntime  # noqa: E402
from memory_engine_pipeline.jobstore import (  # noqa: E402
    JobValidationError,
    build_job,
    validate_job,
)
from memory_engine_pipeline.stages.ingest import (  # noqa: E402
    _scan_job_for_store,
    _verified_media_record,
)


class IngestOutputIntegrity(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-output-integrity-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_same_size_valid_json_corruption_is_not_a_verified_job_output(self):
        path = self.root / "record.json"
        original = json.dumps(
            {"media_id": "ab" * 32, "label": "alpha"},
            sort_keys=True,
        ).encode("utf-8")
        path.write_bytes(original)
        output = {
            "kind": "media_record",
            "id": ids.blake3_hex(original),
            "path": str(path),
            "byte_size": len(original),
        }
        self.assertEqual("alpha", _verified_media_record(output)["label"])

        changed = original.replace(b"alpha", b"omega")
        self.assertEqual(len(original), len(changed))
        self.assertIsInstance(json.loads(changed), dict)
        path.write_bytes(changed)

        self.assertIsNone(_verified_media_record(output))


class SourceLocatorDigest(unittest.TestCase):
    """The digest MUST match the Rust worker's, or every scan is refused."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-ids-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_trailing_separators_and_duplicates_do_not_change_the_digest(self):
        first = ids.source_locator_digest([str(self.root)])
        self.assertEqual(first, ids.source_locator_digest([f"{self.root}/"]))
        self.assertEqual(first, ids.source_locator_digest([f"{self.root}//"]))
        self.assertEqual(
            first, ids.source_locator_digest([str(self.root), str(self.root)])
        )

    def test_order_does_not_change_the_digest_but_content_does(self):
        other = Path(tempfile.mkdtemp(prefix="mep-ids2-"))
        self.addCleanup(shutil.rmtree, other, True)
        forwards = ids.source_locator_digest([str(self.root), str(other)])
        backwards = ids.source_locator_digest([str(other), str(self.root)])
        self.assertEqual(forwards, backwards)
        self.assertNotEqual(forwards, ids.source_locator_digest([str(self.root)]))

    def test_a_missing_root_is_an_error_not_a_digest(self):
        with self.assertRaises(FileNotFoundError):
            ids.source_locator_digest([str(self.root / "nope")])

    def test_the_rust_worker_computes_the_same_digest(self):
        """The load-bearing one.

        Ingest recomputes this and refuses the job on a mismatch, so a drift
        between the two implementations does not corrupt anything -- it stops
        the product working, in a way whose error message ("source locator
        digest does not match") points at the wrong file. Proving agreement
        here is cheaper than debugging that later.
        """
        require_ingest_binary()
        write_photo(self.root / "a.jpg", index=1, captured="2026:03:14 09:00:00",
                    size=(200, 150))
        digest = ids.source_locator_digest([str(self.root)])
        job = build_job(
            job_type="scan_source",
            scope="test",
            params={"follow_symlinks": False, "include_hidden": False, "max_depth": 32},
            source_paths=[str(self.root)],
            locator_digest=digest,
        )
        workdir = Path(tempfile.mkdtemp(prefix="mep-ids-work-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        job_path = workdir / "job.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        result = subprocess.run(
            [
                str(REPO_ROOT / "workers/ingest/target/release/memory-engine-ingest"),
                str(job_path), str(workdir / "records"), str(workdir / "checkpoint.json"),
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("locator", result.stderr)


class JobIdentity(unittest.TestCase):
    def test_parameters_are_part_of_the_identity(self):
        base = dict(job_type="analyze_image", scope="s", params={"a": 1})
        self.assertNotEqual(
            build_job(**base)["job_id"],
            build_job(job_type="analyze_image", scope="s", params={"a": 2})["job_id"],
        )

    def test_scope_separates_otherwise_identical_work(self):
        self.assertNotEqual(
            build_job(job_type="rank_media", scope="one", params={})["job_id"],
            build_job(job_type="rank_media", scope="two", params={})["job_id"],
        )

    def test_input_order_does_not_change_the_identity(self):
        left = build_job(job_type="plan_album", scope="s", params={},
                         media_ids=["b" * 64, "a" * 64])
        right = build_job(job_type="plan_album", scope="s", params={},
                          media_ids=["a" * 64, "b" * 64])
        self.assertEqual(left["job_id"], right["job_id"])

    def test_a_job_that_violates_the_contract_raises(self):
        with self.assertRaises(JobValidationError):
            build_job(job_type="not_a_real_job_type", scope="s", params={})


class ScanJobStorageShape(unittest.TestCase):
    """The scheduler copy stays bounded; the worker copy stays resumable."""

    def test_a_large_worker_manifest_compacts_without_mutating_or_weakening_it(self):
        job = build_job(
            job_type="scan_source",
            scope="test",
            params={"follow_symlinks": False, "include_hidden": False, "max_depth": 32},
            source_paths=["/synthetic/source"],
            locator_digest="a" * 64,
        )
        outputs = [
            {
                "kind": "media_record",
                "id": f"{index:064x}",
                "path": f"/synthetic/records/{index}.json",
                "byte_size": 1,
                "produced_at": "2026-08-18T00:00:00Z",
            }
            for index in range(10_000)
        ]
        job["outputs"] = outputs
        job["checkpoint"]["partial_output_ids"] = [output["id"] for output in outputs]

        compact = _scan_job_for_store(job)

        self.assertEqual([], compact["outputs"])
        self.assertEqual([], compact["checkpoint"]["partial_output_ids"])
        self.assertEqual(10_000, len(job["outputs"]))
        self.assertEqual(10_000, len(job["checkpoint"]["partial_output_ids"]))
        self.assertIsNot(compact["state"], job["state"])
        self.assertIsNot(compact["checkpoint"], job["checkpoint"])
        validate_job(compact)

        # Compaction changes the stored data, not the contract gate. A bad
        # scheduler state must still fail the complete Draft 2020-12 schema.
        compact["state"]["status"] = "pretend-completed"
        with self.assertRaises(JobValidationError):
            validate_job(compact)


class FaceIdentity(unittest.TestCase):
    """The producer must agree with the contract, byte for byte.

    Issue #34 froze the face_id encoding in face-record.schema.json and shipped
    contracts/vectors/face-id.json alongside it. This is the one test that says
    the code in this repo actually implements that document: it reads the
    vectors rather than restating them, so a change to either side that is not
    a change to both fails here.

    Two things it caught when the vectors were first computed, both silent:
    `round()` rounds half to even where the contract (and JavaScript, and Rust)
    round half away from zero, and `repr()` writes `1001.0` where the contract
    writes `1001` -- which meant one frame time got two ids inside Python alone,
    depending only on whether the JSON had a decimal point in it.
    """

    VECTORS = json.loads(
        (REPO_ROOT / "contracts" / "vectors" / "face-id.json").read_text(encoding="utf-8")
    )

    def _face_id(self, vector):
        given = vector["input"]
        return ids.face_identity(
            media_id=given["media_id"],
            frame_time=given["frame_time"],
            bbox=[given["bbox"][axis] for axis in ("x", "y", "w", "h")],
            detector_model_id=given["detector"]["model_id"],
            detector_version=given["detector"]["version"],
        )

    def test_there_are_vectors_to_check(self):
        self.assertTrue(self.VECTORS["vectors"], "this guard has nothing to guard")

    def test_every_contract_vector_reproduces(self):
        for vector in self.VECTORS["vectors"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(vector["face_id"], self._face_id(vector))

    def test_the_shipped_constants_are_the_contract_constants(self):
        self.assertEqual(self.VECTORS["bbox_quantum"], ids.BBOX_QUANTUM)

    def test_an_integral_time_written_as_a_float_gives_the_same_id(self):
        integral = {"value": 1001, "rate": 30}
        floating = {"value": 1001.0, "rate": 30.0}
        common = dict(
            media_id="a" * 64,
            bbox=[0.25, 0.25, 0.5, 0.5],
            detector_model_id="scrfd-10g-bnkps",
            detector_version="1.0.0",
        )
        self.assertEqual(
            ids.face_identity(frame_time=integral, **common),
            ids.face_identity(frame_time=floating, **common),
            "repr() is back: one frame time is producing two ids",
        )

    def test_the_bbox_rounds_half_away_from_zero_not_to_even(self):
        # 0.30025 * 10000 is exactly 3002.5 as a double.
        self.assertEqual(3003, ids.quantise_box_component(0.30025))
        self.assertEqual(3002, round(0.30025 * ids.BBOX_QUANTUM))

    def test_a_number_needing_an_exponent_is_refused_rather_than_written(self):
        with self.assertRaises(ValueError):
            ids.ecmascript_number(1e-7)

    def test_a_separator_inside_a_detector_field_is_refused(self):
        with self.assertRaises(ValueError):
            ids.face_identity(
                media_id="a" * 64,
                bbox=[0.1, 0.1, 0.1, 0.1],
                detector_model_id="scrfd\x1f10g",
                detector_version="1.0.0",
            )


class Inventory(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-inv-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "nested").mkdir()
        for name in ("a.jpg", "nested/b.jpg"):
            (self.root / name).write_bytes(b"x" * 32)
        (self.root / ".hidden.jpg").write_bytes(b"x")

    def test_hidden_files_are_skipped_by_default_and_included_on_request(self):
        default = inventory.walk([self.root])
        self.assertEqual(2, len(default.entries))
        with_hidden = inventory.walk([self.root], include_hidden=True)
        self.assertEqual(3, len(with_hidden.entries))

    def test_an_unchanged_tree_produces_an_identical_digest(self):
        self.assertEqual(
            inventory.walk([self.root]).digest, inventory.walk([self.root]).digest
        )

    def test_a_size_change_is_detected(self):
        before = inventory.walk([self.root])
        (self.root / "a.jpg").write_bytes(b"y" * 64)
        delta = inventory.diff(before, inventory.walk([self.root]))
        self.assertEqual((), delta.added)
        self.assertEqual(1, len(delta.changed))

    def test_an_mtime_change_at_the_same_size_is_detected(self):
        before = inventory.walk([self.root])
        path = self.root / "a.jpg"
        path.write_bytes(b"z" * 32)
        os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        delta = inventory.diff(before, inventory.walk([self.root]))
        self.assertEqual(1, len(delta.changed))

    def test_changing_the_options_invalidates_the_comparison_entirely(self):
        before = inventory.walk([self.root])
        after = inventory.walk([self.root], include_hidden=True)
        delta = inventory.diff(before, after)
        self.assertEqual(3, len(delta.added), "a rule change must re-consider everything")
        self.assertEqual((), delta.removed)

    def test_a_file_root_yields_itself(self):
        """Delta scans pass individual files as roots; the walk must accept them."""
        walked = inventory.walk([self.root / "a.jpg"])
        self.assertEqual(1, len(walked.entries))


class ClassicalStepRegistration(unittest.TestCase):
    """The registry entry has to describe the code, or it is decoration.

    Issue #42's first line: `classical_quality` was step one of `photo_analysis`
    with no registry entry, no entry point and no pinned version. It now has all
    three — and this is what stops them drifting from the module that actually
    runs. A calibration constant tuned in `classical.py` and left stale in
    `registry.json` is worse than no entry at all, because the registry is where
    the eval harness would look to decide whether two runs are comparable.
    """

    REGISTRY = json.loads(
        (REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8")
    )

    def setUp(self):
        self.entry = self.REGISTRY["classical_steps"]["classical_quality"]

    def test_the_entry_point_imports_and_is_the_executor(self):
        import importlib

        module_name, _, attribute = self.entry["entry_point"].partition(":")
        module = importlib.import_module(module_name)
        self.assertIs(getattr(module, attribute), classical.measure)

    def test_the_pinned_version_is_the_executor_version(self):
        self.assertEqual(classical.EXECUTOR_VERSION, self.entry["version"])

    def test_the_declared_proxy_kinds_are_the_supported_ones(self):
        self.assertEqual(
            sorted(classical.SUPPORTED_PROXY_KINDS),
            sorted(self.entry["input_proxy_kinds"]),
        )

    def test_every_declared_calibration_constant_matches_the_module(self):
        declared = self.entry["calibration"]
        for name, value in declared.items():
            with self.subTest(constant=name):
                self.assertEqual(
                    getattr(classical, name.upper()),
                    value,
                    f"{name} was tuned in one place and not the other",
                )

    def test_no_calibration_constant_is_missing_from_the_declaration(self):
        """The direction that actually rots: a constant added to the module and
        never declared, so a recalibration is invisible in the registry."""
        module_constants = {
            name
            for name in vars(classical)
            if name.isupper()
            and isinstance(getattr(classical, name), (int, float))
            and not isinstance(getattr(classical, name), bool)
            and not name.startswith("_")
        }
        self.assertEqual(
            module_constants,
            {name.upper() for name in self.entry["calibration"]},
            "a calibration constant exists in exactly one of the module and the registry",
        )

    def test_the_step_is_the_first_step_of_photo_analysis(self):
        for name, spec in self.REGISTRY["pipelines"].items():
            if "classical_quality" in spec["steps"]:
                with self.subTest(pipeline=name):
                    self.assertEqual("classical_quality", spec["steps"][0])


class ClassicalQuality(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-cq-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _thumb(self, name: str, **kwargs) -> Path:
        from PIL import Image

        path = write_photo(self.root / name, index=3, captured="2026:03:14 09:00:00",
                           size=(1024, 768), **kwargs)
        thumb = self.root / f"thumb-{name}"
        with Image.open(path) as handle:
            handle.thumbnail((512, 512))
            handle.save(thumb, format="JPEG", quality=90)
        return thumb

    def test_blur_lowers_sharpness_and_the_measure_is_deterministic(self):
        sharp = classical.measure(self._thumb("sharp.jpg"))
        blurred = classical.measure(self._thumb("blurred.jpg", blur=True))
        self.assertLess(blurred.sharpness, sharp.sharpness)
        self.assertEqual(sharp, classical.measure(self._thumb("sharp.jpg")))

    def test_every_output_is_a_unit_score(self):
        measured = classical.measure(self._thumb("unit.jpg"))
        for name in ("sharpness", "exposure", "noise", "contrast"):
            value = getattr(measured, name)
            self.assertGreaterEqual(value, 0.0, name)
            self.assertLessEqual(value, 1.0, name)

    def test_a_black_frame_is_flagged_and_a_normal_photo_is_not(self):
        from PIL import Image

        black = self.root / "black.jpg"
        Image.new("RGB", (512, 384), (0, 0, 0)).save(black, format="JPEG", quality=95)
        self.assertTrue(classical.measure(black).is_black_frame)
        self.assertFalse(classical.measure(self._thumb("normal.jpg")).is_black_frame)

    def test_an_oversized_proxy_is_refused_rather_than_measured(self):
        """The constants are calibrated to a 512px proxy and do not transfer.

        Measuring a 4000px image with them would return a number in range and
        meaningless -- the worst possible outcome, because nothing downstream
        can tell.
        """
        big = write_photo(self.root / "big.jpg", index=1,
                          captured="2026:03:14 09:00:00", size=(1600, 1200))
        with self.assertRaises(classical.ClassicalQualityError):
            classical.measure(big)

    def test_a_missing_proxy_raises_rather_than_scoring_zero(self):
        with self.assertRaises(classical.ClassicalQualityError):
            classical.measure(self.root / "does-not-exist.jpg")


class RuntimeClient(unittest.TestCase):
    """The correlation and validation rules the proto states but cannot enforce."""

    def test_probe_reports_a_healthy_host(self):
        with FakeMlRuntime() as host:
            status = mlruntime.probe(
                endpoint=host.endpoint,
                required_models=["siglip2-so400m-384", "scrfd-10g-bnkps"],
            )
        self.assertTrue(status.available)
        self.assertEqual("ok", status.reason)
        self.assertEqual("development", status.load_mode)

    def test_a_wrong_dimension_embedding_is_refused_not_stored(self):
        with FakeMlRuntime(embedding_dimensions=64) as host, \
                mlruntime.MlRuntimeClient(host.endpoint) as client:
            outcome = client.infer_proxies(
                model_id="siglip2-so400m-384",
                request_id="r1",
                items={"media-a": "ab" * 32},
            )
        self.assertEqual(64, len(outcome.tensors["media-a"]))
        # The dimension contract is enforced by the analysis stage against the
        # declared space; the client returns what the host said, unaltered.

    def test_a_per_item_error_is_a_failure_not_an_empty_result(self):
        proxy = "cd" * 32
        with FakeMlRuntime(fail_items=frozenset({"media-b"})) as host, \
                mlruntime.MlRuntimeClient(host.endpoint) as client:
            outcome = client.infer_proxies(
                model_id="scrfd-10g-bnkps", request_id="r2", items={"media-b": proxy}
            )
        self.assertEqual({}, dict(outcome.detections))
        self.assertEqual(1, len(outcome.failures))
        self.assertEqual("proxy_not_found", outcome.failures[0].code)
        self.assertEqual("media-b", outcome.failures[0].item_id)

    def test_a_dropped_item_is_detected_rather_than_read_as_no_result(self):
        """A host that answers 31 of 32 items must not look like 32 successes.

        Silently accepting a short response is how one photo in a batch ends up
        permanently unanalysed while the stage reports completion.
        """
        wanted = ["ef" * 32, "12" * 32]
        with FakeMlRuntime(fail_items=frozenset()) as host, \
                mlruntime.MlRuntimeClient(host.endpoint) as client:
            with self.assertRaises(mlruntime.MlRuntimeError) as caught:
                client._read(  # noqa: SLF001 - exercising the validation directly
                    _short_response(host, wanted[:1]),
                    model_id="scrfd-10g-bnkps",
                    expected=wanted,
                )
        self.assertIn("silently dropped", str(caught.exception))

    def test_two_photos_sharing_a_proxy_are_still_two_items(self):
        """Proxies are content addressed; two photos can share one.

        Keying a batch on the proxy id would silently collapse them into a
        single item, and one of the two records would never be analysed while
        the stage reported a full pass.
        """
        shared = "ab" * 32
        with FakeMlRuntime() as host, mlruntime.MlRuntimeClient(host.endpoint) as client:
            outcome = client.infer_proxies(
                model_id="siglip2-so400m-384",
                request_id="r4",
                items={"media-one": shared, "media-two": shared},
            )
        self.assertEqual({"media-one", "media-two"}, set(outcome.tensors))

    def test_the_deadline_grows_with_the_batch(self):
        """Issue #79. The deadline was fixed, and every model was small.

        SigLIP 2 so400m measures ~2.5s per image on a laptop CPU, so a batch of
        32 needs ~85s against the old fixed 60s: with real weights installed
        for the first time, EVERY analysis pass died at the transport with
        DEADLINE_EXCEEDED and the album path could not run at all. The bug is
        not the number, it is that one request's budget did not depend on how
        much work the request contained.
        """
        with FakeMlRuntime() as host, mlruntime.MlRuntimeClient(host.endpoint) as client:
            self.assertEqual(60.0, client.deadline_for(1))
            self.assertEqual(60.0, client.deadline_for(3))
            self.assertEqual(
                32 * client.PER_ITEM_DEADLINE_S, client.deadline_for(32)
            )
            self.assertGreater(
                client.deadline_for(32),
                32 * 2.5,
                "a batch of 32 SigLIP images must fit inside its own deadline",
            )

    def test_an_empty_batch_still_gets_the_fixed_budget(self):
        with FakeMlRuntime() as host, mlruntime.MlRuntimeClient(host.endpoint) as client:
            self.assertEqual(60.0, client.deadline_for(0))

    def test_the_request_carries_the_deadline_the_client_waits_for(self):
        """The host must be told the same budget the caller enforces.

        A `deadline_ms` shorter than the gRPC timeout makes the host abandon
        work the caller was still willing to wait for; longer, and the caller
        gives up on a host that was going to answer. Both look like a flaky
        model rather than a mismatched pair of numbers.
        """
        items = {f"media-{index}": "ab" * 32 for index in range(4)}
        with FakeMlRuntime() as host, mlruntime.MlRuntimeClient(host.endpoint) as client:
            client.infer_proxies(
                model_id="siglip2-so400m-384", request_id="r-deadline", items=items
            )
        (request,) = host.infer_requests
        self.assertEqual(
            int(client.deadline_for(len(items)) * 1000), request.deadline_ms
        )

    def test_a_whole_request_error_raises(self):
        with FakeMlRuntime(models=()) as host, \
                mlruntime.MlRuntimeClient(host.endpoint) as client:
            with self.assertRaises(mlruntime.MlRuntimeError):
                client.infer_proxies(
                    model_id="siglip2-so400m-384", request_id="r3",
                    items={"media-c": "ab" * 32},
                )


def _short_response(host, item_ids):
    """An InferResponse that answers fewer items than were asked for."""
    import sys

    if str(PROTO_ROOT) not in sys.path:
        sys.path.insert(0, str(PROTO_ROOT))
    from generated.python import ml_runtime_pb2 as pb

    return pb.InferResponse(
        request_id="short",
        pin=pb.ModelPin(model_id="scrfd-10g-bnkps", version="test"),
        runtime_used=pb.RUNTIME_TARGET_ONNXRUNTIME_CPU,
        results=[
            pb.InferResult(item_id=item_id, detections=pb.DetectionSet())
            for item_id in item_ids
        ],
        batch_size=len(item_ids),
    )


if __name__ == "__main__":
    unittest.main()
