use std::{
    collections::BTreeSet,
    ffi::OsString,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use memory_engine_contracts::{JobSpec, JobStateStatus, MediaRecord};
use memory_engine_ingest::source_locator_digest;
use serde_json::{json, Value};
use tempfile::TempDir;

const FILE_COUNT: usize = 200;
const KILL_AFTER: u64 = 10;

struct ScanFixture {
    _temp: TempDir,
    _source: PathBuf,
    output: PathBuf,
    request: PathBuf,
    checkpoint: PathBuf,
    expected_outputs: usize,
}

impl ScanFixture {
    fn new(file_count: usize) -> Self {
        Self::with_duplicate_prefix(file_count, false)
    }

    fn with_duplicate_prefix(file_count: usize, duplicate_prefix: bool) -> Self {
        let temp = tempfile::tempdir().expect("temporary scan directory");
        let source = temp.path().join("source");
        let output = temp.path().join("output");
        let request = temp.path().join("request.json");
        let checkpoint = temp.path().join("checkpoint.json");
        fs::create_dir(&source).expect("source directory");
        for index in 0..file_count {
            let content_index = if duplicate_prefix && index == 1 {
                0
            } else {
                index as u32
            };
            fs::write(
                source.join(format!("image-{index:06}.bmp")),
                bmp(content_index),
            )
            .expect("synthetic BMP");
        }
        let locator = source_locator_digest(std::slice::from_ref(&source)).expect("locator digest");
        let job: JobSpec = serde_json::from_value(json!({
            "schema_version": "v0",
            "job_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "job_type": "scan_source",
            "inputs": {
                "media_ids": [],
                "source_paths": [source.clone()],
                "source_locator_digest": locator
            },
            "params": {"follow_symlinks": false, "include_hidden": false, "max_depth": 32},
            "params_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "scope": "library:checkpoint-journal-test",
            "egress": {"requires_egress": false},
            "state": {"status": "pending", "attempts": 0},
            "checkpoint": {"resumable": true, "cursor": null, "checkpoint_version": 1}
        }))
        .expect("scan JobSpec");
        fs::write(
            &request,
            serde_json::to_vec_pretty(&job).expect("serialize request"),
        )
        .expect("request file");
        Self {
            _temp: temp,
            _source: source,
            output,
            request,
            checkpoint,
            expected_outputs: file_count - usize::from(duplicate_prefix),
        }
    }

    fn journal(&self) -> PathBuf {
        with_suffix(&self.checkpoint, ".scan-journal")
    }

    fn required_marker(&self) -> PathBuf {
        with_suffix(&self.checkpoint, ".scan-journal-required")
    }

    fn spawn(&self) -> std::process::Child {
        Command::new(env!("CARGO_BIN_EXE_memory-engine-ingest"))
            .args([&self.request, &self.output, &self.checkpoint])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn ingest worker")
    }

    fn kill_mid_tail(&self) -> u64 {
        let mut child = self.spawn();
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if let Some(status) = child.try_wait().expect("poll ingest worker") {
                panic!("ingest worker completed before the crash point: {status}");
            }
            if let Ok(bytes) = fs::read(&self.checkpoint) {
                if let Ok(snapshot) = serde_json::from_slice::<Value>(&bytes) {
                    let done = snapshot
                        .pointer("/state/progress/units_done")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0) as u64;
                    if done >= KILL_AFTER && done < FILE_COUNT as u64 {
                        child.kill().expect("kill ingest worker");
                        child.wait().expect("reap killed ingest worker");
                        assert!(self.journal().is_file(), "journal survives the kill");
                        assert!(
                            self.required_marker().is_file(),
                            "required-journal marker survives the kill"
                        );
                        return done;
                    }
                }
            }
            assert!(
                Instant::now() < deadline,
                "worker did not reach crash point"
            );
            thread::sleep(Duration::from_millis(2));
        }
    }

    fn resume(&self) -> std::process::Output {
        fs::copy(&self.checkpoint, &self.request).expect("use compact snapshot as resume request");
        Command::new(env!("CARGO_BIN_EXE_memory-engine-ingest"))
            .args([&self.request, &self.output, &self.checkpoint])
            .output()
            .expect("resume ingest worker")
    }

    fn assert_full_completion(&self, output: &std::process::Output, minimum_skips: u64) {
        assert!(
            output.status.success(),
            "resume failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let report: Value = serde_json::from_slice(&output.stdout).expect("scan report");
        assert_eq!(report["complete"], true);
        assert!(report["resumed_skips"].as_u64().unwrap_or(0) >= minimum_skips);

        let completed: JobSpec =
            serde_json::from_slice(&fs::read(&self.checkpoint).expect("completed checkpoint"))
                .expect("completed JobSpec");
        assert_eq!(completed.state.status, JobStateStatus::Completed);
        let outputs = completed.outputs.as_deref().expect("output manifest");
        assert_eq!(outputs.len(), self.expected_outputs);
        let ids = outputs
            .iter()
            .map(|artifact| artifact.id.as_str())
            .collect::<BTreeSet<_>>();
        assert_eq!(ids.len(), self.expected_outputs, "output IDs remain unique");
        let partial_ids = completed
            .checkpoint
            .as_ref()
            .and_then(|checkpoint| checkpoint.partial_output_ids.as_deref())
            .expect("partial output IDs");
        assert_eq!(partial_ids.len(), self.expected_outputs);
        assert_eq!(
            ids,
            partial_ids.iter().map(String::as_str).collect(),
            "checkpoint IDs exactly match content-addressed outputs"
        );
        let mut media_ids = BTreeSet::new();
        for artifact in outputs {
            let path = artifact.path.as_deref().expect("artifact path");
            let bytes = fs::read(path).expect("persisted output remains present");
            assert_eq!(artifact.byte_size, Some(bytes.len() as i64));
            let record: MediaRecord = serde_json::from_slice(&bytes).expect("MediaRecord output");
            assert_eq!(
                blake3::hash(&bytes).to_hex().as_str(),
                artifact.id,
                "JobOutput identifies the serialized artifact"
            );
            assert_ne!(
                record.media_id, artifact.id,
                "source-media and produced-artifact identities stay distinct"
            );
            media_ids.insert(record.media_id);
        }
        assert_eq!(media_ids.len(), self.expected_outputs);
        assert!(!self.journal().exists(), "journal compacted at completion");
        assert!(
            !self.required_marker().exists(),
            "required marker retired at completion"
        );
    }
}

