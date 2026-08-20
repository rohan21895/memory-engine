"""The selection sidecar: every candidate's fate, in words and real numbers.

The AlbumSpec's embedded SelectionReport is contract-bound and deliberately
terse -- bare {media_id, reason}, merit-losers dropped. That is right for the
spec and useless for the swap UX: "show me the other frames of this shot and
tell me why the album picked this one" needs the shot groups, the signal
deltas, and the placement geometry, none of which belong in a print contract.

So this module writes a SIDECAR artifact next to the album outputs, keyed by
the same `inputs_digest` as the AlbumSpec it describes. It is an INTERNAL
artifact, not a contract schema: its shape is versioned by `sidecar_version`
and may change without a contracts PR.

Every candidate that entered selection appears in exactly ONE of four places:

  selected[]                  -- in the book, with its placement and reasons
  selected[].alternatives[]   -- the other frames of a selected photo's shot
  rejected[]                  -- removed by a stated rule, with the full detail
  unselected[]                -- survived every gate, lost on merit

Reasons are DERIVED from the numbers selection actually scored with (the
`Selection.diagnostics` exposure and the candidates' own signals), never
invented: every human-readable string carries the values behind it.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from typing import Any

SIDECAR_VERSION = 1

#: Alternatives at or above this cosine similarity to the winner get an
#: explicit "similar to the chosen frame" line. Below it the shot link was
#: transitive (frame 1 and frame 40 of a drifting burst) and quoting a
#: similarity would overstate what was measured.
_SIMILARITY_MENTION = 0.85

#: Percentile above which an expression axis is worth naming as a reason the
#: winner was chosen ("top 20% of the pool on aesthetics").
_NOTABLE_PERCENTILE = 0.80


def build_selection_report(
    *,
    selection: Any,
    candidates: Sequence[Any],
    pages: Sequence[Mapping[str, Any]],
    policy: Any,
    dpi_floor: float,
    planner: str,
    planner_version: str,
    inputs_digest: str,
    pixel_sizes: Mapping[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """The sidecar dict for one planned album.

    `selection` is the album engine's `Selection`; `candidates` the same list
    handed to `select`; `pages` the laid-out AlbumSpec pages (placement
    geometry and effective DPI are read from them, never recomputed);
    `pixel_sizes` maps media_id -> (width, height) of the oriented image so
    slot-fit for ALTERNATIVES can be answered with the layout engine's own
    DPI arithmetic. A missing size degrades that answer, it never fails.
    """
    by_id = {c.media_id: c for c in candidates}
    pixel_sizes = pixel_sizes or {}
    groups: Mapping[str, str] = getattr(selection, "groups", {}) or {}
    diagnostics: Mapping[str, Mapping[str, Any]] = (
        getattr(selection, "diagnostics", {}) or {}
    )
    selected_ids = list(selection.selected)
    selected_set = set(selected_ids)

    # Which selected photo carries each shot group's alternatives. When the
    # fill phase deepened a shot (two frames of one group in the book), the
    # group's alternatives attach to the higher-standing winner so each
    # candidate appears exactly once.
    def _standing(media_id: str) -> float:
        entry = diagnostics.get(media_id)
        if entry is not None and entry.get("standing") is not None:
            return float(entry["standing"])
        candidate = by_id.get(media_id)
        return float(candidate.score.value) if candidate is not None else 0.0

    carrier_for_group: dict[str, str] = {}
    for media_id in sorted(selected_set, key=lambda m: (-_standing(m), m)):
        group = groups.get(media_id)
        if group is not None and group not in carrier_for_group:
            carrier_for_group[group] = media_id

    alternatives_of: dict[str, list[str]] = {m: [] for m in selected_ids}
    accounted: set[str] = set(selected_set)
    for media_id in sorted(groups):
        if media_id in selected_set:
            continue
        carrier = carrier_for_group.get(groups[media_id])
        if carrier is not None:
            alternatives_of[carrier].append(media_id)
            accounted.add(media_id)

    rejection_of = {r.media_id: r for r in selection.rejected}
    placement_of = _placements_by_media(pages)

    selected_entries = []
    for media_id in selected_ids:
        winner = by_id[media_id]
        alternates = sorted(
            (by_id[m] for m in alternatives_of[media_id]),
            key=lambda c: (-c.score.value, c.media_id),
        )
        page_index, placement = placement_of.get(media_id, (None, None))
        alternative_entries = []
        for rank, alternate in enumerate(alternates, start=1):
            rejection = rejection_of.get(alternate.media_id)
            fits, fit_detail = _slot_fit(
                alternate.media_id,
                pixel_sizes.get(alternate.media_id),
                alternate.long_edge_px,
                placement,
                dpi_floor,
            )
            alternative_entries.append(
                {
                    "media_id": alternate.media_id,
                    "rank": rank,
                    "quality": alternate.score.value,
                    "not_chosen_because": _not_chosen_because(
                        winner, alternate, policy, rejection
                    ),
                    "fits_slot": fits,
                    "slot_fit_detail": fit_detail,
                }
            )
        runner_up = alternates[0].media_id if alternates else None
        margin = (
            round(_standing(media_id) - _standing(runner_up), 6)
            if runner_up is not None
            else None
        )
        selected_entries.append(
            {
                "media_id": media_id,
                "shot_group": groups.get(media_id),
                "placement": _placement_entry(
                    page_index, placement, winner, pixel_sizes.get(media_id), dpi_floor
                ),
                "chosen_because": _chosen_because(
                    winner, alternates, diagnostics.get(media_id) or {}
                ),
                "confidence": {"margin": margin, "runner_up": runner_up},
                "alternatives": alternative_entries,
            }
        )

    rejected_entries = [
        {"media_id": r.media_id, "reason": r.reason, "detail": r.detail}
        for r in sorted(selection.rejected, key=lambda r: r.media_id)
        if r.media_id not in accounted
    ]
    accounted.update(entry["media_id"] for entry in rejected_entries)

    unselected_entries = [
        _unselected_entry(media_id, diagnostics, selected_ids, _standing)
        for media_id in sorted(selection.unselected)
        if media_id not in accounted
    ]

    return {
        "sidecar_version": SIDECAR_VERSION,
        "kind": "selection_sidecar",
        "album_id": inputs_digest,
        "planner": planner,
        "planner_version": planner_version,
        # Sets (pinned/excluded media ids) become sorted lists: JSON has no
        # set, and a stable order keeps the sidecar diffable across runs.
        "policy": {
            key: sorted(value) if isinstance(value, (set, frozenset)) else value
            for key, value in dataclasses.asdict(policy).items()
        },
        "counts": {
            "candidates": selection.candidate_count,
            "selected": len(selected_entries),
            "alternatives": sum(len(e["alternatives"]) for e in selected_entries),
            "rejected": len(rejected_entries),
            "unselected": len(unselected_entries),
        },
        "selected": selected_entries,
        "rejected": rejected_entries,
        "unselected": unselected_entries,
    }


# ------------------------------------------------------------------ pieces --


def _placements_by_media(
    pages: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[int, Mapping[str, Any]]]:
    """media_id -> (page_index, placement). Interior pages win over the cover:
    the hero repeats on the front cover, and "its page in the book" is the
    interior one a swap would actually affect."""
    chosen: dict[str, tuple[bool, int, Mapping[str, Any]]] = {}
    for page in pages:
        interior = page.get("side") in ("left", "right")
        for placement in page.get("placements") or ():
            media_id = placement["media_id"]
            held = chosen.get(media_id)
            if held is None or (interior and not held[0]):
                chosen[media_id] = (interior, int(page["page_index"]), placement)
    return {m: (index, placement) for m, (_, index, placement) in chosen.items()}


def _needed_long_edge_px(frame: Mapping[str, Any], dpi_floor: float) -> int:
    from memory_engine_album.layout import MM_PER_INCH  # noqa: PLC0415

    frame_long_mm = max(float(frame["width_mm"]), float(frame["height_mm"]))
    return int(math.ceil(dpi_floor * frame_long_mm / MM_PER_INCH))


def _placement_entry(
    page_index: int | None,
    placement: Mapping[str, Any] | None,
    winner: Any,
    size: tuple[int, int] | None,
    dpi_floor: float,
) -> dict[str, Any] | None:
    if placement is None or page_index is None:
        return None
    frame = placement["frame"]
    available = (
        max(int(size[0]), int(size[1])) if size is not None else winner.long_edge_px
    )
    return {
        "page_index": page_index,
        "placement_id": placement.get("placement_id"),
        "frame_mm": {
            "x_mm": frame["x_mm"],
            "y_mm": frame["y_mm"],
            "width_mm": frame["width_mm"],
            "height_mm": frame["height_mm"],
        },
        "effective_dpi": placement.get("effective_dpi"),
        "is_hero": bool(placement.get("is_hero", False)),
        "long_edge_px_available": available,
        "long_edge_px_needed": _needed_long_edge_px(frame, dpi_floor),
    }


def _slot_fit(
    media_id: str,
    size: tuple[int, int] | None,
    long_edge_px: int | None,
    placement: Mapping[str, Any] | None,
    dpi_floor: float,
) -> tuple[bool | None, str]:
    """Would this alternative satisfy the winner's placement?

    Uses the layout engine's own arithmetic (`cover_crop` + `effective_dpi`)
    rather than a reimplementation, so this answer and the print validator's
    can never drift. Aspect is held to the same full-bleed tolerance the page
    planner uses: a frame CAN cover-crop any aspect, but past the tolerance
    the crop eats a meaningful part of the picture and the swap would not be
    the photo the user thinks it is.
    """
    if placement is None:
        return None, "the chosen frame has no placement to measure against"
    frame = placement["frame"]
    needed = _needed_long_edge_px(frame, dpi_floor)

    if size is not None:
        from memory_engine_album.layout import (  # noqa: PLC0415
            LayoutError,
            Photo,
            RectMm,
            cover_crop,
            effective_dpi,
        )

        from .stages.album import _FULL_BLEED_ASPECT_TOLERANCE  # noqa: PLC0415

        photo = Photo(
            media_id=media_id, pixel_width=int(size[0]), pixel_height=int(size[1])
        )
        rect = RectMm(
            float(frame["x_mm"]),
            float(frame["y_mm"]),
            float(frame["width_mm"]),
            float(frame["height_mm"]),
        )
        aspect_ok = abs(photo.aspect / rect.aspect - 1.0) <= _FULL_BLEED_ASPECT_TOLERANCE
        try:
            dpi = effective_dpi(photo, cover_crop(photo, rect.aspect), rect)
        except LayoutError as error:
            return False, f"layout refuses this frame: {error}"
        detail = (
            f"effective {dpi:.0f} DPI vs floor {dpi_floor:.0f} at "
            f"{rect.width_mm:.0f}x{rect.height_mm:.0f}mm; "
            f"aspect {photo.aspect:.2f} vs frame {rect.aspect:.2f}"
        )
        if dpi < dpi_floor:
            return False, f"too few pixels for this placement: {detail}"
        if not aspect_ok:
            return False, f"aspect mismatch would crop meaningfully: {detail}"
        return True, detail

    if long_edge_px is not None:
        fits = long_edge_px >= needed
        return fits, (
            f"long edge {long_edge_px}px vs {needed}px needed at "
            f"{dpi_floor:.0f} DPI (aspect unchecked: full pixel size unknown)"
        )
    return None, "pixel size unknown, slot fit cannot be measured"


def _similarity_between(winner: Any, alternate: Any) -> float | None:
    if winner.embedding is None or alternate.embedding is None:
        return None
    if winner.embedding_space != alternate.embedding_space:
        return None
    from memory_engine_ranking.dedupe import cosine_distance  # noqa: PLC0415

    return 1.0 - cosine_distance(winner.embedding, alternate.embedding)


def _chosen_because(
    winner: Any, alternates: Sequence[Any], diag: Mapping[str, Any]
) -> list[str]:
    """Why THIS frame, in the numbers that decided it. Always non-empty."""
    reasons: list[str] = []
    if alternates:
        group_size = len(alternates) + 1
        best_other = max(a.score.value for a in alternates)
        if winner.score.value >= best_other:
            reasons.append(
                f"highest fused quality in its {group_size}-frame group "
                f"({winner.score.value:.2f} vs next {best_other:.2f})"
            )

        def group_best(name: str, template: str, decimals: int) -> None:
            value = getattr(winner, name)
            others = [v for a in alternates if (v := getattr(a, name)) is not None]
            if value is not None and others and value >= max(others):
                reasons.append(
                    template.format(
                        wv=round(value, decimals), best=round(max(others), decimals)
                    )
                )

        group_best(
            "face_sharpness", "sharpest faces in its group ({wv} vs next {best})", 2
        )
        group_best(
            "clean_frame",
            "cleanest frame of its group (clean_frame {wv} vs next {best})",
            3,
        )
        if winner.awake is not None and winner.awake > 0.0 and any(
            a.awake is not None and a.awake < 0.0 for a in alternates
        ):
            reasons.append(
                f"eyes read open (awake {winner.awake:+.2f}; a group-mate reads closed)"
            )

    for axis, label in (
        ("aesthetic_pct", "aesthetics"),
        ("smile_pct", "expression"),
        ("composed_pct", "composure"),
    ):
        percentile = diag.get(axis)
        if percentile is not None and percentile >= _NOTABLE_PERCENTILE:
            top = max(1, round((1.0 - float(percentile)) * 100))
            reasons.append(f"top {top}% of the pool on {label}")

    if not reasons:
        standing = diag.get("standing")
        detail = f"fused quality {winner.score.value:.2f}"
        if standing is not None:
            detail += f", within-class standing {float(standing):.3f}"
        reasons.append(detail)
    return reasons


def _not_chosen_because(
    winner: Any, alternate: Any, policy: Any, rejection: Any
) -> list[str]:
    """Why this group-mate lost to the winner: signal deltas at the same
    perceptual margins selection's own domination check uses. Always
    non-empty, always carrying the real numbers."""
    from memory_engine_album.selection import (  # noqa: PLC0415
        _VERSION_SIGNAL_MARGINS,
        _VERSION_VALUE_MARGIN,
    )

    reasons: list[str] = []

    similarity = _similarity_between(winner, alternate)
    if similarity is not None and similarity >= _SIMILARITY_MENTION:
        reasons.append(f"{similarity:.2f} similar to the chosen frame")

    templates = {
        "awake": ("eyes read less open (awake {av:+.2f} vs {wv:+.2f})", 2),
        "smile": ("weaker expression (smile {av:+.2f} vs {wv:+.2f})", 2),
        "aesthetic": ("weaker light or framing (aesthetic {av:+.2f} vs {wv:+.2f})", 2),
        "face_sharpness": ("visibly softer faces ({av:.2f} vs {wv:.2f})", 2),
    }
    margined = list(_VERSION_SIGNAL_MARGINS) + [
        ("clean_frame", policy.clean_frame_group_margin)
    ]
    for name, margin in margined:
        winner_value = getattr(winner, name)
        alternate_value = getattr(alternate, name)
        if winner_value is None or alternate_value is None:
            continue
        if winner_value - alternate_value > margin:
            if name == "clean_frame":
                reasons.append(
                    "frame is less clean -- a bystander or clutter at the edge "
                    f"(clean_frame {alternate_value:.3f} vs {winner_value:.3f})"
                )
            else:
                template, _decimals = templates[name]
                reasons.append(template.format(av=alternate_value, wv=winner_value))

    if winner.score.value - alternate.score.value > _VERSION_VALUE_MARGIN:
        reasons.append(
            f"decisively out-scored overall "
            f"({alternate.score.value:.2f} vs {winner.score.value:.2f})"
        )

    if rejection is not None:
        detail = f": {rejection.detail}" if rejection.detail else ""
        reasons.append(f"also blocked by a rule ({rejection.reason}{detail})")

    if not reasons:
        reasons.append(
            f"ranked below the chosen frame (fused quality "
            f"{alternate.score.value:.2f} vs {winner.score.value:.2f})"
        )
    return reasons


def _unselected_entry(
    media_id: str,
    diagnostics: Mapping[str, Mapping[str, Any]],
    selected_ids: Sequence[str],
    standing_of: Any,
) -> dict[str, Any]:
    """A merit-loser: what beat it, stated against the weakest photo that DID
    make the book (the bar it failed to clear)."""
    diag = diagnostics.get(media_id) or {}
    standing = diag.get("standing")
    weakest_selected = (
        min(standing_of(m) for m in selected_ids) if selected_ids else None
    )
    if diag.get("worse_version_in_group"):
        detail = (
            "a group-mate is a strictly better version of this frame "
            "(same shot, worse on at least one signal, better on none)"
        )
    elif standing is not None and weakest_selected is not None:
        # Standing is higher-is-better. A loser ABOVE the weakest pick did not
        # lose on quality -- it lost on the album objective (too similar to a
        # selected frame, or adding no new moment or person) -- and saying
        # "vs the weakest" there reads as a contradiction.
        if float(standing) >= weakest_selected:
            detail = (
                "survived every gate and out-scores the weakest selected photo "
                f"on raw quality ({float(standing):.3f} vs {weakest_selected:.3f}) "
                "-- it lost on the album objective: too similar to a selected "
                "frame, or adding no new moment, person, or scene"
            )
        else:
            detail = (
                "survived every gate but never offered the best marginal gain "
                f"while slots remained: quality standing {float(standing):.3f} vs "
                f"{weakest_selected:.3f} for the weakest selected photo"
            )
    elif standing is not None:
        detail = (
            "survived every gate but never offered the best marginal gain "
            f"(quality standing {float(standing):.3f})"
        )
    else:
        detail = "survived every gate but never offered the best marginal gain"
    return {"media_id": media_id, "reason": "lost_on_merit", "detail": detail}
