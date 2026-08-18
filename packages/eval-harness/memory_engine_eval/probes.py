"""The things that actually measure. One probe per benchmark case.

A probe runs code that is on disk right now and returns a number in [0,1]. It is
the half of a benchmark that a comparator cannot supply: `harness.py` is
excellent at refusing an incomparable comparison and says nothing at all about
whether anybody ran anything.

WHAT IS AND IS NOT MEASURABLE HERE, STATED ONCE

There are no photographs in this repository, and the user's own photographs are
not a test fixture. What genuinely exists is `scripts/demo/make_library.py`'s
synthetic library, three real ONNX checkpoints on a developer machine, and the
deterministic packages themselves. So every probe here measures one of exactly
two things:

  * DETERMINISM -- the same input produces the same output, ids are stable under
    permutation, a hard gate fires on a layout built to violate it. These are
    honest on synthetic data because they are properties of the CODE, not claims
    about photographs.
  * PLUMBING -- the code path ran and returned the shape it promised. A graph
    whose real input tensor is named what the registry says it is named has
    demonstrated that the config describes the checkpoint. It has demonstrated
    nothing about whether the model is any good.

No probe here measures QUALITY, and `benchmarks.load_suite` will not let one
claim to (`Probe.claim_ceiling`). `docs/benchmark-libraries.md` states exactly
what has to exist before a quality claim is possible.

EVERY PROBE CAN BE BROKEN ON PURPOSE

`Probe.falsifications` names the deliberate breaks the probe implements, and
`measure(..., falsify=mode)` applies one. This is not a debugging aid. A
benchmark nobody has watched fail is a number that happens to be printed, and
`tests/test_falsification.py` runs every declared break and asserts the case
drops below the bound the case declares. That test is the reason a case may be
believed at all.

Every falsification LOWERS the score. That direction is not a convention, it is
the only one that can be asserted: a break that leaves the number at 1.0 is
indistinguishable from a break that did nothing, so it demonstrates nothing.
Where the interesting failure would RAISE a score -- "a verification that reads
the manifest back to itself scores a perfect 1.0" -- the break is expressed on
the input side instead (edit the manifest's declared digest, and a probe that
genuinely recomputes must disagree with it), which is the same claim in a form a
test can hold.

The breaks come in two flavours and both are used:
  * break the INPUT (rot the pHash bits, feed a clean layout where a violating
    one belongs, edit a declared digest) -- proves the probe reads its inputs;
  * break the CONFIG (open the decisive Hamming threshold to 64, read a clock
    for `validated_at`, hand out group ids by arrival order) -- proves the probe
    is sensitive to the decision the code under test actually makes.
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .library import ClaimClass, ResolvedLibrary, file_digest

try:
    from blake3 import blake3
except ImportError as error:  # pragma: no cover - dependency guard
    # Deliberately fatal. Falling back to blake2b would produce a digest that
    # claims to be a content address, is not, and compares unequal to every
    # digest the rest of the repo computes -- which would read as a stale input
    # rather than as a missing dependency.
    raise ImportError(
        "memory_engine_eval.probes requires blake3; content-addressed identity is "
        "load-bearing here and there is no acceptable fallback"
    ) from error

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
INPUT_DIR = PACKAGE_ROOT / "benchmarks" / "inputs"

# The intelligence packages are not installed; they are imported from the
# monorepo the way every other cross-package test in this repository does it.
for _package in ("ranking-engine", "album-engine", "story-engine"):
    _path = str(REPO_ROOT / "packages" / _package)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from memory_engine_album import validator as _validator  # noqa: E402
from memory_engine_ranking import dedupe as _dedupe  # noqa: E402
from memory_engine_story import reel as _reel  # noqa: E402

__all__ = ["PROBES", "Probe", "ProbeContext", "ProbeError", "digest_strings"]

DEDUPE_SOURCE = (
    REPO_ROOT / "packages" / "ranking-engine" / "memory_engine_ranking" / "dedupe.py"
)
VALIDATOR_SOURCE = (
    REPO_ROOT / "packages" / "album-engine" / "memory_engine_album" / "validator.py"
)
REEL_SOURCE = REPO_ROOT / "packages" / "story-engine" / "memory_engine_story" / "reel.py"
MODELS_DIR = REPO_ROOT / "models"

# Pinned rather than read from a clock. A validation report that embeds "now" is
# not reproducible, and the whole point of `album_report_determinism` is to
# notice when one starts to be.
FIXED_VALIDATED_AT = "2026-03-17T14:22:11+05:30"


class ProbeError(Exception):
    """The probe could not run. Never a score of zero.

    A probe that returned 0.0 when its input was missing would put "we could not
    measure this" and "the code is completely broken" on the same axis, and a
    waiver written for the second would silently forgive the first. The runner
    maps this onto EXIT_REFUSED, which is never green.
    """


def digest_strings(parts: Sequence[str]) -> str:
    """BLAKE3 over length-prefixed UTF-8 fields, in the given order.

    Same encoding as `library.inventory_digest`, for the same reason: a digest
    over a re-serialisation is a digest over whichever language wrote it. The
    length prefix is what stops ("ab", "c") and ("a", "bc") digesting alike.
    """
    hasher = blake3()
    for part in parts:
        raw = part.encode("utf-8")
        hasher.update(len(raw).to_bytes(4, "big"))
        hasher.update(raw)
    return hasher.hexdigest()


@dataclass(frozen=True)
class ProbeContext:
    """What the environment has available. Absent things stay None.

    Never a flag saying "skip the checks". A probe whose requirement is unmet
    raises ProbeError, and the runner reports the suite as unmeasured, which is
    a refusal rather than a pass.
    """

    library: ResolvedLibrary | None = None
    weights_root: Path | None = None


@dataclass(frozen=True)
class Probe:
    """One measurable thing.

    `sources` are the files whose bytes become the run's `weights_blake3`. This
    is the honest answer to "what model produced this number" for a benchmark
    with no model: the code did, and here is its digest. Editing `dedupe.py`
    moves the candidate's pins, so `harness` stops calling the run a no-op and
    compares it as a real delta -- which is exactly what should happen when
    somebody changes the clustering and burst recovery falls.
    """

    probe_id: str
    metric_name: str
    direction: str
    claim_ceiling: ClaimClass
    requires: tuple[str, ...]
    param_names: tuple[str, ...]
    falsifications: tuple[str, ...]
    sources: tuple[Path, ...]
    load: Callable[[ProbeContext, Mapping[str, Any]], tuple[Any, str]]
    measure: Callable[..., float]

    def sources_digest(self) -> str:
        """BLAKE3 over the source files' bytes, in declared order.

        Repo-relative paths are digested alongside the content so that moving a
        file is a change rather than a silent no-op.
        """
        parts: list[str] = []
        for path in self.sources:
            if not path.is_file():
                raise ProbeError(f"{self.probe_id}: source {path} does not exist")
            parts.append(str(path.relative_to(REPO_ROOT)))
            parts.append(file_digest(path))
        return digest_strings(parts)


# ==========================================================================
# Shared input: the recorded perceptual hashes of the synthetic library
# ==========================================================================

PHASH_FIXTURE = INPUT_DIR / "synthetic-demo-phash.json"


def _load_phash_fixture() -> Mapping[str, Any]:
    try:
        document = json.loads(PHASH_FIXTURE.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProbeError(
            f"no recorded pHash fixture at {PHASH_FIXTURE}; regenerate it with "
            "`python3 -m memory_engine_eval.record_inputs`"
        ) from error
    for key in ("library_id", "library_version", "algorithm", "items", "bursts"):
        if key not in document:
            raise ProbeError(f"{PHASH_FIXTURE} is missing {key!r}")
    if not document["items"]:
        raise ProbeError(f"{PHASH_FIXTURE} records no items")
    if not document["bursts"]:
        raise ProbeError(f"{PHASH_FIXTURE} declares no bursts to recover")
    return document


def _phash_inputs(_context: ProbeContext, _params: Mapping[str, Any]):
    """The recorded hashes, plus a digest over exactly what was read.

    The digest covers (media_id, phash_hex, quality) for every item and the burst
    membership. Shorten the fixture, edit one hash, or drop a burst, and the
    digest moves -- so `harness` refuses the comparison instead of reporting a
    delta between two different benchmarks. This is the check a hand-editable
    fixture most needs, because it is the file somebody edits to make a red build
    green.
    """
    document = _load_phash_fixture()
    items = sorted(document["items"], key=lambda item: item["media_id"])
    parts: list[str] = [
        document["library_id"],
        str(document["library_version"]),
        document["algorithm"],
    ]
    for item in items:
        parts.extend([item["media_id"], item["phash_hex"], repr(item["quality"])])
    for burst_id in sorted(document["bursts"]):
        parts.append(burst_id)
        parts.extend(sorted(document["bursts"][burst_id]))
    return (items, document["bursts"]), digest_strings(parts)


def _candidates(
    items: Sequence[Mapping[str, Any]], *, rot: Mapping[str, int] | None = None
) -> list[_dedupe.Candidate]:
    """Build dedupe Candidates from the recorded hashes.

    No embeddings, deliberately. There is no image embedder with a pinned
    checkpoint in the registry, so a benchmark that fed embeddings would be
    measuring a stand-in. Without them the pHash is the sole arbiter and
    `DEFAULT_HAMMING_DECISIVE_THRESHOLD` is the decision under test -- which is
    the more dangerous path anyway, since it is the one that runs on a machine
    with no embedder.
    """
    rot = rot or {}
    out: list[_dedupe.Candidate] = []
    for item in items:
        bits = int(item["phash_hex"], 16)
        flip = rot.get(item["media_id"], 0)
        if flip:
            # Flip the `flip` lowest bits. A pHash differing in 20+ bits is a
            # different picture as far as any threshold is concerned.
            bits ^= (1 << flip) - 1
        out.append(
            _dedupe.Candidate(
                media_id=item["media_id"],
                phash_hex=f"{bits:016x}",
                phash_bits=64,
                quality=item["quality"],
            )
        )
    return out


def _membership(groups: Sequence[Any]) -> dict[str, frozenset[str]]:
    return {
        member: frozenset(group.members) for group in groups for member in group.members
    }


def _thresholds(params: Mapping[str, Any]) -> dict[str, int]:
    return {
        "hamming_threshold": params.get(
            "hamming_threshold", _dedupe.DEFAULT_HAMMING_THRESHOLD
        ),
        "hamming_decisive_threshold": params.get(
            "hamming_decisive_threshold", _dedupe.DEFAULT_HAMMING_DECISIVE_THRESHOLD
        ),
    }


# --------------------------------------------------------------------------
# Probe: dedupe recovers the declared bursts, exactly
# --------------------------------------------------------------------------


def _measure_burst_recovery(
    inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    items, bursts = inputs
    thresholds = _thresholds(params)
    rot: dict[str, int] = {}

    if falsify == "phash_bit_rot":
        # Break the INPUT: rot the last frame of every burst so it is no longer
        # near its siblings. A probe still reporting perfect recovery is not
        # reading the hashes it claims to read.
        for members in bursts.values():
            rot[sorted(members)[-1]] = 24
    elif falsify == "decisive_threshold_zero":
        # Break the CONFIG: demand byte-identical hashes. One of the two bursts
        # in this library happens to hash identically across all five frames and
        # survives; the other differs by 2 bits and does not. So this break can
        # only reach 0.5, and the bound in the suite says so -- a falsification
        # is an upper bound on the broken score, not a claim that everything
        # collapses.
        thresholds["hamming_decisive_threshold"] = 0
    elif falsify is not None:
        raise ProbeError(f"unknown falsification {falsify!r}")

    membership = _membership(_dedupe.find_duplicates(_candidates(items, rot=rot), **thresholds))

    recovered = 0
    for members in bursts.values():
        declared = frozenset(members)
        # EXACTLY the declared set: a group that also swept in a sixth photo is
        # not a recovered burst, it is a merge that silently drops a photo from
        # every automated output. Recall alone would score that 1.0.
        if all(membership.get(member) == declared for member in declared):
            recovered += 1
    return recovered / len(bursts)


BURST_RECOVERY = Probe(
    probe_id="dedupe_burst_recovery",
    metric_name="burst_partition_exactness",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=("hamming_threshold", "hamming_decisive_threshold"),
    falsifications=("phash_bit_rot", "decisive_threshold_zero"),
    sources=(DEDUPE_SOURCE,),
    load=_phash_inputs,
    measure=_measure_burst_recovery,
)


# --------------------------------------------------------------------------
# Probe: the bursts stay apart, and neither swallows a neighbour
# --------------------------------------------------------------------------


def _measure_burst_separation(
    inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """1 - (contaminated burst members / all burst members).

    Contamination is a burst member grouped with anything that is not one of its
    own siblings. Asymmetric on purpose, matching `dedupe.py`'s own reason for an
    asymmetric threshold: a false merge deletes a photo and says nothing, a false
    split shows the user two similar photos, and only the second is recoverable
    by the person looking at it.
    """
    items, bursts = inputs
    thresholds = _thresholds(params)

    if falsify == "collapse_to_one_hash":
        # Break the INPUT: give every item the same hash. Nothing distinguishes
        # anything, so a probe that still scores 1.0 is counting something other
        # than what it says it counts.
        items = [dict(item, phash_hex="0" * 16) for item in items]
    elif falsify == "foreign_frames_injected":
        # Break the INPUT precisely: copy one burst's hash onto three photos
        # that are NOT in it. The library keeps its size, its file set and every
        # other hash; only that burst is now contaminated. This is the failure
        # the asymmetric threshold exists to prevent -- a merge that deletes a
        # photo from every automated output -- and a purity measure that missed
        # it would be measuring nothing.
        burst_ids = {member for members in bursts.values() for member in members}
        victim = sorted(bursts)[0]
        stolen = next(
            item["phash_hex"]
            for item in items
            if item["media_id"] == sorted(bursts[victim])[0]
        )
        outsiders = [item for item in items if item["media_id"] not in burst_ids][:3]
        outsider_ids = {item["media_id"] for item in outsiders}
        items = [
            dict(item, phash_hex=stolen) if item["media_id"] in outsider_ids else item
            for item in items
        ]
    elif falsify is not None:
        raise ProbeError(f"unknown falsification {falsify!r}")

    membership = _membership(_dedupe.find_duplicates(_candidates(items), **thresholds))

    total = 0
    contaminated = 0
    for members in bursts.values():
        declared = frozenset(members)
        for member in declared:
            total += 1
            group = membership.get(member, frozenset({member}))
            if not group <= declared:
                contaminated += 1
    if total == 0:  # pragma: no cover - _load_phash_fixture refuses an empty set
        raise ProbeError("the fixture declares no burst members")
    return 1.0 - contaminated / total


BURST_SEPARATION = Probe(
    probe_id="dedupe_burst_separation",
    metric_name="burst_purity",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=("hamming_threshold", "hamming_decisive_threshold"),
    falsifications=("collapse_to_one_hash", "foreign_frames_injected"),
    sources=(DEDUPE_SOURCE,),
    load=_phash_inputs,
    measure=_measure_burst_separation,
)


# --------------------------------------------------------------------------
# Probe: the same library produces byte-identical ids
# --------------------------------------------------------------------------


def _measure_id_stability(
    inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """Group ids, primaries and membership, under permuted input order.

    `dedupe.py` promises "same library in, same groups and same primaries out,
    every time", and derives the group id from membership so a re-run keeps its
    identifiers without a database round-trip. That promise is exactly the kind
    that holds until a dict iteration order changes, and it is invisible when it
    breaks: the album is still an album, it just contains a different photo than
    it did last week.

    Score: the fraction of permutations whose full (group_id, primary, sorted
    members) signature matches the first ordering's.
    """
    items, _bursts = inputs
    permutations = int(params.get("permutations", 4))
    if permutations < 2:
        raise ProbeError("id stability needs at least two orderings to compare")
    if falsify not in (None, "group_ids_by_arrival", "primary_by_arrival"):
        raise ProbeError(f"unknown falsification {falsify!r}")

    def signature(ordered: Sequence[Mapping[str, Any]]) -> tuple:
        candidates = _candidates(ordered)
        group_id_for = None
        if falsify == "group_ids_by_arrival":
            # Break the CONFIG: number the groups by where their anchor first
            # appears in THIS ordering -- what an implementation that numbered
            # groups as it walked the input would produce. `group_id_for` is a
            # real public parameter of find_duplicates, so this is the actual
            # failure mode rather than a mock of it.
            position = {item["media_id"]: index for index, item in enumerate(ordered)}
            group_id_for = {
                min(group.members): f"group-{position[min(group.members)]:04d}"
                for group in _dedupe.find_duplicates(candidates)
            }
        groups = _dedupe.find_duplicates(candidates, group_id_for=group_id_for)
        rows = []
        for group in groups:
            primary = group.primary_media_id
            if falsify == "primary_by_arrival":
                # Break the CONFIG: take the member that arrived first instead
                # of the one `select_primary` chose. This is the shape the
                # defect takes in practice -- a primary picked while iterating
                # rather than by the documented precedence -- and it silently
                # changes which photo reaches the album.
                position = {item["media_id"]: i for i, item in enumerate(ordered)}
                primary = min(group.members, key=lambda m: position[m])
            rows.append((group.group_id, primary, tuple(sorted(group.members))))
        return tuple(sorted(rows))

    base = signature(list(items))
    agreeing = 0
    for index in range(1, permutations):
        shuffled = list(items)
        random.Random(index).shuffle(shuffled)
        agreeing += signature(shuffled) == base
    return agreeing / (permutations - 1)


ID_STABILITY = Probe(
    probe_id="dedupe_id_stability",
    metric_name="id_agreement_under_permutation",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=("permutations",),
    falsifications=("group_ids_by_arrival", "primary_by_arrival"),
    sources=(DEDUPE_SOURCE,),
    load=_phash_inputs,
    measure=_measure_id_stability,
)


# ==========================================================================
# The print validator's hard gates
# ==========================================================================

PROFILE_PATH = (
    REPO_ROOT
    / "packages"
    / "album-engine"
    / "vendor_profiles"
    / "layflat-300-square.json"
)

_BIG_MEDIA = "1" * 64
_SMALL_MEDIA = "2" * 64
_BIG_SOURCE = (6000, 4000)
_SMALL_SOURCE = (1600, 1067)

HARD_GATES = (
    "dpi_floor",
    "face_in_trim_zone",
    "bleed_coverage",
    "color_profile_match",
    "page_count_valid",
)

_DOCUMENT_OK = {"icc_name": "FOGRA39 Coated", "intent": "relative_colorimetric"}


def _face_safety(trim: int = 0, margin: float | None = None) -> dict[str, Any]:
    return {
        "face_count": 1 if trim else 0,
        "all_faces_in_safe_zone": trim == 0,
        "faces_in_gutter": 0,
        "faces_in_trim_zone": trim,
        "min_face_margin_mm": margin,
        "cropped_face_ids": [],
    }


def _placement(
    placement_id: str = "p1",
    *,
    media_id: str = _BIG_MEDIA,
    source: tuple[int, int] = _BIG_SOURCE,
    x: float = 51.0,
    y: float = 86.0,
    width_mm: float = 203.9,
    height_mm: float = 135.9,
    crop: float = 0.86,
    bleeds: Sequence[str] = (),
    safety: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A self-consistent placement.

    `effective_dpi` is COMPUTED from the frame, the crop and the source, never
    passed in. A placement whose declared DPI disagreed with its own geometry
    would let the dpi_floor case pass or fail for a reason that has nothing to
    do with the gate under test.
    """
    dpi = min(
        crop * source[0] * _validator.MM_PER_INCH / width_mm,
        crop * source[1] * _validator.MM_PER_INCH / height_mm,
    )
    return {
        "placement_id": placement_id,
        "media_id": media_id,
        "frame": {
            "x_mm": x,
            "y_mm": y,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "rotation_deg": 0,
        },
        "crop": {
            "x": (1.0 - crop) / 2.0,
            "y": (1.0 - crop) / 2.0,
            "w": crop,
            "h": crop,
            "rotation_deg": 0,
        },
        "effective_dpi": dpi,
        "z_index": 0,
        "bleeds": list(bleeds),
        "is_hero": False,
        "face_safety": _face_safety() if safety is None else dict(safety),
        "enhancement_ops": [],
        "caption": None,
        "border": None,
    }