#[test]
fn killed_mid_tail_scan_resumes_exactly_from_constant_size_deltas() {
    let fixture = ScanFixture::new(FILE_COUNT);
    let durable = fixture.kill_mid_tail();
    let compact: Value =
        serde_json::from_slice(&fs::read(&fixture.checkpoint).expect("compact checkpoint"))
            .expect("compact JobSpec");
    assert_eq!(compact["state"]["status"], "running");
    assert_eq!(compact["outputs"], json!([]));
    assert_eq!(compact["checkpoint"]["partial_output_ids"], json!([]));
    assert_eq!(compact["checkpoint"]["completed_input_ids"], json!([]));

    let lines = journal_values(&fixture.journal());
    assert_eq!(lines[0]["record_type"], "header");
    assert_eq!(lines[0]["job"]["outputs"], json!([]));
    assert_eq!(
        lines[0]["job"]["checkpoint"]["partial_output_ids"],
        json!([]),
        "the header is configuration, never the growing manifest"
    );
    assert_eq!(
        lines[0]["job"]["checkpoint"]["completed_input_ids"],
        json!([])
    );
    assert!(
        lines
            .iter()
            .skip(1)
            .all(|line| line["record_type"] == "progress"),
        "a fresh scan appends only one per-file delta"
    );
    let journal_text = fs::read_to_string(fixture.journal()).expect("journal text");
    let last_delta = journal_text.lines().last().expect("last progress delta");
    OpenOptions::new()
        .append(true)
        .open(fixture.journal())
        .and_then(|mut file| writeln!(file, "{last_delta}"))
        .expect("replay the final durable delta");

    let resumed = fixture.resume();
    fixture.assert_full_completion(&resumed, durable);
}

#[test]
fn replaced_artifact_delta_removes_the_superseded_output_on_replay() {
    let fixture = ScanFixture::with_duplicate_prefix(FILE_COUNT, true);
    let durable = fixture.kill_mid_tail();
    let lines = journal_values(&fixture.journal());
    assert!(
        lines.iter().any(|line| {
            line["record_type"] == "progress"
                && !line["replaced_output_id"].is_null()
                && !line["replaced_partial_output_id"].is_null()
        }),
        "the second path for identical source bytes replaces the merged record artifact"
    );

    let resumed = fixture.resume();

    fixture.assert_full_completion(&resumed, durable);
}

#[test]
fn truncated_final_delta_is_discarded_and_the_file_is_reprocessed() {
    let fixture = ScanFixture::new(FILE_COUNT);
    let durable = fixture.kill_mid_tail();
    let bytes = fs::read(fixture.journal()).expect("journal bytes");
    assert_eq!(bytes.last(), Some(&b'\n'));
    let last_start = bytes[..bytes.len() - 1]
        .iter()
        .rposition(|byte| *byte == b'\n')
        .map_or(0, |position| position + 1);
    let truncated_len = last_start + (bytes.len() - last_start) / 2;
    OpenOptions::new()
        .write(true)
        .open(fixture.journal())
        .and_then(|file| file.set_len(truncated_len as u64))
        .expect("truncate final journal delta");

    let resumed = fixture.resume();
    fixture.assert_full_completion(&resumed, durable.saturating_sub(1));
}

