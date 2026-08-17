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
| **refused** | The render fails, listing *every* offender at once with the issue that has to close. |
| **not acted upon** | Planner provenance the renderer has no business executing (`story_arc`, `subject_lock`, `smoothing`, `markers`, `variant`). Recorded, never executed. |
| **interpreted** | Executed under a stated convention because the contract pins the quantity but not the last detail of its realisation. See [#60](https://github.com/rohan21895/memory-engine/issues/60). |

### What is refused today

| Declaration | Why | Issue |
|---|---|---|
| any `ColorOp` | `amount` is normalised to [-1,1] with no transfer function; `match_to_reference` is a planner computation | [#49](https://github.com/rohan21895/memory-engine/issues/49) |
| any `time_effect` | the contract does not say whether `source_range` or `timeline_range` is authoritative under a speed change, and the golden fixture and the field description disagree | [#50](https://github.com/rohan21895/memory-engine/issues/50) |
| `interpolation: "smooth"` | names no curve — and it is the only mode the reel planner emits | [#51](https://github.com/rohan21895/memory-engine/issues/51) |
| `easing` other than `linear`; `wipe`/`push`/`blur_dissolve`/`match_cut`/`custom`; a transition with ambient audio under it | no curve, no parameter set, and no statement of what the beds do across the blend | [#52](https://github.com/rohan21895/memory-engine/issues/52) |
| `noise_suppression` other than `none`; any `high_pass_hz`; a clip carrying both an ambient gain and a non-zero `ClipAudio.gain_db` | DSP specified by process rather than by outcome, and no gain composition rule | [#53](https://github.com/rohan21895/memory-engine/issues/53) |
| ducking with non-zero `attack_ms`/`release_ms`, or any trigger but `explicit_ranges` | no envelope shape; detection triggers are not reproducible | [#54](https://github.com/rohan21895/memory-engine/issues/54) |
| `loop: true` on a `MusicCue` | the loop join is undefined | [#57](https://github.com/rohan21895/memory-engine/issues/57) |
| any HLG or PQ source, or `working_space` ≠ `output_transform` | `ColorPipeline` names no tone-mapping operator | [#58](https://github.com/rohan21895/memory-engine/issues/58) |
| a non-zero `global_start_time` | no timecode track or drop-frame convention is specified for the delivered file | [#56](https://github.com/rohan21895/memory-engine/issues/56) |

`contracts/fixtures/edl/valid/reel-beat-locked-vertical-reframe.json` is refused today, on
six of those grounds at once. `test/gate.test.ts` asserts the exact set, so the day the
issues close the test is the checklist.

### Not in the EDL at all

`RenderTarget` carries no encode profile — no codec, container, pixel format, rate control,
GOP or scaler ([#56](https://github.com/rohan21895/memory-engine/issues/56)). Rather than
keep a destination-to-codec table inside the renderer, where it would be a delivery
decision made invisibly, the profile arrives as a required and fully explicit
`JobSpec.params.encode` block with no defaults and no fallbacks. This follows the
precedent `workers/render-print` set for the ICC profile.

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
- a rotation flag, an HDR transfer, or a rate that is not the timeline rate → refused.

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
- **`smooth` interpolation is refused**, which today means every reframe track the reel
  planner emits is refused. That is the intended pressure on
  [#51](https://github.com/rohan21895/memory-engine/issues/51), not an oversight.
