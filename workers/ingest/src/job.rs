use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

#[cfg(unix)]
use std::fs::File;

use chrono::Utc;
use memory_engine_contracts::{
    Checkpoint, JobOutput, JobOutputKind, JobSpec, JobSpecJobType, JobState, JobStateStatus,
    Progress, ProgressUnit,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use unicode_normalization::UnicodeNormalization;

use crate::{
    gopro,
    media::{atomic_write, ingest_file, needs_capability_retry, IngestError},
    scan_paths, ScanIssue, ScanOptions,
};

const CHECKPOINT_VERSION: i64 = 1;
const SCAN_JOURNAL_VERSION: i64 = 1;

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
    #[error("persisted media record could not be loaded; location redacted")]
    PersistedRecordUnreadable,
    #[error("persisted media record does not match its JobOutput integrity metadata")]
    PersistedRecordIntegrity,
    #[error("scan checkpoint journal is missing or corrupt: {0}")]
    CheckpointJournal(&'static str),
    #[error(transparent)]
    Ingest(#[from] IngestError),
}

#[derive(Clone, Debug)]
pub struct CheckpointStore {
    path: PathBuf,
}

#[derive(Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "record_type", rename_all = "snake_case")]
enum ScanJournalRecord {
    Header {
        version: i64,
        job: Box<JobSpec>,
    },
    Seed {
        version: i64,
        job_id: String,
        output: Option<JobOutput>,
        partial_output_id: Option<String>,
        completed_input_id: Option<String>,
    },
    Progress {
        version: i64,
        job_id: String,
        sequence: u64,
        state: Box<JobState>,
        cursor: Option<String>,
        checkpoint_updated_at: Option<String>,
        output: Option<JobOutput>,
        replaced_output_id: Option<String>,
        partial_output_id: Option<String>,
        replaced_partial_output_id: Option<String>,
    },
    Commit {
        version: i64,
        job_id: String,
        snapshot_blake3: String,
    },
}

#[derive(Debug, Default)]
struct ScanOutputDelta {
    output: Option<JobOutput>,
    replaced_output_id: Option<String>,
    partial_output_id: Option<String>,
    replaced_partial_output_id: Option<String>,
}

impl CheckpointStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    fn sidecar(&self, suffix: &str) -> PathBuf {
        let mut path = self.path.as_os_str().to_owned();
        path.push(suffix);
        PathBuf::from(path)
    }

    fn journal_path(&self) -> PathBuf {
        self.sidecar(".scan-journal")
    }

    fn journal_required_path(&self) -> PathBuf {
        self.sidecar(".scan-journal-required")
    }

    fn ensure_parent(&self) -> Result<(), JobExecutionError> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(JobExecutionError::Checkpoint)?;
        }
        Ok(())
    }

    fn write_snapshot_bytes(&self, bytes: &[u8]) -> Result<(), JobExecutionError> {
        self.ensure_parent()?;
        let temporary = self.path.with_extension("tmp");
        write_durable(&temporary, bytes)?;
        fs::rename(temporary, &self.path).map_err(JobExecutionError::Checkpoint)?;
        sync_parent_directory(&self.path)
    }

    fn append_journal(&self, record: &ScanJournalRecord) -> Result<(), JobExecutionError> {
        let mut bytes = serde_json::to_vec(record).map_err(JobExecutionError::Serialize)?;
        bytes.push(b'\n');
        let mut journal = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.journal_path())
            .map_err(JobExecutionError::Checkpoint)?;
        journal
            .write_all(&bytes)
            .and_then(|()| journal.sync_data())
            .map_err(JobExecutionError::Checkpoint)
    }

    fn remove_if_present(path: &Path) -> Result<(), JobExecutionError> {
        match fs::remove_file(path) {
            Ok(()) => sync_parent_directory(path),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(JobExecutionError::Checkpoint(error)),
        }
    }

    /// Atomically compact the complete JobSpec and retire any scan journal.
    pub fn save(&self, job: &JobSpec) -> Result<(), JobExecutionError> {
        self.ensure_parent()?;
        let bytes = serde_json::to_vec_pretty(job).map_err(JobExecutionError::Serialize)?;
        let temporary = self.path.with_extension("tmp");
        write_durable(&temporary, &bytes)?;

        let marker = self.journal_required_path();
        let journal = self.journal_path();
        if marker.is_file() {
            if !journal.is_file() {
                return Err(JobExecutionError::CheckpointJournal(
                    "required journal is absent",
                ));
            }
            self.append_journal(&ScanJournalRecord::Commit {
                version: SCAN_JOURNAL_VERSION,
                job_id: job.job_id.clone(),
                snapshot_blake3: blake3::hash(&bytes).to_hex().to_string(),
            })?;
        }

        fs::rename(temporary, &self.path).map_err(JobExecutionError::Checkpoint)?;
        sync_parent_directory(&self.path)?;
        // Marker first: if cleanup is interrupted, an orphaned journal beside a
        // complete snapshot is harmless. The reverse order would leave a marker
        // requiring a journal that had already been deleted.
        Self::remove_if_present(&marker)?;
        Self::remove_if_present(&journal)
    }

    /// Start a scan journal once. The header contains only the constant-sized
    /// job configuration and progress state. A legacy full checkpoint is
    /// migrated with one seed delta per existing output rather than embedding
    /// its growing manifest in the header; subsequent per-file writes remain
    /// constant-sized deltas.
    fn begin_scan(&self, job: &mut JobSpec) -> Result<(), JobExecutionError> {
        self.ensure_parent()?;
        let journal = self.journal_path();
        let marker = self.journal_required_path();
        if marker.is_file() {
            if journal.is_file() {
                return Ok(());
            }
            return Err(JobExecutionError::CheckpointJournal(
                "required journal is absent",
            ));
        }
        // A journal without its marker was created before the marker became
        // durable. The checkpoint is still self-contained, so discard the
        // uncommitted sidecar and begin again.
        Self::remove_if_present(&journal)?;
        let mut compact_job = job.clone();
        let outputs = compact_job.outputs.take().unwrap_or_default();
        let partial_output_ids = compact_job
            .checkpoint
            .as_mut()
            .and_then(|checkpoint| checkpoint.partial_output_ids.take())
            .unwrap_or_default();
        let completed_input_ids = compact_job
            .checkpoint
            .as_mut()
            .and_then(|checkpoint| checkpoint.completed_input_ids.take())
            .unwrap_or_default();
        compact_job.outputs = Some(Vec::new());
        if let Some(checkpoint) = compact_job.checkpoint.as_mut() {
            checkpoint.partial_output_ids = Some(Vec::new());
            checkpoint.completed_input_ids = Some(Vec::new());
        }
        let header = ScanJournalRecord::Header {
            version: SCAN_JOURNAL_VERSION,
            job: Box::new(compact_job),
        };
        let mut bytes = serde_json::to_vec(&header).map_err(JobExecutionError::Serialize)?;
        bytes.push(b'\n');
        for index in 0..outputs
            .len()
            .max(partial_output_ids.len())
            .max(completed_input_ids.len())
        {
            let seed = ScanJournalRecord::Seed {
                version: SCAN_JOURNAL_VERSION,
                job_id: job.job_id.clone(),
                output: outputs.get(index).cloned(),
                partial_output_id: partial_output_ids.get(index).cloned(),
                completed_input_id: completed_input_ids.get(index).cloned(),
            };
            bytes.extend(serde_json::to_vec(&seed).map_err(JobExecutionError::Serialize)?);
            bytes.push(b'\n');
        }
        let temporary = self.sidecar(".scan-journal.tmp");
        write_durable(&temporary, &bytes)?;
        fs::rename(temporary, &journal).map_err(JobExecutionError::Checkpoint)?;
        sync_parent_directory(&journal)?;
        let marker_temporary = self.sidecar(".scan-journal-required.tmp");
        write_durable(&marker_temporary, job.job_id.as_bytes())?;
        fs::rename(marker_temporary, &marker).map_err(JobExecutionError::Checkpoint)?;
        sync_parent_directory(&marker)?;
        self.write_compact_progress(job)
    }

    /// Append one durable scan delta, then refresh the small progress snapshot.
    fn save_scan_progress(
        &self,
        job: &mut JobSpec,
        delta: ScanOutputDelta,
    ) -> Result<(), JobExecutionError> {
        let checkpoint = job
            .checkpoint
            .as_ref()
            .ok_or(JobExecutionError::CheckpointJournal("checkpoint is absent"))?;
        let sequence = progress_sequence(job)?;
        self.append_journal(&ScanJournalRecord::Progress {
            version: SCAN_JOURNAL_VERSION,
            job_id: job.job_id.clone(),
            sequence,
            state: Box::new(job.state.clone()),
            cursor: checkpoint.cursor.clone(),
            checkpoint_updated_at: checkpoint.updated_at.clone(),
            output: delta.output,
            replaced_output_id: delta.replaced_output_id,
            partial_output_id: delta.partial_output_id,
            replaced_partial_output_id: delta.replaced_partial_output_id,
        })?;
        self.write_compact_progress(job)
    }

    /// Keep the contract-shaped checkpoint and live progress cheap to poll.
    /// Moving the two growing vectors out avoids even an O(n) clone here.
    fn write_compact_progress(&self, job: &mut JobSpec) -> Result<(), JobExecutionError> {
        let outputs = job.outputs.take();
        let partial_output_ids = job
            .checkpoint
            .as_mut()
            .and_then(|checkpoint| checkpoint.partial_output_ids.take());
        let completed_input_ids = job
            .checkpoint
            .as_mut()
            .and_then(|checkpoint| checkpoint.completed_input_ids.take());
        job.outputs = Some(Vec::new());
        if let Some(checkpoint) = job.checkpoint.as_mut() {
            checkpoint.partial_output_ids = Some(Vec::new());
            checkpoint.completed_input_ids = Some(Vec::new());
        }
        let serialized = serde_json::to_vec_pretty(job);
        job.outputs = outputs;
        if let Some(checkpoint) = job.checkpoint.as_mut() {
            checkpoint.partial_output_ids = partial_output_ids;
            checkpoint.completed_input_ids = completed_input_ids;
        }
        let bytes = serialized.map_err(JobExecutionError::Serialize)?;
        self.write_snapshot_bytes(&bytes)
    }

    /// Rebuild a full worker JobSpec from a compact snapshot plus its journal.
    /// A truncated final record was never durable and is discarded; malformed
    /// complete records fail closed.
    pub fn recover(&self, requested: &mut JobSpec) -> Result<(), JobExecutionError> {
        let marker = self.journal_required_path();
        let journal_path = self.journal_path();
        if !marker.is_file() {
            Self::remove_if_present(&journal_path)?;
            return Ok(());
        }
        if !journal_path.is_file() {
            return Err(JobExecutionError::CheckpointJournal(
                "required journal is absent",
            ));
        }

        let snapshot_bytes = fs::read(&self.path).ok();
        let journal_bytes = fs::read(&journal_path).map_err(JobExecutionError::Checkpoint)?;
        let mut parsed = Vec::new();
        let mut offset = 0_usize;
        for chunk in journal_bytes.split_inclusive(|byte| *byte == b'\n') {
            if !chunk.ends_with(b"\n") {
                break;
            }
            let start = offset;
            offset += chunk.len();
            let record = serde_json::from_slice::<ScanJournalRecord>(&chunk[..chunk.len() - 1])
                .map_err(|_| {
                    JobExecutionError::CheckpointJournal("complete journal record is invalid")
                })?;
            parsed.push((start, record));
        }
        if offset < journal_bytes.len() {
            truncate_durable(&journal_path, offset as u64)?;
        }

        let Some((_, ScanJournalRecord::Header { version, job })) = parsed.first() else {
            return Err(JobExecutionError::CheckpointJournal(
                "journal header is absent",
            ));
        };
        if *version != SCAN_JOURNAL_VERSION || job.job_id != requested.job_id {
            return Err(JobExecutionError::CheckpointJournal(
                "journal identity or version disagrees",
            ));
        }

        if let Some((
            commit_index,
            (
                commit_start,
                ScanJournalRecord::Commit {
                    version,
                    job_id,
                    snapshot_blake3,
                },
            ),
        )) = parsed
            .iter()
            .enumerate()
            .find(|(_, (_, record))| matches!(record, ScanJournalRecord::Commit { .. }))
        {
            if commit_index + 1 != parsed.len()
                || *version != SCAN_JOURNAL_VERSION
                || job_id != &requested.job_id
            {
                return Err(JobExecutionError::CheckpointJournal(
                    "journal commit is invalid",
                ));
            }
            if snapshot_bytes
                .as_ref()
                .is_some_and(|bytes| blake3::hash(bytes).to_hex().as_str() == snapshot_blake3)
            {
                let snapshot: JobSpec = serde_json::from_slice(snapshot_bytes.as_ref().unwrap())
                    .map_err(|_| {
                        JobExecutionError::CheckpointJournal("committed snapshot is invalid")
                    })?;
                if snapshot.job_id != requested.job_id {
                    return Err(JobExecutionError::CheckpointJournal(
                        "committed snapshot identity disagrees",
                    ));
                }
                validate_recovered_manifest(&snapshot)?;
                *requested = snapshot;
                Self::remove_if_present(&marker)?;
                Self::remove_if_present(&journal_path)?;
                return Ok(());
            }
            // The commit reached the journal but its snapshot rename did not.
            // Remove the unmatched commit so new progress can append cleanly.
            truncate_durable(&journal_path, *commit_start as u64)?;
            parsed.truncate(commit_index);
        }

        let ScanJournalRecord::Header { job, .. } = &parsed[0].1 else {
            unreachable!("header checked above")
        };
        let mut recovered = (**job).clone();
        let mut record_index = 1;
        while let Some((
            _,
            ScanJournalRecord::Seed {
                version,
                job_id,
                output,
                partial_output_id,
                completed_input_id,
            },
        )) = parsed.get(record_index)
        {
            if *version != SCAN_JOURNAL_VERSION || job_id != &recovered.job_id {
                return Err(JobExecutionError::CheckpointJournal(
                    "journal seed identity or version disagrees",
                ));
            }
            if let Some(output) = output {
                let outputs = recovered.outputs.get_or_insert_with(Vec::new);
                if outputs.iter().any(|existing| existing.id == output.id) {
                    return Err(JobExecutionError::CheckpointJournal(
                        "journal seed contains a duplicate output",
                    ));
                }
                outputs.push(output.clone());
            }
            if let Some(media_id) = partial_output_id {
                let ids = recovered
                    .checkpoint
                    .as_mut()
                    .ok_or(JobExecutionError::CheckpointJournal("checkpoint is absent"))?
                    .partial_output_ids
                    .get_or_insert_with(Vec::new);
                if ids.iter().any(|existing| existing == media_id) {
                    return Err(JobExecutionError::CheckpointJournal(
                        "journal seed contains a duplicate partial output",
                    ));
                }
                ids.push(media_id.clone());
            }
            if let Some(input_id) = completed_input_id {
                let ids = recovered
                    .checkpoint
                    .as_mut()
                    .ok_or(JobExecutionError::CheckpointJournal("checkpoint is absent"))?
                    .completed_input_ids
                    .get_or_insert_with(Vec::new);
                if ids.iter().any(|existing| existing == input_id) {
                    return Err(JobExecutionError::CheckpointJournal(
                        "journal seed contains a duplicate completed input",
                    ));
                }
                ids.push(input_id.clone());
            }
            record_index += 1;
        }
        let mut expected = progress_sequence(&recovered)? + 1;
        let mut previous_progress: Option<&ScanJournalRecord> = None;
        for (_, record) in parsed.iter().skip(record_index) {
            let ScanJournalRecord::Progress {
                version,
                job_id,
                sequence,
                state,
                cursor,
                checkpoint_updated_at,
                output,
                replaced_output_id,
                partial_output_id,
                replaced_partial_output_id,
            } = record
            else {
                return Err(JobExecutionError::CheckpointJournal(
                    "unexpected journal record",
                ));
            };
            if *version != SCAN_JOURNAL_VERSION || job_id != &recovered.job_id {
                return Err(JobExecutionError::CheckpointJournal(
                    "journal progress identity or version disagrees",
                ));
            }
            if sequence.checked_add(1) == Some(expected) {
                if previous_progress == Some(record) {
                    continue;
                }
                return Err(JobExecutionError::CheckpointJournal(
                    "duplicate progress sequence conflicts with its prior delta",
                ));
            }
            if *sequence != expected || progress_units(&state.progress)? != *sequence {
                return Err(JobExecutionError::CheckpointJournal(
                    "journal progress sequence has a gap",
                ));
            }
            if replaced_output_id.is_some() && output.is_none() {
                return Err(JobExecutionError::CheckpointJournal(
                    "journal output replacement has no new artifact",
                ));
            }
            if let Some(output) = output {
                let outputs = recovered.outputs.get_or_insert_with(Vec::new);
                if let Some(replaced_id) = replaced_output_id {
                    if replaced_id == &output.id {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal output replacement repeats its artifact id",
                        ));
                    }
                    let Some(index) = outputs.iter().position(|item| item.id == *replaced_id)
                    else {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal output replacement target is absent",
                        ));
                    };
                    if outputs[index].kind != output.kind || outputs[index].path != output.path {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal output replacement changes logical record",
                        ));
                    }
                    outputs.remove(index);
                    if outputs.iter().any(|item| item.id == output.id) {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal output replacement duplicates an artifact",
                        ));
                    }
                    outputs.push(output.clone());
                } else if let Some(existing) = outputs.iter().find(|item| item.id == output.id) {
                    if existing != output {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal output conflicts with its manifest",
                        ));
                    }
                } else {
                    outputs.push(output.clone());
                }
            }
            if replaced_partial_output_id.is_some() && partial_output_id.is_none() {
                return Err(JobExecutionError::CheckpointJournal(
                    "journal partial-output replacement has no new artifact id",
                ));
            }
            if let Some(artifact_id) = partial_output_id {
                let ids = recovered
                    .checkpoint
                    .as_mut()
                    .ok_or(JobExecutionError::CheckpointJournal("checkpoint is absent"))?
                    .partial_output_ids
                    .get_or_insert_with(Vec::new);
                if let Some(replaced_id) = replaced_partial_output_id {
                    if replaced_output_id.as_deref() != Some(replaced_id.as_str()) {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal output and partial-output replacements disagree",
                        ));
                    }
                    if output.as_ref().map(|item| item.id.as_str()) != Some(artifact_id.as_str()) {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal partial-output replacement disagrees with artifact",
                        ));
                    }
                    let Some(index) = ids.iter().position(|id| id == replaced_id) else {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal partial-output replacement target is absent",
                        ));
                    };
                    ids.remove(index);
                    if ids.iter().any(|id| id == artifact_id) {
                        return Err(JobExecutionError::CheckpointJournal(
                            "journal partial-output replacement duplicates an artifact",
                        ));
                    }
                    ids.push(artifact_id.clone());
                } else if !ids.iter().any(|id| id == artifact_id) {
                    ids.push(artifact_id.clone());
                }
            }
            let checkpoint = recovered
                .checkpoint
                .as_mut()
                .ok_or(JobExecutionError::CheckpointJournal("checkpoint is absent"))?;
            checkpoint.cursor = cursor.clone();
            checkpoint.checkpoint_version = Some(CHECKPOINT_VERSION);
            checkpoint.updated_at = checkpoint_updated_at.clone();
            recovered.state = (**state).clone();
            expected += 1;
            previous_progress = Some(record);
        }
        validate_recovered_manifest(&recovered)?;
        *requested = recovered;
        Ok(())
    }
}

