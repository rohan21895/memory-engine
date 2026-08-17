# tests/fixtures

`ingest-29-97.idx` — a frame-index sidecar captured **verbatim** from a real
run of `workers/ingest`'s `generate_video_proxy` job (macOS, VideoToolbox,
FFmpeg 7.0) over the demo library's 29.97 fps clip
(`2023-06 Hills/VID_20230612_101533.mp4`).

It is here for one reason: it is the only thing in the suite that pins
`proxy.read_frame_index` against the **writer** rather than against another
test helper. `tests/_support.py::write_sidecar` produces the same format for
generated clips, and a bug in that helper would otherwise be invisible — the
reader and the writer would agree with each other and both disagree with
ingest.

29.97 is the deliberate choice of rate. `30000/1001` has no exact float form,
so this file is also the case that proves the reader derives its rate from the
time base and the PTS delta (`1/30000` with a delta of `1001`, exactly
`30000/1001`) rather than from the float in the header.

Do not regenerate it to make a test pass. If it stops parsing, either the
sidecar format changed — in which case the reader must be updated deliberately,
and this file replaced with a newly captured one — or the reader broke.
