use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

use chrono::Utc;
use memory_engine_contracts::{
    Checkpoint, JobOutput, JobOutputKind, JobSpec, JobSpecJobType, JobStateStatus, Progress,
    ProgressUnit,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use unicode_normalization::UnicodeNormalization;

use crate::{
    gopro,
    media::{atomic_write, ingest_file, IngestError},
    scan_paths, ScanIssue, ScanOptions,
};

const CHECKPOINT_VERSION: i64 = 1;

#[derive(Debug, Error)]
pub enum JobExecutionError {
    #[error("ingest only accepts scan_source JobSpecs")]
    WrongJobType,
    #[error("local ingest jobs must declare no network egress")]
    EgressDeclared,
    #[error("scan_source jobs must be resumable")]
    NotResumable,
    #[error("scan_source job has no source paths")]
    MissingSourcePaths,
    #[error("scan parameters are invalid")]
    InvalidParameters(#[source] serde_json::Error),
    #[error("source locator digest does not match canonical source roots")]
    SourceLocatorMismatch,
    #[error("source root could not be canonicalized")]
    Canonicalize(#[source] std::io::Error),
    #[error("job checkpoint could not be persisted")]
    Checkpoint(#[source] std::io::Error),
    #[error("media record could not be serialized")]
    Serialize(#[source] serde_json::Error),
    #[error(transparent)]
    Ingest(#[from] IngestError),
}

#[derive(Clone, Debug)]
pub struct CheckpointStore {
    path: PathBuf,
}

impl CheckpointStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub fn save(&self, job: &JobSpec) -> Result<(), JobExecutionError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(JobExecutionError::Checkpoint)?;
        }
        let bytes = serde_json::to_vec_pretty(job).map_err(JobExecutionError::Serialize)?;
        let temporary = self.path.with_extension("tmp");
        fs::write(&temporary, bytes).map_err(JobExecutionError::Checkpoint)?;
        fs::rename(temporary, &self.path).map_err(JobExecutionError::Checkpoint)
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ScanReport {
    pub processed: usize,
    pub resumed_skips: usize,
    pub assemblies_created: usize,
    pub span_members_updated: usize,
    pub quarantined: usize,
    pub issues: Vec<ScanIssue>,
    pub complete: bool,
}

#[derive(Debug, Deserialize, Serialize)]
struct ScanCursor {
    version: i64,
    last_path: String,
}

pub fn execute_scan(
    job: &mut JobSpec,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
) -> Result<ScanReport, JobExecutionError> {
    execute_scan_batch(job, output_dir, checkpoint_store, None)
}

/// Execute at most `max_files` inputs, durably saving after every file.
///
/// The optional limit is used by schedulers to cooperatively yield. A killed process has
/// the same recovery path: the next invocation resumes strictly after the stored cursor.
pub fn execute_scan_batch(
    job: &mut JobSpec,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
    max_files: Option<usize>,
) -> Result<ScanReport, JobExecutionError> {
    validate_job(job)?;
    if job.state.status == JobStateStatus::Completed {
        let mut report = ScanReport {
            complete: true,
            ..ScanReport::default()
        };
        reconcile_gopro_outputs(job, output_dir, checkpoint_store, &mut report)?;
        return Ok(report);
    }

    let source_paths = job
        .inputs
        .source_paths
        .as_ref()
        .filter(|paths| !paths.is_empty())
        .ok_or(JobExecutionError::MissingSourcePaths)?;
    let roots = canonical_roots(source_paths)?;
    let actual_locator = digest_canonical_roots(&roots);
    if job.inputs.source_locator_digest.as_deref() != Some(&actual_locator) {
        return Err(JobExecutionError::SourceLocatorMismatch);
    }
    let options = scan_options(job.params.as_ref())?;
    let resume_after = decode_cursor(job.checkpoint.as_ref());
    let (entries, issues) = scan_paths(&roots, options);
    let resumed_skips = resume_after.as_ref().map_or(0, |cursor| {
        entries
            .iter()
            .take_while(|entry| entry.cursor <= *cursor)
            .count()
    });
    let mut report = ScanReport {
        resumed_skips,
        issues,
        ..ScanReport::default()
    };
    let total = entries.len();
    let mut bytes_processed = job
        .state
        .progress
        .as_ref()
        .and_then(|progress| progress.bytes_processed)
        .unwrap_or(0);
    let now = Utc::now().to_rfc3339();
    job.state.status = JobStateStatus::Running;
    job.state.heartbeat_at = Some(now.clone());
    if job.state.started_at.is_none() {
        job.state.started_at = Some(now);
    }

    for entry in entries.iter().skip(resumed_skips) {
        if max_files.is_some_and(|limit| report.processed >= limit) {
            break;
        }
        match ingest_file(&entry.path, output_dir) {
            Ok(ingested) => {
                bytes_processed += ingested.record.byte_size;
                if matches!(
                    ingested.record.processing.state,
                    memory_engine_contracts::ProcessingStateState::Quarantined
                ) {
                    report.quarantined += 1;
                }
                let (record_path, artifact_bytes) = persist_record(&ingested.record, output_dir)?;
                append_output(job, &ingested.record.media_id, &record_path, artifact_bytes);
                update_partial_outputs(job, &ingested.record.media_id);
            }
            Err(error) => report.issues.push(ScanIssue {
                code: memory_engine_contracts::JobErrorCode::FileUnreadable,
                message: error.to_string(),
            }),
        }
        report.processed += 1;
        update_progress(
            job,
            resumed_skips + report.processed,
            total,
            bytes_processed,
        );
        update_cursor(job, &entry.cursor);
        checkpoint_store.save(job)?;
    }

    report.complete = resumed_skips + report.processed >= total;
    if report.complete {
        reconcile_gopro_outputs(job, output_dir, checkpoint_store, &mut report)?;
    }
    let finished = Utc::now().to_rfc3339();
    if report.complete {
        job.state.status = JobStateStatus::Completed;
        job.state.finished_at = Some(finished.clone());
        if let Some(progress) = &mut job.state.progress {
            progress.message = Some("source scan complete".to_owned());
        }
    } else {
        job.state.status = JobStateStatus::Pending;
        if let Some(progress) = &mut job.state.progress {
            progress.message = Some("source scan yielded; resumable checkpoint saved".to_owned());
        }
    }
    job.state.heartbeat_at = Some(finished);
    checkpoint_store.save(job)?;
    Ok(report)
}

fn reconcile_gopro_outputs(
    job: &mut JobSpec,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
    report: &mut ScanReport,
) -> Result<(), JobExecutionError> {
    let mut records = Vec::new();
    for output in job
        .outputs
        .as_deref()
        .unwrap_or_default()
        .iter()
        .filter(|output| output.kind == JobOutputKind::MediaRecord)
    {
        let loaded = output
            .path
            .as_deref()
            .ok_or(())
            .and_then(|path| fs::read(path).map_err(|_| ()))
            .and_then(|bytes| serde_json::from_slice(&bytes).map_err(|_| ()));
        match loaded {
            Ok(record) => records.push(record),
            Err(()) => report.issues.push(ScanIssue {
                code: memory_engine_contracts::JobErrorCode::FileUnreadable,
                message: "persisted media record could not be loaded; location redacted".to_owned(),
            }),
        }
    }
    let built = gopro::build(&records);
    report
        .issues
        .extend(built.issues.into_iter().map(|message| ScanIssue {
            code: memory_engine_contracts::JobErrorCode::DependencyFailed,
            message,
        }));

    for member in built.members {
        let (path, artifact_bytes) = persist_record(&member, output_dir)?;
        upsert_output(job, &member.media_id, &path, artifact_bytes);
        update_partial_outputs(job, &member.media_id);
        report.span_members_updated += 1;
        checkpoint_store.save(job)?;
    }
    for desired in built.assemblies {
        let now = Utc::now().to_rfc3339();
        let assembly_path = record_path(output_dir, &desired.media_id);
        let external_existing = fs::read(&assembly_path).ok().and_then(|bytes| {
            serde_json::from_slice::<memory_engine_contracts::MediaRecord>(&bytes).ok()
        });
        let assembly = external_existing
            .as_ref()
            .map_or(desired.clone(), |existing| {
                gopro::merge_existing_assembly(existing, &desired, &now)
            });
        let path = record_path(output_dir, &assembly.media_id);
        let existing = fs::read(&path).ok().and_then(|bytes| {
            serde_json::from_slice::<memory_engine_contracts::MediaRecord>(&bytes).ok()
        });
        let existed = existing.is_some();
        let (path, artifact_bytes) = if existing.as_ref() == Some(&assembly) {
            let bytes = fs::metadata(&path)
                .map_err(JobExecutionError::Checkpoint)?
                .len() as i64;
            (path, bytes)
        } else {
            persist_record(&assembly, output_dir)?
        };
        let output_changed = upsert_output(job, &assembly.media_id, &path, artifact_bytes);
        if !existed {
            report.assemblies_created += 1;
        }
        if !existed || output_changed || existing.as_ref() != Some(&assembly) {
            update_partial_outputs(job, &assembly.media_id);
            checkpoint_store.save(job)?;
        }
    }
    Ok(())
}

pub fn source_locator_digest(paths: &[PathBuf]) -> Result<String, JobExecutionError> {
    canonical_roots(
        &paths
            .iter()
            .map(|path| path.to_string_lossy().into_owned())
            .collect::<Vec<_>>(),
    )
    .map(|roots| digest_canonical_roots(&roots))
}

fn validate_job(job: &JobSpec) -> Result<(), JobExecutionError> {
    if job.job_type != JobSpecJobType::ScanSource {
        return Err(JobExecutionError::WrongJobType);
    }
    if job.egress.requires_egress {
        return Err(JobExecutionError::EgressDeclared);
    }
    if !job.checkpoint.as_ref().is_some_and(|state| state.resumable) {
        return Err(JobExecutionError::NotResumable);
    }
    Ok(())
}

fn canonical_roots(paths: &[String]) -> Result<Vec<PathBuf>, JobExecutionError> {
    let mut roots = paths
        .iter()
        .map(PathBuf::from)
        .map(|path| fs::canonicalize(path).map_err(JobExecutionError::Canonicalize))
        .collect::<Result<Vec<_>, _>>()?;
    roots.sort();
    roots.dedup();
    Ok(roots)
}

fn digest_canonical_roots(roots: &[PathBuf]) -> String {
    let mut hasher = blake3::Hasher::new();
    for (index, root) in roots.iter().enumerate() {
        if index > 0 {
            hasher.update(&[0]);
        }
        let normalized: String = root.to_string_lossy().nfc().collect();
        hasher.update(
            normalized
                .trim_end_matches(std::path::MAIN_SEPARATOR)
                .as_bytes(),
        );
    }
    hasher.finalize().to_hex().to_string()
}

fn scan_options(
    params: Option<&BTreeMap<String, serde_json::Value>>,
) -> Result<ScanOptions, JobExecutionError> {
    let value = params.cloned().map_or_else(
        || serde_json::Value::Object(Default::default()),
        |params| serde_json::Value::Object(params.into_iter().collect()),
    );
    serde_json::from_value(value).map_err(JobExecutionError::InvalidParameters)
}

fn decode_cursor(checkpoint: Option<&Checkpoint>) -> Option<String> {
    let checkpoint = checkpoint?;
    if checkpoint.checkpoint_version != Some(CHECKPOINT_VERSION) {
        return None;
    }
    let cursor: ScanCursor = serde_json::from_str(checkpoint.cursor.as_ref()?).ok()?;
    (cursor.version == CHECKPOINT_VERSION).then_some(cursor.last_path)
}

fn update_cursor(job: &mut JobSpec, path: &str) {
    let checkpoint = job.checkpoint.get_or_insert(Checkpoint {
        resumable: true,
        cursor: None,
        checkpoint_version: Some(CHECKPOINT_VERSION),
        updated_at: None,
        completed_input_ids: Some(Vec::new()),
        partial_output_ids: Some(Vec::new()),
    });
    checkpoint.checkpoint_version = Some(CHECKPOINT_VERSION);
    checkpoint.cursor = Some(
        serde_json::to_string(&ScanCursor {
            version: CHECKPOINT_VERSION,
            last_path: path.to_owned(),
        })
        .expect("cursor serialization cannot fail"),
    );
    checkpoint.updated_at = Some(Utc::now().to_rfc3339());
}

fn update_partial_outputs(job: &mut JobSpec, media_id: &str) {
    let checkpoint = job.checkpoint.as_mut().expect("validated checkpoint");
    let ids = checkpoint.partial_output_ids.get_or_insert_with(Vec::new);
    if !ids.iter().any(|id| id == media_id) {
        ids.push(media_id.to_owned());
    }
}

fn update_progress(job: &mut JobSpec, units_done: usize, total: usize, bytes_processed: i64) {
    job.state.progress = Some(Progress {
        units_done: units_done as f64,
        units_total: Some(total as f64),
        unit: ProgressUnit::Files,
        bytes_processed: Some(bytes_processed),
        message: Some("scanning local source".to_owned()),
    });
    job.state.heartbeat_at = Some(Utc::now().to_rfc3339());
}

fn persist_record(
    record: &memory_engine_contracts::MediaRecord,
    output_dir: &Path,
) -> Result<(PathBuf, i64), JobExecutionError> {
    let directory = output_dir
        .join("records")
        .join(&record.media_id[..2])
        .join(&record.media_id[2..4]);
    fs::create_dir_all(&directory).map_err(JobExecutionError::Checkpoint)?;
    let path = directory.join(format!("{}.json", record.media_id));
    let mut merged = record.clone();
    if let Ok(existing_bytes) = fs::read(&path) {
        if let Ok(existing) =
            serde_json::from_slice::<memory_engine_contracts::MediaRecord>(&existing_bytes)
        {
            for source in existing.sources {
                if !merged
                    .sources
                    .iter()
                    .any(|candidate| candidate.path == source.path)
                {
                    merged.sources.push(source);
                }
            }
        }
    }
    let bytes = serde_json::to_vec_pretty(&merged).map_err(JobExecutionError::Serialize)?;
    atomic_write(&path, &bytes)?;
    Ok((path, bytes.len() as i64))
}

fn record_path(output_dir: &Path, media_id: &str) -> PathBuf {
    output_dir
        .join("records")
        .join(&media_id[..2])
        .join(&media_id[2..4])
        .join(format!("{media_id}.json"))
}

fn append_output(job: &mut JobSpec, media_id: &str, path: &Path, byte_size: i64) {
    let outputs = job.outputs.get_or_insert_with(Vec::new);
    if outputs.iter().any(|output| output.id == media_id) {
        return;
    }
    outputs.push(JobOutput {
        kind: JobOutputKind::MediaRecord,
        id: media_id.to_owned(),
        path: Some(path.to_string_lossy().into_owned()),
        byte_size: Some(byte_size),
        produced_at: Some(Utc::now().to_rfc3339()),
    });
}

fn upsert_output(job: &mut JobSpec, media_id: &str, path: &Path, byte_size: i64) -> bool {
    let outputs = job.outputs.get_or_insert_with(Vec::new);
    if let Some(output) = outputs
        .iter_mut()
        .find(|output| output.kind == JobOutputKind::MediaRecord && output.id == media_id)
    {
        let path = path.to_string_lossy().into_owned();
        if output.path.as_deref() == Some(&path) && output.byte_size == Some(byte_size) {
            return false;
        }
        output.path = Some(path);
        output.byte_size = Some(byte_size);
        output.produced_at = Some(Utc::now().to_rfc3339());
        return true;
    }
    outputs.push(JobOutput {
        kind: JobOutputKind::MediaRecord,
        id: media_id.to_owned(),
        path: Some(path.to_string_lossy().into_owned()),
        byte_size: Some(byte_size),
        produced_at: Some(Utc::now().to_rfc3339()),
    });
    true
}

#[cfg(test)]
mod tests {
    use std::fs;

    use image::{ImageBuffer, Rgb};
    use memory_engine_contracts::JobSpec;
    use serde_json::json;
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn batch_resume_skips_completed_files_and_finishes() {
        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");
        for (index, name) in ["a.jpg", "b.jpg"].into_iter().enumerate() {
            ImageBuffer::from_pixel(8, 8, Rgb([index as u8, 34, 56]))
                .save(source.join(name))
                .expect("fixture image");
        }
        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let mut job: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("job contract");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));

        let first = execute_scan_batch(&mut job, &output, &store, Some(1)).expect("first batch");
        assert_eq!(first.processed, 1);
        assert!(!first.complete);
        assert_eq!(job.state.status, JobStateStatus::Pending);

        let second = execute_scan(&mut job, &output, &store).expect("resume");
        assert_eq!(second.resumed_skips, 1);
        assert_eq!(second.processed, 1);
        assert!(second.complete);
        assert_eq!(job.state.status, JobStateStatus::Completed);
        assert_eq!(job.outputs.as_ref().map(Vec::len), Some(2));
    }

    #[test]
    fn gopro_assembly_is_emitted_only_after_scan_closure_and_is_idempotent() {
        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");
        for (name, marker) in [("GH010042.MP4", 1_u8), ("GH020042.MP4", 2_u8)] {
            let mut bytes = vec![0, 0, 0, 24];
            bytes.extend_from_slice(b"ftypmp42");
            bytes.extend_from_slice(&[marker; 64]);
            fs::write(source.join(name), bytes).expect("GoPro chapter fixture");
        }
        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let mut job: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("job contract");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));

        let first = execute_scan_batch(&mut job, &output, &store, Some(1)).expect("first chapter");
        assert!(!first.complete);
        assert_eq!(first.assemblies_created, 0);
        let first_record_path =
            PathBuf::from(job.outputs.as_ref().unwrap()[0].path.as_ref().unwrap());
        let first_record: memory_engine_contracts::MediaRecord =
            serde_json::from_slice(&fs::read(first_record_path).unwrap()).unwrap();
        assert!(first_record.span.is_none());

        let second = execute_scan(&mut job, &output, &store).expect("close chapter set");
        assert!(second.complete);
        assert_eq!(second.assemblies_created, 1);
        assert_eq!(second.span_members_updated, 2);
        assert_eq!(job.outputs.as_ref().map(Vec::len), Some(3));
        let records = job
            .outputs
            .as_ref()
            .unwrap()
            .iter()
            .map(|artifact| {
                serde_json::from_slice::<memory_engine_contracts::MediaRecord>(
                    &fs::read(artifact.path.as_ref().unwrap()).unwrap(),
                )
                .unwrap()
            })
            .collect::<Vec<_>>();
        let assembly = records
            .iter()
            .find(|record| {
                record.asset_kind == memory_engine_contracts::MediaRecordAssetKind::VirtualAssembly
            })
            .expect("virtual assembly");
        let members = records
            .iter()
            .filter(|record| {
                record.asset_kind == memory_engine_contracts::MediaRecordAssetKind::PhysicalFile
            })
            .collect::<Vec<_>>();
        assert_eq!(assembly.byte_size, 0);
        assert!(assembly.sources.is_empty());
        assert_eq!(assembly.proxies.as_ref().map(Vec::len), Some(0));
        assert_eq!(
            assembly.span.as_ref().unwrap().role,
            memory_engine_contracts::SpanRole::Assembly
        );
        assert!(members.iter().all(|member| {
            member.span.as_ref().is_some_and(|span| {
                span.role == memory_engine_contracts::SpanRole::Member
                    && span.span_id == assembly.media_id
            })
        }));

        let assembly_path = record_path(&output, &assembly.media_id);
        let before = fs::read(&assembly_path).expect("assembly bytes");
        let repeated = execute_scan(&mut job, &output, &store).expect("completed reconciliation");
        assert_eq!(repeated.assemblies_created, 0);
        assert_eq!(repeated.span_members_updated, 0);
        assert_eq!(
            before,
            fs::read(assembly_path).expect("stable assembly bytes")
        );
        assert_eq!(job.outputs.as_ref().map(Vec::len), Some(3));
    }

    #[test]
    fn rejects_any_egress_declaration() {
        let fixture =
            include_str!("../../../contracts/fixtures/job-spec/valid/job-scan-source-root-a.json");
        let mut job: JobSpec = serde_json::from_str(fixture).expect("golden JobSpec");
        job.egress.requires_egress = true;
        assert!(matches!(
            validate_job(&job),
            Err(JobExecutionError::EgressDeclared)
        ));
    }
}
