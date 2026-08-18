# render-video

Deterministic local `EDL` to video rendering. The worker resolves every source by content
hash, compiles the plan into a single ffmpeg filtergraph built from integer frame counts,
encodes with an explicitly supplied profile, and reads the finished file back to check it
is the thing the plan asked for.

```text
memory-engine-render-video run <job-spec.json> <edl.json>
```

## The renderer decides nothing

Hard rule 3 says every creative decision lives in the plan. Applying that needs a sharper
test than "does this feel creative", because almost every gap arrives dressed as a
technical detail. The test this worker uses:

> A declaration is renderable when the contract pins it **structurally** (a time, a range,
> an id, a geometry, a gain in dB) or **by outcome** (a measurable result any correct
> implementation lands on, such as an integrated LUFS target or a true-peak ceiling).
>
> A declaration is **refused** when realising it would make the renderer choose a curve, a
> scale, a filter design or a tie-break the contract does not state — even where a
> plausible default exists, and especially where one does.

Every EDL therefore sorts into three buckets, all three of which travel with the render
result and are printed by the CLI. Nothing is skipped quietly.

| Bucket | Meaning |
|---|---|
| **refused (contract gap)** | The render fails, listing *every* offender at once with the issue that has to close. A question for the planner side. |
| **refused (not implemented here)** | The contract pins it completely and this worker has not built it yet. A question for this worker, and reported separately so a closed issue does not look open. |
| **not acted upon** | Planner provenance the renderer has no business executing (`story_arc`, `subject_lock`, `smoothing`, `markers`, `variant`). Recorded, never executed. |
| **interpreted** | Executed under a stated convention. Since [#60](https://github.com/rohan21895/memory-engine/issues/60) closed, the conventions are stated by the contract rather than chosen here; they stay in the report so the realisation is on the record with the render. |

### What is refused today

| Declaration | Why | Issue |
|---|---|---|
| a non-zero `global_start_time` | no timecode track or drop-frame convention is specified for the delivered file | unfiled |
| a transition against a gap or a track edge | no handle source on one side; no planner emits one | unfiled |
| a rotated crop, or a crop window that changes size | neither has a pinned resampling convention; [#51](https://github.com/rohan21895/memory-engine/issues/51) settled the interpolation curve and nothing else about crop geometry | unfiled |
| a full-range source | every `ColorEncoding` token is limited range, and reading one as the other moves every code value without raising | [#101](https://github.com/rohan21895/memory-engine/issues/101) |

Everything else has closed:
[#49](https://github.com/rohan21895/memory-engine/issues/49),
[#52](https://github.com/rohan21895/memory-engine/issues/52),
[#53](https://github.com/rohan21895/memory-engine/issues/53),
[#54](https://github.com/rohan21895/memory-engine/issues/54),
[#55](https://github.com/rohan21895/memory-engine/issues/55),
[#56](https://github.com/rohan21895/memory-engine/issues/56) and
[#58](https://github.com/rohan21895/memory-engine/issues/58) have all closed. What each of
them used to refuse:

| Was refused | Closed by |
|---|---|
| any `ColorOp` | #49: every op in the enum has a transfer function from `amount` to a physical quantity — exposure buys ±2 stops, saturation a chroma scale of `1 + a` — the list fuses into one 3×3 matrix applied once in linear light, and `match_to_reference`, which asked the renderer to *measure* a second clip, is out of the enum and resolved by the planner. |
| any HLG or PQ source | #58: every source names its `color_encoding` and its graded peak, the working space is linear and named, the delivered encoding is enumerated, and `ToneMap` names an operator whose curve is written out as a formula. |
| `easing` other than `linear`; `wipe`/`push`/`blur_dissolve`/`match_cut`/`custom`; a transition with ambient audio under it | #52: the four easings are polynomials, the un-parameterised transition types are out of the enum, and the beds butt-cut. |
| `high_pass_hz`, and a clip carrying two ambient levels | #53: the level is stated once, on the clip, and the high-pass names its response, order and section Q values. |
| ducking with a non-zero attack or release | #54: linear in dB, attack ending at the range start, release beginning at the range end. |
| an encode profile the plan did not carry | #56: `RenderTarget.encode` is contract data. |

### What is refused because this worker has not built it

| Declaration | State |
|---|---|
| any `time_effect` | [#50](https://github.com/rohan21895/memory-engine/issues/50) closed: `source_range` is the media read, the timeline extent is `source_range.duration / time_scalar` (or `hold_duration` for a freeze), and output frame *k* draws source frame `start + floor(k * time_scalar)`. `program.ts` lays a retimed clip out on that extent and validates it; emitting the retimed picture needs a filter chain that neither duplicates nor drops a frame of its own accord, and that is not built. The number this would get wrong is *which frame*. |

`contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json` now carries **no**
unpinned declaration at all — the only thing left is the unimplemented retime on `clip-05`.
`test/gate.test.ts` asserts that set is empty, which makes it the checklist in both
directions: a closed issue has to leave it, and nothing may enter it without an issue
behind it.

### Not in the EDL at all

Source paths arrive the same way, keyed by `media_id`, for the same reason: the EDL
addresses content by hash and never carries a path. **The renderer opens exactly the files
the job named for a `media_id` the EDL declared, and never scans a directory.**

## Determinism: what was actually verified

The claim tested in `test/renderer.test.ts` is the strong one — **byte-identical output**,
not merely an identical command graph:

- One EDL rendered twice, from two independent work directories, produces byte-identical
  files. Verified for **FFV1 in Matroska** and for **libx264 in MP4**, by comparing the
  full file bytes and the BLAKE3 of each.
- The decoded picture, hashed as raw RGB frames, is identical across runs.
- The `commandGraphDigest` — BLAKE3 over the resolved source hashes, the filter text and
  the whole command with filesystem paths replaced by the `media_ref` they stand for — is
  stable across work directories and changes when the plan changes.

The scope of that claim, stated precisely: it was verified **on one machine, with one
ffmpeg build (7.0), across separate process invocations and separate work directories**. It
is not a claim that two different ffmpeg or libx264 builds produce the same bytes; they do
not, and no flag makes them. What the bitexact flags buy is that nothing about *when* or
*with what* the file was made enters it: `-fflags +bitexact`, `-flags +bitexact` on both
streams, `-map_metadata -1` and `-map_chapters -1` strip the muxer's creation time, the
encoder version string and the source metadata. Cross-build reproducibility would need the
ffmpeg build itself pinned in the plan, which is a contract question, not a code one.

`-fps_mode passthrough` is there for a different reason: it forbids ffmpeg from padding or
dropping a frame to hit a rate. If the filtergraph produces the wrong number of frames the
file gets the wrong number, and the post-render check catches it — rather than the
difference being papered over and every cut after it landing late for ever.

## Time

Every position is an integer count of timeline frames. `RationalTime` exists precisely so
30000/1001 never has to be a float number of seconds, so this worker refuses a time that is
not a whole frame at the timeline rate, and refuses a time expressed at a different rate
rather than rescaling it. Frames become seconds or samples exactly once, at the ffmpeg
argument boundary, always from an absolute frame index and never from an accumulator.

`beat_lock` is re-derived rather than trusted: each locked clip's in-point is recomputed
from the integer layout and checked against the grid, against the error the plan recorded,
and against the grid's own tolerance. A one-frame disagreement between the plan's audit
trail and the cut it actually describes is a hard failure.

## Transitions

The schema's `$comment` says a clip's `source_range` already includes the handles a
transition consumes. The golden fixture does the opposite, and the fixture's arithmetic is
the one that works — it is what keeps its timeline at 899 frames and its downbeats where
`beat_lock` says they are. So:

> A transition does not change either neighbour's timeline extent. The blend region
> straddles the cut, covering the outgoing clip's last `in_offset` frames and the incoming
> clip's first `out_offset` frames, and the frames it needs beyond each `source_range` come
> from `available_range` — the handles, whose availability is checked before the render
> starts.

A dissolve is a `blend` driven by the filter's frame counter, not an `xfade` driven by
seconds: `xfade` needs a declared constant frame rate on its inputs, `trim` marks its
output rate unknown by design, and conforming it back with `fps` would put a filter that
may duplicate or drop a frame in the middle of a beat-locked cut. A dip is two `fade`s
around the cut, because `fade` knows what "black" and "white" mean in the working pixel
format and an arithmetic blend against a constant would not.

## Sources fail loudly

The worst failure available to a video renderer is one that exits zero and contains black
where a shot should be. Nobody reviews a render that succeeded. So before a frame is
encoded, every source is opened, hashed and measured:

- missing, unreadable, not a regular file, or zero bytes → hard failure;
- BLAKE3 of the file must equal the `media_id` the plan was made against — for a span
  assembly, `BLAKE3(concat of member ids in index order)` must equal it, so chapters handed
  over in the wrong order fail rather than render scrambled;
- variable frame rate → refused, because a frame-indexed trim against VFR is meaningless
  and the drift would be silent;
- the probed frame count must equal the declared `available_range` — a source shorter than
  the plan believes is exactly how a clip becomes black frames, and for a chaptered
  recording a frame dropped at the split looks like this
  ([#55](https://github.com/rohan21895/memory-engine/issues/55));
- a rotation flag, or a rate that is not the timeline rate → refused;
- an HDR transfer is **accepted** since #58, and checked instead: the plan's declared
  `color_encoding` must not contradict the tags the container carries. The plan wins where
  the file is silent — an untagged action-camera file is the case `input_transform: "auto"`
  handled worst — but a plan that says BT.709 over a file tagged PQ is either the wrong
  footage or a plan made before a regrade.

Afterwards the finished file is probed again: frame count, resolution and nominal rate must
match the program, and where there is audio the integrated loudness and true peak are
measured with `ebur128` and checked against the `MixPlan`.

## Known limits

- **One video layer.** More than one enabled video track needs a blend and an alpha
  convention this worker does not implement.
- **No input seeking.** Each source use is a separate ffmpeg input decoded from the start,
  which is correct and slow: a seventeen-minute source cut into eight clips is decoded eight
  times. Keyframe-accurate seeking is the obvious optimisation and the one place where
  getting it wrong would silently move a cut, so it is deliberately not in v1.
- **Crop size is fixed per track.** A crop window that changes size is a zoom, and neither
  the resampling nor the per-frame rounding of a moving crop size is pinned.
- **HDR delivery is not possible**, only HDR *sources*. `ColorPipeline.output_encoding`
  accepts the four SDR encodings, because an HDR delivery needs a mastering-display block
  (MaxCLL, MaxFALL, mastering primaries) that no worker writes and the contract does not
  carry.
- **The tone map applies to HDR sources only.** An SDR source in a mixed cut passes through
  unmapped; matching the two across the cut is a planner decision, expressed as colour ops.
