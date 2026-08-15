use std::{
    fs::{self, File},
    io::{self, BufRead, BufReader, BufWriter, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use chrono::Utc;
use memory_engine_contracts::{
    FrameIndexSidecar, FrameIndexSidecarMapping, JobError, JobErrorCode, JobOutput, JobOutputKind,
    JobSpec, JobSpecJobType, JobStateStatus, MediaRecord, PixelSize, ProcessingStateState,
    Progress, ProgressUnit, ProxyRef, ProxyRefKind, StageState, StageStateStatus,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::{job::JobExecutionError, media::atomic_write, CheckpointStore};

const CHECKPOINT_VERSION: i64 = 1;
const SIDECAR_VERSION: i64 = 1;

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
    #[error("only the macOS VideoToolbox backend is available in this phase")]
    UnsupportedBackend,
    #[error("video proxy job has no media inputs")]
    MissingInputs,
    #[error("input MediaRecord is missing")]
    MissingRecord,
    #[error("input MediaRecord has no present source")]
    MissingSource,
    #[error("FFmpeg with VideoToolbox is unavailable")]
    FfmpegUnavailable,
    #[error("hardware proxy generation failed without software fallback")]
    FfmpegFailed,
    #[error("frame-index output was incomplete")]
    IncompleteFrameIndex,
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
    verify_videotoolbox(ffmpeg_path)?;
    if job.state.status == JobStateStatus::Completed {
        return Ok(VideoProxyReport {
            complete: true,
            ..VideoProxyReport::default()
        });
    }
    let media_ids = job
        .inputs
        .media_ids
        .clone()
        .filter(|ids| !ids.is_empty())
        .ok_or(VideoProxyError::MissingInputs)?;
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
        ) {
            Ok(generated) => generated,
            Err(error) => {
                record_failure(job, &media_id, &error);
                checkpoint_store.save(job)?;
                return Err(error);
            }
        };
        report.frames += generated.frame_index.entry_count;
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
    Ok(report)
}

struct GeneratedProxy {
    proxy: ProxyRef,
    frame_index: FrameIndexSidecar,
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
) -> Result<GeneratedProxy, VideoProxyError> {
    let work = output_dir.join("proxies").join("work");
    fs::create_dir_all(&work)?;
    let nonce = format!("{}-{}", std::process::id(), Utc::now().timestamp_micros());
    let partial_video = work.join(format!("{media_id}-{nonce}.mp4"));
    let rows_path = work.join(format!("{media_id}-{nonce}.rows"));
    let mut rows = BufWriter::new(File::create(&rows_path)?);
    let bitrate = videotoolbox_bitrate(params.crf);
    let filter = format!(
        "scale_vt=w=ceil(iw*{0}/ih/2)*2:h={0},hwdownload,format=nv12,setsar=1,showinfo",
        params.height
    );
    let mut child = Command::new(ffmpeg_path)
        .args([
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-hwaccel",
            "videotoolbox",
            "-hwaccel_output_format",
            "videotoolbox_vld",
            "-i",
        ])
        .arg(source)
        .args([
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
            "h264_videotoolbox",
            "-allow_sw",
            "0",
            "-profile:v",
            "high",
            "-b:v",
            &bitrate.to_string(),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "-y",
        ])
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
            "memory-engine-ingest/0.1.0+ffmpeg+videotoolbox+crf{}",
            params.crf
        )),
        frame_index: Some(frame_index.clone()),
    };
    Ok(GeneratedProxy { proxy, frame_index })
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
        || params.hardware_decode != "videotoolbox"
        || !params.emit_frame_index
    {
        return Err(VideoProxyError::UnsupportedBackend);
    }
    Ok(params)
}

fn verify_videotoolbox(ffmpeg_path: &Path) -> Result<(), VideoProxyError> {
    #[cfg(not(target_os = "macos"))]
    {
        let _ = ffmpeg_path;
        Err(VideoProxyError::UnsupportedBackend)
    }
    #[cfg(target_os = "macos")]
    {
        let output = Command::new(ffmpeg_path)
            .args(["-hide_banner", "-hwaccels"])
            .output()
            .map_err(|_| VideoProxyError::FfmpegUnavailable)?;
        let stdout = String::from_utf8_lossy(&output.stdout);
        if !output.status.success() || !stdout.lines().any(|line| line.trim() == "videotoolbox") {
            return Err(VideoProxyError::FfmpegUnavailable);
        }
        Ok(())
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
        VideoProxyError::FfmpegUnavailable | VideoProxyError::UnsupportedBackend => {
            JobErrorCode::GpuUnavailable
        }
        VideoProxyError::FfmpegFailed => JobErrorCode::UnsupportedCodec,
        _ => JobErrorCode::InternalError,
    };
    job.error = Some(JobError {
        code,
        message: "hardware video proxy failed; source details redacted".to_owned(),
        retryable: matches!(error, VideoProxyError::FfmpegFailed),
        attempt: Some(job.state.attempts),
        occurred_at: Some(now.clone()),
        failed_input_id: Some(media_id.to_owned()),
    });
    job.state.status = JobStateStatus::Failed;
    job.state.heartbeat_at = Some(now);
}

fn videotoolbox_bitrate(crf: i32) -> i64 {
    let scale = 2_f64.powf(f64::from(23 - crf) / 6.0);
    (1_500_000.0 * scale).round().clamp(400_000.0, 6_000_000.0) as i64
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
    fn golden_proxy_job_requires_videotoolbox_without_egress() {
        let fixture =
            include_str!("../../../contracts/fixtures/job-spec/valid/job-video-proxy-resumed.json");
        let job: JobSpec = serde_json::from_str(fixture).expect("golden JobSpec");
        let params = validate_job(&job).expect("supported parameters");
        assert_eq!(params.height, 480);
        assert_eq!(videotoolbox_bitrate(params.crf), 1_060_660);
    }
}
