use std::{
    fs::{self, File},
    io::{self, Read},
    path::Path,
};

use chrono::Utc;
use image::{codecs::jpeg::JpegEncoder, DynamicImage};
use memory_engine_contracts::{
    ErrorInfo, ExclusionState, ExclusionStateReasonsItem, ImageProperties,
    ImagePropertiesColorSpace, MediaRecord, MediaRecordAssetKind, MediaRecordFileFormat,
    MediaRecordKind, PerceptualFingerprint, PixelSize, ProcessingState, ProcessingStateStages,
    ProcessingStateState, ProxyRef, ProxyRefKind, SourceLocation, SourceLocationAdapter,
    StageState, StageStateStatus,
};
use thiserror::Error;

use crate::{format, gopro, metadata, phash, still_decoder};

const READ_BUFFER_BYTES: usize = 64 * 1024;
const MAX_BUFFERED_STILL_BYTES: u64 = 512 * 1024 * 1024;
const GENERATOR_VERSION: &str = "memory-engine-ingest/0.1.0";

#[derive(Debug, Error)]
pub enum IngestError {
    #[error("source file could not be read")]
    Read(#[source] io::Error),
    #[error("derived artifact could not be written")]
    Write(#[source] io::Error),
    #[error("derived image could not be encoded")]
    Encode(#[source] image::ImageError),
}

#[derive(Debug)]
pub struct IngestedFile {
    pub record: MediaRecord,
}

pub fn ingest_file(path: &Path, output_dir: &Path) -> Result<IngestedFile, IngestError> {
    let source = read_source_once(path)?;
    let now = Utc::now().to_rfc3339();
    let detected = format::detect(&source.prefix);
    let metadata = metadata::extract(source.full_bytes.as_deref().unwrap_or(&source.prefix), path);
    let source_location = SourceLocation {
        path: path.to_string_lossy().into_owned(),
        volume_id: None,
        adapter: detect_adapter(path),
        sidecar_paths: Some(Vec::new()),
        original_filename: path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned()),
        first_seen_at: now.clone(),
        last_verified_at: Some(now.clone()),
        present: true,
    };

    let mut record = MediaRecord {
        schema_version: "v0".to_owned(),
        media_id: source.hash,
        asset_kind: MediaRecordAssetKind::PhysicalFile,
        kind: detected.map_or(MediaRecordKind::Unknown, |value| value.kind),
        byte_size: source.byte_size as i64,
        mime_type: detected.map(|value| value.mime_type.to_owned()),
        file_format: detected
            .map(|value| value.file_format)
            .or(Some(MediaRecordFileFormat::Unknown)),
        sources: vec![source_location],
        span: None,
        capture: metadata.capture,
        image: None,
        video: None,
        perceptual: None,
        proxies: Some(Vec::new()),
        processing: processing_pending(),
        quality: None,
        content: None,
        faces: None,
        dedupe: None,
        exclusion: Some(ExclusionState {
            excluded_from_automation: false,
            reasons: Some(Vec::new()),
            user_override: None,
        }),
        user: None,
        model_runs: Some(Vec::new()),
        first_seen_at: Some(now.clone()),
        updated_at: Some(now.clone()),
    };

    record.processing.stages.hash = Some(done_stage(&now));

    if source.byte_size == 0 {
        quarantine(
            &mut record,
            &now,
            "zero_byte_file",
            ExclusionStateReasonsItem::Unreadable,
        );
        return Ok(IngestedFile { record });
    }

    let Some(detected) = detected else {
        quarantine(
            &mut record,
            &now,
            "unsupported_format",
            ExclusionStateReasonsItem::UnsupportedCodec,
        );
        return Ok(IngestedFile { record });
    };

    if detected.kind != MediaRecordKind::Image {
        record.processing.state = ProcessingStateState::Hashed;
        record.processing.stages.metadata = Some(done_stage(&now));
        record.processing.stages.thumbnail = Some(pending_stage());
        record.processing.stages.perceptual_hash = Some(pending_stage());
        return Ok(IngestedFile { record });
    }

    if source.full_bytes.is_none() {
        processing_failure(
            &mut record,
            &now,
            "still_exceeds_memory_limit",
            ExclusionStateReasonsItem::UnsupportedCodec,
            false,
        );
        return Ok(IngestedFile { record });
    }

    let bytes = source.full_bytes.as_deref().expect("checked above");
    let decoded = match still_decoder::decode(bytes, detected.file_format) {
        Ok(decoded) => decoded,
        Err(still_decoder::StillDecodeError::MissingCapability) => {
            processing_failure(
                &mut record,
                &now,
                still_decoder::missing_capability_code(detected.file_format),
                ExclusionStateReasonsItem::UnsupportedCodec,
                true,
            );
            return Ok(IngestedFile { record });
        }
        Err(still_decoder::StillDecodeError::DecodeFailed) => {
            quarantine(
                &mut record,
                &now,
                "file_corrupt",
                ExclusionStateReasonsItem::Corrupt,
            );
            return Ok(IngestedFile { record });
        }
    };

    let stored_size = PixelSize {
        width: i64::from(decoded.image.width()),
        height: i64::from(decoded.image.height()),
    };
    let oriented = apply_orientation(decoded.image, metadata.orientation);
    let oriented_size = PixelSize {
        width: i64::from(oriented.width()),
        height: i64::from(oriented.height()),
    };
    record.image = Some(ImageProperties {
        stored_size,
        oriented_size,
        orientation: Some(i64::from(metadata.orientation)),
        bit_depth: None,
        color_space: Some(ImagePropertiesColorSpace::Unknown),
        icc_profile_name: None,
        has_alpha: Some(decoded.has_alpha),
        is_raw: Some(false),
        is_hdr: Some(false),
        paired_motion_media_id: None,
    });
    record.perceptual = Some(PerceptualFingerprint {
        image_hash: Some(phash::dct_64(&oriented)),
        keyframe_hashes: Some(Vec::new()),
    });

    let proxy = write_thumbnail(&oriented, output_dir)?;
    record.proxies = Some(vec![proxy]);
    record.processing.state = ProcessingStateState::Proxied;
    record.processing.stages.metadata = Some(done_stage(&now));
    record.processing.stages.thumbnail = Some(done_stage(&now));
    record.processing.stages.perceptual_hash = Some(done_stage(&now));

    Ok(IngestedFile { record })
}

struct SourceRead {
    hash: String,
    byte_size: u64,
    prefix: Vec<u8>,
    full_bytes: Option<Vec<u8>>,
}

fn read_source_once(path: &Path) -> Result<SourceRead, IngestError> {
    let mut file = File::open(path).map_err(IngestError::Read)?;
    let expected_size = file.metadata().map_err(IngestError::Read)?.len();
    let mut hasher = blake3::Hasher::new();
    let mut prefix = Vec::new();
    let mut full_bytes = None;
    let mut buffer = vec![0_u8; READ_BUFFER_BYTES];
    let mut byte_size = 0_u64;

    loop {
        let count = file.read(&mut buffer).map_err(IngestError::Read)?;
        if count == 0 {
            break;
        }
        let chunk = &buffer[..count];
        let first_chunk = byte_size == 0;
        if prefix.len() < READ_BUFFER_BYTES {
            let needed = READ_BUFFER_BYTES - prefix.len();
            prefix.extend_from_slice(&chunk[..chunk.len().min(needed)]);
        }
        if first_chunk
            && expected_size <= MAX_BUFFERED_STILL_BYTES
            && format::detect(&prefix)
                .is_some_and(|detected| detected.kind == MediaRecordKind::Image)
        {
            full_bytes = Some(chunk.to_vec());
        } else if let Some(bytes) = &mut full_bytes {
            bytes.extend_from_slice(chunk);
        }
        hasher.update(chunk);
        byte_size += count as u64;
    }

    Ok(SourceRead {
        hash: hasher.finalize().to_hex().to_string(),
        byte_size,
        prefix,
        full_bytes,
    })
}

fn apply_orientation(image: DynamicImage, orientation: u32) -> DynamicImage {
    match orientation {
        2 => image.fliph(),
        3 => image.rotate180(),
        4 => image.flipv(),
        5 => image.rotate90().fliph(),
        6 => image.rotate90(),
        7 => image.rotate270().fliph(),
        8 => image.rotate270(),
        _ => image,
    }
}

fn write_thumbnail(image: &DynamicImage, output_dir: &Path) -> Result<ProxyRef, IngestError> {
    let thumbnail = image.thumbnail(512, 512);
    let mut encoded = Vec::new();
    JpegEncoder::new_with_quality(&mut encoded, 85)
        .encode_image(&thumbnail)
        .map_err(IngestError::Encode)?;
    let proxy_id = blake3::hash(&encoded).to_hex().to_string();
    let directory = output_dir
        .join("proxies")
        .join(&proxy_id[..2])
        .join(&proxy_id[2..4]);
    fs::create_dir_all(&directory).map_err(IngestError::Write)?;
    let path = directory.join(format!("{proxy_id}.jpg"));
    atomic_write(&path, &encoded)?;

    Ok(ProxyRef {
        proxy_id,
        kind: ProxyRefKind::Thumbnail512,
        path: path.to_string_lossy().into_owned(),
        size: Some(PixelSize {
            width: i64::from(thumbnail.width()),
            height: i64::from(thumbnail.height()),
        }),
        byte_size: Some(encoded.len() as i64),
        generator_version: Some(GENERATOR_VERSION.to_owned()),
        frame_index: None,
    })
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), IngestError> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, bytes).map_err(IngestError::Write)?;
    fs::rename(&temporary, path).map_err(IngestError::Write)
}