def _page(page_index: int, placements: Sequence[Mapping[str, Any]] | None = None):
    return {
        "page_index": page_index,
        "spread_id": f"spread-{page_index // 2:02d}",
        "side": "left" if page_index % 2 == 0 else "right",
        "section_id": None,
        "background": None,
        "placements": (
            [_placement(f"p{page_index}")]
            if placements is None
            else [dict(p) for p in placements]
        ),
        "text_blocks": [],
        "layout": None,
    }


def _clean_album(count: int = 20) -> list[dict[str, Any]]:
    return [_page(index) for index in range(count)]


def _sources() -> dict[str, Any]:
    return {
        _BIG_MEDIA: _validator.SourceImage(*_BIG_SOURCE),
        _SMALL_MEDIA: _validator.SourceImage(*_SMALL_SOURCE),
    }


def _profile() -> dict[str, Any]:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _violating(gate: str) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """(pages, profile, document_color) built to violate exactly one hard gate.

    One violation at a time. An album breaking three gates at once would let the
    probe score 3/5 while two of the gates were dead, because every failure would
    be attributed to whichever gate happened to be checked.
    """
    pages = _clean_album()
    profile = _profile()
    document = dict(_DOCUMENT_OK)

    if gate == "dpi_floor":
        # A 1600px phone-era JPEG across a 203.9mm frame prints at ~171 DPI. It
        # is the only way under a 300 DPI floor on a book this size: 6000px of
        # source cannot be stretched thin enough to fail.
        pages[4] = _page(
            4, [_placement("p4", media_id=_SMALL_MEDIA, source=_SMALL_SOURCE)]
        )
    elif gate == "face_in_trim_zone":
        # -9.0mm: 9mm past the 8mm safe boundary, so the face is 1mm PAST the
        # trim line and the blade goes through it. A smaller intrusion (-2.5mm,
        # which this layout used first) is a warning and does not block an
        # export -- the validator draws that line at `guillotine_drift_mm`, and
        # a case built on the wrong side of it would have measured a gate that
        # never fires.
        pages[6] = _page(6, [_placement("p6", safety=_face_safety(trim=1, margin=-9.0))])
    elif gate == "bleed_coverage":
        # A placement DECLARING a top bleed whose frame stops 86mm short of the
        # page edge: the ink does not reach the trim, so the guillotine leaves a
        # white sliver down the cut edge.
        pages[8] = _page(8, [_placement("p8", bleeds=["top"])])
    elif gate == "color_profile_match":
        document = {"icc_name": "sRGB IEC61966-2.1", "intent": "perceptual"}
    elif gate == "page_count_valid":
        # 21 pages, against an increment of 2.
        pages = _clean_album(21)
    else:  # pragma: no cover - HARD_GATES is the only caller
        raise ProbeError(f"no violating layout defined for gate {gate!r}")
    return pages, profile, document


