use std::{
    collections::BTreeSet,
    fs::{self, File},
    io::{self, BufRead, BufReader, BufWriter, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use chrono::Utc;
use memory_engine_contracts::{
    AudioStream, FrameIndexSidecar, FrameIndexSidecarMapping, JobError, JobErrorCode, JobOutput,
    JobOutputKind, JobSpec, JobSpecJobType, JobStateStatus, MediaRecord, PixelSize,
    ProcessingStateState, Progress, ProgressUnit, ProxyRef, ProxyRefKind, RationalTime, SpanRole,
    StageState, StageStateStatus, VideoProperties, VideoPropertiesFrameRate,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use crate::{gopro, job::JobExecutionError, media::atomic_write, CheckpointStore};

const CHECKPOINT_VERSION: i64 = 1;
const SIDECAR_VERSION: i64 = 1;

/// How far the proxy raster may sit from the source's oriented aspect ratio
/// before the two are treated as describing different pictures.
///
/// The scaler pins the proxy's height and derives its width, rounding to an even
/// number, so the width can legitimately land up to a pixel either side of the
/// exact ratio. Two pixels is looser than any rounding and far tighter than the
/// smallest thing this is guarding against: a quarter turn, which moves the
/// width by a factor of the aspect ratio itself.
const RASTER_AGREEMENT_PX: i64 = 2;

/// How far the container's declared frame rate may sit from the rate the decode
/// reported before the two are treated as different claims. Relative, and far
/// looser than a double's precision on these magnitudes.
const RATE_AGREEMENT: f64 = 1e-9;

#[derive(Debug, Error)]
pub enum VideoProxyError {
    #[error("video proxy only accepts generate_video_proxy JobSpecs")]
    WrongJobType,
    #[error("video proxy jobs must be local-only")]
    EgressDeclared,
    #[error("video proxy jobs must require hardware decode")]
    HardwareDecodeNotRequired,
    #[error("video proxy jobs must carry a resumable checkpoint")]
    NotResumable,
    #[error("video proxy parameters are invalid")]
    InvalidParameters(#[source] serde_json::Error),
    #[error("the requested hardware video backend is unsupported on this host")]
    UnsupportedBackend,
    #[error("video proxy job has no media inputs")]
    MissingInputs,
    #[error("input MediaRecord is missing")]
    MissingRecord,
    #[error("input MediaRecord has no present source")]
    MissingSource,
    #[error("FFmpeg does not expose the required hardware video pipeline")]
    FfmpegUnavailable,
    #[error("ffprobe could not be started, so no source geometry can be measured")]
    ProbeUnavailable,
    #[error("the source stream could not be measured")]
    ProbeFailed,
    #[error("the source declares a rotation that is not a quarter turn")]
    UnsupportedRotation,
    #[error("the proxy raster contradicts the source's oriented geometry")]
    OrientationMismatch,
    #[error("hardware proxy generation failed without software fallback")]
    FfmpegFailed,
    #[error("frame-index output was incomplete")]
    IncompleteFrameIndex,
    #[error("GoPro span assembly is inconsistent")]
    SpanAssemblyInvalid,
    #[error("video proxy I/O failed")]
    Io(#[from] io::Error),
    #[error("video proxy contract serialization failed")]
    Serialize(#[from] serde_json::Error),
    #[error(transparent)]
    Job(#[from] JobExecutionError),
    #[error(transparent)]
    Artifact(#[from] crate::IngestError),
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct VideoProxyReport {
    pub processed: usize,
    pub resumed_skips: usize,
    pub frames: i64,
    pub complete: bool,
    pub issues: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VideoProxyParams {
    height: u32,
    codec: String,
    crf: i32,
    hardware_decode: String,
    emit_frame_index: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum HardwareBackend {
    VideoToolbox,
    Nvdec,
    Qsv,
}

impl HardwareBackend {
    fn parse(value: &str) -> Result<Self, VideoProxyError> {
        match value {
            "videotoolbox" => Ok(Self::VideoToolbox),
            "nvdec" => Ok(Self::Nvdec),
            "qsv" => Ok(Self::Qsv),
            _ => Err(VideoProxyError::UnsupportedBackend),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::VideoToolbox => "videotoolbox",
            Self::Nvdec => "nvdec",
            Self::Qsv => "qsv",
        }
    }

    fn capabilities(self) -> [(&'static str, &'static str); 3] {
        match self {
            Self::VideoToolbox => [
                ("-hwaccels", "videotoolbox"),
                ("-filters", "scale_vt"),
                ("-encoders", "h264_videotoolbox"),
            ],
            Self::Nvdec => [
                ("-hwaccels", "cuda"),
                ("-filters", "scale_cuda"),
                ("-encoders", "h264_nvenc"),
            ],
            Self::Qsv => [
                ("-hwaccels", "qsv"),
                ("-filters", "scale_qsv"),
                ("-encoders", "h264_qsv"),
            ],
        }
    }

    fn supported_on_host(self) -> bool {
        match self {
            Self::VideoToolbox => cfg!(target_os = "macos"),
            Self::Nvdec | Self::Qsv => cfg!(target_os = "windows"),
        }
    }
}

#[derive(Debug)]
struct ProxyCommandArgs {
    before_input: Vec<String>,
    after_input: Vec<String>,
}

#[derive(Debug, Serialize)]
struct SidecarHeader {
    schema: &'static str,
    version: i64,
    mapping: &'static str,
    entry_count: i64,
    source_rate: Option<f64>,
    proxy_rate: Option<f64>,
    source_time_base_numerator: Option<i64>,
    source_time_base_denominator: Option<i64>,
}

#[derive(Debug, Serialize)]
struct SidecarEntry {
    proxy_frame: i64,
    source_pts: i64,
    source_time_seconds: f64,
}

/// Everything the container says about the source stream, read once before the
/// proxy pass because the proxy's own filter chain depends on it.
///
/// Nothing here is inferred from a filename, a codec or a default. A field the
/// container does not carry is `None`, and the two fields that cannot be absent
/// without the proxy being wrong -- the stored raster and the rotation -- are
/// hard failures rather than fallbacks.
#[derive(Clone, Debug, PartialEq)]
struct SourceProbe {
    stored_size: PixelSize,
    /// Clockwise rotation a player must apply to the stored raster to display
    /// the picture. See `display_rotation_degrees` for the sign convention,
    /// which is the single easiest thing in this file to get backwards.
    rotation_deg: i64,
    /// `r_frame_rate`, exactly, as the container states it. 30000/1001 stays
    /// 30000/1001; the float form is derived where a float is required and
    /// never stored as the source of truth.
    frame_rate: (i64, i64),
    /// `(duration_ts, time_base_numerator, time_base_denominator)` — the exact
    /// integer duration in the stream's own time base, when the container
    /// declares one.
    duration: Option<(i64, i64, i64)>,
    video_codec: Option<String>,
    bit_rate: Option<i64>,
    color_primaries: Option<String>,
    transfer_characteristics: Option<String>,
    audio_streams: Vec<AudioStream>,
}

impl SourceProbe {
    /// The picture as it is displayed. **Every normalised coordinate in this
    /// system is relative to this**, not to the stored raster, which is why a
    /// quarter turn has to swap the axes here rather than somewhere downstream
    /// that cannot see the display matrix.
    fn oriented_size(&self) -> PixelSize {
        if self.rotation_deg == 90 || self.rotation_deg == 270 {
            PixelSize {
                width: self.stored_size.height,
                height: self.stored_size.width,
            }
        } else {
            self.stored_size.clone()
        }
    }

    fn frame_rate_float(&self) -> f64 {
        self.frame_rate.0 as f64 / self.frame_rate.1 as f64
    }
}

#[derive(Debug, Default)]
struct FrameIndexStats {
    entry_count: i64,
    first_size: Option<PixelSize>,
    source_rate: Option<f64>,
    source_time_base: Option<(i64, i64)>,
    previous_pts: Option<i64>,
    first_delta: Option<i64>,
    variable_delta: bool,
}

pub fn execute_video_proxy(
    job: &mut JobSpec,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
    ffmpeg_path: &Path,
) -> Result<VideoProxyReport, VideoProxyError> {
    let params = validate_job(job)?;
    let backend = HardwareBackend::parse(&params.hardware_decode)?;
    verify_backend(ffmpeg_path, backend)?;
    let media_ids = job
        .inputs
        .media_ids
        .clone()
        .filter(|ids| !ids.is_empty())
        .ok_or(VideoProxyError::MissingInputs)?;
    let span_ids = input_span_ids(output_dir, &media_ids)?;
    if job.state.status == JobStateStatus::Completed {
        let mut report = VideoProxyReport {
            complete: true,
            ..VideoProxyReport::default()
        };
        refresh_spans_for_job(output_dir, &span_ids, &mut report);
        return Ok(report);
    }
    let completed = job
        .checkpoint
        .as_ref()
        .and_then(|checkpoint| checkpoint.completed_input_ids.clone())
        .unwrap_or_default();
    let mut report = VideoProxyReport {
        resumed_skips: media_ids
            .iter()
            .filter(|media_id| completed.contains(media_id))
            .count(),
        ..VideoProxyReport::default()
    };
    job.state.status = JobStateStatus::Running;
    job.state
        .started_at
        .get_or_insert_with(|| Utc::now().to_rfc3339());

    for media_id in media_ids {
        if completed.contains(&media_id) {
            continue;
        }
        let mut record = load_record(output_dir, &media_id)?;
        let source = record
            .sources
            .iter()
            .find(|source| source.present && Path::new(&source.path).is_file())
            .map(|source| PathBuf::from(&source.path))
            .ok_or(VideoProxyError::MissingSource)?;
        let generated = match generate_one(
            job,
            &media_id,
            &source,
            output_dir,
            checkpoint_store,
            ffmpeg_path,
            &params,
            backend,
        ) {
            Ok(generated) => generated,
            Err(error) => {
                record_failure(job, &media_id, &error);
                checkpoint_store.save(job)?;
                return Err(error);
            }
        };
        report.frames += generated.frame_index.entry_count;
        report.issues.extend(generated.issues.iter().cloned());
        record.video = Some(generated.video.clone());
        attach_proxy(&mut record, generated.proxy.clone());
        persist_record(output_dir, &record)?;
        append_output(job, &generated.proxy);
        mark_completed(job, &media_id, &generated.proxy.proxy_id);
        report.processed += 1;
        job.state.progress = Some(Progress {
            units_done: report.frames as f64,
            units_total: None,
            unit: ProgressUnit::Frames,
            bytes_processed: None,
            message: Some(format!(
                "completed {} of {} video inputs",
                report.resumed_skips + report.processed,
                job.inputs.media_ids.as_ref().map_or(0, Vec::len)
            )),
        });
        job.state.heartbeat_at = Some(Utc::now().to_rfc3339());
        job.error = None;
        checkpoint_store.save(job)?;
    }

    report.complete = true;
    let now = Utc::now().to_rfc3339();
    job.state.status = JobStateStatus::Completed;
    job.state.finished_at = Some(now.clone());
    job.state.heartbeat_at = Some(now);
    checkpoint_store.save(job)?;
    // Span reconciliation is downstream bookkeeping. Proxy artifacts and their
    // completed checkpoint remain terminal success even when a card was ejected
    // or a sibling record is temporarily unavailable.
    refresh_spans_for_job(output_dir, &span_ids, &mut report);
    Ok(report)
}

struct GeneratedProxy {
    proxy: ProxyRef,
    frame_index: FrameIndexSidecar,
    video: VideoProperties,
    issues: Vec<String>,
}

#[allow(clippy::too_many_arguments)]
fn generate_one(
    job: &mut JobSpec,
    media_id: &str,
    source: &Path,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
    ffmpeg_path: &Path,
    params: &VideoProxyParams,
    backend: HardwareBackend,
) -> Result<GeneratedProxy, VideoProxyError> {
    // Measured BEFORE the pass, because the filter chain depends on the answer.
    // A proxy built without knowing the source's rotation is the exact defect
    // this measurement exists to remove, so an unprobeable source fails rather
    // than falling back to "assume it is the right way up".
    let probe = probe_source(&ffprobe_path(ffmpeg_path), source)?;

    let work = output_dir.join("proxies").join("work");
    fs::create_dir_all(&work)?;
    let nonce = format!("{}-{}", std::process::id(), Utc::now().timestamp_micros());
    let partial_video = work.join(format!("{media_id}-{nonce}.mp4"));
    let rows_path = work.join(format!("{media_id}-{nonce}.rows"));
    let mut rows = BufWriter::new(File::create(&rows_path)?);
    let command_args = proxy_command_args(backend, params.height, params.crf, probe.rotation_deg);
    let mut child = Command::new(ffmpeg_path)
        .args(&command_args.before_input)
        .arg(source)
        .args(&command_args.after_input)
        .arg(&partial_video)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| VideoProxyError::FfmpegUnavailable)?;
    let stderr = child.stderr.take().ok_or(VideoProxyError::FfmpegFailed)?;
    let mut stats = FrameIndexStats::default();
    for line in BufReader::new(stderr).lines() {
        let line = line?;
        if let Some(rate) = parse_frame_rate(&line) {
            stats.source_rate = Some(rate);
        }
        if let Some(time_base) = parse_time_base(&line) {
            stats.source_time_base = Some(time_base);
        }
        if let Some(frame) = parse_showinfo(&line) {
            if stats.first_size.is_none() {
                stats.first_size = parse_size(&line);
            }
            update_stats(&mut stats, &frame);
            serde_json::to_writer(&mut rows, &frame)?;
            rows.write_all(b"\n")?;
            if stats.entry_count % 120 == 0 {
                update_running_checkpoint(job, media_id, stats.entry_count);
                checkpoint_store.save(job)?;
            }
        }
    }
    rows.flush()?;
    let status = child.wait()?;
    if !status.success() {
        let _ = fs::remove_file(&partial_video);
        let _ = fs::remove_file(&rows_path);
        return Err(VideoProxyError::FfmpegFailed);
    }
    if stats.entry_count == 0 || stats.first_size.is_none() {
        return Err(VideoProxyError::IncompleteFrameIndex);
    }

    let raster = stats
        .first_size
        .clone()
        .ok_or(VideoProxyError::IncompleteFrameIndex)?;
    let oriented = probe.oriented_size();
    if !raster_matches_oriented(&oriented, &raster) {
        // Two measurements of the same picture disagree. Either the orientation
        // filter did not run or the container and the decoder describe
        // different streams; both produce a proxy whose every coordinate is
        // wrong in a way no later stage can see, so nothing is written.
        let _ = fs::remove_file(&partial_video);
        let _ = fs::remove_file(&rows_path);
        return Err(VideoProxyError::OrientationMismatch);
    }
    // The container's frame rate and the decoder's, cross-checked. They come
    // from the same library but by different routes, and the sidecar and the
    // MediaRecord are read by different consumers that must not be able to
    // disagree about what rate a clip is.
    if let Some(decoded) = stats.source_rate {
        let declared = probe.frame_rate_float();
        if (decoded - declared).abs() > RATE_AGREEMENT * declared.max(1.0) {
            return Err(VideoProxyError::ProbeFailed);
        }
    }
    let (video, duration_issue) = video_properties(&probe, &stats);

    let proxy_id = hash_file(&partial_video)?;
    let final_directory = output_dir
        .join("proxies")
        .join(&proxy_id[..2])
        .join(&proxy_id[2..4]);
    fs::create_dir_all(&final_directory)?;
    let final_video = final_directory.join(format!("{proxy_id}.mp4"));
    move_idempotently(&partial_video, &final_video)?;
    let sidecar_path = final_directory.join(format!("{proxy_id}.idx"));
    write_sidecar(&sidecar_path, &rows_path, &stats)?;
    let _ = fs::remove_file(rows_path);
    let mapping = if stats.variable_delta {
        FrameIndexSidecarMapping::Table
    } else {
        FrameIndexSidecarMapping::Identity
    };
    let frame_index = FrameIndexSidecar {
        path: sidecar_path.to_string_lossy().into_owned(),
        entry_count: stats.entry_count,
        mapping,
        source_rate: stats.source_rate,
        proxy_rate: stats.source_rate,
    };
    let proxy = ProxyRef {
        proxy_id,
        kind: ProxyRefKind::VideoProxy480p,
        path: final_video.to_string_lossy().into_owned(),
        size: stats.first_size,
        byte_size: Some(fs::metadata(&final_video)?.len() as i64),
        generator_version: Some(format!(
            "memory-engine-ingest/0.1.0+ffmpeg+{}+crf{}",
            backend.name(),
            params.crf
        )),
        frame_index: Some(frame_index.clone()),
    };
    Ok(GeneratedProxy {
        proxy,
        frame_index,
        video,
        issues: duration_issue.into_iter().collect(),
    })
}

// ---------------------------------------------------------------- probing --

/// `ffprobe` beside the FFmpeg this job was given, or the one the environment
/// names.
///
/// The two ship together, so deriving one from the other is right far more
/// often than a bare `ffprobe` off `PATH` would be: a job pointed at a pinned
/// FFmpeg build must not silently measure its sources with a different one.
fn ffprobe_path(ffmpeg_path: &Path) -> PathBuf {
    std::env::var_os("MEMORY_ENGINE_FFPROBE")
        .map_or_else(|| ffprobe_beside(ffmpeg_path), PathBuf::from)
}

fn ffprobe_beside(ffmpeg_path: &Path) -> PathBuf {
    match ffmpeg_path.file_name().and_then(|name| name.to_str()) {
        Some(name) if name.contains("ffmpeg") => {
            ffmpeg_path.with_file_name(name.replace("ffmpeg", "ffprobe"))
        }
        _ => PathBuf::from("ffprobe"),
    }
}

fn probe_source(ffprobe_path: &Path, source: &Path) -> Result<SourceProbe, VideoProxyError> {
    let output = Command::new(ffprobe_path)
        .args([
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
        ])
        .arg(source)
        .output()
        .map_err(|_| VideoProxyError::ProbeUnavailable)?;
    if !output.status.success() {
        return Err(VideoProxyError::ProbeFailed);
    }
    parse_probe(&String::from_utf8_lossy(&output.stdout))
}

/// Reads `ffprobe -show_streams -show_format -of json`.
///
/// Split from the process call so it can be tested against real captured
/// output rather than against a mock of what ffprobe was assumed to print.
fn parse_probe(text: &str) -> Result<SourceProbe, VideoProxyError> {
    let document: Value = serde_json::from_str(text).map_err(|_| VideoProxyError::ProbeFailed)?;
    let streams = document
        .get("streams")
        .and_then(Value::as_array)
        .ok_or(VideoProxyError::ProbeFailed)?;
    let video = streams
        .iter()
        .find(|stream| stream.get("codec_type").and_then(Value::as_str) == Some("video"))
        .ok_or(VideoProxyError::ProbeFailed)?;

    let width = json_i64(video, "width").ok_or(VideoProxyError::ProbeFailed)?;
    let height = json_i64(video, "height").ok_or(VideoProxyError::ProbeFailed)?;
    if width <= 0 || height <= 0 {
        return Err(VideoProxyError::ProbeFailed);
    }
    let frame_rate = json_str(video, "r_frame_rate")
        .and_then(parse_exact_rational)
        .ok_or(VideoProxyError::ProbeFailed)?;
    let rotation_deg = display_rotation_degrees(display_matrix_rotation(video).unwrap_or(0.0))
        .ok_or(
            // A rotation that is not a quarter turn cannot be applied by a
            // transpose, and rounding it to one would put the whole picture at
            // an angle nothing downstream could see.
            VideoProxyError::UnsupportedRotation,
        )?;

    let duration = json_i64(video, "duration_ts").and_then(|ticks| {
        let (numerator, denominator) =
            json_str(video, "time_base").and_then(parse_exact_rational)?;
        (ticks > 0 && numerator > 0 && denominator > 0).then_some((ticks, numerator, denominator))
    });
    // The VIDEO stream's bit rate, or nothing. The container's `format.bit_rate`
    // is the whole file — video plus every audio track plus muxing overhead —
    // and it sits one field away from being read as this one. A stream that
    // does not declare a bit rate has not been measured, and a number that is
    // wrong by however much audio is in the file is exactly the plausible
    // wrong number this project keeps finding.
    let bit_rate = json_i64(video, "bit_rate").filter(|value| *value >= 0);

    Ok(SourceProbe {
        stored_size: PixelSize { width, height },
        rotation_deg,
        frame_rate,
        duration,
        video_codec: declared_string(video, "codec_name"),
        bit_rate,
        color_primaries: declared_string(video, "color_primaries"),
        transfer_characteristics: declared_string(video, "color_transfer"),
        audio_streams: streams.iter().filter_map(parse_audio_stream).collect(),
    })
}

fn parse_audio_stream(stream: &Value) -> Option<AudioStream> {
    if stream.get("codec_type").and_then(Value::as_str) != Some("audio") {
        return None;
    }
    let channels = json_i64(stream, "channels")?;
    let sample_rate = json_i64(stream, "sample_rate")?;
    if channels < 1 || sample_rate <= 0 {
        return None;
    }
    Some(AudioStream {
        // The CONTAINER's stream index, which is the number that selects this
        // track (`-map 0:<index>`). Not an ordinal among the audio tracks:
        // those two agree only for a file whose first stream is audio.
        stream_index: json_i64(stream, "index").filter(|index| *index >= 0)?,
        channels,
        sample_rate,
        codec: declared_string(stream, "codec_name"),
        // FFmpeg writes `und` when a track declares no language. Carrying that
        // through as a language would be a claim; it is the absence of one.
        language: stream
            .get("tags")
            .and_then(|tags| declared_string(tags, "language"))
            .filter(|language| language != "und"),
        // Silence is not measured here. `null` is "nobody looked", which is a
        // different statement from `false`.
        is_silent: None,
    })
}

/// The display matrix rotation, and only that.
///
/// The legacy `rotate` stream tag is deliberately not consulted: it carries the
/// OPPOSITE sign to the display-matrix value, so reading the two as
/// interchangeable turns a 90-degree clockwise source into a 90-degree
/// counter-clockwise one — a failure that looks like a correctly rotated video
/// until you notice it is upside down. FFmpeg synthesises the display matrix
/// from that tag when it demuxes, so nothing is lost by ignoring it.
fn display_matrix_rotation(video: &Value) -> Option<f64> {
    video
        .get("side_data_list")
        .and_then(Value::as_array)?
        .iter()
        .find_map(|entry| entry.get("rotation").and_then(Value::as_f64))
}

/// Converts ffprobe's display-matrix rotation into the clockwise rotation that
/// has to be applied to the stored raster to display the picture.
///
/// ffprobe reports `av_display_rotation_get`, whose sign convention is
/// counter-clockwise: a portrait phone clip stored as a landscape raster
/// reports **-90**. FFmpeg's own autorotate negates that and then inserts a
/// **clockwise** transpose, which is what makes the frame portrait. So the
/// number this system stores — `VideoProperties.rotation_deg` — is the negated
/// one: 90 for that clip, meaning "turn the stored raster 90 degrees clockwise
/// and you have the picture".
///
/// Returns `None` for anything that is not a quarter turn.
fn display_rotation_degrees(probe_rotation: f64) -> Option<i64> {
    if !probe_rotation.is_finite() {
        return None;
    }
    let clockwise = (-probe_rotation).rem_euclid(360.0);
    let quarters = (clockwise / 90.0).round();
    ((clockwise - quarters * 90.0).abs() < 1.0).then(|| (quarters as i64 % 4) * 90)
}

fn parse_exact_rational(value: &str) -> Option<(i64, i64)> {
    let (numerator, denominator) = value.split_once('/')?;
    let numerator: i64 = numerator.trim().parse().ok()?;
    let denominator: i64 = denominator.trim().parse().ok()?;
    (numerator > 0 && denominator > 0).then_some((numerator, denominator))
}

/// ffprobe emits some integers as numbers and others as strings in the same
/// document (`width` is a number, `bit_rate` is a string), so both are read.
fn json_i64(object: &Value, key: &str) -> Option<i64> {
    let value = object.get(key)?;
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|text| text.trim().parse().ok()))
}

fn json_str<'a>(object: &'a Value, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

/// A string field that the container actually declares. ffprobe writes
/// `unknown` and `reserved` for fields a stream leaves unset, and storing those
/// as if they were measurements is how "nobody said" becomes a value.
fn declared_string(object: &Value, key: &str) -> Option<String> {
    json_str(object, key)
        .map(str::trim)
        .filter(|value| !value.is_empty() && *value != "unknown" && *value != "reserved")
        .map(str::to_owned)
}

// ------------------------------------------------------- measured geometry --

/// The proxy raster and the source's oriented size must describe the same
/// picture, up to the scaler's even-width rounding.
///
/// This is the check that makes a sideways proxy detectable. FFmpeg's autorotate
/// is inserted into the *software* filter chain only: with `-hwaccel` and a
/// hardware output format it does not run at all, so a rotated source decodes
/// straight into a landscape raster and every coordinate measured on that proxy
/// is a quarter turn away from the convention the whole system is built on.
/// Nothing downstream can see that, because the proxy is the only picture the
/// analysis stack ever opens. So it is measured here, against the one number
/// that knows better.
fn raster_matches_oriented(oriented: &PixelSize, raster: &PixelSize) -> bool {
    if oriented.width <= 0 || oriented.height <= 0 || raster.width <= 0 || raster.height <= 0 {
        return false;
    }
    let expected = oriented.width * raster.height;
    let actual = raster.width * oriented.height;
    (expected - actual).abs() <= RASTER_AGREEMENT_PX * oriented.height
}

/// The source's duration, exactly, from whichever of the two exact answers
/// applies.
///
/// A constant-rate source has one: the number of frames the decode counted, at
/// the container's exact frame rate. That is a measurement, not a claim — every
/// frame passed through `showinfo` — and it is the form the golden fixtures use.
///
/// A variable-rate source does not: a frame count at a nominal rate is not an
/// elapsed time, and writing one would be the plausible wrong number this
/// project keeps finding. So its duration comes from the container's own exact
/// integer duration in its own time base.
///
/// If neither is available the frame count is used and the caller is told, so
/// the weaker number is never mistaken for the stronger one.
fn source_duration(probe: &SourceProbe, frames: i64, variable: bool) -> (RationalTime, bool) {
    if !variable && frames > 0 {
        return (
            RationalTime {
                value: frames as f64,
                rate: probe.frame_rate_float(),
            },
            true,
        );
    }
    if let Some((ticks, numerator, denominator)) = probe.duration {
        return (
            RationalTime {
                value: ticks as f64,
                rate: denominator as f64 / numerator as f64,
            },
            true,
        );
    }
    (
        RationalTime {
            value: frames as f64,
            rate: probe.frame_rate_float(),
        },
        false,
    )
}

/// `MediaRecord.video`, from the container's account of the source and the
/// decode this job just performed.
///
/// The two are combined deliberately. `is_variable_frame_rate` is the DECODE's
/// answer — every frame's presentation timestamp went past `update_stats`, with
/// `-fps_mode passthrough` keeping them the source's own — rather than the
/// container's `avg_frame_rate` versus `r_frame_rate` heuristic, which
/// disagrees with itself on plenty of constant-rate files whose last frame is
/// held a little longer. The frame index sidecar already publishes exactly this
/// measurement as its `mapping`, so a record and its sidecar cannot say
/// different things about the same file.
fn video_properties(
    probe: &SourceProbe,
    stats: &FrameIndexStats,
) -> (VideoProperties, Option<String>) {
    let (duration, duration_is_exact) =
        source_duration(probe, stats.entry_count, stats.variable_delta);
    let issue = (!duration_is_exact).then(|| {
        "a variable-rate source declares no container duration; its recorded duration is a \
         frame COUNT at the nominal rate and not an elapsed time"
            .to_owned()
    });
    let properties = VideoProperties {
        stored_size: Some(probe.stored_size.clone()),
        oriented_size: probe.oriented_size(),
        rotation_deg: Some(probe.rotation_deg),
        duration,
        frame_rate: VideoPropertiesFrameRate {
            numerator: probe.frame_rate.0,
            denominator: probe.frame_rate.1,
        },
        is_variable_frame_rate: Some(stats.variable_delta),
        // Not read. An SMPTE start timecode is drop-frame or non-drop-frame at
        // 29.97 and 59.94, the two rates where it matters, and the two land on
        // different frame numbers. Guessing which one a container meant would
        // offset a professional round-trip by up to 108 frames an hour with
        // nothing to show for it, so this stays absent until something reads
        // the flag rather than the string.
        start_timecode: None,
        video_codec: probe.video_codec.clone(),
        bit_rate: probe.bit_rate,
        color_primaries: probe.color_primaries.clone(),
        transfer_characteristics: probe.transfer_characteristics.clone(),
        audio_streams: Some(probe.audio_streams.clone()),
    };
    (properties, issue)
}

fn validate_job(job: &JobSpec) -> Result<VideoProxyParams, VideoProxyError> {
    if job.job_type != JobSpecJobType::GenerateVideoProxy {
        return Err(VideoProxyError::WrongJobType);
    }
    if job.egress.requires_egress {
        return Err(VideoProxyError::EgressDeclared);
    }
    if !job
        .requirements
        .as_ref()
        .and_then(|requirements| requirements.hardware_decode)
        .unwrap_or(false)
    {
        return Err(VideoProxyError::HardwareDecodeNotRequired);
    }
    if !job
        .checkpoint
        .as_ref()
        .is_some_and(|checkpoint| checkpoint.resumable)
    {
        return Err(VideoProxyError::NotResumable);
    }
    let value = job.params.clone().map_or_else(
        || serde_json::Value::Object(Default::default()),
        |params| serde_json::Value::Object(params.into_iter().collect()),
    );
    let params: VideoProxyParams =
        serde_json::from_value(value).map_err(VideoProxyError::InvalidParameters)?;
    if params.height != 480
        || params.codec != "h264"
        || HardwareBackend::parse(&params.hardware_decode).is_err()
        || !params.emit_frame_index
    {
        return Err(VideoProxyError::UnsupportedBackend);
    }
    Ok(params)
}

fn verify_backend(ffmpeg_path: &Path, backend: HardwareBackend) -> Result<(), VideoProxyError> {
    if !backend.supported_on_host() {
        return Err(VideoProxyError::UnsupportedBackend);
    }
    for (listing, capability) in backend.capabilities() {
        let output = Command::new(ffmpeg_path)
            .args(["-hide_banner", listing])
            .output()
            .map_err(|_| VideoProxyError::FfmpegUnavailable)?;
        let mut listing_text = String::from_utf8_lossy(&output.stdout).into_owned();
        listing_text.push_str(&String::from_utf8_lossy(&output.stderr));
        if !output.status.success() || !listing_has_capability(&listing_text, capability) {
            return Err(VideoProxyError::FfmpegUnavailable);
        }
    }
    Ok(())
}

fn listing_has_capability(listing: &str, capability: &str) -> bool {
    listing.split_whitespace().any(|token| {
        token.trim_matches(|character: char| !character.is_ascii_alphanumeric() && character != '_')
            == capability
    })
}

/// The filters that turn the stored raster into the displayed picture.
///
/// These are exactly what FFmpeg's own autorotate inserts for the same display
/// matrix (`transpose=1` is clockwise), applied here explicitly because
/// autorotate does not run on a hardware-decoded frame.
///
/// DO NOT ADD `-noautorotate` TO MAKE THIS EXPLICIT. It looks like the obvious
/// belt-and-braces here and it breaks the output instead. Measured on FFmpeg
/// 7.0, macOS, against a real -90 clip:
///
///   default (what this code does)  proxy is 270x480, upright, and carries NO
///                                  display matrix — the turn is baked in once
///                                  and nothing downstream can apply it again
///   `-noautorotate`                proxy is 270x480, upright, and carries
///                                  `rotation: -90` — because the flag also
///                                  stops FFmpeg clearing the matrix on the
///                                  output stream, so every later decode turns
///                                  the picture a SECOND time
///
/// The second is a silent quarter turn in everything that opens the proxy.
/// `a_rotated_proxy_carries_no_display_matrix_of_its_own` is the check that
/// fails if this is ever "hardened".
fn orientation_filters(rotation_deg: i64) -> Option<&'static str> {
    match rotation_deg {
        90 => Some("transpose=1"),
        180 => Some("hflip,vflip"),
        270 => Some("transpose=2"),
        _ => None,
    }
}

fn proxy_command_args(
    backend: HardwareBackend,
    height: u32,
    crf: i32,
    rotation_deg: i64,
) -> ProxyCommandArgs {
    let bitrate = hardware_bitrate(crf).to_string();
    let (hwaccel, output_format, scaler, encoder, encoder_options): (
        &str,
        &str,
        &str,
        &str,
        &[&str],
    ) = match backend {
        HardwareBackend::VideoToolbox => (
            "videotoolbox",
            "videotoolbox_vld",
            "scale_vt",
            "h264_videotoolbox",
            &["-allow_sw", "0", "-profile:v", "high"],
        ),
        HardwareBackend::Nvdec => (
            "cuda",
            "cuda",
            "scale_cuda",
            "h264_nvenc",
            &["-preset:v", "p4", "-profile:v", "high"],
        ),
        HardwareBackend::Qsv => (
            "qsv",
            "qsv",
            "scale_qsv",
            "h264_qsv",
            &["-preset:v", "medium", "-profile:v", "high"],
        ),
    };
    let scaled_width = if backend == HardwareBackend::VideoToolbox {
        format!("ceil(iw*{height}/ih/2)*2")
    } else {
        "-2".to_owned()
    };
    // An unrotated source takes the hardware scaler, unchanged: it is the fast
    // path, it is what every existing proxy in the world was produced by, and
    // the proxy id is the BLAKE3 of those bytes.
    //
    // A rotated source cannot. The hardware scalers pin the OUTPUT height and
    // derive the width from `iw`/`ih` of the frame they are handed, and that
    // frame is still the stored raster because autorotate did not run — so a
    // portrait clip would be scaled as if it were landscape and then never
    // turned. Downloading first costs a full-resolution frame over the bus,
    // which is the price of a correctly oriented proxy, and the decode stays on
    // the hardware either way.
    let filter = match orientation_filters(rotation_deg) {
        None => {
            format!("{scaler}=w={scaled_width}:h={height},hwdownload,format=nv12,setsar=1,showinfo")
        }
        Some(orientation) => {
            format!("hwdownload,format=nv12,{orientation},scale=w=-2:h={height},setsar=1,showinfo")
        }
    };
    let before_input = [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-hwaccel",
        hwaccel,
        "-hwaccel_output_format",
        output_format,
        "-i",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect();
    let mut after_input: Vec<String> = [
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "-1",
        "-vf",
        &filter,
        "-fps_mode",
        "passthrough",
        "-c:v",
        encoder,
    ]
    .into_iter()
    .map(str::to_owned)
    .collect();
    after_input.extend(encoder_options.iter().map(|value| (*value).to_owned()));
    after_input.extend([
        "-b:v".to_owned(),
        bitrate,
        "-c:a".to_owned(),
        "aac".to_owned(),
        "-b:a".to_owned(),
        "96k".to_owned(),
        "-movflags".to_owned(),
        "+faststart".to_owned(),
        "-y".to_owned(),
    ]);
    ProxyCommandArgs {
        before_input,
        after_input,
    }
}

fn parse_frame_rate(line: &str) -> Option<f64> {
    let marker = "frame_rate: ";
    let value = line.split_once(marker)?.1.split_whitespace().next()?;
    let (numerator, denominator) = value.split_once('/')?;
    let numerator: f64 = numerator.parse().ok()?;
    let denominator: f64 = denominator.parse().ok()?;
    (denominator != 0.0).then_some(numerator / denominator)
}

fn parse_time_base(line: &str) -> Option<(i64, i64)> {
    let value = line.split_once("time_base: ")?.1.split([',', ' ']).next()?;
    let (numerator, denominator) = value.split_once('/')?;
    let numerator = numerator.parse().ok()?;
    let denominator = denominator.parse().ok()?;
    (denominator != 0).then_some((numerator, denominator))
}

fn parse_showinfo(line: &str) -> Option<SidecarEntry> {
    if !line.contains("showinfo") || !line.contains(" pts:") || !line.contains(" pts_time:") {
        return None;
    }
    let frame = parse_token(line, " n:")?;
    let pts = parse_token(line, " pts:")?;
    let seconds = parse_token_f64(line, " pts_time:")?;
    Some(SidecarEntry {
        proxy_frame: frame,
        source_pts: pts,
        source_time_seconds: seconds,
    })
}

fn parse_size(line: &str) -> Option<PixelSize> {
    let value = line.split_once(" s:")?.1.split_whitespace().next()?;
    let (width, height) = value.split_once('x')?;
    Some(PixelSize {
        width: width.parse().ok()?,
        height: height.parse().ok()?,
    })
}

fn parse_token(line: &str, marker: &str) -> Option<i64> {
    line.split_once(marker)?
        .1
        .split_whitespace()
        .next()?
        .parse()
        .ok()
}

fn parse_token_f64(line: &str, marker: &str) -> Option<f64> {
    line.split_once(marker)?
        .1
        .split_whitespace()
        .next()?
        .parse()
        .ok()
}

fn update_stats(stats: &mut FrameIndexStats, frame: &SidecarEntry) {
    stats.entry_count += 1;
    if let Some(previous) = stats.previous_pts {
        let delta = frame.source_pts - previous;
        match stats.first_delta {
            Some(first) if first != delta => stats.variable_delta = true,
            None => stats.first_delta = Some(delta),
            _ => {}
        }
    }
    stats.previous_pts = Some(frame.source_pts);
}

fn write_sidecar(
    path: &Path,
    rows_path: &Path,
    stats: &FrameIndexStats,
) -> Result<(), VideoProxyError> {
    let temporary = path.with_extension("idx.tmp");
    let mut writer = BufWriter::new(File::create(&temporary)?);
    let mapping = if stats.variable_delta {
        "table"
    } else {
        "identity"
    };
    serde_json::to_writer(
        &mut writer,
        &SidecarHeader {
            schema: "memory-engine-frame-index",
            version: SIDECAR_VERSION,
            mapping,
            entry_count: stats.entry_count,
            source_rate: stats.source_rate,
            proxy_rate: stats.source_rate,
            source_time_base_numerator: stats.source_time_base.map(|value| value.0),
            source_time_base_denominator: stats.source_time_base.map(|value| value.1),
        },
    )?;
    writer.write_all(b"\n")?;
    io::copy(&mut File::open(rows_path)?, &mut writer)?;
    writer.flush()?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn hash_file(path: &Path) -> Result<String, VideoProxyError> {
    let mut file = File::open(path)?;
    let mut hasher = blake3::Hasher::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(hasher.finalize().to_hex().to_string())
}

fn move_idempotently(source: &Path, destination: &Path) -> Result<(), io::Error> {
    if destination.exists() {
        fs::remove_file(source)
    } else {
        fs::rename(source, destination)
    }
}

fn record_path(output_dir: &Path, media_id: &str) -> PathBuf {
    output_dir
        .join("records")
        .join(&media_id[..2])
        .join(&media_id[2..4])
        .join(format!("{media_id}.json"))
}

fn load_record(output_dir: &Path, media_id: &str) -> Result<MediaRecord, VideoProxyError> {
    let bytes =
        fs::read(record_path(output_dir, media_id)).map_err(|_| VideoProxyError::MissingRecord)?;
    serde_json::from_slice(&bytes).map_err(VideoProxyError::Serialize)
}

fn input_span_ids(
    output_dir: &Path,
    media_ids: &[String],
) -> Result<BTreeSet<String>, VideoProxyError> {
    let mut spans = BTreeSet::new();
    for media_id in media_ids {
        if let Some(span) = load_record(output_dir, media_id)?.span {
            if span.role == SpanRole::Member {
                spans.insert(span.span_id);
            }
        }
    }
    Ok(spans)
}

fn refresh_spans_for_job(
    output_dir: &Path,
    span_ids: &BTreeSet<String>,
    report: &mut VideoProxyReport,
) {
    if refresh_span_assemblies(output_dir, span_ids).is_err() {
        report.issues.push(
            "video proxies completed; GoPro span refresh deferred because a local record was unavailable"
                .to_owned(),
        );
    }
}

fn refresh_span_assemblies(
    output_dir: &Path,
    span_ids: &BTreeSet<String>,
) -> Result<(), VideoProxyError> {
    for span_id in span_ids {
        let existing =
            load_record(output_dir, span_id).map_err(|_| VideoProxyError::SpanAssemblyInvalid)?;
        let span = existing
            .span
            .as_ref()
            .filter(|span| span.role == SpanRole::Assembly && span.span_id == *span_id)
            .ok_or(VideoProxyError::SpanAssemblyInvalid)?;
        let member_ids = span
            .member_media_ids
            .as_deref()
            .filter(|members| members.len() >= 2)
            .ok_or(VideoProxyError::SpanAssemblyInvalid)?;
        let mut records = Vec::with_capacity(member_ids.len() + 1);
        records.push(existing.clone());
        for media_id in member_ids {
            records.push(
                load_record(output_dir, media_id)
                    .map_err(|_| VideoProxyError::SpanAssemblyInvalid)?,
            );
        }
        let built = gopro::build(&records);
        if !built.issues.is_empty() {
            return Err(VideoProxyError::SpanAssemblyInvalid);
        }
        for member in built.members {
            persist_record(output_dir, &member)?;
        }
        let desired = built
            .assemblies
            .into_iter()
            .find(|assembly| assembly.media_id == *span_id)
            .ok_or(VideoProxyError::SpanAssemblyInvalid)?;
        let refreshed =
            gopro::merge_existing_assembly(&existing, &desired, &Utc::now().to_rfc3339());
        if refreshed != existing {
            persist_record(output_dir, &refreshed)?;
        }
    }
    Ok(())
}

fn persist_record(output_dir: &Path, record: &MediaRecord) -> Result<(), VideoProxyError> {
    let bytes = serde_json::to_vec_pretty(record)?;
    atomic_write(&record_path(output_dir, &record.media_id), &bytes)?;
    Ok(())
}

fn attach_proxy(record: &mut MediaRecord, proxy: ProxyRef) {
    let proxies = record.proxies.get_or_insert_with(Vec::new);
    proxies.retain(|existing| existing.kind != ProxyRefKind::VideoProxy480p);
    proxies.push(proxy);
    if record.processing.state == ProcessingStateState::Hashed
        || record.processing.state == ProcessingStateState::Discovered
    {
        record.processing.state = ProcessingStateState::Proxied;
    }
    let now = Utc::now().to_rfc3339();
    record.processing.stages.video_proxy = Some(StageState {
        status: StageStateStatus::Done,
        attempts: Some(1),
        completed_at: Some(now.clone()),
        job_id: None,
        skip_reason: None,
        last_error: None,
    });
    record.updated_at = Some(now);
}

fn append_output(job: &mut JobSpec, proxy: &ProxyRef) {
    let outputs = job.outputs.get_or_insert_with(Vec::new);
    if outputs.iter().any(|output| output.id == proxy.proxy_id) {
        return;
    }
    outputs.push(JobOutput {
        kind: JobOutputKind::Proxy,
        id: proxy.proxy_id.clone(),
        path: Some(proxy.path.clone()),
        byte_size: proxy.byte_size,
        produced_at: Some(Utc::now().to_rfc3339()),
    });
}

fn mark_completed(job: &mut JobSpec, media_id: &str, proxy_id: &str) {
    let checkpoint = job
        .checkpoint
        .as_mut()
        .expect("validated JobSpec checkpoint");
    checkpoint.checkpoint_version = Some(CHECKPOINT_VERSION);
    let completed = checkpoint.completed_input_ids.get_or_insert_with(Vec::new);
    if !completed.iter().any(|id| id == media_id) {
        completed.push(media_id.to_owned());
    }
    let outputs = checkpoint.partial_output_ids.get_or_insert_with(Vec::new);
    if !outputs.iter().any(|id| id == proxy_id) {
        outputs.push(proxy_id.to_owned());
    }
    checkpoint.cursor = Some(
        serde_json::json!({"version": CHECKPOINT_VERSION, "media_id": media_id, "done": true})
            .to_string(),
    );
    checkpoint.updated_at = Some(Utc::now().to_rfc3339());
}

fn update_running_checkpoint(job: &mut JobSpec, media_id: &str, frame: i64) {
    let checkpoint = job
        .checkpoint
        .as_mut()
        .expect("validated JobSpec checkpoint");
    checkpoint.checkpoint_version = Some(CHECKPOINT_VERSION);
    checkpoint.cursor = Some(
        serde_json::json!({
            "version": CHECKPOINT_VERSION,
            "media_id": media_id,
            "frame": frame,
            "pass": 1
        })
        .to_string(),
    );
    checkpoint.updated_at = Some(Utc::now().to_rfc3339());
    job.state.progress = Some(Progress {
        units_done: frame as f64,
        units_total: None,
        unit: ProgressUnit::Frames,
        bytes_processed: None,
        message: Some("hardware proxy pass in progress".to_owned()),
    });
    job.state.heartbeat_at = Some(Utc::now().to_rfc3339());
}

fn record_failure(job: &mut JobSpec, media_id: &str, error: &VideoProxyError) {
    let now = Utc::now().to_rfc3339();
    let code = match error {
        VideoProxyError::MissingSource | VideoProxyError::MissingRecord => {
            JobErrorCode::FileNotFound
        }
        VideoProxyError::FfmpegUnavailable
        | VideoProxyError::ProbeUnavailable
        | VideoProxyError::UnsupportedBackend => JobErrorCode::GpuUnavailable,
        VideoProxyError::FfmpegFailed => JobErrorCode::UnsupportedCodec,
        VideoProxyError::ProbeFailed | VideoProxyError::UnsupportedRotation => {
            JobErrorCode::UnsupportedFormat
        }
        VideoProxyError::SpanAssemblyInvalid => JobErrorCode::DependencyFailed,
        _ => JobErrorCode::InternalError,
    };
    let message = match error {
        VideoProxyError::SpanAssemblyInvalid => {
            "video proxy completed but span assembly refresh failed; details redacted"
        }
        VideoProxyError::OrientationMismatch => {
            "the 480p proxy raster contradicts the source's oriented geometry; \
             nothing was written because every coordinate measured on it would be \
             a quarter turn out"
        }
        _ => "hardware video proxy failed; source details redacted",
    };
    job.error = Some(JobError {
        code,
        message: message.to_owned(),
        retryable: matches!(error, VideoProxyError::FfmpegFailed),
        attempt: Some(job.state.attempts),
        occurred_at: Some(now.clone()),
        failed_input_id: Some(media_id.to_owned()),
    });
    job.state.status = JobStateStatus::Failed;
    job.state.heartbeat_at = Some(now);
}

fn hardware_bitrate(crf: i32) -> i64 {
    let scale = 2_f64.powf(f64::from(23 - crf) / 6.0);
    (1_500_000.0 * scale).round().clamp(400_000.0, 6_000_000.0) as i64
}

#[cfg(test)]
mod probe_tests {
    use super::*;

    /// Real `ffprobe -show_streams -show_format -of json` output, FFmpeg 7.0,
    /// on a 1920x1080 29.97 clip with one mono AAC track. `disposition` blocks
    /// are the only thing removed; every value below is what ffprobe printed,
    /// including the integers it chooses to emit as strings.
    const NTSC_PROBE: &str = r#"{
      "streams": [
        {"index": 0, "codec_name": "h264", "profile": "High", "codec_type": "video",
         "width": 1920, "height": 1080, "coded_width": 1920, "coded_height": 1080,
         "sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9", "pix_fmt": "yuv420p",
         "r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001", "time_base": "1/30000",
         "start_pts": 0, "start_time": "0.000000", "duration_ts": 59059, "duration": "1.968633",
         "bit_rate": "6236314", "nb_frames": "59",
         "tags": {"language": "und", "handler_name": "VideoHandler"}},
        {"index": 1, "codec_name": "aac", "profile": "LC", "codec_type": "audio",
         "sample_fmt": "fltp", "sample_rate": "48000", "channels": 1, "channel_layout": "mono",
         "r_frame_rate": "0/0", "avg_frame_rate": "0/0", "time_base": "1/48000",
         "duration_ts": 96256, "duration": "2.005333", "bit_rate": "69577",
         "tags": {"language": "und", "handler_name": "SoundHandler"}}
      ],
      "format": {"filename": "ntsc.mp4", "nb_streams": 2, "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                 "duration": "2.005333", "size": "1552686", "bit_rate": "6194094"}
    }"#;

    /// Real output for a clip stored 1920x1080 carrying a display matrix of
    /// -90 degrees -- what a portrait phone video looks like. The displaymatrix
    /// string is ffprobe's own, newlines and all.
    const ROTATED_PROBE: &str = r#"{
      "streams": [
        {"index": 0, "codec_name": "h264", "codec_type": "video",
         "width": 1920, "height": 1080, "sample_aspect_ratio": "1:1",
         "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "time_base": "1/15360",
         "duration_ts": 46080, "duration": "3.000000", "bit_rate": "6369344", "nb_frames": "90",
         "tags": {"language": "und"},
         "side_data_list": [{"side_data_type": "Display Matrix",
           "displaymatrix": "\n00000000:            0       65536           0\n00000001:       -65536           0           0\n00000002:            0           0  1073741824\n",
           "rotation": -90}]}
      ],
      "format": {"duration": "3.000000", "bit_rate": "6374389"}
    }"#;

    fn ntsc() -> SourceProbe {
        parse_probe(NTSC_PROBE).expect("a real ffprobe document")
    }

    #[test]
    fn reads_the_fields_ffprobe_actually_prints() {
        let probe = ntsc();
        assert_eq!(
            probe.stored_size,
            PixelSize {
                width: 1920,
                height: 1080
            }
        );
        assert_eq!(probe.rotation_deg, 0);
        // Exactly, as a rational. 29.97 is not a frame rate.
        assert_eq!(probe.frame_rate, (30_000, 1001));
        assert_eq!(probe.duration, Some((59_059, 1, 30_000)));
        // ffprobe emits bit_rate as a string and width as a number. This is the
        // VIDEO stream's 6236314, not the container's 6194094 one field away.
        assert_eq!(probe.bit_rate, Some(6_236_314));
        // A stream that declares none has not been measured, and the
        // container's total is a different quantity, not a substitute for it.
        let mut without = serde_json::from_str::<Value>(NTSC_PROBE).expect("json");
        without["streams"][0]
            .as_object_mut()
            .expect("the video stream")
            .remove("bit_rate");
        assert_eq!(
            parse_probe(&without.to_string())
                .expect("still a probe")
                .bit_rate,
            None
        );
        assert_eq!(probe.video_codec.as_deref(), Some("h264"));
        // The stream declares neither, so neither is claimed.
        assert_eq!(probe.color_primaries, None);
        assert_eq!(probe.transfer_characteristics, None);
    }

    #[test]
    fn audio_streams_carry_the_container_index_and_no_silence_claim() {
        let probe = ntsc();
        assert_eq!(probe.audio_streams.len(), 1);
        let audio = &probe.audio_streams[0];
        // 1, not 0: this is the index that selects the track, and the video is 0.
        assert_eq!(audio.stream_index, 1);
        assert_eq!(audio.channels, 1);
        assert_eq!(audio.sample_rate, 48_000);
        assert_eq!(audio.codec.as_deref(), Some("aac"));
        // `und` is the absence of a language, not a language.
        assert_eq!(audio.language, None);
        // Nothing measured silence, so nothing says it is not silent.
        assert_eq!(audio.is_silent, None);
    }

    #[test]
    fn a_rotated_source_reports_the_size_after_rotation() {
        let probe = parse_probe(ROTATED_PROBE).expect("a real ffprobe document");
        // The raster as stored is landscape...
        assert_eq!(
            probe.stored_size,
            PixelSize {
                width: 1920,
                height: 1080
            }
        );
        // ...and the picture is portrait. THIS is what every normalised
        // coordinate in the system is relative to.
        assert_eq!(
            probe.oriented_size(),
            PixelSize {
                width: 1080,
                height: 1920
            }
        );
        // ffprobe said -90; the stored number is the clockwise turn.
        assert_eq!(probe.rotation_deg, 90);
    }

    #[test]
    fn rotation_is_the_clockwise_turn_and_a_partial_turn_is_refused() {
        // ffprobe's convention is counter-clockwise, so the signs invert.
        assert_eq!(display_rotation_degrees(-90.0), Some(90));
        assert_eq!(display_rotation_degrees(90.0), Some(270));
        assert_eq!(display_rotation_degrees(180.0), Some(180));
        assert_eq!(display_rotation_degrees(-180.0), Some(180));
        assert_eq!(display_rotation_degrees(-270.0), Some(270));
        assert_eq!(display_rotation_degrees(0.0), Some(0));
        assert_eq!(display_rotation_degrees(-360.0), Some(0));
        // A display matrix can hold any angle. Rounding one to a quarter turn
        // would tilt the whole picture with nothing to show for it.
        assert_eq!(display_rotation_degrees(45.0), None);
        assert_eq!(display_rotation_degrees(f64::NAN), None);
    }

    #[test]
    fn a_document_without_a_video_stream_is_a_failure_not_a_default() {
        assert!(matches!(
            parse_probe(r#"{"streams": [{"index": 0, "codec_type": "audio"}]}"#),
            Err(VideoProxyError::ProbeFailed)
        ));
        assert!(matches!(
            parse_probe("not json"),
            Err(VideoProxyError::ProbeFailed)
        ));
        // No frame rate is not "assume 30".
        assert!(matches!(
            parse_probe(r#"{"streams": [{"codec_type": "video", "width": 640, "height": 480}]}"#),
            Err(VideoProxyError::ProbeFailed)
        ));
        assert!(matches!(
            parse_probe(
                r#"{"streams": [{"codec_type": "video", "width": 640, "height": 480,
                    "r_frame_rate": "30/1",
                    "side_data_list": [{"rotation": -33}]}]}"#
            ),
            Err(VideoProxyError::UnsupportedRotation)
        ));
    }

    #[test]
    fn ffprobe_is_taken_from_beside_the_ffmpeg_the_job_was_given() {
        assert_eq!(
            ffprobe_beside(Path::new("/opt/homebrew/bin/ffmpeg")),
            PathBuf::from("/opt/homebrew/bin/ffprobe")
        );
        assert_eq!(
            ffprobe_beside(Path::new("C:/tools/ffmpeg.exe")),
            PathBuf::from("C:/tools/ffprobe.exe")
        );
        // A bare name stays a bare name, so PATH resolves it.
        assert_eq!(
            ffprobe_beside(Path::new("ffmpeg")),
            PathBuf::from("ffprobe")
        );
        // Something that is not FFmpeg gets the default rather than a mangled
        // sibling that does not exist.
        assert_eq!(
            ffprobe_beside(Path::new("/usr/local/bin/transcoder")),
            PathBuf::from("ffprobe")
        );
    }
}

#[cfg(test)]
mod geometry_tests {
    use super::*;

    fn probe(width: i64, height: i64, rotation_deg: i64) -> SourceProbe {
        SourceProbe {
            stored_size: PixelSize { width, height },
            rotation_deg,
            frame_rate: (30_000, 1001),
            duration: Some((59_059, 1, 30_000)),
            video_codec: Some("h264".to_owned()),
            bit_rate: None,
            color_primaries: None,
            transfer_characteristics: None,
            audio_streams: Vec::new(),
        }
    }

    fn stats(entry_count: i64, variable: bool) -> FrameIndexStats {
        FrameIndexStats {
            entry_count,
            variable_delta: variable,
            ..FrameIndexStats::default()
        }
    }

    #[test]
    fn the_proxy_raster_has_to_describe_the_same_picture_as_the_source() {
        let portrait = PixelSize {
            width: 1080,
            height: 1920,
        };
        // The scaler pins the height and rounds the width to an even number.
        assert!(raster_matches_oriented(
            &portrait,
            &PixelSize {
                width: 270,
                height: 480
            }
        ));
        assert!(raster_matches_oriented(
            &portrait,
            &PixelSize {
                width: 272,
                height: 480
            }
        ));
        // A quarter turn is not rounding. This is the whole point: a proxy
        // built without the orientation filter lands here.
        assert!(!raster_matches_oriented(
            &portrait,
            &PixelSize {
                width: 854,
                height: 480
            }
        ));
        // And a landscape source with a landscape proxy still agrees.
        assert!(raster_matches_oriented(
            &PixelSize {
                width: 1920,
                height: 1080
            },
            &PixelSize {
                width: 854,
                height: 480
            }
        ));
    }

    #[test]
    fn a_constant_rate_duration_is_the_frames_this_decode_counted() {
        let (duration, exact) = source_duration(&probe(1920, 1080, 0), 59, false);
        assert!(exact);
        assert_eq!(duration.value, 59.0);
        assert!((duration.rate - 30_000.0 / 1001.0).abs() < 1e-12);
    }

    #[test]
    fn a_variable_rate_duration_comes_from_the_container_not_a_frame_count() {
        // 90 frames whose nominal rate would claim 3.0s; the container says
        // 59059/30000 = 1.968633s, and that is the one that is true.
        let (duration, exact) = source_duration(&probe(1920, 1080, 0), 90, true);
        assert!(exact);
        assert_eq!(duration.value, 59_059.0);
        assert_eq!(duration.rate, 30_000.0);

        let mut without = probe(1920, 1080, 0);
        without.duration = None;
        let (fallback, exact) = source_duration(&without, 90, true);
        // Nothing exact is available, so the weaker number is used AND said.
        assert!(!exact);
        assert_eq!(fallback.value, 90.0);
    }

    #[test]
    fn variable_frame_rate_is_the_decodes_answer_and_travels_into_the_record() {
        let (constant, issue) = video_properties(&probe(1920, 1080, 0), &stats(59, false));
        assert_eq!(constant.is_variable_frame_rate, Some(false));
        assert_eq!(issue, None);
        assert_eq!(
            constant.frame_rate,
            VideoPropertiesFrameRate {
                numerator: 30_000,
                denominator: 1001
            }
        );
        assert_eq!(constant.rotation_deg, Some(0));
        assert_eq!(constant.start_timecode, None);

        let (variable, _) = video_properties(&probe(1920, 1080, 0), &stats(90, true));
        assert_eq!(variable.is_variable_frame_rate, Some(true));

        let mut no_container_duration = probe(1920, 1080, 0);
        no_container_duration.duration = None;
        let (_, issue) = video_properties(&no_container_duration, &stats(90, true));
        assert!(issue.expect("a stated weakness").contains("frame COUNT"));
    }

    #[test]
    fn a_rotated_source_records_the_rotated_size() {
        let (video, _) = video_properties(&probe(1920, 1080, 270), &stats(90, false));
        assert_eq!(
            video.stored_size,
            Some(PixelSize {
                width: 1920,
                height: 1080
            })
        );
        assert_eq!(
            video.oriented_size,
            PixelSize {
                width: 1080,
                height: 1920
            }
        );
        assert_eq!(video.rotation_deg, Some(270));
    }

    #[test]
    fn a_rotated_source_gets_an_orientation_filter_and_loses_the_hardware_scaler() {
        for backend in [
            HardwareBackend::VideoToolbox,
            HardwareBackend::Nvdec,
            HardwareBackend::Qsv,
        ] {
            let upright = proxy_command_args(backend, 480, 26, 0);
            let upright_filter = filter_of(&upright);
            // The unrotated path is untouched: it still scales on the hardware,
            // and the proxy id is the hash of what it produces.
            assert!(upright_filter.starts_with(match backend {
                HardwareBackend::VideoToolbox => "scale_vt",
                HardwareBackend::Nvdec => "scale_cuda",
                HardwareBackend::Qsv => "scale_qsv",
            }));
            assert!(!upright_filter.contains("transpose"));

            for (rotation, expected) in [(90, "transpose=1"), (270, "transpose=2")] {
                let rotated = proxy_command_args(backend, 480, 26, rotation);
                let filter = filter_of(&rotated);
                assert!(filter.contains(expected), "{rotation}: {filter}");
                // Download first, or the hardware scaler resolves iw/ih against
                // the raster before it has been turned.
                assert!(filter.starts_with("hwdownload,format=nv12,"), "{filter}");
                assert!(filter.ends_with(",showinfo"), "{filter}");
                assert!(
                    filter.find("transpose").unwrap() < filter.find("scale=").unwrap(),
                    "the turn must precede the scale: {filter}"
                );
            }
            let upside_down = filter_of(&proxy_command_args(backend, 480, 26, 180));
            assert!(upside_down.contains("hflip,vflip"), "{upside_down}");
            assert!(!upside_down.contains("transpose"), "{upside_down}");
        }
    }

    fn filter_of(args: &ProxyCommandArgs) -> String {
        let position = args
            .after_input
            .iter()
            .position(|argument| argument == "-vf")
            .expect("a filter chain");
        args.after_input[position + 1].clone()
    }
}

/// Tests that drive real FFmpeg over real files.
///
/// Everything above measures the arithmetic. These measure the thing the
/// arithmetic is about, which is the only way to find out that FFmpeg's
/// autorotate does not run behind `-hwaccel` -- no unit test of ours could have
/// told us that, because the assumption was in FFmpeg, not in this file.
///
/// They announce a skip loudly when the tools are absent rather than passing
/// quietly: a check that did not run must never read like one that did.
#[cfg(test)]
mod real_media_tests {
    use super::*;

    fn ffmpeg() -> Option<PathBuf> {
        let path = std::env::var_os("MEMORY_ENGINE_FFMPEG")
            .map_or_else(|| PathBuf::from("ffmpeg"), PathBuf::from);
        let usable = Command::new(&path)
            .arg("-version")
            .output()
            .is_ok_and(|output| output.status.success());
        usable.then_some(path)
    }

    fn encode(ffmpeg_path: &Path, arguments: &[&str]) {
        let output = Command::new(ffmpeg_path)
            .args(["-y", "-hide_banner", "-loglevel", "error", "-nostdin"])
            .args(arguments)
            .output()
            .expect("ffmpeg runs");
        assert!(
            output.status.success(),
            "ffmpeg failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    /// A 1920x1080 clip carrying a -90 display matrix: what a phone writes when
    /// it is held upright. FFmpeg only sets the matrix on an input, so this is
    /// a two-step -- encode, then remux with the rotation applied.
    ///
    /// `marked` paints the LEFT HALF of the stored raster red. A clockwise
    /// quarter turn puts the left column along the top, so the picture's top
    /// half is red and its bottom half is not — which is what makes the
    /// DIRECTION of the turn measurable rather than merely its axis.
    fn rotated_clip(ffmpeg_path: &Path, directory: &Path) -> PathBuf {
        rotated_clip_inner(ffmpeg_path, directory, false, "-90")
    }

    fn marked_rotated_clip(ffmpeg_path: &Path, directory: &Path) -> PathBuf {
        rotated_clip_inner(ffmpeg_path, directory, true, "-90")
    }

    /// The same marked fixture under any display matrix.
    ///
    /// `display_rotation` is FFmpeg's own input option, whose sign is
    /// COUNTER-clockwise — the same convention `display_rotation_degrees`
    /// reads back off the container and negates.
    fn marked_clip_rotated_by(
        ffmpeg_path: &Path,
        directory: &Path,
        display_rotation: &str,
    ) -> PathBuf {
        rotated_clip_inner(ffmpeg_path, directory, true, display_rotation)
    }

    fn rotated_clip_inner(
        ffmpeg_path: &Path,
        directory: &Path,
        marked: bool,
        display_rotation: &str,
    ) -> PathBuf {
        let upright = directory.join(if marked { "marked.mp4" } else { "upright.mp4" });
        let mut arguments = vec![
            "-f",
            "lavfi",
            "-i",
            if marked {
                "color=c=black:s=1920x1080:r=30:d=1"
            } else {
                "testsrc2=size=1920x1080:rate=30:duration=1"
            },
        ];
        if marked {
            arguments.extend(["-vf", "drawbox=x=0:y=0:w=960:h=1080:c=red:t=fill"]);
        }
        arguments.extend([
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            upright.to_str().expect("path"),
        ]);
        encode(ffmpeg_path, &arguments);
        let rotated = directory.join(if marked {
            format!("marked-rotated{display_rotation}.mp4")
        } else {
            format!("rotated{display_rotation}.mp4")
        });
        encode(
            ffmpeg_path,
            &[
                "-display_rotation:v:0",
                display_rotation,
                "-i",
                upright.to_str().expect("path"),
                "-c",
                "copy",
                rotated.to_str().expect("path"),
            ],
        );
        rotated
    }

    /// The mean colour of one patch of the first frame, as RGB.
    ///
    /// A patch rather than a half, and cropped rather than scaled: swscale
    /// smears across a downscale of several hundred to one, so "average the
    /// left half" and "average the right half" come back as two muddy numbers
    /// that both contain the boundary. Cropping well inside a region and
    /// averaging that is the same question asked in a way the scaler answers.
    ///
    /// `region` is `(x, y, w, h)` as fractions of the frame.
    fn patch_rgb(
        ffmpeg_path: &Path,
        clip: &Path,
        region: (f64, f64, f64, f64),
        autorotate: bool,
    ) -> [u8; 3] {
        let (x, y, w, h) = region;
        let filter =
            format!("crop=w=iw*{w}:h=ih*{h}:x=iw*{x}:y=ih*{y},scale=1:1:flags=area,format=rgb24");
        let mut command = Command::new(ffmpeg_path);
        command.args(["-v", "error", "-nostdin"]);
        if !autorotate {
            command.arg("-noautorotate");
        }
        let output = command
            .arg("-i")
            .arg(clip)
            .args(["-frames:v", "1", "-vf", &filter, "-f", "rawvideo", "-"])
            .output()
            .expect("ffmpeg runs");
        assert!(
            output.status.success(),
            "ffmpeg failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.stdout.len(), 3, "one rgb24 pixel");
        [output.stdout[0], output.stdout[1], output.stdout[2]]
    }

    /// True where the patch is the fixture's red rather than its black.
    fn is_red(pixel: [u8; 3]) -> bool {
        pixel[0] > 120 && pixel[1] < 90 && pixel[2] < 90
    }

    /// Frames on two different presentation intervals in one file. Not a
    /// container that merely *declares* a variable rate: the timestamps really
    /// do step by 1000 and then by 2000.
    fn variable_rate_clip(ffmpeg_path: &Path, directory: &Path) -> PathBuf {
        let path = directory.join("variable.mp4");
        encode(
            ffmpeg_path,
            &[
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x480:rate=30:duration=2",
                "-vf",
                "setpts='if(lt(N,30), N/30/TB, (30+(N-30)*2)/30/TB)'",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-video_track_timescale",
                "30000",
                path.to_str().expect("path"),
            ],
        );
        path
    }

    #[test]
    fn a_real_rotated_file_measures_as_the_picture_and_not_as_the_raster() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!(
                "SKIPPED a_real_rotated_file_measures_as_the_picture_and_not_as_the_raster: \
                 no runnable ffmpeg. This check did NOT run."
            );
            return;
        };
        let directory = tempfile::tempdir().expect("tempdir");
        let clip = rotated_clip(&ffmpeg_path, directory.path());

        let probe =
            probe_source(&ffprobe_path(&ffmpeg_path), &clip).expect("a real file probes cleanly");

        assert_eq!(
            probe.stored_size,
            PixelSize {
                width: 1920,
                height: 1080
            }
        );
        assert_eq!(probe.rotation_deg, 90);
        assert_eq!(
            probe.oriented_size(),
            PixelSize {
                width: 1080,
                height: 1920
            },
            "oriented_size must be the size AFTER the display matrix is applied"
        );
    }

    fn stage(output: &Path, media_id: &str, source: &Path) {
        let mut record: MediaRecord = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ))
        .expect("a golden video record");
        record.media_id = media_id.to_owned();
        record.span = None;
        record.video = None;
        record.proxies = None;
        record.sources.truncate(1);
        record.sources[0].path = source.to_string_lossy().into_owned();
        record.sources[0].present = true;
        fs::create_dir_all(record_path(output, media_id).parent().expect("parent")).expect("mkdir");
        persist_record(output, &record).expect("record written");
    }

    fn proxy_job(media_id: &str) -> JobSpec {
        let mut job: JobSpec = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/job-spec/valid/job-video-proxy-resumed.json"
        ))
        .expect("a golden JobSpec");
        job.inputs.media_ids = Some(vec![media_id.to_owned()]);
        job.params.as_mut().expect("params").insert(
            "hardware_decode".to_owned(),
            serde_json::Value::String(
                if cfg!(target_os = "macos") {
                    "videotoolbox"
                } else {
                    "qsv"
                }
                .to_owned(),
            ),
        );
        job.state.status = JobStateStatus::Pending;
        job.state.finished_at = None;
        job.outputs = None;
        job.error = None;
        let checkpoint = job.checkpoint.as_mut().expect("checkpoint");
        checkpoint.completed_input_ids = Some(Vec::new());
        checkpoint.partial_output_ids = Some(Vec::new());
        checkpoint.cursor = None;
        job
    }

    /// Runs the whole executor. `None` when this host has no hardware backend
    /// the worker will accept, which is every platform but the one the backend
    /// belongs to.
    fn run_proxy(source: &Path, output: &Path) -> Option<MediaRecord> {
        let ffmpeg_path = ffmpeg()?;
        let media_id = "aa11".to_owned() + &"0".repeat(60);
        stage(output, &media_id, source);
        let mut job = proxy_job(&media_id);
        let store = CheckpointStore::new(output.join("checkpoint.json"));
        match execute_video_proxy(&mut job, output, &store, &ffmpeg_path) {
            Ok(_) => Some(load_record(output, &media_id).expect("the rewritten record")),
            Err(VideoProxyError::UnsupportedBackend | VideoProxyError::FfmpegUnavailable) => None,
            Err(error) => panic!("the proxy pass failed: {error}"),
        }
    }

    #[test]
    fn a_real_rotated_file_produces_an_upright_proxy_and_an_upright_record() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!("SKIPPED a_real_rotated_file_produces_an_upright_proxy: no ffmpeg.");
            return;
        };
        let directory = tempfile::tempdir().expect("tempdir");
        let clip = rotated_clip(&ffmpeg_path, directory.path());
        let Some(record) = run_proxy(&clip, directory.path()) else {
            eprintln!(
                "SKIPPED a_real_rotated_file_produces_an_upright_proxy: this host has no \
                 hardware backend the worker accepts. This check did NOT run."
            );
            return;
        };

        let video = record.video.expect("MediaRecord.video is populated");
        assert_eq!(
            video.oriented_size,
            PixelSize {
                width: 1080,
                height: 1920
            }
        );
        assert_eq!(video.rotation_deg, Some(90));
        let proxy = record.proxies.expect("a proxy")[0].clone();
        let raster = proxy.size.expect("a measured proxy raster");
        assert!(
            raster.height > raster.width,
            "the proxy of a portrait source must be portrait, not {}x{}",
            raster.width,
            raster.height
        );
        assert!(raster_matches_oriented(&video.oriented_size, &raster));
    }

    /// The turn goes the RIGHT WAY, measured in pixels.
    ///
    /// Every other check in this file compares one of our numbers against
    /// another of our numbers, so all of them survive a coherently INVERTED
    /// convention — one where `display_rotation_degrees` does not negate,
    /// `orientation_filters` maps the other way, and the expectations were
    /// written from the same wrong belief. `oriented_size` is 1080x1920 either
    /// way; the proxy raster is 270x480 either way; `raster_matches_oriented`
    /// compares an aspect ratio, which a half turn does not change. Measured:
    /// under exactly that mutation, 61 of the 62 tests here still pass and this
    /// is the one that does not.
    ///
    /// So this one asks the pixels rather than the arithmetic. The source is
    /// red down its LEFT HALF; a clockwise quarter turn — which is what a
    /// display matrix of -90 means, and what FFmpeg's own autorotate does with
    /// one — lays that column along the TOP. Red on the bottom is the inverted
    /// convention, and a picture that is upside down in every reel and every
    /// face box the system will ever compute from it.
    #[test]
    fn the_turn_goes_clockwise_and_the_pixels_say_so() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!("SKIPPED the_turn_goes_clockwise_and_the_pixels_say_so: no ffmpeg.");
            return;
        };
        let directory = tempfile::tempdir().expect("tempdir");
        let clip = marked_rotated_clip(&ffmpeg_path, directory.path());

        // The premise, asserted rather than assumed: red down the LEFT of the
        // raster as stored, black down the right. Read with the display matrix
        // ignored, which is what "as stored" means.
        let left = patch_rgb(&ffmpeg_path, &clip, (0.05, 0.4, 0.3, 0.2), false);
        let right = patch_rgb(&ffmpeg_path, &clip, (0.65, 0.4, 0.3, 0.2), false);
        assert!(
            is_red(left) && !is_red(right),
            "the fixture must be red on the left of the STORED raster, \
             got left={left:?} right={right:?}"
        );

        let Some(record) = run_proxy(&clip, directory.path()) else {
            eprintln!(
                "SKIPPED the_turn_goes_clockwise_and_the_pixels_say_so: this host has no \
                 hardware backend the worker accepts. This check did NOT run."
            );
            return;
        };
        let proxy = record.proxies.expect("a proxy")[0].clone();
        let proxy_path = PathBuf::from(&proxy.path);
        let top = patch_rgb(&ffmpeg_path, &proxy_path, (0.4, 0.05, 0.2, 0.3), true);
        let bottom = patch_rgb(&ffmpeg_path, &proxy_path, (0.4, 0.65, 0.2, 0.3), true);

        assert!(
            is_red(top) && !is_red(bottom),
            "a -90 display matrix is a CLOCKWISE quarter turn, so the stored left half \
             belongs along the top of the proxy. Got top={top:?} bottom={bottom:?}; red \
             on the bottom means the sign convention is inverted and the picture is \
             upside down while every other check still passes."
        );
    }

    /// The other two quarter turns, also measured in pixels.
    ///
    /// `the_turn_goes_clockwise_and_the_pixels_say_so` anchors ONE of the three
    /// branches of `orientation_filters` to the picture. The other two were
    /// asserted only as strings — `contains("hflip,vflip")`,
    /// `contains("transpose=2")` — and a string test cannot tell a correct
    /// mapping from a confident wrong one, because the expectation is written
    /// from the same belief as the code. Neither can anything downstream: a
    /// half turn does not change the aspect ratio, so `raster_matches_oriented`
    /// passes on an upside-down proxy, and a 270 source that took the 90 filter
    /// still lands on a 270x480 raster.
    ///
    /// The fixture is red down the LEFT HALF of the stored raster, so each turn
    /// puts it somewhere different and only one place is right:
    ///
    ///   180 (`hflip,vflip`)       left half -> RIGHT half, raster stays wide
    ///   270 (`transpose=2`, ccw)  left half -> BOTTOM half, raster goes tall
    ///
    /// Both were checked against FFmpeg's own software autorotate of the same
    /// file before being written down here, so these are the reference
    /// behaviour rather than this worker's opinion of it.
    #[test]
    fn the_half_turn_and_the_counter_clockwise_turn_land_where_the_pixels_say() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!(
                "SKIPPED the_half_turn_and_the_counter_clockwise_turn_land_where_the_pixels_say: \
                 no runnable ffmpeg. This check did NOT run."
            );
            return;
        };

        // `-display_rotation` is counter-clockwise, so 180 reads back as a half
        // turn and +90 reads back as the 270 clockwise turn.
        for (display_rotation, expected_deg) in [("180", 180_i64), ("90", 270)] {
            let directory = tempfile::tempdir().expect("tempdir");
            let clip = marked_clip_rotated_by(&ffmpeg_path, directory.path(), display_rotation);

            // The premise, asserted rather than assumed.
            let left = patch_rgb(&ffmpeg_path, &clip, (0.05, 0.4, 0.3, 0.2), false);
            let right = patch_rgb(&ffmpeg_path, &clip, (0.65, 0.4, 0.3, 0.2), false);
            assert!(
                is_red(left) && !is_red(right),
                "the fixture must be red on the left of the STORED raster, \
                 got left={left:?} right={right:?}"
            );

            let Some(record) = run_proxy(&clip, directory.path()) else {
                eprintln!(
                    "SKIPPED the_half_turn_and_the_counter_clockwise_turn_land_where_the_pixels_\
                     say: this host has no hardware backend the worker accepts. This check did \
                     NOT run."
                );
                return;
            };
            let video = record.video.expect("MediaRecord.video is populated");
            assert_eq!(
                video.rotation_deg,
                Some(expected_deg),
                "-display_rotation {display_rotation} is a {expected_deg} degree clockwise turn"
            );
            let proxy = record.proxies.expect("a proxy")[0].clone();
            let proxy_path = PathBuf::from(&proxy.path);

            // Where the red half has to be after this particular turn. Read
            // WITH autorotate, so a proxy that wrongly kept a display matrix
            // of its own would be turned again here and land somewhere else.
            let (red_region, plain_region) = if expected_deg == 180 {
                ((0.65, 0.4, 0.3, 0.2), (0.05, 0.4, 0.3, 0.2))
            } else {
                ((0.4, 0.65, 0.2, 0.3), (0.4, 0.05, 0.2, 0.3))
            };
            let red = patch_rgb(&ffmpeg_path, &proxy_path, red_region, true);
            let plain = patch_rgb(&ffmpeg_path, &proxy_path, plain_region, true);
            assert!(
                is_red(red) && !is_red(plain),
                "a {expected_deg} degree turn puts the stored left half at {red_region:?} of \
                 the proxy, not at {plain_region:?}. Got {red:?} and {plain:?}; this is a \
                 picture that is turned the wrong way in every reel and every face box \
                 computed from it, and every other check in this file still passes."
            );

            let raster = proxy.size.expect("a measured proxy raster");
            let tall = raster.height > raster.width;
            assert_eq!(
                tall,
                expected_deg == 270,
                "a {expected_deg} degree turn of a 1920x1080 source: got {}x{}",
                raster.width,
                raster.height
            );
        }
    }

    /// The turn must be applied exactly once, and the proxy must not carry the
    /// instruction to apply it again.
    ///
    /// A proxy that is upright AND declares `rotation: -90` decodes a quarter
    /// turn out in everything that opens it, and it looks correct in every
    /// number this worker records: the raster is 270x480 either way, so
    /// `raster_matches_oriented` cannot see it. The only way to tell is to ask
    /// the file. `-noautorotate` produces exactly that file — see
    /// `orientation_filters`.
    #[test]
    fn a_rotated_proxy_carries_no_display_matrix_of_its_own() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!("SKIPPED a_rotated_proxy_carries_no_display_matrix_of_its_own: no ffmpeg.");
            return;
        };
        let directory = tempfile::tempdir().expect("tempdir");
        let clip = rotated_clip(&ffmpeg_path, directory.path());
        let Some(record) = run_proxy(&clip, directory.path()) else {
            eprintln!(
                "SKIPPED a_rotated_proxy_carries_no_display_matrix_of_its_own: this host has \
                 no hardware backend the worker accepts. This check did NOT run."
            );
            return;
        };

        let proxy = record.proxies.expect("a proxy")[0].clone();
        let probed = probe_source(&ffprobe_path(&ffmpeg_path), Path::new(&proxy.path))
            .expect("the proxy probes cleanly");
        assert_eq!(
            probed.rotation_deg, 0,
            "the proxy pixels are already turned; a display matrix on top of them turns \
             the picture a second time in every consumer"
        );
        // And the pixels really are the turned ones, not the raster.
        assert!(probed.stored_size.height > probed.stored_size.width);
    }

    #[test]
    fn a_real_variable_rate_file_is_recorded_as_variable() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!("SKIPPED a_real_variable_rate_file_is_recorded_as_variable: no ffmpeg.");
            return;
        };
        let directory = tempfile::tempdir().expect("tempdir");
        let clip = variable_rate_clip(&ffmpeg_path, directory.path());
        let Some(record) = run_proxy(&clip, directory.path()) else {
            eprintln!(
                "SKIPPED a_real_variable_rate_file_is_recorded_as_variable: this host has no \
                 hardware backend the worker accepts. This check did NOT run."
            );
            return;
        };

        let video = record.video.expect("MediaRecord.video is populated");
        assert_eq!(
            video.is_variable_frame_rate,
            Some(true),
            "workers/video-analysis refuses a VFR grid by name, and it can only do that \
             if this says so"
        );
        // The frame index has to agree with the record: same file, same answer.
        let proxy = record.proxies.expect("a proxy")[0].clone();
        assert_eq!(
            proxy.frame_index.expect("a sidecar").mapping,
            FrameIndexSidecarMapping::Table
        );
    }

    #[test]
    fn a_constant_rate_file_is_not_recorded_as_variable() {
        let Some(ffmpeg_path) = ffmpeg() else {
            eprintln!("SKIPPED a_constant_rate_file_is_not_recorded_as_variable: no ffmpeg.");
            return;
        };
        let directory = tempfile::tempdir().expect("tempdir");
        let clip = directory.path().join("constant.mp4");
        encode(
            &ffmpeg_path,
            &[
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=30000/1001:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                clip.to_str().expect("path"),
            ],
        );
        let Some(record) = run_proxy(&clip, directory.path()) else {
            eprintln!(
                "SKIPPED a_constant_rate_file_is_not_recorded_as_variable: this host has no \
                 hardware backend the worker accepts. This check did NOT run."
            );
            return;
        };

        let video = record.video.expect("MediaRecord.video is populated");
        assert_eq!(video.is_variable_frame_rate, Some(false));
        assert_eq!(video.rotation_deg, Some(0));
        assert_eq!(
            video.frame_rate,
            VideoPropertiesFrameRate {
                numerator: 30_000,
                denominator: 1001
            },
            "30000/1001 has to survive as a rational"
        );
        assert_eq!(
            video.oriented_size,
            PixelSize {
                width: 1280,
                height: 720
            }
        );
        // Duration is the frames this decode counted, at that exact rate.
        assert_eq!(video.duration.value, 30.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_showinfo_frame_and_rate() {
        let config = "[Parsed_showinfo_3] config in time_base: 1/30000, frame_rate: 30000/1001";
        assert!((parse_frame_rate(config).unwrap() - 29.970_029_97).abs() < 0.0001);
        assert_eq!(parse_time_base(config), Some((1, 30_000)));
        let line =
            "[Parsed_showinfo_3] n:  59 pts: 59059 pts_time:1.968633 duration: 1001 s:854x480";
        let frame = parse_showinfo(line).expect("showinfo frame");
        assert_eq!(frame.proxy_frame, 59);
        assert_eq!(frame.source_pts, 59_059);
        assert_eq!(
            parse_size(line),
            Some(PixelSize {
                width: 854,
                height: 480
            })
        );
    }

    #[test]
    fn detects_variable_frame_timing() {
        let mut stats = FrameIndexStats::default();
        for (frame, pts) in [0, 1001, 2002, 4004].into_iter().enumerate() {
            update_stats(
                &mut stats,
                &SidecarEntry {
                    proxy_frame: frame as i64,
                    source_pts: pts,
                    source_time_seconds: 0.0,
                },
            );
        }
        assert!(stats.variable_delta);
    }

    #[test]
    fn completed_proxy_indexes_refresh_gopro_member_offsets() {
        let directory = tempfile::tempdir().expect("tempdir");
        let output = directory.path();
        let mut first: MediaRecord = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ))
        .expect("chapter one");
        let mut second: MediaRecord = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ))
        .expect("chapter two");
        let mut proxies = std::collections::BTreeMap::new();
        proxies.insert(
            first.media_id.clone(),
            first.proxies.take().unwrap().remove(0),
        );
        proxies.insert(
            second.media_id.clone(),
            second.proxies.take().unwrap().remove(0),
        );
        first.video = None;
        first.span.as_mut().unwrap().offset_in_span = None;
        first.span.as_mut().unwrap().continuity =
            Some(memory_engine_contracts::SpanContinuity::Unverified);
        second.video = None;
        second.span.as_mut().unwrap().offset_in_span = None;
        second.span.as_mut().unwrap().continuity =
            Some(memory_engine_contracts::SpanContinuity::Unverified);
        let initial = gopro::build(&[second.clone(), first.clone()]);
        let assembly = initial.assemblies.into_iter().next().expect("assembly");
        let span_id = assembly.media_id.clone();
        for record in [first, second].into_iter().chain(std::iter::once(assembly)) {
            fs::create_dir_all(record_path(output, &record.media_id).parent().unwrap()).unwrap();
            persist_record(output, &record).unwrap();
        }
        for (media_id, proxy) in proxies {
            let mut member = load_record(output, &media_id).unwrap();
            attach_proxy(&mut member, proxy);
            persist_record(output, &member).unwrap();
        }

        refresh_span_assemblies(output, &BTreeSet::from([span_id.clone()])).unwrap();
        let refreshed = load_record(output, &span_id).unwrap();
        let member_ids = refreshed
            .span
            .as_ref()
            .unwrap()
            .member_media_ids
            .as_ref()
            .unwrap();
        let chapter_two = load_record(output, &member_ids[1]).unwrap();
        assert_eq!(
            chapter_two
                .span
                .as_ref()
                .unwrap()
                .offset_in_span
                .as_ref()
                .unwrap()
                .value,
            42_405.0
        );
        assert_eq!(
            refreshed
                .span
                .as_ref()
                .unwrap()
                .offset_in_span
                .as_ref()
                .unwrap()
                .value,
            0.0
        );
    }

    #[test]
    fn unavailable_span_after_proxy_completion_is_a_report_issue_not_a_failure() {
        let directory = tempfile::tempdir().expect("tempdir");
        let output = directory.path();
        let first: MediaRecord = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ))
        .expect("chapter one");
        let second: MediaRecord = serde_json::from_str(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ))
        .expect("chapter two");
        let initial = gopro::build(&[second.clone(), first.clone()]);
        let assembly = initial.assemblies.into_iter().next().expect("assembly");
        let span_id = assembly.media_id.clone();
        for mut record in [first, second].into_iter().chain(std::iter::once(assembly)) {
            if record.asset_kind == memory_engine_contracts::MediaRecordAssetKind::PhysicalFile {
                for source in &mut record.sources {
                    source.present = false;
                }
            }
            fs::create_dir_all(record_path(output, &record.media_id).parent().unwrap()).unwrap();
            persist_record(output, &record).unwrap();
        }
        let mut report = VideoProxyReport {
            complete: true,
            ..VideoProxyReport::default()
        };

        refresh_spans_for_job(output, &BTreeSet::from([span_id]), &mut report);

        assert!(report.complete);
        assert_eq!(report.issues.len(), 1);
        assert!(report.issues[0].contains("span refresh deferred"));
    }

    #[test]
    fn golden_proxy_job_requires_hardware_without_egress() {
        let fixture =
            include_str!("../../../contracts/fixtures/job-spec/valid/job-video-proxy-resumed.json");
        let job: JobSpec = serde_json::from_str(fixture).expect("golden JobSpec");
        let params = validate_job(&job).expect("supported parameters");
        assert_eq!(params.height, 480);
        assert_eq!(hardware_bitrate(params.crf), 1_060_660);
    }

    #[test]
    fn builds_fail_closed_hardware_commands_for_each_platform_backend() {
        for (backend, hwaccel, scaler, encoder) in [
            (
                HardwareBackend::VideoToolbox,
                "videotoolbox",
                "scale_vt",
                "h264_videotoolbox",
            ),
            (HardwareBackend::Nvdec, "cuda", "scale_cuda", "h264_nvenc"),
            (HardwareBackend::Qsv, "qsv", "scale_qsv", "h264_qsv"),
        ] {
            let args = proxy_command_args(backend, 480, 26, 0);
            assert!(args
                .before_input
                .windows(2)
                .any(|pair| pair == ["-hwaccel", hwaccel]));
            assert!(args.after_input.iter().any(|arg| arg.contains(scaler)));
            assert!(args
                .after_input
                .windows(2)
                .any(|pair| pair == ["-c:v", encoder]));
            assert!(!args
                .after_input
                .windows(2)
                .any(|pair| pair == ["-c:v", "libx264"]));
        }

        let videotoolbox = proxy_command_args(HardwareBackend::VideoToolbox, 480, 26, 0);
        assert!(videotoolbox
            .after_input
            .windows(2)
            .any(|pair| pair == ["-allow_sw", "0"]));
    }

    #[test]
    fn parses_ffmpeg_capability_tables_without_substring_matches() {
        assert!(listing_has_capability(
            " V..... h264_nvenc NVIDIA NVENC H.264 encoder",
            "h264_nvenc"
        ));
        assert!(listing_has_capability(
            "Hardware acceleration methods:\nqsv\ncuda\n",
            "qsv"
        ));
        assert!(!listing_has_capability("h264_nvenc_extra", "h264_nvenc"));
    }

    #[test]
    fn rejects_unknown_backend_but_accepts_windows_backends() {
        assert_eq!(
            HardwareBackend::parse("nvdec").unwrap(),
            HardwareBackend::Nvdec
        );
        assert_eq!(HardwareBackend::parse("qsv").unwrap(), HardwareBackend::Qsv);
        assert!(matches!(
            HardwareBackend::parse("software"),
            Err(VideoProxyError::UnsupportedBackend)
        ));
    }

    #[test]
    fn host_backend_allowlist_matches_the_compiled_platform() {
        assert_eq!(
            HardwareBackend::VideoToolbox.supported_on_host(),
            cfg!(target_os = "macos")
        );
        assert_eq!(
            HardwareBackend::Nvdec.supported_on_host(),
            cfg!(target_os = "windows")
        );
        assert_eq!(
            HardwareBackend::Qsv.supported_on_host(),
            cfg!(target_os = "windows")
        );
    }
}