fn write_durable(path: &Path, bytes: &[u8]) -> Result<(), JobExecutionError> {
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(path)
        .map_err(JobExecutionError::Checkpoint)?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(JobExecutionError::Checkpoint)
}

fn truncate_durable(path: &Path, length: u64) -> Result<(), JobExecutionError> {
    let file = OpenOptions::new()
        .write(true)
        .open(path)
        .map_err(JobExecutionError::Checkpoint)?;
    file.set_len(length)
        .and_then(|()| file.sync_data())
        .map_err(JobExecutionError::Checkpoint)
}

#[cfg(unix)]
fn sync_parent_directory(path: &Path) -> Result<(), JobExecutionError> {
    let parent = path.parent().ok_or(JobExecutionError::CheckpointJournal(
        "checkpoint parent is absent",
    ))?;
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(JobExecutionError::Checkpoint)
}

// Rust cannot open a directory with `File::open` on Windows. The file itself
// is still flushed before `rename`; Windows rename durability is delegated to
// the platform filesystem until Rust exposes a portable directory flush.
#[cfg(not(unix))]
fn sync_parent_directory(_path: &Path) -> Result<(), JobExecutionError> {
    Ok(())
}

fn progress_units(progress: &Option<Progress>) -> Result<u64, JobExecutionError> {
    let Some(units) = progress.as_ref().map(|progress| progress.units_done) else {
        return Ok(0);
    };
    if !units.is_finite() || units < 0.0 || units.fract() != 0.0 || units > u64::MAX as f64 {
        return Err(JobExecutionError::CheckpointJournal(
            "scan progress is not an integral file count",
        ));
    }
    Ok(units as u64)
}

