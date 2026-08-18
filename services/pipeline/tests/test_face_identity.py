"""Faces, end to end: detect -> align -> embed -> cluster -> assign -> album.

WHAT THIS SUITE IS ACTUALLY DEFENDING

Two failures, and they are opposites.

1. THE VACUOUS PASS. The album used to be planned with `faces=()`, so
   album-engine's `face_safety` reported `face_count: 0` on every placement and
   the print validator's trim-zone gate passed without checking anything.
   CLAUDE.md rule 5 -- "a wrong person in a family album is a catastrophic
   failure" -- rested on a gate that could not fail. The tests below assert
   that real rectangles now reach the layout, and that a photo claiming faces
   the library cannot show stops the book rather than being planned around.

2. THE CONFIDENT WRONG NAME. An embedding produced from a crop warped onto the
   wrong five-point template is a plausible 512-vector that clusters cleanly
   and puts a name on a face. Nothing downstream can detect it. So the pairing
   is refused at configuration time, the scheme is checked again per request,
   and a host whose detections disagree with its own config fails the stage.

And the outcome that is CORRECT and looks like a bug: zero faces eligible for
automated output. There is no calibration for these weights and no enrolled
person, so every face goes to review. A test that expected a non-zero
eligibility count would be asserting the failure this architecture exists to
prevent.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from support import (  # noqa: E402
    PRINT_SAFE_SIZE,
    FakeMlRuntime,
    make_library,
    require_ingest_binary,
)

from memory_engine_pipeline.mlruntime import FaceCrop, MlRuntimeError  # noqa: E402
from memory_engine_pipeline.runner import run_pipeline  # noqa: E402
from memory_engine_pipeline.stages.base import Settings, StageStatus  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"

PHOTOS = 6
STAGES = ["ingest", "analysis", "faces"]
WITH_ALBUM = ["ingest", "analysis", "faces", "ranking", "album"]


def _stage(report, name):
    for result in report.results:
        if result.stage == name:
            return result
    raise AssertionError(f"{name} did not run")


def _face_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(
        documents["face-record.schema.json"], registry=registry
    )


class FaceIdentityWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ingest_binary()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mep-face-src-"))
        self.workdir = Path(tempfile.mkdtemp(prefix="mep-face-work-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        make_library(self.root, PHOTOS, size=PRINT_SAFE_SIZE)

    def _run(self, host, *, stages=None, **overrides):
        settings = Settings(
            ml_runtime_endpoint=host.endpoint,
            render_print=False,
            render_video=False,
            **overrides,
        )
        return run_pipeline(
            [self.root],
            self.workdir,
            settings=settings,
            stages=list(stages or STAGES),
        )

    def _database(self):
        from memory_engine_media_db import Database

        return Database.open(self.workdir / "library.db")

    def _faces(self):
        with self._database() as database:
            return database.list_faces(limit=1000)

    # -- the happy path ---------------------------------------------------

    def test_every_detected_face_is_recorded_embedded_and_left_to_review(self):
        with FakeMlRuntime() as host:
            report = self._run(host)

        analysis = _stage(report, "analysis")
        faces = _stage(report, "faces")
        self.assertEqual(StageStatus.COMPLETED, analysis.status, analysis.detail)
        self.assertEqual(StageStatus.COMPLETED, faces.status, faces.detail)

        detected = analysis.counts["faces_found"]
        self.assertGreater(detected, 0, "the fixture library produced no faces at all")
        self.assertEqual(detected, analysis.counts["faces_embedded"])
        self.assertEqual(0, analysis.counts["faces_without_landmarks"])

        self.assertEqual(detected, faces.counts["faces"])
        self.assertEqual(detected, faces.counts["embedded"])
        self.assertEqual(0, faces.counts["without_embedding"])
        self.assertEqual(0, faces.counts["unreadable_embeddings"])
        self.assertGreater(faces.counts["clusters"], 0)

        # THE POINT. Not an accident of the fixture: `assign_identities` cannot
        # emit an eligible assignment while its calibrator is uncalibrated, and
        # no calibrated one exists.
        self.assertEqual(0, faces.counts["eligible_for_automated_output"])
        self.assertEqual(detected, faces.counts["awaiting_review"])
        self.assertIn("awaiting review", faces.detail)

    def test_every_face_record_satisfies_the_contract(self):
        with FakeMlRuntime() as host:
            self._run(host)

        validator = _face_validator()
        records = self._faces()
        self.assertTrue(records)
        for record in records:
            errors = sorted(
                f"{list(error.path)}: {error.message}"
                for error in validator.iter_errors(record)
            )
            self.assertEqual([], errors, record["face_id"][:12])

            self.assertFalse(record["identity"]["eligible_for_automated_output"])
            self.assertIsNone(record["identity"]["person_id"])
            # `unknown` is the schema default and is explicitly NOT adult.
            self.assertEqual("unknown", record["sensitive"]["minor_status"])
            self.assertEqual(
                "arcface_buffalo_l_512", record["embedding"]["space"]
            )
            self.assertEqual("insightface_5", record["landmarks"]["scheme"])
            self.assertEqual(5, len(record["landmarks"]["points"]))
            self.assertIsNotNone(record["cluster"])

    def test_the_stored_vector_is_the_one_the_record_points_at(self):
        with FakeMlRuntime() as host:
            self._run(host)

        with self._database() as database:
            for record in database.list_faces(limit=1000):
                reference = record["embedding"]
                values = database.vectors.get(
                    "face", reference["index_key"], reference["space"]
                )
                self.assertIsNotNone(values, record["face_id"][:12])
                self.assertEqual(reference["dimensions"], len(values))

    def test_the_review_queue_is_written_and_names_the_real_reason(self):
        with FakeMlRuntime() as host:
            report = self._run(host)

        path = Path(_stage(report, "faces").outputs[0])
        queue = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreater(queue["question_count"], 0)
        reasons = {question["reason"] for question in queue["questions"]}
        # `new_cluster` is the honest reason with an empty gallery: there is
        # nobody to match against. The contract's enum can carry it, unlike
        # `uncalibrated_threshold`, which is why the queue and the record can
        # disagree about detail without either of them lying.
        self.assertIn("new_cluster", reasons)

    # -- the album -------------------------------------------------------

    def test_the_album_is_planned_against_real_face_rectangles(self):
        with FakeMlRuntime() as host:
            report = self._run(host, stages=WITH_ALBUM)

        album = _stage(report, "album")
        self.assertEqual(StageStatus.COMPLETED, album.status, album.detail)
        spec = json.loads(Path(album.outputs[0]).read_text(encoding="utf-8"))

        with self._database() as database:
            stored = {
                record["media_id"]: 0 for record in database.list_faces(limit=1000)
            }
            for record in database.list_faces(limit=1000):
                stored[record["media_id"]] += 1

        counted = 0
        for page in spec["pages"]:
            for placement in page["placements"]:
                safety = placement["face_safety"]
                self.assertIsNotNone(safety)
                expected = stored.get(placement["media_id"], 0)
                # Not equality: a crop may exclude a face entirely, and
                # `face_safety` counts only the faces still visible. What must
                # never happen is a placement reporting MORE faces than the
                # library holds, or a photo with faces reporting none of them.
                self.assertLessEqual(safety["face_count"], expected)
                if expected:
                    self.assertGreater(
                        safety["face_count"], 0,
                        f"{placement['media_id'][:12]} has {expected} stored face(s) "
                        "and the placement reports none, which is the vacuous pass",
                    )
                    self.assertIsNotNone(safety["min_face_margin_mm"])
                counted += safety["face_count"]

        self.assertGreater(
            counted, 0,
            "no placement in the whole book carried a face, so the print "
            "validator's face gate was still checking nothing",
        )

    def test_the_album_refuses_a_photo_whose_faces_are_not_stored(self):
        """The vacuous pass, caught rather than planned around.

        Deleting the face rows while leaving `face_count` on the MediaRecord is
        exactly the state a library analysed by an older build is in, and it is
        indistinguishable -- to layout -- from a photo with no faces in it.
        """
        with FakeMlRuntime() as host:
            first = self._run(host, stages=WITH_ALBUM)
            self.assertEqual(
                StageStatus.COMPLETED, _stage(first, "album").status
            )

            with self._database() as database:
                with_faces = [
                    record["face_id"]
                    for record in database.list_faces(limit=1000)
                ]
                self.assertTrue(with_faces)
                for face_id in with_faces:
                    database.delete_face(face_id)

            second = self._run(host, stages=WITH_ALBUM)

        album = _stage(second, "album")
        self.assertEqual(StageStatus.FAILED, album.status)
        self.assertIn("face rectangle", album.detail)

    # -- the alignment guard ---------------------------------------------

    def test_a_detector_that_speaks_another_landmark_scheme_is_refused(self):
        """yunet_5 landmarks and an insightface_5 template.

        Both are five points; feeding one to the other's template produces a
        plausible warp and a wrong embedding. The pairing is refused before any
        photo is touched, so the failure names two models rather than arriving
        as a thousand identical per-face errors.
        """
        with FakeMlRuntime() as host:
            report = self._run(host, face_model="yunet-2023mar")

        analysis = _stage(report, "analysis")
        self.assertEqual(StageStatus.FAILED, analysis.status)
        self.assertIn("yunet_5", analysis.detail)
        self.assertIn("insightface_5", analysis.detail)
        self.assertEqual([], self._faces(), "a refused pairing wrote face records")

    def test_a_host_whose_detections_contradict_its_config_fails_the_stage(self):
        """The registry says insightface_5 and the host returns yunet_5.

        Then the pin does not describe what ran, and no landmark from that run
        can be trusted onto a template. This is not a per-face condition to log
        and continue past.
        """
        with FakeMlRuntime(landmark_scheme="yunet_5") as host:
            report = self._run(host)

        analysis = _stage(report, "analysis")
        self.assertEqual(StageStatus.FAILED, analysis.status)
        self.assertIn("disagree", analysis.detail)

    def test_a_face_with_no_landmarks_cannot_even_be_described_as_a_crop(self):
        """The guard that does not need a host to fire."""
        with self.assertRaises(MlRuntimeError) as raised:
            FaceCrop(
                item_id="a" * 64,
                proxy_id="b" * 64,
                landmarks=(),
                landmark_scheme="insightface_5",
            )
        self.assertIn("cannot be aligned", str(raised.exception))

    def test_the_client_refuses_a_mismatched_crop_before_it_is_sent(self):
        """The belt-and-braces guard, actually exercised.

        The stage's own checks mean a mismatched crop should never reach this
        point, so this is the layer that has no natural way to fire and is
        therefore the one most likely to be wrong without anybody noticing.
        Asserting that the request never reached the host is the whole test:
        a guard that raises AFTER sending has not prevented anything.
        """
        crop = FaceCrop(
            item_id="a" * 64,
            proxy_id="b" * 64,
            landmarks=((0.1, 0.1), (0.2, 0.1), (0.15, 0.15), (0.11, 0.2), (0.19, 0.2)),
            landmark_scheme="yunet_5",
        )
        with FakeMlRuntime() as host:
            from memory_engine_pipeline.mlruntime import MlRuntimeClient

            with MlRuntimeClient(host.endpoint) as client, self.assertRaises(
                MlRuntimeError
            ) as raised:
                client.infer_faces(
                    model_id="arcface-buffalo-l",
                    request_id="mismatched-scheme",
                    crops=[crop],
                    required_landmark_scheme="insightface_5",
                )
            self.assertEqual(
                [], host.face_embedding_requests,
                "the mismatched crop was sent and only then complained about",
            )
        message = str(raised.exception)
        self.assertIn("yunet_5", message)
        self.assertIn("insightface_5", message)

    def test_the_accepted_schemes_are_exactly_the_protos(self):
        """The set in mlruntime.py is written out; this is what keeps it true.

        A scheme added to ml_runtime.proto and not here is refused by FaceCrop,
        which reads as "unknown scheme" rather than as "nobody updated the
        client". A scheme here and not in the proto raises at send time. Both
        are survivable and neither is discoverable, so the disagreement itself
        is the failure this asserts against.
        """
        from memory_engine_pipeline import mlruntime as client_module
        from memory_engine_pipeline.mlruntime import _load_stubs

        _grpc, pb, _stub = _load_stubs()
        from_proto = {
            name.removeprefix("LANDMARK_SCHEME_").lower()
            for name in pb.LandmarkScheme.keys()
            if name != "LANDMARK_SCHEME_UNSPECIFIED"
        }
        self.assertEqual(from_proto, set(client_module._LANDMARK_SCHEMES))

    # -- the defects a review found ---------------------------------------

    def test_two_detections_on_one_grid_cell_are_one_face_not_two(self):
        """A count that outruns the rectangles bricks the book permanently.

        `face_id` quantises the box to 1e-4 of the frame, so a detector that
        returns the same box twice produces one id. Counting the detections
        rather than the ids left `face_count: 2` against a single stored row,
        which the album stage reads -- correctly -- as face evidence it cannot
        see, and refuses. Nothing an operator can do fixes it: re-detecting
        reproduces it exactly.
        """
        box = (0.10, 0.20, 0.12, 0.16)
        with FakeMlRuntime(face_boxes=lambda _proxy_id: (box, box)) as host:
            report = self._run(host, stages=WITH_ALBUM)

        analysis = _stage(report, "analysis")
        self.assertEqual(StageStatus.COMPLETED, analysis.status, analysis.detail)
        self.assertEqual(
            StageStatus.COMPLETED, _stage(report, "album").status,
            _stage(report, "album").detail,
        )

        with self._database() as database:
            self.assertEqual(database.count_faces(), analysis.counts["faces_found"])
            for record in database.list_faces(limit=1000):
                media = database.get_media(record["media_id"])
                ids = media["faces"]["face_ids"]
                self.assertEqual(len(set(ids)), len(ids), "a face_id was listed twice")
                self.assertEqual(media["faces"]["face_count"], len(ids))

    def test_a_library_holding_two_vector_spaces_is_refused_not_clustered(self):
        """Two spaces have no distance between them, only a plausible number.

        common.schema.json: "Two vectors may only be compared when their space
        matches exactly, including the model version that produced them."
        Before the stage read each record's own space, a vector moved into
        `adaface_ir101_512` was clustered against arcface vectors with no
        error and no note.
        """
        with FakeMlRuntime() as host:
            self._run(host)

            with self._database() as database:
                victim = database.list_faces(limit=1000)[0]
                reference = victim["embedding"]
                values = database.vectors.get(
                    "face", reference["index_key"], reference["space"]
                )
                database.vectors.put(
                    "face", victim["face_id"], "adaface_ir101_512", values
                )
                database.vectors.delete(
                    "face", victim["face_id"], reference["space"]
                )
                victim["embedding"] = dict(reference, space="adaface_ir101_512")
                database.put_face(victim)
                database.connection.execute(
                    "DELETE FROM job WHERE job_type = 'cluster_faces'"
                )
                database.connection.commit()

            report = self._run(host)

        faces = _stage(report, "faces")
        self.assertEqual(StageStatus.FAILED, faces.status)
        self.assertIn("adaface_ir101_512", faces.detail)
        self.assertIn("arcface_buffalo_l_512", faces.detail)

    def test_a_human_answer_about_a_child_survives_re_detection(self):
        """`--reanalyze-faces` rewrites the same face_id; it must not forget it.

        Measured before this was fixed: `minor_status` reverted from
        `estimated_minor` to `unknown` and `excluded_from_sharing` from true to
        false, silently, and `created_at` moved to the re-detection. Hard rule
        7 -- no silent data loss -- and it failed in the unsafe direction.
        """
        with FakeMlRuntime() as host:
            self._run(host)
            with self._database() as database:
                target = database.list_faces(limit=1000)[0]
                target["sensitive"]["minor_status"] = "estimated_minor"
                target["sensitive"]["excluded_from_sharing"] = True
                database.put_face(target)
            face_id = target["face_id"]
            created_at = target["created_at"]

            self._run(host, reanalyze_faces=True)

        with self._database() as database:
            after = [
                record
                for record in database.list_faces(limit=1000)
                if record["face_id"] == face_id
            ][0]
        self.assertEqual("estimated_minor", after["sensitive"]["minor_status"])
        self.assertTrue(after["sensitive"]["excluded_from_sharing"])
        self.assertEqual(created_at, after["created_at"])
        self.assertNotEqual(created_at, after["updated_at"])
        # And it is still not eligible, which `estimated_minor` guarantees on
        # its own even once a calibration exists.
        self.assertFalse(after["identity"]["eligible_for_automated_output"])

    def test_a_user_confirmed_identity_survives_re_detection(self):
        """A stable face_id keeps the human decision, not the new model default.

        The sensitive-envelope regression above is deliberately not enough:
        before this test, re-detection restored that envelope while silently
        replacing a user-confirmed name with an unassigned model decision.
        """
        person_id = "3f2a91c4-7b6e-4d1a-9c85-2e4b7a1f6d03"
        decided_at = "2026-08-18T12:00:00Z"
        with FakeMlRuntime() as host:
            self._run(host, stages=["ingest", "analysis"])
            with self._database() as database:
                target = database.list_faces(limit=1000)[0]
                target["sensitive"]["minor_status"] = "confirmed_adult"
                target["identity"] = {
                    "person_id": person_id,
                    "assignment": "user_confirmed",
                    "confidence": 1.0,
                    "threshold_profile": "automated_output",
                    "threshold_used": 0.92,
                    "eligible_for_automated_output": True,
                    "candidates": [],
                    "review_reason": None,
                    "decided_by": "user",
                    "decided_at": decided_at,
                }
                database.put_face(target)
                expected_identity = dict(target["identity"])
                face_id = target["face_id"]
                media_id = target["media_id"]

            report = self._run(
                host,
                stages=["ingest", "analysis"],
                reanalyze_faces=True,
            )

        analysis = _stage(report, "analysis")
        self.assertEqual(StageStatus.COMPLETED, analysis.status, analysis.detail)
        with self._database() as database:
            after = next(
                record
                for record in database.list_faces(limit=1000)
                if record["face_id"] == face_id
            )
        self.assertIsNotNone(after)
        self.assertEqual(expected_identity, after["identity"])
        self.assertEqual(person_id, after["identity"]["person_id"])
        self.assertTrue(after["identity"]["eligible_for_automated_output"])
        self.assertEqual("user", after["identity"]["decided_by"])
        with self._database() as database:
            summary = database.get_media(media_id)["faces"]
        self.assertIn(person_id, summary["confirmed_person_ids"])
        self.assertEqual(summary["face_count"] - 1, summary["pending_review_count"])

    def test_per_face_embedding_errors_fail_the_affected_media_and_stage(self):
        """A host answer saying every face failed is not a completed analysis."""
        with FakeMlRuntime() as host:
            first = self._run(host, stages=["ingest", "analysis"])
            self.assertEqual(
                StageStatus.COMPLETED,
                _stage(first, "analysis").status,
                _stage(first, "analysis").detail,
            )
            with self._database() as database:
                faces = database.list_faces(limit=1000)
            face_ids = {face["face_id"] for face in faces}
            affected_media = {face["media_id"] for face in faces}
            self.assertTrue(face_ids, "the fixture produced no face failures to test")
            host.fail_items = frozenset(face_ids)

            report = self._run(
                host,
                stages=["ingest", "analysis"],
                reanalyze_faces=True,
            )

        analysis = _stage(report, "analysis")
        self.assertEqual(StageStatus.FAILED, analysis.status, analysis.detail)
        self.assertEqual(2, report.exit_code)
        self.assertEqual(len(face_ids), analysis.counts["faces_embedding_failed"])
        self.assertEqual(
            {
                "retryable": 0,
                "non_retryable": len(face_ids),
                "by_reason": {"proxy_not_found": len(face_ids)},
            },
            analysis.counts["face_embedding_failures"],
        )
        self.assertGreater(analysis.counts["still_pending"], 0)

        with self._database() as database:
            stored_faces = {
                face["face_id"]: face for face in database.list_faces(limit=1000)
            }
            for media_id in affected_media:
                media = database.get_media(media_id)
                step = media["processing"]["stages"]["face_embedding"]
                self.assertEqual("failed", step["status"], media_id)
                self.assertFalse(step["last_error"]["retryable"], media_id)
                self.assertEqual("analyzing", media["processing"]["state"])
            for face_id in face_ids:
                self.assertIsNone(stored_faces[face_id]["embedding"])

    def test_a_face_with_no_landmarks_is_kept_and_never_embedded(self):
        """An unembeddable face is still a face.

        face-record.schema.json keeps it "because it counts toward 'how many
        people are in this photo'", and -- the part that matters here -- its
        rectangle still protects it from the guillotine.
        """
        with FakeMlRuntime(emit_landmarks=False) as host:
            report = self._run(host)

        analysis = _stage(report, "analysis")
        self.assertEqual(StageStatus.COMPLETED, analysis.status, analysis.detail)
        self.assertGreater(analysis.counts["faces_found"], 0)
        self.assertEqual(0, analysis.counts["faces_embedded"])
        self.assertEqual(
            analysis.counts["faces_found"], analysis.counts["faces_without_landmarks"]
        )

        faces = _stage(report, "faces")
        self.assertEqual(0, faces.counts["embedded"])
        self.assertEqual(faces.counts["faces"], faces.counts["without_embedding"])
        self.assertEqual(0, faces.counts["eligible_for_automated_output"])

        for record in self._faces():
            self.assertIsNone(record["embedding"])
            self.assertIsNone(record["landmarks"])
            self.assertEqual("unassigned", record["identity"]["assignment"])

    # -- resumption and re-runs -------------------------------------------

    def test_a_second_run_re_clusters_nothing(self):
        with FakeMlRuntime() as host:
            self._run(host)
            host.infer_calls.clear()
            second = self._run(host)

        self.assertEqual([], host.infer_calls)
        self.assertEqual(StageStatus.SKIPPED, _stage(second, "faces").status)

    def test_reanalyze_faces_redoes_the_face_steps_and_only_those(self):
        with FakeMlRuntime() as host:
            self._run(host)
            before = {record["face_id"] for record in self._faces()}
            host.infer_calls.clear()
            report = self._run(host, reanalyze_faces=True)

        models = [model for model, _count in host.infer_calls]
        self.assertIn("scrfd-10g-bnkps", models)
        self.assertIn("arcface-buffalo-l", models)
        self.assertNotIn(
            "siglip2-so400m-384", models,
            "--reanalyze-faces re-ran the image embedding, which it must not",
        )
        self.assertEqual(
            StageStatus.COMPLETED, _stage(report, "analysis").status,
            _stage(report, "analysis").detail,
        )
        # Same detector, same version, same boxes: content addressing means the
        # ids are identical rather than duplicated.
        self.assertEqual(before, {record["face_id"] for record in self._faces()})


if __name__ == "__main__":
    unittest.main(verbosity=2)