fn detect_adapter(path: &Path) -> SourceLocationAdapter {
    let full = path.to_string_lossy().to_ascii_lowercase();
    let filename = path
        .file_name()
        .map(|name| name.to_string_lossy().to_ascii_uppercase())
        .unwrap_or_default();
    if full.contains("google takeout") || full.contains("/takeout/") {
        SourceLocationAdapter::GoogleTakeout
    } else if full.contains("whatsapp")
        || filename.starts_with("IMG-") && filename.contains("-WA")
        || filename.starts_with("VID-") && filename.contains("-WA")
    {
        SourceLocationAdapter::Whatsapp
    } else if gopro::parse_filename(&filename).is_some() {
        SourceLocationAdapter::GoproCard
    } else if full.contains("icloud") {
        SourceLocationAdapter::IcloudExport
    } else if full.contains("/dcim/") {
        SourceLocationAdapter::DslrCard
    } else {
        SourceLocationAdapter::Filesystem
    }
}

fn processing_pending() -> ProcessingState {
    ProcessingState {
        state: ProcessingStateState::Discovered,
        stages: ProcessingStateStages {
            hash: Some(pending_stage()),
            metadata: Some(pending_stage()),
            thumbnail: Some(pending_stage()),
            video_proxy: None,
            perceptual_hash: Some(pending_stage()),
            classical_quality: None,
            image_embedding: None,
            face_detection: None,
            // Added by contracts: the analysis stage records detection and
            // embedding separately, so a detector that ran and an embedder
            // that was missing leaves the library with face boxes rather
            // than with neither. Ingest runs neither.
            face_embedding: None,
            iqa: None,
            aesthetic: None,
            tagging: None,
            safety: None,
            ocr: None,
            shot_detection: None,
            transcription: None,
            audio_events: None,
            moment_scoring: None,
        },
    }
}