fn progress_sequence(job: &JobSpec) -> Result<u64, JobExecutionError> {
    progress_units(&job.state.progress)
}

fn validate_journal_output(output: &JobOutput) -> Result<String, JobExecutionError> {
    if output.kind != JobOutputKind::MediaRecord {
        return Err(JobExecutionError::CheckpointJournal(
            "scan journal contains a non-media output",
        ));
    }
    let path = output
        .path
        .as_deref()
        .ok_or(JobExecutionError::PersistedRecordIntegrity)?;
    let bytes = fs::read(path).map_err(|_| JobExecutionError::PersistedRecordUnreadable)?;
    if output.byte_size != Some(bytes.len() as i64) {
        return Err(JobExecutionError::PersistedRecordIntegrity);
    }
    let record: memory_engine_contracts::MediaRecord =
        serde_json::from_slice(&bytes).map_err(|_| JobExecutionError::PersistedRecordUnreadable)?;
    if blake3::hash(&bytes).to_hex().as_str() != output.id {
        return Err(JobExecutionError::PersistedRecordIntegrity);
    }
    Ok(record.media_id)
}

fn validate_recovered_manifest(job: &JobSpec) -> Result<(), JobExecutionError> {
    let mut output_ids = std::collections::BTreeSet::new();
    let mut media_ids = std::collections::BTreeSet::new();
    for output in job.outputs.as_deref().unwrap_or_default() {
        let media_id = validate_journal_output(output)?;
        if !output_ids.insert(output.id.as_str()) {
            return Err(JobExecutionError::CheckpointJournal(
                "recovered manifest contains a duplicate output",
            ));
        }
        if !media_ids.insert(media_id) {
            return Err(JobExecutionError::CheckpointJournal(
                "recovered manifest contains duplicate source media",
            ));
        }
    }
    let mut partial_ids = std::collections::BTreeSet::new();
    for media_id in job
        .checkpoint
        .as_ref()
        .and_then(|checkpoint| checkpoint.partial_output_ids.as_deref())
        .unwrap_or_default()
    {
        if !partial_ids.insert(media_id.as_str()) {
            return Err(JobExecutionError::CheckpointJournal(
                "recovered manifest contains a duplicate partial output",
            ));
        }
        if !output_ids.contains(media_id.as_str()) {
            return Err(JobExecutionError::CheckpointJournal(
                "partial output is absent from the recovered manifest",
            ));
        }
    }
    let mut completed_ids = std::collections::BTreeSet::new();
    for input_id in job
        .checkpoint
        .as_ref()
        .and_then(|checkpoint| checkpoint.completed_input_ids.as_deref())
        .unwrap_or_default()
    {
        if !completed_ids.insert(input_id.as_str()) {
            return Err(JobExecutionError::CheckpointJournal(
                "recovered manifest contains a duplicate completed input",
            ));
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ScanReport {
    pub processed: usize,
    pub resumed_skips: usize,
    pub assemblies_created: usize,
    pub span_members_updated: usize,
    pub capability_retries: usize,
    pub capability_retries_remaining: usize,
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
///
/// Every invocation runs up to three passes, and the order between them is a contract:
///
/// 1. **Capability retry** re-ingests records that failed only because a decoder was
///    unavailable last time (`retry_capability_blocked_outputs`).
/// 2. **Scan** ingests newly discovered files.
/// 3. **Span reconciliation** derives GoPro chapter spans from the persisted records
///    (`reconcile_gopro_outputs`).
///
/// Reconciliation runs last, and only once no capability retry is still outstanding,
/// because it *derives* span state from whatever the records currently say. A retry
/// rewrites a record from scratch, so running it after reconciliation would leave the
/// just-published assembly describing a record state that no longer exists. Deferring
/// reconciliation while `capability_retries_remaining > 0` also stops us publishing an
/// assembly built from a half-repaired library: the job stays `pending`, the next batch
/// finishes the retries, and reconciliation then rebuilds the span from the repaired
/// records. Reconciliation is unconditional and idempotent once reached, so a span whose
/// member was touched by a retry is always rebuilt in the same invocation.
///
/// The two passes cannot collide today — `gopro::build` only groups `mp4` videos and
/// `needs_capability_retry` only fires for HEIF-family stills — so this ordering is a
/// guarantee for when that stops being true (an HEVC or RAW capability retry), not a fix
/// for a live bug. The durable half of the invariant is independent of ordering:
/// `persist_record` re-merges the on-disk `span` when the incoming record has none, so a
/// retry's from-scratch re-ingest can never erase span membership.
pub fn execute_scan_batch(
    job: &mut JobSpec,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
    max_files: Option<usize>,
) -> Result<ScanReport, JobExecutionError> {
    validate_job(job)?;
    validate_media_outputs(job)?;
    let mut report =
        retry_capability_blocked_outputs(job, output_dir, checkpoint_store, max_files)?;
    if job.state.status == JobStateStatus::Completed {
        report.complete = report.capability_retries_remaining == 0;
        if report.complete {
            reconcile_gopro_outputs(job, output_dir, checkpoint_store, &mut report)?;
        } else {
            // Retries were deferred by the batch budget. Reopen the job so a scheduler
            // comes back, and leave the spans untouched until the library is repaired.
            job.state.status = JobStateStatus::Pending;
            checkpoint_store.save(job)?;
        }
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
    report.resumed_skips = resumed_skips;
    report.issues.extend(issues);
    let total = entries.len();
    let mut newly_processed = 0;
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
    checkpoint_store.begin_scan(job)?;

    for entry in entries.iter().skip(resumed_skips) {
        if max_files.is_some_and(|limit| report.processed >= limit) {
            break;
        }
        let mut journal_delta = ScanOutputDelta::default();
        match ingest_file(&entry.path, output_dir) {
            Ok(ingested) => {
                bytes_processed += ingested.record.byte_size;
                if matches!(
                    ingested.record.processing.state,
                    memory_engine_contracts::ProcessingStateState::Quarantined
                ) {
                    report.quarantined += 1;
                }
                let (record_path, artifact_bytes, artifact_id) =
                    persist_record(&ingested.record, output_dir)?;
                journal_delta = upsert_output(
                    job,
                    &ingested.record.media_id,
                    &record_path,
                    artifact_bytes,
                    &artifact_id,
                );
                if update_partial_outputs(job, &artifact_id) {
                    journal_delta.partial_output_id = Some(artifact_id);
                }
            }
            Err(error) => report.issues.push(ScanIssue {
                code: memory_engine_contracts::JobErrorCode::FileUnreadable,
                message: error.to_string(),
            }),
        }
        report.processed += 1;
        newly_processed += 1;
        update_progress(job, resumed_skips + newly_processed, total, bytes_processed);
        update_cursor(job, &entry.cursor);
        checkpoint_store.save_scan_progress(job, journal_delta)?;
    }

    // `newly_processed`, not `report.processed`: the latter also counts capability
    // retries, which are not entries in this scan's cursor space.
    report.complete =
        resumed_skips + newly_processed >= total && report.capability_retries_remaining == 0;
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

fn retry_capability_blocked_outputs(
    job: &mut JobSpec,
    output_dir: &Path,
    checkpoint_store: &CheckpointStore,
    max_files: Option<usize>,
) -> Result<ScanReport, JobExecutionError> {
    let record_paths = job
        .outputs
        .as_deref()
        .unwrap_or_default()
        .iter()
        .filter(|output| output.kind == JobOutputKind::MediaRecord)
        .filter_map(|output| output.path.as_deref())
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    let mut report = ScanReport::default();

    for record_path in record_paths {
        let Ok(bytes) = fs::read(&record_path) else {
            continue;
        };
        let Ok(record) = serde_json::from_slice::<memory_engine_contracts::MediaRecord>(&bytes)
        else {
            continue;
        };
        // A retry re-reads a file. Virtual assemblies have no file behind them and no
        // present sources; they are derived, and reconciliation is the only pass that
        // writes them. Skipping them here keeps that ownership boundary explicit.
        if record.asset_kind != memory_engine_contracts::MediaRecordAssetKind::PhysicalFile {
            continue;
        }
        if !needs_capability_retry(&record) {
            continue;
        }
        if max_files.is_some_and(|limit| report.processed >= limit) {
            report.capability_retries_remaining += 1;
            continue;
        }

        let mut retried = None;
        let mut last_error = None;
        for source in record.sources.iter().filter(|source| source.present) {
            match ingest_file(Path::new(&source.path), output_dir) {
                Ok(ingested) if ingested.record.media_id == record.media_id => {
                    retried = Some(ingested);
                    break;
                }
                Ok(_) => {
                    last_error = Some("capability retry skipped because source bytes changed");
                }
                Err(_) => {
                    last_error = Some("capability retry could not read any known source");
                }
            }
        }

        let Some(ingested) = retried else {
            report.issues.push(ScanIssue {
                code: memory_engine_contracts::JobErrorCode::FileUnreadable,
                message: last_error
                    .unwrap_or("capability retry has no present source")
                    .to_owned(),
            });
            continue;
        };
        if ingested.record.processing.state
            == memory_engine_contracts::ProcessingStateState::Quarantined
        {
            report.quarantined += 1;
        }
        // `persist_record` merges the on-disk `span` back in, so re-ingesting a record
        // that reconciliation had already made a span member does not drop membership.
        let (persisted_path, artifact_bytes, artifact_id) =
            persist_record(&ingested.record, output_dir)?;
        upsert_output(
            job,
            &ingested.record.media_id,
            &persisted_path,
            artifact_bytes,
            &artifact_id,
        );
        update_partial_outputs(job, &artifact_id);
        report.processed += 1;
        report.capability_retries += 1;
        checkpoint_store.save(job)?;
    }

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
        records.push(loaded.map_err(|()| JobExecutionError::PersistedRecordUnreadable)?);
    }
    let built = gopro::build(&records);
    report
        .issues
        .extend(built.issues.into_iter().map(|message| ScanIssue {
            code: memory_engine_contracts::JobErrorCode::DependencyFailed,
            message,
        }));

    for member in built.members {
        let (path, artifact_bytes, artifact_id) = persist_record(&member, output_dir)?;
        upsert_output(job, &member.media_id, &path, artifact_bytes, &artifact_id);
        update_partial_outputs(job, &artifact_id);
        report.span_members_updated += 1;
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
        let (path, artifact_bytes, artifact_id) = if existing.as_ref() == Some(&assembly) {
            let bytes = fs::read(&path).map_err(JobExecutionError::Checkpoint)?;
            let byte_size = bytes.len() as i64;
            let artifact_id = blake3::hash(&bytes).to_hex().to_string();
            (path, byte_size, artifact_id)
        } else {
            persist_record(&assembly, output_dir)?
        };
        let output_changed =
            upsert_output(job, &assembly.media_id, &path, artifact_bytes, &artifact_id)
                .output
                .is_some();
        if !existed {
            report.assemblies_created += 1;
        }
        if !existed || output_changed || existing.as_ref() != Some(&assembly) {
            update_partial_outputs(job, &artifact_id);
        }
    }
    // Reconciliation writes are content-addressed and idempotent. One checkpoint
    // after the batch avoids serialising a 100k-output JobSpec for every member;
    // a crash before this save simply replays the same deterministic upserts.
    checkpoint_store.save(job)?;
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

/// Verify every persisted MediaRecord against the JobOutput contract.
///
/// Older ingest checkpoints used `MediaRecord.media_id` as the output id and
/// therefore never authenticated the serialized record. They cannot be
/// migrated safely: doing so would bless whatever bytes happen to occupy the
/// old path. Any disagreement is corruption, not a cache hit, and must fail
/// before a completed job is trusted or a cursor advances.
fn validate_media_outputs(job: &JobSpec) -> Result<(), JobExecutionError> {
    let mut artifact_ids = BTreeSet::new();
    let mut media_ids = BTreeSet::new();

    for output in job
        .outputs
        .as_deref()
        .unwrap_or_default()
        .iter()
        .filter(|output| output.kind == JobOutputKind::MediaRecord)
    {
        let path = output
            .path
            .as_deref()
            .ok_or(JobExecutionError::PersistedRecordIntegrity)?;
        let bytes = fs::read(path).map_err(|_| JobExecutionError::PersistedRecordUnreadable)?;
        if output.byte_size != Some(bytes.len() as i64) {
            return Err(JobExecutionError::PersistedRecordIntegrity);
        }
        let record: memory_engine_contracts::MediaRecord = serde_json::from_slice(&bytes)
            .map_err(|_| JobExecutionError::PersistedRecordUnreadable)?;
        if !media_ids.insert(record.media_id.clone()) {
            return Err(JobExecutionError::PersistedRecordIntegrity);
        }
        let artifact_id = blake3::hash(&bytes).to_hex().to_string();
        if output.id != artifact_id {
            return Err(JobExecutionError::PersistedRecordIntegrity);
        }
        if !artifact_ids.insert(artifact_id) {
            return Err(JobExecutionError::PersistedRecordIntegrity);
        }
    }

    if let Some(partial_ids) = job
        .checkpoint
        .as_ref()
        .and_then(|checkpoint| checkpoint.partial_output_ids.as_ref())
    {
        let mut seen = BTreeSet::new();
        for partial_id in partial_ids {
            if !artifact_ids.contains(partial_id) || !seen.insert(partial_id.clone()) {
                return Err(JobExecutionError::PersistedRecordIntegrity);
            }
        }
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

fn update_partial_outputs(job: &mut JobSpec, media_id: &str) -> bool {
    let checkpoint = job.checkpoint.as_mut().expect("validated checkpoint");
    let ids = checkpoint.partial_output_ids.get_or_insert_with(Vec::new);
    if ids.iter().any(|id| id == media_id) {
        return false;
    }
    ids.push(media_id.to_owned());
    true
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
) -> Result<(PathBuf, i64, String), JobExecutionError> {
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
            if merged.span.is_none() {
                merged.span = existing.span.clone();
            }
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
    let artifact_id = blake3::hash(&bytes).to_hex().to_string();
    atomic_write(&path, &bytes)?;
    Ok((path, bytes.len() as i64, artifact_id))
}

fn record_path(output_dir: &Path, media_id: &str) -> PathBuf {
    output_dir
        .join("records")
        .join(&media_id[..2])
        .join(&media_id[2..4])
        .join(format!("{media_id}.json"))
}

/// Point the `media_record` output for `media_id` at `path`, describing the exact
/// manifest replacement for the journal. Both the capability-retry and the span-
/// reconciliation pass go through here, so it has to be a no-op when the record is
/// already recorded identically — that keeps a repeat reconciliation from churning
/// `produced_at`.
fn upsert_output(
    job: &mut JobSpec,
    media_id: &str,
    path: &Path,
    byte_size: i64,
    artifact_id: &str,
) -> ScanOutputDelta {
    let path = path.to_string_lossy().into_owned();
    let outputs = job.outputs.get_or_insert_with(Vec::new);
    if let Some(index) = outputs.iter_mut().position(|output| {
        output.kind == JobOutputKind::MediaRecord
            && (output.path.as_deref() == Some(path.as_str()) || output.id == media_id)
    }) {
        let old_id = outputs[index].id.clone();
        if old_id == artifact_id
            && outputs[index].path.as_deref() == Some(path.as_str())
            && outputs[index].byte_size == Some(byte_size)
        {
            return ScanOutputDelta::default();
        }
        let output = &mut outputs[index];
        output.id = artifact_id.to_owned();
        output.path = Some(path);
        output.byte_size = Some(byte_size);
        output.produced_at = Some(Utc::now().to_rfc3339());
        let output = output.clone();
        let replaced_output_id = (old_id != artifact_id).then_some(old_id.clone());
        let replaced_partial_output_id =
            replace_partial_output_id(job, &old_id, artifact_id).then_some(old_id);
        return ScanOutputDelta {
            output: Some(output),
            replaced_output_id,
            partial_output_id: replaced_partial_output_id
                .as_ref()
                .map(|_| artifact_id.to_owned()),
            replaced_partial_output_id,
        };
    }
    let output = JobOutput {
        kind: JobOutputKind::MediaRecord,
        id: artifact_id.to_owned(),
        path: Some(path),
        byte_size: Some(byte_size),
        produced_at: Some(Utc::now().to_rfc3339()),
    };
    outputs.push(output.clone());
    ScanOutputDelta {
        output: Some(output),
        ..ScanOutputDelta::default()
    }
}

fn replace_partial_output_id(job: &mut JobSpec, old_id: &str, new_id: &str) -> bool {
    if old_id == new_id {
        return false;
    }
    let Some(ids) = job
        .checkpoint
        .as_mut()
        .and_then(|checkpoint| checkpoint.partial_output_ids.as_mut())
    else {
        return false;
    };
    let mut replaced = false;
    for id in ids.iter_mut().filter(|id| id.as_str() == old_id) {
        *id = new_id.to_owned();
        replaced = true;
    }
    ids.sort();
    ids.dedup();
    replaced
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
                "source_paths": [source.clone()],
                "source_locator_digest": locator.clone()
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
        let output_ids = job
            .outputs
            .as_ref()
            .unwrap()
            .iter()
            .map(|item| {
                let bytes = fs::read(item.path.as_ref().unwrap()).expect("record artifact");
                assert_eq!(item.byte_size, Some(bytes.len() as i64));
                assert_eq!(item.id, blake3::hash(&bytes).to_hex().to_string());
                item.id.clone()
            })
            .collect::<BTreeSet<_>>();
        let partial_ids = job
            .checkpoint
            .as_ref()
            .and_then(|checkpoint| checkpoint.partial_output_ids.as_ref())
            .unwrap()
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        assert_eq!(partial_ids, output_ids);
    }

    #[test]
    fn completed_scan_rejects_same_size_valid_json_record_corruption() {
        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");
        fs::write(source.join("a.jpg"), []).expect("hostile fixture");
        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let mut job: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "acacacacacacacacacacacacacacacacacacacacacacacacacacacacacacacac",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:integrity-test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("job contract");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));
        execute_scan(&mut job, &output, &store).expect("initial scan");
        let record_path = PathBuf::from(
            job.outputs.as_ref().unwrap()[0]
                .path
                .as_ref()
                .expect("record path"),
        );
        let bytes = fs::read(&record_path).expect("record bytes");
        let text = String::from_utf8(bytes).expect("record JSON is UTF-8");
        let original: memory_engine_contracts::MediaRecord =
            serde_json::from_str(&text).expect("original MediaRecord JSON");
        assert!(
            text.contains("a.jpg"),
            "fixture path is present in the record"
        );
        let changed = text.replacen("a.jpg", "z.jpg", 1);
        assert_eq!(changed.len(), text.len(), "mutation preserves byte size");
        let parsed: memory_engine_contracts::MediaRecord =
            serde_json::from_str(&changed).expect("mutation remains valid MediaRecord JSON");
        assert_eq!(parsed.media_id, original.media_id);
        fs::write(&record_path, changed).expect("replace record with same-size valid JSON");

        let error = execute_scan(&mut job, &output, &store).expect_err("corruption must fail");
        assert!(matches!(error, JobExecutionError::PersistedRecordIntegrity));
        assert_eq!(job.state.status, JobStateStatus::Completed);
    }

    #[test]
    fn legacy_media_ids_are_rejected_before_completed_reuse() {
        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");
        ImageBuffer::from_pixel(8, 8, Rgb([9_u8, 8, 7]))
            .save(source.join("legacy.jpg"))
            .expect("fixture image");
        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let mut job: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "adadadadadadadadadadadadadadadadadadadadadadadadadadadadadadadad",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:legacy-output-id-test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("job contract");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));
        execute_scan(&mut job, &output, &store).expect("initial scan");
        let output_path = PathBuf::from(job.outputs.as_ref().unwrap()[0].path.as_ref().unwrap());
        let bytes = fs::read(&output_path).expect("record bytes");
        let record: memory_engine_contracts::MediaRecord =
            serde_json::from_slice(&bytes).expect("MediaRecord");
        let artifact_id = blake3::hash(&bytes).to_hex().to_string();
        assert_ne!(record.media_id, artifact_id);

        job.outputs.as_mut().unwrap()[0].id = record.media_id.clone();
        job.checkpoint.as_mut().unwrap().partial_output_ids = Some(Vec::new());
        store.save(&job).expect("persist legacy checkpoint shape");

        let error = execute_scan(&mut job, &output, &store)
            .expect_err("an unauthenticated legacy artifact must not be blessed");
        assert!(matches!(error, JobExecutionError::PersistedRecordIntegrity));
        assert_eq!(job.outputs.as_ref().unwrap()[0].id, record.media_id);
        assert_ne!(job.outputs.as_ref().unwrap()[0].id, artifact_id);
    }

    #[test]
    fn legacy_full_checkpoint_migrates_to_seed_deltas_and_recovers_exactly() {
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
            "job_id": "abababababababababababababababababababababababababababababababab",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:legacy-checkpoint-test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("job contract");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));
        execute_scan_batch(&mut job, &output, &store, Some(1)).expect("legacy first batch");
        job.checkpoint.as_mut().unwrap().completed_input_ids = Some(vec![
            "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd".to_owned(),
        ]);
        let expected_outputs = job.outputs.clone();
        let expected_partial_ids = job
            .checkpoint
            .as_ref()
            .and_then(|checkpoint| checkpoint.partial_output_ids.clone());
        let expected_completed_ids = job
            .checkpoint
            .as_ref()
            .and_then(|checkpoint| checkpoint.completed_input_ids.clone());
        assert_eq!(expected_outputs.as_ref().map(Vec::len), Some(1));

        store
            .begin_scan(&mut job)
            .expect("migrate legacy checkpoint");

        let records = fs::read_to_string(store.journal_path())
            .expect("journal")
            .lines()
            .map(|line| serde_json::from_str::<ScanJournalRecord>(line).expect("journal record"))
            .collect::<Vec<_>>();
        let ScanJournalRecord::Header { job: header, .. } = &records[0] else {
            panic!("first record is not a header");
        };
        assert_eq!(header.outputs.as_ref().map(Vec::len), Some(0));
        assert_eq!(
            header
                .checkpoint
                .as_ref()
                .and_then(|checkpoint| checkpoint.partial_output_ids.as_ref())
                .map(Vec::len),
            Some(0),
            "the header never embeds a legacy manifest"
        );
        assert_eq!(
            header
                .checkpoint
                .as_ref()
                .and_then(|checkpoint| checkpoint.completed_input_ids.as_ref())
                .map(Vec::len),
            Some(0)
        );
        assert_eq!(
            records
                .iter()
                .filter(|record| matches!(record, ScanJournalRecord::Seed { .. }))
                .count(),
            1,
            "the old manifest migrates once as per-output seed deltas"
        );
        let compact: JobSpec = serde_json::from_slice(
            &fs::read(&store.path).expect("compact contract-shaped checkpoint"),
        )
        .expect("compact JobSpec");
        assert_eq!(compact.outputs.as_ref().map(Vec::len), Some(0));

        let mut recovered = compact;
        store.recover(&mut recovered).expect("recover migrated job");
        assert_eq!(recovered.outputs, expected_outputs);
        assert_eq!(
            recovered
                .checkpoint
                .as_ref()
                .and_then(|checkpoint| checkpoint.partial_output_ids.clone()),
            expected_partial_ids
        );
        assert_eq!(
            recovered
                .checkpoint
                .as_ref()
                .and_then(|checkpoint| checkpoint.completed_input_ids.clone()),
            expected_completed_ids
        );
    }

    #[test]
    fn journal_recovery_rejects_same_size_valid_json_artifact_corruption() {
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
            "job_id": "aeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeaeae",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:journal-integrity-test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("job contract");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));
        execute_scan_batch(&mut job, &output, &store, Some(1)).expect("first batch");
        store.begin_scan(&mut job).expect("begin journal");

        let artifact = PathBuf::from(job.outputs.as_ref().unwrap()[0].path.as_ref().unwrap());
        let original = fs::read(&artifact).expect("record artifact");
        let changed = String::from_utf8(original.clone())
            .expect("record UTF-8")
            .replacen("a.jpg", "z.jpg", 1);
        assert_eq!(changed.len(), original.len());
        serde_json::from_str::<memory_engine_contracts::MediaRecord>(&changed)
            .expect("still-valid MediaRecord JSON");
        fs::write(&artifact, changed).expect("same-size artifact corruption");

        let mut compact: JobSpec =
            serde_json::from_slice(&fs::read(&store.path).expect("compact checkpoint"))
                .expect("compact JobSpec");
        let error = store
            .recover(&mut compact)
            .expect_err("journal recovery must authenticate record bytes");

        assert!(matches!(error, JobExecutionError::PersistedRecordIntegrity));
        assert_ne!(compact.state.status, JobStateStatus::Completed);
    }

    #[test]
    fn gopro_assembly_uses_a_stable_provisional_id_across_trailing_arrivals() {
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
                "source_paths": [source.clone()],
                "source_locator_digest": locator.clone()
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
        assert_eq!(
            assembly.span.as_ref().unwrap().continuity,
            Some(memory_engine_contracts::SpanContinuity::IncompleteSet)
        );
        assert_eq!(assembly.span.as_ref().unwrap().member_count, None);
        assert!(members.iter().all(|member| member
            .span
            .as_ref()
            .is_some_and(|span| span.offset_in_span.is_none())));
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

        let provisional_id = assembly.media_id.clone();
        let mut bytes = vec![0, 0, 0, 24];
        bytes.extend_from_slice(b"ftypmp42");
        bytes.extend_from_slice(&[3_u8; 64]);
        fs::write(source.join("GH030042.MP4"), bytes).expect("third GoPro chapter");
        let mut rescan: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
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
        .expect("rescan job contract");
        let rescan_store = CheckpointStore::new(directory.path().join("rescan.json"));
        execute_scan(&mut rescan, &output, &rescan_store).expect("rescan with trailing chapter");
        let rescan_records = rescan
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
        let rescanned_assembly = rescan_records
            .iter()
            .find(|record| {
                record.asset_kind == memory_engine_contracts::MediaRecordAssetKind::VirtualAssembly
            })
            .expect("rescanned virtual assembly");
        assert_eq!(rescanned_assembly.media_id, provisional_id);
        assert_eq!(
            rescanned_assembly
                .span
                .as_ref()
                .and_then(|span| span.member_media_ids.as_ref())
                .map(Vec::len),
            Some(3)
        );
        let virtual_records = walkdir::WalkDir::new(output.join("records"))
            .into_iter()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().is_file())
            .filter_map(|entry| fs::read(entry.path()).ok())
            .filter_map(|bytes| {
                serde_json::from_slice::<memory_engine_contracts::MediaRecord>(&bytes).ok()
            })
            .filter(|record| {
                record.asset_kind == memory_engine_contracts::MediaRecordAssetKind::VirtualAssembly
            })
            .count();
        assert_eq!(virtual_records, 1);
    }

    #[test]
    fn unreadable_persisted_record_aborts_reconciliation() {
        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");
        ImageBuffer::from_pixel(8, 8, Rgb([1_u8, 2, 3]))
            .save(source.join("photo.jpg"))
            .expect("fixture image");
        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let mut job: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
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
        execute_scan(&mut job, &output, &store).expect("initial scan");
        let persisted = job.outputs.as_ref().unwrap()[0].path.as_ref().unwrap();
        fs::write(persisted, b"{").expect("corrupt persisted record");

        let replay = execute_scan(&mut job, &output, &store);

        assert!(matches!(
            replay,
            Err(JobExecutionError::PersistedRecordIntegrity)
        ));
        assert_eq!(job.state.status, JobStateStatus::Completed);
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

    #[cfg(target_os = "macos")]
    #[test]
    fn completed_scan_retries_legacy_heic_quarantine_when_capability_appears() {
        use memory_engine_contracts::{
            ErrorInfo, JobStateStatus, ProcessingStateState, StageState, StageStateStatus,
        };

        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");
        let source_path = source.join("IMG_0001.JPG");
        let jpeg_path = directory.path().join("source.jpg");
        ImageBuffer::from_pixel(8, 6, Rgb([30_u8, 60, 120]))
            .save(&jpeg_path)
            .expect("JPEG fixture");
        let sips = std::process::Command::new("sips")
            .arg("-s")
            .arg("format")
            .arg("heic")
            .arg(&jpeg_path)
            .arg("--out")
            .arg(&source_path)
            .output()
            .expect("run sips");
        assert!(
            sips.status.success(),
            "{}",
            String::from_utf8_lossy(&sips.stderr)
        );

        let mut record = ingest_file(&source_path, &output)
            .expect("initial decode")
            .record;
        record.processing.state = ProcessingStateState::Quarantined;
        let legacy_error = ErrorInfo {
            code: "unsupported_codec".to_owned(),
            message: "media processing failed; source details redacted".to_owned(),
            retryable: false,
            occurred_at: Some(Utc::now().to_rfc3339()),
        };
        record.processing.stages.thumbnail = Some(StageState {
            status: StageStateStatus::Failed,
            attempts: Some(1),
            completed_at: Some(Utc::now().to_rfc3339()),
            job_id: None,
            skip_reason: None,
            last_error: Some(legacy_error),
        });
        record.proxies = Some(Vec::new());
        record.image = None;
        record.perceptual = None;
        let (record_path, record_bytes, record_artifact_id) =
            persist_record(&record, &output).expect("legacy record");
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
            "state": {"status": "completed", "attempts": 1},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1},
            "outputs": [{
                "kind": "media_record",
                "id": record_artifact_id,
                "path": record_path,
                "byte_size": record_bytes
            }]
        }))
        .expect("completed job");
        assert_eq!(job.state.status, JobStateStatus::Completed);
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));

        let mut limited_job = job.clone();
        let limited_store = CheckpointStore::new(directory.path().join("limited-checkpoint.json"));
        let limited = execute_scan_batch(&mut limited_job, &output, &limited_store, Some(0))
            .expect("limited capability retry");
        assert!(!limited.complete);
        assert_eq!(limited.capability_retries_remaining, 1);
        assert_eq!(limited_job.state.status, JobStateStatus::Pending);

        let report = execute_scan(&mut job, &output, &store).expect("capability retry");
        assert!(report.complete);
        assert_eq!(report.capability_retries, 1);
        assert_eq!(report.processed, 1);
        let repaired: memory_engine_contracts::MediaRecord =
            serde_json::from_slice(&fs::read(record_path).expect("repaired record"))
                .expect("MediaRecord");
        assert_eq!(repaired.processing.state, ProcessingStateState::Proxied);
        assert_eq!(repaired.proxies.as_ref().map(Vec::len), Some(1));
    }

    /// The ordering contract on `execute_scan_batch`: span reconciliation is gated behind
    /// the capability-retry pass. While a retry is still outstanding no assembly is
    /// published; once the retries drain, the repair and the span build happen in the
    /// same invocation, so the span is always derived from post-retry records.
    #[cfg(target_os = "macos")]
    #[test]
    fn span_reconciliation_waits_for_outstanding_capability_retries() {
        use memory_engine_contracts::{
            ErrorInfo, MediaRecordAssetKind, ProcessingStateState, StageState, StageStateStatus,
        };

        let directory = tempdir().expect("tempdir");
        let source = directory.path().join("source");
        let output = directory.path().join("output");
        fs::create_dir(&source).expect("source directory");

        // Two GoPro chapters that no reconciliation pass has seen yet.
        for (name, marker) in [("GH010042.MP4", 1_u8), ("GH020042.MP4", 2_u8)] {
            let mut bytes = vec![0, 0, 0, 24];
            bytes.extend_from_slice(b"ftypmp42");
            bytes.extend_from_slice(&[marker; 64]);
            fs::write(source.join(name), bytes).expect("GoPro chapter fixture");
        }

        // A HEIC still parked in the legacy `unsupported_codec` quarantine, i.e. one
        // capability retry is owed.
        let heic_path = source.join("IMG_0001.HEIC");
        let jpeg_path = directory.path().join("source.jpg");
        ImageBuffer::from_pixel(8, 6, Rgb([30_u8, 60, 120]))
            .save(&jpeg_path)
            .expect("JPEG fixture");
        let sips = std::process::Command::new("sips")
            .arg("-s")
            .arg("format")
            .arg("heic")
            .arg(&jpeg_path)
            .arg("--out")
            .arg(&heic_path)
            .output()
            .expect("run sips");
        assert!(
            sips.status.success(),
            "{}",
            String::from_utf8_lossy(&sips.stderr)
        );

        let mut outputs = Vec::new();
        for path in [
            source.join("GH010042.MP4"),
            source.join("GH020042.MP4"),
            heic_path.clone(),
        ] {
            let mut record = ingest_file(&path, &output).expect("ingest").record;
            if path == heic_path {
                record.processing.state = ProcessingStateState::Quarantined;
                record.processing.stages.thumbnail = Some(StageState {
                    status: StageStateStatus::Failed,
                    attempts: Some(1),
                    completed_at: Some(Utc::now().to_rfc3339()),
                    job_id: None,
                    skip_reason: None,
                    last_error: Some(ErrorInfo {
                        code: "unsupported_codec".to_owned(),
                        message: "media processing failed; source details redacted".to_owned(),
                        retryable: false,
                        occurred_at: Some(Utc::now().to_rfc3339()),
                    }),
                });
                record.proxies = Some(Vec::new());
                record.image = None;
                record.perceptual = None;
            }
            let (record_path, record_bytes, record_artifact_id) =
                persist_record(&record, &output).expect("persist");
            outputs.push(json!({
                "kind": "media_record",
                "id": record_artifact_id,
                "path": record_path,
                "byte_size": record_bytes
            }));
        }
        let heic_media_id = ingest_file(&heic_path, &output)
            .expect("heic id")
            .record
            .media_id;

        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let job_value = json!({
            "schema_version": "v0",
            "job_id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
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
            "state": {"status": "completed", "attempts": 1},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1},
            "outputs": outputs
        });

        let count_assemblies = || {
            walkdir::WalkDir::new(output.join("records"))
                .into_iter()
                .filter_map(Result::ok)
                .filter(|entry| entry.file_type().is_file())
                .filter_map(|entry| fs::read(entry.path()).ok())
                .filter_map(|bytes| {
                    serde_json::from_slice::<memory_engine_contracts::MediaRecord>(&bytes).ok()
                })
                .filter(|record| record.asset_kind == MediaRecordAssetKind::VirtualAssembly)
                .count()
        };
        assert_eq!(
            count_assemblies(),
            0,
            "no assembly exists before reconciling"
        );

        // Budget of zero: the retry is deferred, so reconciliation must not run and the
        // completed job must reopen rather than publish a span over a half-repaired
        // library.
        let mut deferred: JobSpec = serde_json::from_value(job_value.clone()).expect("job");
        let deferred_store = CheckpointStore::new(directory.path().join("deferred.json"));
        let report = execute_scan_batch(&mut deferred, &output, &deferred_store, Some(0))
            .expect("deferred retry");
        assert!(!report.complete);
        assert_eq!(report.capability_retries, 0);
        assert_eq!(report.capability_retries_remaining, 1);
        assert_eq!(report.assemblies_created, 0);
        assert_eq!(report.span_members_updated, 0);
        assert_eq!(deferred.state.status, JobStateStatus::Pending);
        assert_eq!(
            count_assemblies(),
            0,
            "reconciliation must not publish an assembly while a retry is owed"
        );

        // Unbounded: the retry drains and the span is reconciled in the same invocation.
        let mut job: JobSpec = serde_json::from_value(job_value).expect("job");
        let store = CheckpointStore::new(directory.path().join("checkpoint.json"));
        let report = execute_scan(&mut job, &output, &store).expect("retry then reconcile");
        assert!(report.complete);
        assert_eq!(report.capability_retries, 1);
        assert_eq!(report.capability_retries_remaining, 0);
        assert_eq!(report.assemblies_created, 1);
        assert_eq!(report.span_members_updated, 2);
        assert_eq!(count_assemblies(), 1);

        // The retried record is repaired, and the span was built alongside it.
        let repaired: memory_engine_contracts::MediaRecord =
            serde_json::from_slice(&fs::read(record_path(&output, &heic_media_id)).expect("read"))
                .expect("MediaRecord");
        assert_eq!(repaired.processing.state, ProcessingStateState::Proxied);
        assert!(repaired.span.is_none(), "a still is never a chapter member");
    }

    /// The durable half of the retry/reconcile invariant, independent of ordering: a
    /// capability retry re-ingests from scratch and so produces `span: None`. Persisting
    /// that over a record reconciliation had already made a span member must not erase
    /// the membership, or the assembly would silently lose a chapter.
    #[test]
    fn reingesting_a_span_member_preserves_its_span_membership() {
        use memory_engine_contracts::{Span, SpanContinuity, SpanRole, SpanSpanKind};

        let directory = tempdir().expect("tempdir");
        let output = directory.path().join("output");
        let source_path = directory.path().join("GH010042.MP4");
        let mut bytes = vec![0, 0, 0, 24];
        bytes.extend_from_slice(b"ftypmp42");
        bytes.extend_from_slice(&[7_u8; 64]);
        fs::write(&source_path, bytes).expect("GoPro chapter fixture");

        let fresh = ingest_file(&source_path, &output).expect("ingest").record;
        assert!(fresh.span.is_none(), "a raw ingest carries no span");

        // Reconciliation stamps membership onto the record.
        let span_id = "a".repeat(64);
        let mut member = fresh.clone();
        member.span = Some(Span {
            span_id: span_id.clone(),
            role: SpanRole::Member,
            span_kind: SpanSpanKind::GoproChapter,
            index: Some(0),
            member_count: Some(2),
            member_media_ids: None,
            offset_in_span: None,
            continuity: Some(SpanContinuity::IncompleteSet),
        });
        persist_record(&member, &output).expect("persist member");

        // A capability retry re-persists the from-scratch record over it.
        let (path, _, _) = persist_record(&fresh, &output).expect("persist retry result");
        let reloaded: memory_engine_contracts::MediaRecord =
            serde_json::from_slice(&fs::read(&path).expect("read")).expect("MediaRecord");

        let span = reloaded.span.expect("span membership survives a re-ingest");
        assert_eq!(span.span_id, span_id);
        assert_eq!(span.role, SpanRole::Member);
        assert_eq!(span.index, Some(0));
    }
}
