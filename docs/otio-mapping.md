# EDL ↔ OpenTimelineIO mapping

**Status:** v0 contract, drafted by Claude, pending Codex sign-off.
**Normative source:** the `$comment` header of `contracts/schemas/edl.schema.json`. This document explains *why* the mapping is shaped the way it is and gives the exporter/importer algorithm. Where the two disagree, the schema wins.

## Why this matters

OTIO is the film industry's interchange format. Supporting it means a user who wants to finish our auto-cut by hand can open it in DaVinci Resolve, Premiere, Flame or Baselight and keep every cut, every speed change, and every marker we placed. Build plan §2 calls this out as the single cheapest thing we can do that reads as "industry-leading" to an editor — cheap precisely because our EDL is already deterministic and already expressed in exact rational time.

The bar is **lossless round trip**: `import(export(edl)) == edl`, byte-identical after canonical JSON serialisation, with the two documented exceptions below.

## The two rules

### Rule 1 — structure maps to OTIO natives

Anything OTIO models natively uses OTIO's exact shape:

| Concept | OTIO type |
|---|---|
| Timeline | `otio.schema.Timeline` |
| Track | `otio.schema.Track` (`kind` is `"Video"` or `"Audio"`) |
| Clip | `otio.schema.Clip` |
| Gap | `otio.schema.Gap` |
| Transition | `otio.schema.Transition` |
| Source reference | `otio.schema.ExternalReference` |
| Marker | `otio.schema.Marker` |
| Speed change | `otio.schema.LinearTimeWarp` / `otio.schema.FreezeFrame` |
| Time | `otio.opentime.RationalTime` (`value`, `rate`) |
| Time range | `otio.opentime.TimeRange` (`start_time`, `duration`, half-open) |

This is the reason `RationalTime` exists in `common.schema.json` and the reason nothing in this contract stores seconds as a float. `30000/1001` has no exact float representation; a plan that stores `29.97` and reconstructs frame positions from it drifts, and a drifting frame is a missed beat. Our `RationalTime` and `TimeRange` are field-for-field identical to OTIO's so the conversion is a constructor call, not a computation.

### Rule 2 — everything else round-trips through one metadata namespace

Beat grids, story arcs, reframe keyframe tracks and ducking rules have no OTIO equivalent. They are written into the OTIO `metadata` dict under a single key, `memory_engine`. OTIO preserves unknown metadata verbatim across read/write, and every NLE ignores it safely.

One namespace, not several, so that "what did we add on top of OTIO?" has a one-line answer: everything under `metadata["memory_engine"]`.

## Field mapping

See the `$comment` header in `contracts/schemas/edl.schema.json` for the complete field-by-field table. Summary of the non-obvious cases:

**`media_refs` → `ExternalReference`.** The EDL stores a BLAKE3 content hash and *no path*. That is what makes a plan portable between machines: the same EDL renders anywhere the same footage exists, whatever it is called and wherever it lives. At export time the exporter resolves the hash against the local media-db and writes a `file://` URL into `target_url`, because an NLE needs a path to open. The hash is also written to `metadata.memory_engine.media_id`, and **on import the hash wins** — a timeline that came back from an editor on another machine still resolves correctly against our library.

**A hard cut is the absence of a `Transition`.** OTIO models a straight cut as two adjacent clips with nothing between them. We do the same. Never emit a zero-duration `Transition` for a cut; it round-trips as a real (if degenerate) transition and some NLEs render a one-frame dissolve.

**`timeline_range` is derived and is not exported.** It is the running sum of preceding durations minus transition overlaps. OTIO recomputes it from structure. We carry it in the EDL only so a validator can catch a planner that emitted an internally inconsistent timeline, and it is excluded from both the OTIO export and the `determinism.inputs_digest`.