fn pending_stage() -> StageState {
    StageState {
        status: StageStateStatus::Pending,
        attempts: Some(0),
        completed_at: None,
        job_id: None,
        skip_reason: None,
        last_error: None,
    }
}

fn done_stage(now: &str) -> StageState {
    StageState {
        status: StageStateStatus::Done,
        attempts: Some(1),
        completed_at: Some(now.to_owned()),
        job_id: None,
        skip_reason: None,
        last_error: None,
    }
}

fn failed_stage(now: &str, code: &str, retryable: bool) -> StageState {
    StageState {
        status: StageStateStatus::Failed,
        attempts: Some(1),
        completed_at: Some(now.to_owned()),
        job_id: None,
        skip_reason: None,
        last_error: Some(ErrorInfo {
            code: code.to_owned(),
            message: "media processing failed; source details redacted".to_owned(),
            retryable,
            occurred_at: Some(now.to_owned()),
        }),
    }
}

fn quarantine(record: &mut MediaRecord, now: &str, code: &str, reason: ExclusionStateReasonsItem) {
    record.processing.state = ProcessingStateState::Quarantined;
    record.processing.stages.metadata = Some(failed_stage(now, code, false));
    record.processing.stages.thumbnail = Some(failed_stage(now, code, false));
    record.processing.stages.perceptual_hash = Some(failed_stage(now, code, false));
    record.exclusion = Some(ExclusionState {
        excluded_from_automation: true,
        reasons: Some(vec![reason]),
        user_override: None,
    });
}

fn processing_failure(
    record: &mut MediaRecord,
    now: &str,
    code: &str,
    reason: ExclusionStateReasonsItem,
    retryable: bool,
) {
    record.processing.state = ProcessingStateState::Failed;
    record.processing.stages.metadata = Some(done_stage(now));
    record.processing.stages.thumbnail = Some(failed_stage(now, code, retryable));
    record.processing.stages.perceptual_hash = Some(failed_stage(now, code, retryable));
    record.exclusion = Some(ExclusionState {
        excluded_from_automation: true,
        reasons: Some(vec![reason]),
        user_override: None,
    });
}