def _validate(pages, profile, document, sources=None):
    return _validator.validate_album(
        pages=pages,
        vendor_profile=profile,
        sources=_sources() if sources is None else sources,
        document_color=document,
        validated_at=FIXED_VALIDATED_AT,
    )


def _gate_fired(report, gate: str) -> bool:
    """A gate fired only if it produced a FAILED ERROR finding that counts.

    Three conditions, all load-bearing. A warning does not block an export, a
    passed finding is the gate saying the album is fine, and a rollup does not
    count toward totals. `docs/architecture.md` records a validator that passed
    every PDF it was handed because it was validating a plan the renderer did not
    execute; this is the shape of assertion that would have caught it.
    """
    return any(
        finding.check_id == gate
        and not finding.passed
        and finding.severity == "error"
        and finding.counts_toward_totals
        for finding in report.checks
    )


def _hard_gate_inputs(_context: ProbeContext, _params: Mapping[str, Any]):
    """Digest over the vendor profile and the gate list.

    The profile is a real committed file and every threshold these cases fire
    against comes out of it. Change the profile and the case's inputs have
    changed, so the comparison is refused rather than silently re-baselined.
    """
    if not PROFILE_PATH.is_file():
        raise ProbeError(f"no vendor profile at {PROFILE_PATH}")
    return None, digest_strings(
        [str(PROFILE_PATH.relative_to(REPO_ROOT)), file_digest(PROFILE_PATH), *HARD_GATES]
    )