#[test]
fn complete_corruption_and_missing_outputs_never_read_as_a_pass() {
    let corrupt = ScanFixture::new(FILE_COUNT);
    corrupt.kill_mid_tail();
    OpenOptions::new()
        .append(true)
        .open(corrupt.journal())
        .and_then(|mut file| file.write_all(b"{not-json}\n"))
        .expect("append corrupt complete record");
    let failed = corrupt.resume();
    assert!(!failed.status.success());
    assert!(String::from_utf8_lossy(&failed.stderr).contains("complete journal record is invalid"));
    assert_checkpoint_not_completed(&corrupt.checkpoint);

    let missing = ScanFixture::new(FILE_COUNT);
    missing.kill_mid_tail();
    let artifact = journal_values(&missing.journal())
        .into_iter()
        .filter_map(|line| line.get("output").cloned())
        .find_map(|output| {
            output
                .get("path")
                .and_then(Value::as_str)
                .map(PathBuf::from)
        })
        .expect("journal contains an output delta");
    fs::remove_file(artifact).expect("remove a required persisted output");
    let failed = missing.resume();
    assert!(!failed.status.success());
    assert!(String::from_utf8_lossy(&failed.stderr).contains("persisted media record"));
    assert_checkpoint_not_completed(&missing.checkpoint);
}

#[test]
fn same_size_valid_json_artifact_corruption_fails_journal_recovery() {
    let fixture = ScanFixture::new(FILE_COUNT);
    fixture.kill_mid_tail();
    let artifact = journal_values(&fixture.journal())
        .into_iter()
        .filter_map(|line| line.get("output").cloned())
        .filter_map(|output| {
            output
                .get("path")
                .and_then(Value::as_str)
                .map(PathBuf::from)
        })
        .next()
        .expect("journal contains an output delta");
    let original = fs::read(&artifact).expect("record artifact");
    let record: MediaRecord = serde_json::from_slice(&original).expect("MediaRecord JSON");
    let source_path = record.sources[0].path.as_bytes();
    let mut replacement = source_path.to_vec();
    let last = replacement.last_mut().expect("source path is nonempty");
    *last = if *last == b'x' { b'y' } else { b'x' };
    let offset = original
        .windows(source_path.len())
        .position(|window| window == source_path)
        .expect("serialized source path");
    let mut corrupted = original.clone();
    corrupted[offset..offset + source_path.len()].copy_from_slice(&replacement);
    assert_eq!(corrupted.len(), original.len());
    serde_json::from_slice::<MediaRecord>(&corrupted).expect("still-valid MediaRecord JSON");
    fs::write(&artifact, corrupted).expect("same-size artifact corruption");

    let failed = fixture.resume();

    assert!(!failed.status.success());
    assert!(
        String::from_utf8_lossy(&failed.stderr).contains("integrity metadata"),
        "unexpected error: {}",
        String::from_utf8_lossy(&failed.stderr)
    );
    assert_checkpoint_not_completed(&fixture.checkpoint);
}

#[test]
fn compact_snapshot_cannot_resume_without_its_required_journal() {
    let fixture = ScanFixture::new(FILE_COUNT);
    fixture.kill_mid_tail();
    fs::remove_file(fixture.journal()).expect("remove required journal");

    let failed = fixture.resume();

    assert!(!failed.status.success());
    assert!(String::from_utf8_lossy(&failed.stderr).contains("required journal is absent"));
    assert_checkpoint_not_completed(&fixture.checkpoint);
}

fn journal_values(path: &Path) -> Vec<Value> {
    fs::read_to_string(path)
        .expect("journal text")
        .lines()
        .map(|line| serde_json::from_str(line).expect("complete journal record"))
        .collect()
}

fn assert_checkpoint_not_completed(path: &Path) {
    let checkpoint: Value =
        serde_json::from_slice(&fs::read(path).expect("checkpoint remains present"))
            .expect("checkpoint remains a JobSpec");
    assert_ne!(checkpoint["state"]["status"], "completed");
    assert_eq!(checkpoint["outputs"], json!([]));
}

fn with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut value: OsString = path.as_os_str().to_owned();
    value.push(suffix);
    PathBuf::from(value)
}

fn bmp(index: u32) -> Vec<u8> {
    let mut bytes = vec![0_u8; 70];
    bytes[0..2].copy_from_slice(b"BM");
    bytes[2..6].copy_from_slice(&70_u32.to_le_bytes());
    bytes[10..14].copy_from_slice(&54_u32.to_le_bytes());
    bytes[14..18].copy_from_slice(&40_u32.to_le_bytes());
    bytes[18..22].copy_from_slice(&2_i32.to_le_bytes());
    bytes[22..26].copy_from_slice(&2_i32.to_le_bytes());
    bytes[26..28].copy_from_slice(&1_u16.to_le_bytes());
    bytes[28..30].copy_from_slice(&24_u16.to_le_bytes());
    bytes[34..38].copy_from_slice(&16_u32.to_le_bytes());
    for (offset, pixel) in bytes[54..].chunks_mut(4).enumerate() {
        let color = index.wrapping_add(offset as u32).to_le_bytes();
        pixel[..3].copy_from_slice(&color[..3]);
    }
    bytes
}