pub(crate) fn needs_capability_retry(record: &MediaRecord) -> bool {
    let Some(file_format) = record.file_format else {
        return false;
    };
    if !still_decoder::is_heif_family(file_format) || !still_decoder::supports(file_format) {
        return false;
    }

    let capability_blocked = [
        record.processing.stages.thumbnail.as_ref(),
        record.processing.stages.perceptual_hash.as_ref(),
    ]
    .into_iter()
    .flatten()
    .filter_map(|stage| stage.last_error.as_ref())
    .any(|error| error.retryable && error.code.starts_with("missing_capability_"));
    let legacy_unsupported = record.processing.state == ProcessingStateState::Quarantined
        && record
            .processing
            .stages
            .thumbnail
            .as_ref()
            .and_then(|stage| stage.last_error.as_ref())
            .is_some_and(|error| error.code == "unsupported_codec");
    capability_blocked || legacy_unsupported
}

#[cfg(test)]
mod tests {
    use std::fs;

    use image::{ImageBuffer, Rgb};
    use memory_engine_contracts::{ProcessingStateState, TimeSource};
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn hashes_extracts_and_generates_still_derivatives() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("photo.jpg");
        let image =
            ImageBuffer::from_fn(800, 600, |x, y| Rgb([(x % 255) as u8, (y % 255) as u8, 90]));
        image.save(&source_path).expect("fixture jpeg");
        let expected = blake3::hash(&fs::read(&source_path).expect("fixture bytes"))
            .to_hex()
            .to_string();

        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(ingested.record.media_id, expected);
        assert_eq!(
            ingested.record.processing.state,
            ProcessingStateState::Proxied
        );
        assert_eq!(
            ingested
                .record
                .perceptual
                .as_ref()
                .and_then(|value| value.image_hash.as_ref())
                .map(|value| value.hex.len()),
            Some(16)
        );
        assert!(Path::new(&ingested.record.proxies.as_ref().unwrap()[0].path).exists());
    }

    #[test]
    fn zero_byte_file_is_retained_and_quarantined() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("empty.jpg");
        fs::write(&source_path, []).expect("zero byte fixture");
        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.media_id,
            "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
        );
        assert_eq!(
            ingested.record.processing.state,
            ProcessingStateState::Quarantined
        );
    }

    #[test]
    fn corrupt_jpeg_is_quarantined_not_dropped() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("broken.jpg");
        fs::write(&source_path, [0xff, 0xd8, 0xff, 0x00, 0x01]).expect("corrupt fixture");
        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.processing.state,
            ProcessingStateState::Quarantined
        );
        assert_eq!(
            ingested.record.file_format,
            Some(MediaRecordFileFormat::Jpeg)
        );
    }

    #[test]
    #[cfg(not(target_os = "macos"))]
    fn unsupported_heic_is_retryable_and_not_quarantined() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("mislabeled.jpg");
        let mut bytes = vec![0, 0, 0, 24];
        bytes.extend_from_slice(b"ftypheic");
        bytes.extend_from_slice(&[0; 32]);
        fs::write(&source_path, bytes).expect("heic fixture");
        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.file_format,
            Some(MediaRecordFileFormat::Heic)
        );
        assert_eq!(
            ingested.record.processing.state,
            ProcessingStateState::Failed
        );
        let error = ingested
            .record
            .processing
            .stages
            .thumbnail
            .and_then(|stage| stage.last_error)
            .expect("capability error");
        assert_eq!(error.code, "missing_capability_heic_decoder");
        assert!(error.retryable);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn heic_is_decoded_by_image_io_even_with_a_jpeg_extension() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("mislabeled.jpg");
        let jpeg_path = directory.path().join("source.jpg");
        ImageBuffer::from_pixel(8, 6, Rgb([40_u8, 80, 120]))
            .save(&jpeg_path)
            .expect("JPEG fixture");
        let output = std::process::Command::new("sips")
            .arg("-s")
            .arg("format")
            .arg("heic")
            .arg(&jpeg_path)
            .arg("--out")
            .arg(&source_path)
            .output()
            .expect("run sips");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );

        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.file_format,
            Some(MediaRecordFileFormat::Heic)
        );
        assert_eq!(
            ingested.record.processing.state,
            ProcessingStateState::Proxied
        );
        assert_eq!(
            ingested
                .record
                .image
                .as_ref()
                .map(|image| &image.oriented_size),
            Some(&PixelSize {
                width: 8,
                height: 6
            })
        );
        assert!(Path::new(&ingested.record.proxies.as_ref().unwrap()[0].path).exists());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn corrupt_heic_is_permanently_quarantined_after_decoder_runs() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("broken.heic");
        let mut bytes = vec![0, 0, 0, 24];
        bytes.extend_from_slice(b"ftypheic");
        bytes.extend_from_slice(&[0; 32]);
        fs::write(&source_path, bytes).expect("corrupt HEIC fixture");

        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.processing.state,
            ProcessingStateState::Quarantined
        );
        let error = ingested
            .record
            .processing
            .stages
            .thumbnail
            .as_ref()
            .and_then(|stage| stage.last_error.as_ref())
            .expect("decode error");
        assert_eq!(error.code, "file_corrupt");
        assert!(!error.retryable);
        assert!(!needs_capability_retry(&ingested.record));
    }

    #[test]
    fn whatsapp_filename_recovers_date_without_exif() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("IMG-20240203-WA0001.jpg");
        let image = ImageBuffer::from_pixel(4, 4, Rgb([1_u8, 2, 3]));
        image.save(&source_path).expect("fixture jpeg");
        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.capture.captured_at.source,
            TimeSource::FilenamePattern
        );
        assert_eq!(
            ingested.record.sources[0].adapter,
            SourceLocationAdapter::Whatsapp
        );
    }

    #[test]
    fn gopro_lrv_sidecar_keeps_the_gopro_source_adapter() {
        let path = Path::new("/Volumes/GOPRO/DCIM/100GOPRO/GH011234.LRV");
        assert_eq!(detect_adapter(path), SourceLocationAdapter::GoproCard);
    }

    #[test]
    fn extracts_exif_date_and_applies_orientation() {
        let directory = tempdir().expect("tempdir");
        let source_path = directory.path().join("camera.jpg");
        let mut jpeg = Vec::new();
        JpegEncoder::new(&mut jpeg)
            .encode_image(&DynamicImage::ImageRgb8(ImageBuffer::from_pixel(
                8,
                4,
                Rgb([4_u8, 5, 6]),
            )))
            .expect("fixture jpeg");
        let exif = exif_app1(6, "2024:02:03 04:05:06");
        jpeg.splice(2..2, exif);
        fs::write(&source_path, jpeg).expect("EXIF fixture");

        let ingested = ingest_file(&source_path, directory.path()).expect("ingest");
        assert_eq!(
            ingested.record.capture.captured_at.source,
            TimeSource::ExifDatetimeOriginal
        );
        assert_eq!(
            ingested.record.capture.captured_at.local.as_deref(),
            Some("2024-02-03T04:05:06")
        );
        let image = ingested.record.image.expect("image properties");
        assert_eq!((image.stored_size.width, image.stored_size.height), (8, 4));
        assert_eq!(
            (image.oriented_size.width, image.oriented_size.height),
            (4, 8)
        );
    }

    fn exif_app1(orientation: u16, captured_at: &str) -> Vec<u8> {
        let mut tiff = Vec::new();
        tiff.extend_from_slice(b"II");
        tiff.extend_from_slice(&42_u16.to_le_bytes());
        tiff.extend_from_slice(&8_u32.to_le_bytes());
        tiff.extend_from_slice(&2_u16.to_le_bytes());
        tiff.extend_from_slice(&0x0112_u16.to_le_bytes());
        tiff.extend_from_slice(&3_u16.to_le_bytes());
        tiff.extend_from_slice(&1_u32.to_le_bytes());
        tiff.extend_from_slice(&orientation.to_le_bytes());
        tiff.extend_from_slice(&0_u16.to_le_bytes());
        tiff.extend_from_slice(&0x8769_u16.to_le_bytes());
        tiff.extend_from_slice(&4_u16.to_le_bytes());
        tiff.extend_from_slice(&1_u32.to_le_bytes());
        tiff.extend_from_slice(&38_u32.to_le_bytes());
        tiff.extend_from_slice(&0_u32.to_le_bytes());
        tiff.extend_from_slice(&1_u16.to_le_bytes());
        tiff.extend_from_slice(&0x9003_u16.to_le_bytes());
        tiff.extend_from_slice(&2_u16.to_le_bytes());
        tiff.extend_from_slice(&20_u32.to_le_bytes());
        tiff.extend_from_slice(&56_u32.to_le_bytes());
        tiff.extend_from_slice(&0_u32.to_le_bytes());
        tiff.extend_from_slice(captured_at.as_bytes());
        tiff.push(0);

        let mut payload = b"Exif\0\0".to_vec();
        payload.extend_from_slice(&tiff);
        let length = u16::try_from(payload.len() + 2).expect("small EXIF fixture");
        let mut segment = vec![0xff, 0xe1];
        segment.extend_from_slice(&length.to_be_bytes());
        segment.extend_from_slice(&payload);
        segment
    }
}