def _measure_hard_gates(
    _inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    fired = 0
    for gate in HARD_GATES:
        if falsify == "violations_removed":
            # Break the INPUT: hand the validator a clean album where a
            # violating one belongs. Every gate must now stay silent, so a probe
            # still scoring 1.0 is not looking at the report at all.
            pages, profile, document = _clean_album(), _profile(), dict(_DOCUMENT_OK)
        elif falsify == "warnings_without_errors":
            # Break the INPUT differently: a clean album under an unreachable
            # PREFERRED dpi, which emits a warning on every page and no error. A
            # probe counting any non-passing finding would score 1.0 here. A
            # warning does not block an export, so it must score 0.
            pages, profile, document = _clean_album(), _profile(), dict(_DOCUMENT_OK)
            profile["dpi_preferred"] = 100000.0
        elif falsify is None:
            pages, profile, document = _violating(gate)
        else:
            raise ProbeError(f"unknown falsification {falsify!r}")
        if _gate_fired(_validate(pages, profile, document), gate):
            fired += 1
    return fired / len(HARD_GATES)


HARD_GATES_FIRE = Probe(
    probe_id="album_hard_gates_fire",
    metric_name="hard_gates_firing_fraction",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=(),
    falsifications=("violations_removed", "warnings_without_errors"),
    sources=(VALIDATOR_SOURCE,),
    load=_hard_gate_inputs,
    measure=_measure_hard_gates,
)


def _measure_clean_passes(
    _inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """The negative control for the gate above.

    Without it, a validator that failed absolutely everything would score a
    perfect 1.0 on `album_hard_gates_fire` and look like the safest print
    pipeline ever written. A print gate has two ways to be useless and a
    benchmark has to be able to tell them apart.
    """
    profile = _profile()
    document = dict(_DOCUMENT_OK)
    if falsify == "impossible_dpi_floor":
        # Break the CONFIG: no album can clear a 100000 DPI floor, so the clean
        # layout must stop passing.
        profile["dpi_floor"] = 100000.0
    elif falsify == "mismatched_document_profile":
        # Break the INPUT: the same clean pages, published in the wrong colour
        # space.
        document = {"icc_name": "sRGB IEC61966-2.1", "intent": "perceptual"}
    elif falsify is not None:
        raise ProbeError(f"unknown falsification {falsify!r}")
    report = _validate(_clean_album(), profile, document)
    return 1.0 if report.status == "pass" and report.error_count == 0 else 0.0


CLEAN_PASSES = Probe(
    probe_id="album_clean_layout_passes",
    metric_name="clean_layout_pass",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=(),
    falsifications=("impossible_dpi_floor", "mismatched_document_profile"),
    sources=(VALIDATOR_SOURCE,),
    load=_hard_gate_inputs,
    measure=_measure_clean_passes,
)


# --------------------------------------------------------------------------
# Probe: the same album produces a byte-identical validation report
# --------------------------------------------------------------------------


def _report_digest(report) -> str:
    parts = [
        report.status,
        str(report.error_count),
        str(report.warning_count),
        report.validator_version or "",
        report.validated_at or "",
    ]
    for finding in report.checks:
        parts.append(json.dumps(finding.to_dict(), sort_keys=True, separators=(",", ":")))
    return digest_strings(parts)


def _measure_report_determinism(
    _inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """Same AlbumSpec in, byte-identical report out -- under shuffled inputs.

    CLAUDE.md rule 3: "Same AlbumSpec = identical PDF." The report is the part of
    that chain this package owns, and the failure mode is ordering: a gate that
    iterates a dict, a findings list assembled in call order rather than in a
    fixed one, a `validated_at` read from a clock. All three produce a report
    that is right on Tuesday.

    The pages are shuffled between runs and the placements within a page are
    reversed on alternate runs, because a determinism check that feeds identical
    input in identical order tests almost nothing.
    """
    repeats = int(params.get("repeats", 4))
    if repeats < 2:
        raise ProbeError("determinism needs at least two runs to compare")
    if falsify not in (None, "clock_read_per_run", "perturb_source_resolution"):
        raise ProbeError(f"unknown falsification {falsify!r}")

    digests: list[str] = []
    for index in range(repeats):
        pages = _clean_album()
        # A validation report is keyed on page_index, not on list position, so
        # shuffling the list must not move a single field in the report.
        random.Random(index).shuffle(pages)
        if index % 2:
            for page in pages:
                page["placements"] = list(reversed(page["placements"]))

        validated_at = FIXED_VALIDATED_AT
        sources = _sources()
        if falsify == "clock_read_per_run" and index:
            # Break the CONFIG: a validator stamping its own "now" would produce
            # a different report every run. `validate_album` takes `validated_at`
            # as an argument precisely so it cannot; this proves the probe would
            # notice if that ever changed.
            validated_at = f"2026-03-17T14:22:{11 + index:02d}+05:30"
        if falsify == "perturb_source_resolution" and index:
            # Break the INPUT: one source a pixel wider. Every effective DPI in
            # the report moves, so the digest must move with it.
            sources[_BIG_MEDIA] = _validator.SourceImage(
                _BIG_SOURCE[0] + index, _BIG_SOURCE[1]
            )
        digests.append(
            _report_digest(
                _validator.validate_album(
                    pages=pages,
                    vendor_profile=_profile(),
                    sources=sources,
                    document_color=dict(_DOCUMENT_OK),
                    validated_at=validated_at,
                )
            )
        )
    return sum(digest == digests[0] for digest in digests[1:]) / (repeats - 1)


REPORT_DETERMINISM = Probe(
    probe_id="album_report_determinism",
    metric_name="report_digest_agreement",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=("repeats",),
    falsifications=("clock_read_per_run", "perturb_source_resolution"),
    sources=(VALIDATOR_SOURCE,),
    load=_hard_gate_inputs,
    measure=_measure_report_determinism,
)


# ==========================================================================
# Probe: the render plan is a pure function of the request
# ==========================================================================

_REEL_MEDIA_ID = "a" * 64
_REEL_RATE = 30000.0 / 1001.0


def _reel_request(**overrides):
    """A minimal, self-consistent ReelRequest.

    Built here rather than imported from `packages/story-engine/tests`: a
    benchmark that depends on a test module's private helper breaks the moment
    somebody refactors a test, and the benchmark would then be measuring
    whichever shape the helper drifted into.

    No music and no beat grid. Both are optional in the request, and leaving them
    out keeps this measuring the one thing it claims to -- that the plan is a
    pure function of its inputs -- rather than the beat snapper as well.
    """
    moments = overrides.pop(
        "moments",
        tuple(
            _reel.SelectedMoment(
                moment_id=f"{0xB0 + index:064x}",
                media_id=_REEL_MEDIA_ID,
                source_start=3031860.0 + index * 9000,
                source_duration=300.0,
                score=0.9 - index * 0.05,
                hook_potential=0.9 - index * 0.1,
                motion_energy=0.5 + index * 0.05,
                snap_points=(
                    _reel.SnapPoint(
                        time=3031860.0 + index * 9000,
                        kind="shot_boundary",
                        strength=0.9,
                        cut_direction="in",
                    ),
                ),
                safe_trim=_reel.SafeTrim(
                    earliest_in=3031860.0 + index * 9000,
                    latest_out=3032160.0 + index * 9000,
                ),
            )
            for index in range(4)
        ),
    )
    media = overrides.pop(
        "media",
        (
            _reel.SourceMedia(
                media_ref_id="src-ride",
                media_id=_REEL_MEDIA_ID,
                available_start=3000000.0,
                available_duration=200000.0,
                aspect_ratio=(16, 9),
            ),
        ),
    )
    defaults = dict(
        rate=_REEL_RATE,
        target=_reel.RenderTarget(
            destination="instagram_reel",
            resolution=(1080, 1920),
            aspect_ratio=(9, 16),
            target_duration=899.0,
            max_duration=5395.0,
        ),
        media=media,
        moments=moments,
        name="Benchmark ride",
        seed=20260318,
        generated_at="2026-03-18T09:00:00+05:30",
        validated_at="2026-03-18T09:00:01+05:30",
    )
    defaults.update(overrides)
    return _reel.ReelRequest(**defaults)


def _reel_inputs(_context: ProbeContext, _params: Mapping[str, Any]):
    """Digest over the planner source and the request this probe builds.

    The request is code rather than a file, so its canonical EDL under the
    CURRENT planner would be a circular input digest -- it would move whenever
    the thing under test moved, and the harness would refuse every comparison
    that mattered. Digesting the request's own declared numbers keeps the input
    identity independent of the planner's output.
    """
    request = _reel_request()
    parts = [
        f"{request.rate!r}",
        request.name,
        str(request.seed),
        *[
            f"{m.moment_id}:{m.source_start!r}:{m.source_duration!r}:{m.score!r}"
            for m in request.moments
        ],
        *[f"{m.media_ref_id}:{m.media_id}" for m in request.media],
    ]
    return None, digest_strings(parts)


def _measure_edl_determinism(
    _inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """Same request in, byte-identical EDL out -- under permuted input order.

    CLAUDE.md rule 3: "Same EDL + same sources = identical render intent." That
    promise starts one step earlier than the renderer: if the PLAN moves, the
    render moves with it, and nothing downstream can notice. The permutations are
    the point -- `moments` and `media` are tuples, and a planner that let arrival
    order reach the timeline would still pass a check that fed the same order
    twice.
    """
    permutations = int(params.get("permutations", 4))
    if permutations < 2:
        raise ProbeError("EDL determinism needs at least two orderings to compare")
    if falsify not in (None, "perturb_one_moment", "drop_one_moment"):
        raise ProbeError(f"unknown falsification {falsify!r}")

    base_request = _reel_request()
    digests: list[str] = []
    for index in range(permutations):
        moments = list(base_request.moments)
        media = list(base_request.media)
        if index:
            random.Random(index).shuffle(moments)
            random.Random(index + 100).shuffle(media)
        if index and falsify == "perturb_one_moment":
            # Break the INPUT: one moment starts eleven frames later. A real
            # difference in the plan, which the digest must reflect.
            first = moments[0]
            shift = 11.0
            moments[0] = _reel.SelectedMoment(
                moment_id=first.moment_id,
                media_id=first.media_id,
                source_start=first.source_start + shift,
                source_duration=first.source_duration,
                score=first.score,
                hook_potential=first.hook_potential,
                motion_energy=first.motion_energy,
                # Shifted with the moment. SelectedMoment refuses a snap point
                # outside its own source range, so perturbing the start alone
                # would raise instead of measuring anything -- and a probe that
                # raises under its own falsification proves nothing about the
                # code under test.
                snap_points=tuple(
                    _reel.SnapPoint(
                        time=snap.time + shift,
                        kind=snap.kind,
                        strength=snap.strength,
                        cut_direction=snap.cut_direction,
                    )
                    for snap in first.snap_points
                ),
                safe_trim=_reel.SafeTrim(
                    earliest_in=first.safe_trim.earliest_in + shift,
                    latest_out=first.safe_trim.latest_out + shift,
                ),
            )
        if index and falsify == "drop_one_moment":
            # Break the INPUT: plan the reel from a smaller pool.
            moments = moments[:-1]
        plan = _reel.plan_reel(
            _reel_request(moments=tuple(moments), media=tuple(media))
        )
        digests.append(_reel.blake3_hex(_reel.canonical_json(plan.edl)))
    return sum(digest == digests[0] for digest in digests[1:]) / (permutations - 1)


EDL_DETERMINISM = Probe(
    probe_id="reel_edl_determinism",
    metric_name="edl_digest_agreement",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=(),
    param_names=("permutations",),
    falsifications=("perturb_one_moment", "drop_one_moment"),
    sources=(REEL_SOURCE,),
    load=_reel_inputs,
    measure=_measure_edl_determinism,
)


# ==========================================================================
# Library-backed probe. Needs the synthetic library on disk.
# ==========================================================================


def _library(context: ProbeContext) -> ResolvedLibrary:
    if context.library is None:
        raise ProbeError(
            "this case needs the benchmark library and none was supplied; generate "
            "it with scripts/demo/make_library.py and pass --library/--library-declaration"
        )
    return context.library


def _library_inputs(context: ProbeContext, _params: Mapping[str, Any]):
    """Digest over the library's RELPATHS, not its bytes.

    Deliberate, and the reasoning matters. The byte-level identity of the library
    is already checked, hard, by `library.resolve` before any probe runs.
    Digesting the bytes again here as the case's `inputs_digest` would make every
    comparison across two machines a refusal -- the same generator with the same
    seed emits different JPEG bytes under a different libjpeg -- while proving
    nothing `resolve` has not already proved.

    The relpath set is what identifies the benchmark: a truncated copy, or a run
    with `--stills 40`, has a different file set and a different digest, so the
    comparison is refused. A library with the same filenames and different pixels
    is caught by `resolve`, not by this.
    """
    library = _library(context)
    return library, digest_strings([library.declaration.ref, *sorted(library.files)])


def _measure_media_id_agreement(
    inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """Every file's BLAKE3, recomputed off the disk, against the manifest.

    This is the `media_id` ingest derives. `MANIFEST.json` records it so a
    consumer can assert against identities rather than guessing from filenames,
    and the assertion is worth nothing unless somebody recomputes it.
    """
    library: ResolvedLibrary = inputs
    declared = dict(library.files)
    if falsify == "manifest_digest_edited":
        # Break the INPUT, on the side that matters: edit the DECLARED digest of
        # one file. A probe that genuinely recomputes must disagree with it; a
        # probe that read the manifest back to itself would not notice, and would
        # still be reporting 1.0 on a library whose media had been swapped.
        first = sorted(declared)[0]
        declared[first] = "0" * 64
    elif falsify == "declared_digests_swapped":
        # Break the INPUT again: swap two files' declared digests. The file set,
        # the file count and the multiset of digests are all unchanged, so only a
        # per-file comparison catches it.
        if len(declared) < 2:  # pragma: no cover - the library has 200+ files
            raise ProbeError("swapping digests needs at least two files")
        first, second = sorted(declared)[:2]
        declared[first], declared[second] = declared[second], declared[first]
    elif falsify is not None:
        raise ProbeError(f"unknown falsification {falsify!r}")

    agreed = 0
    for relpath, expected in sorted(declared.items()):
        agreed += file_digest(library.root / relpath) == expected
    return agreed / len(declared)


MEDIA_ID_AGREEMENT = Probe(
    probe_id="library_media_id_agreement",
    metric_name="media_id_agreement",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.DETERMINISM,
    requires=("library",),
    param_names=(),
    falsifications=("manifest_digest_edited", "declared_digests_swapped"),
    sources=(Path(__file__).resolve(),),
    load=_library_inputs,
    measure=_measure_media_id_agreement,
)


# ==========================================================================
# Weights-backed probe. Needs the real ONNX checkpoints on disk.
# ==========================================================================


def _weights_root(context: ProbeContext) -> Path:
    root = context.weights_root or (MODELS_DIR / "weights")
    if not root.is_dir():
        raise ProbeError(f"no weights directory at {root}")
    present = sorted(path.name for path in root.glob("*.onnx"))
    if not present:
        raise ProbeError(
            f"{root} holds no .onnx checkpoint; fetch them with "
            "scripts/models/fetch_weights.py, or pass --weights"
        )
    return root


def _graph_claims(context: ProbeContext, _params: Mapping[str, Any]):
    """Every (model, claim) pair the registry makes about a checkpoint present.

    Only models whose weights file is actually on this machine. A registry entry
    with no file is not a failing claim, it is an unmeasured one, and scoring it
    zero would make "fetch fewer weights" a way to fail the benchmark.
    """
    root = _weights_root(context)
    configs_dir = MODELS_DIR / "configs"
    if not configs_dir.is_dir():
        raise ProbeError(f"no model configs at {configs_dir}")

    entries: list[tuple[str, Path, Mapping[str, Any]]] = []
    for config_path in sorted(configs_dir.glob("*.json")):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        weights = config.get("weights") or {}
        filename = weights.get("filename")
        if not filename or weights.get("format") != "onnx":
            continue
        checkpoint = root / filename
        if not checkpoint.is_file():
            continue
        entries.append((str(config["model_id"]), checkpoint, config))
    if not entries:
        raise ProbeError(
            f"no registry config in {configs_dir} has its ONNX weights present in {root}"
        )
    # The digest covers which models were measured and the bytes of each
    # checkpoint. Fetch a different revision of a checkpoint and the comparison
    # is refused rather than reported as a quality change -- which is the whole
    # reason weights get pinned at all.
    parts: list[str] = []
    for model_id, checkpoint, _config in entries:
        parts.extend([model_id, checkpoint.name, file_digest(checkpoint)])
    return entries, digest_strings(parts)


def _session(path: Path):
    try:
        import onnxruntime  # noqa: PLC0415
    except ImportError as missing:
        raise ProbeError(
            "onnxruntime is not installed; the graph-contract case cannot read a "
            "checkpoint without it"
        ) from missing
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )


def _measure_graph_contract(
    inputs, params: Mapping[str, Any], *, falsify: str | None = None
) -> float:
    """Does the registry config describe the checkpoint that is actually there?

    Every claim in the config that the ONNX graph can adjudicate, checked against
    the graph:

      * `preprocessing.input_name` against the graph's input name;
      * `preprocessing.input_size` against the graph's spatial dimensions,
        where the graph pins them (a dynamic axis is not a disagreement);
      * every declared output `name` against the graph's output names.

    `docs/architecture.md` records the state this measures: "No checkpoint in the
    registry is pinned... eight of nine entries now say batch 1 because nothing
    has read their input shape." A config that has drifted from its checkpoint
    produces a detector that seems mediocre -- issue #36 was exactly this, SCRFD
    with the wrong output names -- and nothing raises.

    PLUMBING, not quality: a graph whose tensors are named correctly may still be
    a bad model.
    """
    entries = inputs
    if falsify not in (None, "claim_wrong_input_name", "claim_wrong_output_names"):
        raise ProbeError(f"unknown falsification {falsify!r}")

    agreed = 0
    total = 0
    for model_id, checkpoint, config in entries:
        del model_id
        session = _session(checkpoint)
        graph_inputs = session.get_inputs()
        graph_outputs = session.get_outputs()
        preprocessing = config.get("preprocessing") or {}

        claimed_input = preprocessing.get("input_name")
        if falsify == "claim_wrong_input_name":
            # Break the CONFIG: claim a tensor name the graph does not have.
            # This is the shape of the defect -- a config edited past its
            # checkpoint -- and the runtime would fail loudly at load, which is
            # exactly why nothing measures it until load time today.
            claimed_input = "not_the_input_tensor"
        if claimed_input is not None:
            total += 1
            agreed += any(entry.name == claimed_input for entry in graph_inputs)

        size = preprocessing.get("input_size") or {}
        if size.get("width") and size.get("height") and graph_inputs:
            shape = list(graph_inputs[0].shape)
            spatial = [dim for dim in shape[-2:] if isinstance(dim, int)]
            if len(spatial) == 2:
                # Only when the graph pins both spatial dimensions. A dynamic
                # axis genuinely permits the declared size, so counting it as a
                # disagreement would punish the configs that are right.
                total += 1
                agreed += spatial == [size["height"], size["width"]]

        claimed_outputs = [
            str(entry["name"]) for entry in (config.get("outputs") or []) if entry.get("name")
        ]
        if falsify == "claim_wrong_output_names":
            claimed_outputs = [f"{name}_renamed" for name in claimed_outputs]
        actual_outputs = {entry.name for entry in graph_outputs}
        for name in claimed_outputs:
            total += 1
            agreed += name in actual_outputs

    if total == 0:  # pragma: no cover - every shipped config makes claims
        raise ProbeError("no config made a claim the graph could adjudicate")
    return agreed / total


GRAPH_CONTRACT = Probe(
    probe_id="registry_graph_contract",
    metric_name="graph_claim_agreement",
    direction="higher_is_better",
    claim_ceiling=ClaimClass.PLUMBING,
    requires=("weights",),
    param_names=(),
    falsifications=("claim_wrong_input_name", "claim_wrong_output_names"),
    sources=(Path(__file__).resolve(),),
    load=_graph_claims,
    measure=_measure_graph_contract,
)


PROBES: dict[str, Probe] = {
    probe.probe_id: probe
    for probe in (
        BURST_RECOVERY,
        BURST_SEPARATION,
        ID_STABILITY,
        HARD_GATES_FIRE,
        CLEAN_PASSES,
        REPORT_DETERMINISM,
        EDL_DETERMINISM,
        MEDIA_ID_AGREEMENT,
        GRAPH_CONTRACT,
    )
}