**A speed change does not change `source_range` (contracts#50).** This is the one place OTIO gives no answer and every adapter has to pick: OTIO's own duration arithmetic (`Item.duration()`, `Track.range_of_child_at_index`) is the sum of `source_range.duration` and ignores effects entirely. Our contract settles it on the media side — `source_range` is always the media read, and the timeline extent is derived as `source_range.duration / time_scalar` for `linear_speed` and `hold_duration` for `freeze_frame`.

So on export we write the media range and the warp, which is what an NLE's own retime looks like; on import we recompute `timeline_range` from the same rule rather than trusting the file. The consequence to know about: **an importer that lays clips out by `source_range.duration` alone will place everything after a retimed clip early** — by exactly the media-versus-timeline difference, 56 frames on the golden fixture's `clip-05`. That is a visible, immediate error rather than a subtle one; the alternative reading (source_range meaning timeline extent under an effect) fails silently instead, by making one field mean media on one clip and timeline on the next. A round-trip test must therefore include a retimed clip, and it must assert the *timeline* extent, not just the fields.

**`smooth` reframe interpolation is a stated curve (contracts#51).** Reframe keyframe tracks have no OTIO equivalent and round-trip through `metadata.memory_engine.reframe_tracks`, so an importer has to reproduce the curve, not just the values. `smooth` is a **uniform Catmull-Rom spline through the keyframe values with the endpoints clamped** (`P[-1] = P[0]`, `P[n] = P[n-1]`), evaluated per component against integer source frame numbers; the exact polynomial is in the `ReframeKeyframe.interpolation` `$comment`. Uniform rather than centripetal so the arithmetic is +, - and * only and is bit-identical across platforms. Note that clamping makes the ends an ease rather than a straight line: a two-keyframe track is `B + (C-B)(0.5u + 1.5u² - u³)`, not a lerp. An importer that substitutes smoothstep or a cosine ease will put the crop measurably elsewhere on a moving subject while every keyframe still matches.

**`audio_plan.music` carries no placement (contracts#59).** A `MusicCue` is licence and provenance attached to the audio-track clips that place the bed, named by `clip_ids`. The placement itself — source range, timeline range, gain, fades — lives on those clips and exports as an ordinary OTIO `Clip` on a `Track` with `kind: "Audio"`, so it survives into Resolve as music rather than as metadata. There is no `loop` flag: a bed that repeats is several clips, and each join is an ordinary cut. On import, the cues are rebuilt from the metadata namespace and re-bound to the clips by id; a cue whose `clip_ids` do not resolve is a failed import, not a silent drop.

**Markers are emitted twice on purpose.** Downbeats and story beats live authoritatively in `metadata.memory_engine.beat_grid` / `.story_arc`. They are *also* emitted as OTIO `Marker`s so that an editor opening the timeline in Resolve can see where the music lands and what each act is doing. On import, markers whose `metadata.memory_engine.generated` is `true` are discarded and the structures are rebuilt from the metadata — otherwise a round trip would duplicate them.

## Exporter algorithm

```
export(edl) -> otio.schema.Timeline:
    timeline = Timeline(name=edl.name)
    timeline.global_start_time = to_rational(edl.global_start_time or zero(edl.rate))
    timeline.metadata["memory_engine"] = {
        "contract_version":  edl.schema_version,
        "edl_id":            edl.edl_id,
        "kind":              edl.kind,
        "target":            edl.target,
        "beat_grid":         edl.beat_grid,
        "story_arc":         edl.story_arc,
        "reframe_tracks":    edl.reframe_tracks,
        "ducking":           edl.audio_plan.ducking,
        "mix":               edl.audio_plan.mix,
        "ambient":           edl.audio_plan.ambient,
        "music":             edl.audio_plan.music,
        "color_pipeline":    edl.color_pipeline,
        "determinism":       edl.determinism,
        "variant":           edl.variant,
    }

    for track in edl.tracks:
        otio_track = Track(name=track.name,
                           kind="Video" if track.kind == "video" else "Audio")
        otio_track.metadata["memory_engine"] = {"track_id": track.track_id,
                                                "role": track.role}
        for item in track.items:
            match item.item_type:
                case "clip":       otio_track.append(export_clip(item, edl))
                case "gap":        otio_track.append(Gap(source_range=range_of(item.duration)))
                case "transition": otio_track.append(export_transition(item))
        timeline.tracks.append(otio_track)

    # visibility-only markers, tagged so import can drop them
    if edl.beat_grid:
        for beat in edl.beat_grid.beats where beat.is_downbeat:
            append_marker(timeline, beat.time, "downbeat", color="BLUE", generated=True)
    if edl.story_arc:
        for act in edl.story_arc.acts where act.timeline_range is not None:
            append_marker(timeline, act.timeline_range, act.name, color="YELLOW", generated=True)

    return timeline


export_clip(clip, edl) -> otio.schema.Clip:
    ref  = lookup(edl.media_refs, clip.media_ref_id)
    path = media_db.resolve_path(ref.media_id)       # export time only
    media_reference = ExternalReference(
        target_url      = as_file_url(path),
        available_range = to_range(ref.available_range),
    )
    media_reference.metadata["memory_engine"] = {
        "media_id":          ref.media_id,           # authoritative on import
        "media_ref_id":      ref.media_ref_id,
        "is_span_assembly":  ref.is_span_assembly,
        "media_kind":        ref.media_kind,
    }

    otio_clip = Clip(name=clip.name,
                     source_range=to_range(clip.source_range),
                     media_reference=media_reference)
    otio_clip.enabled = clip.enabled

    # source_range above is the MEDIA the clip reads, unchanged by the effect
    # (contracts#50). The timeline extent OTIO will compute from it is wrong for a
    # retimed clip; that is OTIO's own gap, and the memory_engine metadata carries
    # the extent so our importer never has to infer it.
    if clip.time_effect?.kind == "linear_speed":
        otio_clip.effects.append(LinearTimeWarp(time_scalar=clip.time_effect.time_scalar))
        otio_clip.metadata["memory_engine"]["timeline_extent"] = \
            clip.source_range.duration.value / clip.time_effect.time_scalar
    elif clip.time_effect?.kind == "freeze_frame":
        otio_clip.effects.append(FreezeFrame())
        # freeze_at == source_range.start_time and source_range.duration == 1 frame,
        # so the only thing OTIO cannot carry is how long the frame is held.
        otio_clip.metadata["memory_engine"]["hold_duration"] = \
            to_rational(clip.time_effect.hold_duration)

    for op in clip.color_ops:
        effect = Effect(effect_name="memory_engine.color")
        effect.metadata["memory_engine"] = op
        otio_clip.effects.append(effect)

    for marker in clip.markers:
        otio_clip.markers.append(to_otio_marker(marker))

    otio_clip.metadata["memory_engine"] = {
        "clip_id":          clip.clip_id,
        "moment_id":        clip.moment_id,
        "beat_lock":        clip.beat_lock,
        "reframe_track_id": clip.reframe_track_id,
        "story_beat_id":    clip.story_beat_id,
        "audio":            clip.audio,
    }
    return otio_clip


export_transition(t) -> otio.schema.Transition:
    return Transition(
        transition_type = "SMPTE_Dissolve" if t.transition_type == "dissolve" else "Custom",
        in_offset       = to_rational(t.in_offset),
        out_offset      = to_rational(t.out_offset),
        metadata        = {"memory_engine": {"transition_id": t.transition_id,
                                             "kind":       t.transition_type,
                                             "easing":     t.easing}},
    )
```

## Importer algorithm

The importer is the exporter run backwards, with one ordering rule: **metadata wins over structure wherever both carry the same fact.** A timeline that has been through an NLE may have had its `target_url` rewritten, its markers duplicated, or its clip names changed by an editor. The `memory_engine` namespace is the part we authored and the part we trust.

```
import(timeline) -> EDL:
    me = timeline.metadata["memory_engine"]
    require me["contract_version"] == "v0"        # refuse unknown versions, never guess

    edl = EDL(schema_version=me["contract_version"], edl_id=me["edl_id"], ...)
    edl.beat_grid, edl.story_arc, edl.reframe_tracks = me["beat_grid"], me["story_arc"], me["reframe_tracks"]
    edl.audio_plan = AudioPlan(music=me["music"], ambient=me["ambient"],
                               ducking=me["ducking"], mix=me["mix"])

    for otio_track in timeline.tracks:
        for child in otio_track:
            if isinstance(child, Clip):
                ref_meta = child.media_reference.metadata["memory_engine"]
                register_media_ref(edl, ref_meta)       # by hash, ignore target_url
                edl_clip = Clip(source_range=child.source_range, **child.metadata["memory_engine"])
            elif isinstance(child, Transition): ...
            elif isinstance(child, Gap): ...

    drop_markers_where(generated=True)                  # rebuilt from metadata, not read back
    recompute timeline_range for every clip             # derived, never imported
    return edl
```

## Documented lossy edges

Two things do **not** survive a round trip, both deliberately:

1. **`timeline_range`** — derived, recomputed on import. Excluded from the determinism digest, so its absence changes no identity.
2. **Generated markers** — emitted for editor visibility, discarded on import and rebuilt from `beat_grid` / `story_arc`. Round-tripping them would duplicate them on every pass.

Everything else must survive exactly. `OtioExportInfo.unmapped_fields` exists to make a violation loud: if the exporter meets an EDL field it has no mapping for, it appends the field path there rather than dropping it silently, and `round_trip_verified` stays `false`. An export with a non-empty `unmapped_fields` is a contract gap to be raised, not a warning to be tolerated.

## Round-trip test plan

Belongs in `contracts/tests/` once `opentimelineio` is available as a dependency (it is not required for the v0 contract itself, which is why these are specified rather than implemented here):

1. **Identity** — for every EDL fixture, `import(export(edl))` equals the original under canonical JSON, ignoring the two documented lossy edges.
2. **Rational exactness** — an EDL at `30000/1001` round-trips with every `RationalTime.value` bit-identical. No float drift anywhere.
3. **Cut fidelity** — clip count, order, and every `source_range` survive.
4. **Transition handles** — `in_offset` / `out_offset` survive, and no zero-length transition is ever emitted for a hard cut.
5. **Foreign-tool tolerance** — take an exported `.otio`, mutate it the way an NLE would (rewrite `target_url` to a different path, append an editor's own marker, rename a clip), and confirm the importer still resolves every source by hash and still reconstructs the beat grid and story arc.
6. **Unmapped-field alarm** — add a field to the EDL schema without updating the exporter, and confirm it lands in `unmapped_fields` rather than vanishing.

## Open questions for Codex

- `workers/render-video` compiles the EDL directly to an FFmpeg filtergraph and does not go through OTIO. Confirm that OTIO export is a **user-facing export feature** (Phase 5, project editor) rather than a step in the render path — that is the assumption this mapping is built on, and it is what keeps the renderer free of an OTIO dependency.
- `target_url` resolution needs a media-db lookup at export time. That crosses the package boundary into Claude's territory; the cleanest split is that `media-db` exposes `resolve_path(media_id) -> Path | None` and the exporter lives on the Codex side of that call. Flagging rather than assuming.
