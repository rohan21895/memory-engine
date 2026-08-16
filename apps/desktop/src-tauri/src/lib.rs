use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

use chrono::Utc;
use memory_engine_contracts::{JobSpec, MediaRecord, ProxyRefKind};
use memory_engine_ingest::{
    execute_scan_batch, source_locator_digest, CheckpointStore, ScanReport,
};
use serde::Serialize;
use serde_json::{json, Value};
use tauri::{ipc::Channel, AppHandle, Manager, State};
use walkdir::WalkDir;

const SCAN_BATCH_SIZE: usize = 24;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ScanUpdate {
    phase: &'static str,
    files_done: usize,
    files_total: Option<usize>,
    quarantined: usize,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ScanSummary {
    files_processed: usize,
    quarantined: usize,
    complete: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LibraryItem {
    media_id: String,
    kind: String,
    thumbnail_path: Option<String>,
    filename: String,
    captured_at: Option<String>,
    favorite: bool,
    width: Option<i64>,
    height: Option<i64>,
    processing_state: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LibraryPage {
    items: Vec<LibraryItem>,
    total: usize,
    offset: usize,
    has_more: bool,
}

#[derive(Clone, Default)]
struct ScanControl(Arc<AtomicBool>);

#[tauri::command]
async fn start_scan(
    app: AppHandle,
    roots: Vec<String>,
    on_event: Channel<ScanUpdate>,
    control: State<'_, ScanControl>,
) -> Result<ScanSummary, String> {
    if roots.is_empty() {
        return Err("Choose at least one folder first.".to_owned());
    }
    let continue_running = Arc::clone(&control.0);
    continue_running.store(true, Ordering::SeqCst);
    tauri::async_runtime::spawn_blocking(move || {
        run_scan(&app, roots, &on_event, &continue_running)
    })
    .await
    .map_err(|error| format!("The scan stopped unexpectedly: {error}"))?
}

#[tauri::command]
fn cancel_scan(control: State<'_, ScanControl>) {
    control.0.store(false, Ordering::SeqCst);
}

#[tauri::command]
fn library_page(
    app: AppHandle,
    query: String,
    offset: usize,
    limit: usize,
) -> Result<LibraryPage, String> {
    let paths = library_paths(&app)?;
    let mut items = read_library(&paths.output, &query)?;
    items.sort_by(|left, right| {
        right
            .captured_at
            .cmp(&left.captured_at)
            .then_with(|| left.filename.cmp(&right.filename))
    });
    let total = items.len();
    let limit = limit.clamp(1, 240);
    let page = items
        .into_iter()
        .skip(offset)
        .take(limit)
        .collect::<Vec<_>>();
    let has_more = offset.saturating_add(page.len()) < total;
    Ok(LibraryPage {
        items: page,
        total,
        offset,
        has_more,
    })
}

struct LibraryPaths {
    output: PathBuf,
    jobs: PathBuf,
}

fn library_paths(app: &AppHandle) -> Result<LibraryPaths, String> {
    let root = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Photeo could not open its private library: {error}"))?;
    let output = root.join("library");
    let jobs = root.join("jobs");
    fs::create_dir_all(&output).map_err(redacted_io_error)?;
    fs::create_dir_all(&jobs).map_err(redacted_io_error)?;
    Ok(LibraryPaths { output, jobs })
}

fn run_scan(
    app: &AppHandle,
    roots: Vec<String>,
    on_event: &Channel<ScanUpdate>,
    continue_running: &AtomicBool,
) -> Result<ScanSummary, String> {
    let root_paths = roots.iter().map(PathBuf::from).collect::<Vec<_>>();
    let locator = source_locator_digest(&root_paths)
        .map_err(|_| "One of those folders is no longer available.".to_owned())?;
    let paths = library_paths(app)?;
    let checkpoint_path = paths.jobs.join(format!("scan-{locator}.json"));
    let store = CheckpointStore::new(&checkpoint_path);
    let mut job = load_or_create_job(&checkpoint_path, &roots, &locator)?;
    let mut processed = progress_done(&job);
    let mut quarantined = 0;

    send_update(
        on_event,
        "preparing",
        processed,
        progress_total(&job),
        quarantined,
        "Counting your photos and videos…",
    )?;

    loop {
        if !continue_running.load(Ordering::SeqCst) {
            send_update(
                on_event,
                "paused",
                processed,
                progress_total(&job),
                quarantined,
                "Paused safely. Your progress has been saved.",
            )?;
            return Ok(ScanSummary {
                files_processed: processed,
                quarantined,
                complete: false,
            });
        }

        let report = execute_scan_batch(&mut job, &paths.output, &store, Some(SCAN_BATCH_SIZE))
            .map_err(|_| {
                "Photeo could not finish this batch. Your saved progress is safe.".to_owned()
            })?;
        processed = progress_done(&job);
        quarantined += report.quarantined;
        send_report(on_event, &job, &report, processed, quarantined)?;
        if report.complete {
            return Ok(ScanSummary {
                files_processed: processed,
                quarantined,
                complete: true,
            });
        }
    }
}

fn load_or_create_job(
    checkpoint_path: &Path,
    roots: &[String],
    locator: &str,
) -> Result<JobSpec, String> {
    if let Ok(bytes) = fs::read(checkpoint_path) {
        if let Ok(job) = serde_json::from_slice::<JobSpec>(&bytes) {
            if job.inputs.source_locator_digest.as_deref() == Some(locator) {
                return Ok(job);
            }
        }
    }

    let params = json!({
        "follow_symlinks": false,
        "include_hidden": false,
        "max_depth": 32
    });
    let params_digest =
        blake3::hash(&serde_json::to_vec(&params).map_err(|error| error.to_string())?)
            .to_hex()
            .to_string();
    let mut job_id_hasher = blake3::Hasher::new();
    job_id_hasher.update(b"desktop-scan-v1\0");
    job_id_hasher.update(locator.as_bytes());
    job_id_hasher.update(b"\0");
    job_id_hasher.update(params_digest.as_bytes());
    let job_id = job_id_hasher.finalize().to_hex().to_string();
    let now = Utc::now().to_rfc3339();

    let mut value: Value = serde_json::from_str(include_str!(
        "../../../../contracts/fixtures/job-spec/valid/job-scan-source-root-a.json"
    ))
    .map_err(|error| error.to_string())?;
    value["job_id"] = json!(job_id);
    value["inputs"]["source_paths"] = json!(roots);
    value["inputs"]["source_locator_digest"] = json!(locator);
    value["params"] = params;
    value["params_digest"] = json!(params_digest);
    value["state"] = json!({
        "status": "pending",
        "attempts": 1,
        "worker_id": "desktop-ingest",
        "started_at": null,
        "heartbeat_at": null,
        "finished_at": null,
        "progress": null
    });
    value["checkpoint"] = json!({
        "resumable": true,
        "cursor": null,
        "checkpoint_version": 1,
        "updated_at": now,
        "completed_input_ids": [],
        "partial_output_ids": []
    });
    value["outputs"] = json!([]);
    value["error"] = Value::Null;
    value["created_at"] = json!(Utc::now().to_rfc3339());
    serde_json::from_value(value)
        .map_err(|_| "Photeo could not create a safe, resumable scan.".to_owned())
}

fn send_report(
    channel: &Channel<ScanUpdate>,
    job: &JobSpec,
    report: &ScanReport,
    processed: usize,
    quarantined: usize,
) -> Result<(), String> {
    let phase = if report.complete {
        "complete"
    } else {
        "scanning"
    };
    let message = if report.complete {
        "Your library is ready."
    } else {
        "Organizing your photos and videos…"
    };
    send_update(
        channel,
        phase,
        processed,
        progress_total(job),
        quarantined,
        message,
    )
}

fn send_update(
    channel: &Channel<ScanUpdate>,
    phase: &'static str,
    files_done: usize,
    files_total: Option<usize>,
    quarantined: usize,
    message: &str,
) -> Result<(), String> {
    channel
        .send(ScanUpdate {
            phase,
            files_done,
            files_total,
            quarantined,
            message: message.to_owned(),
        })
        .map_err(|_| "The progress window closed unexpectedly.".to_owned())
}

fn progress_done(job: &JobSpec) -> usize {
    job.state
        .progress
        .as_ref()
        .map_or(0, |progress| progress.units_done.max(0.0) as usize)
}

fn progress_total(job: &JobSpec) -> Option<usize> {
    job.state
        .progress
        .as_ref()
        .and_then(|progress| progress.units_total)
        .map(|total| total.max(0.0) as usize)
}

fn read_library(output_dir: &Path, query: &str) -> Result<Vec<LibraryItem>, String> {
    let records = output_dir.join("records");
    if !records.exists() {
        return Ok(Vec::new());
    }
    let query = query.trim().to_lowercase();
    let mut items = Vec::new();
    for entry in WalkDir::new(records).into_iter().filter_map(Result::ok) {
        if !entry.file_type().is_file()
            || entry.path().extension().and_then(|value| value.to_str()) != Some("json")
        {
            continue;
        }
        let Ok(bytes) = fs::read(entry.path()) else {
            continue;
        };
        let Ok(record) = serde_json::from_slice::<MediaRecord>(&bytes) else {
            continue;
        };
        let item = library_item(&record);
        if query.is_empty() || search_text(&record, &item).contains(&query) {
            items.push(item);
        }
    }
    Ok(items)
}

fn library_item(record: &MediaRecord) -> LibraryItem {
    let source = record.sources.iter().find(|source| source.present);
    let filename = source
        .and_then(|source| source.original_filename.clone())
        .or_else(|| {
            source.and_then(|source| {
                Path::new(&source.path)
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
            })
        })
        .unwrap_or_else(|| "Untitled memory".to_owned());
    let thumbnail_path = record.proxies.as_ref().and_then(|proxies| {
        proxies
            .iter()
            .find(|proxy| proxy.kind == ProxyRefKind::Thumbnail512)
            .map(|proxy| proxy.path.clone())
    });
    let size = record
        .image
        .as_ref()
        .map(|image| &image.oriented_size)
        .or_else(|| record.video.as_ref().map(|video| &video.oriented_size));
    LibraryItem {
        media_id: record.media_id.clone(),
        kind: enum_string(&record.kind),
        thumbnail_path,
        filename,
        captured_at: record
            .capture
            .captured_at
            .utc
            .clone()
            .or_else(|| record.capture.captured_at.local.clone()),
        favorite: record
            .user
            .as_ref()
            .and_then(|user| user.favorite)
            .unwrap_or(false),
        width: size.map(|size| size.width),
        height: size.map(|size| size.height),
        processing_state: enum_string(&record.processing.state),
    }
}

fn search_text(record: &MediaRecord, item: &LibraryItem) -> String {
    let mut parts = vec![item.filename.clone()];
    if let Some(user) = &record.user {
        parts.extend(user.tags.clone().unwrap_or_default());
        if let Some(caption) = &user.caption {
            parts.push(caption.clone());
        }
    }
    if let Some(content) = &record.content {
        parts.extend(
            content
                .tags
                .as_ref()
                .into_iter()
                .flatten()
                .map(|tag| tag.label.clone()),
        );
    }
    parts.join(" ").to_lowercase()
}

fn enum_string<T: Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_else(|| "unknown".to_owned())
}

fn redacted_io_error(_: std::io::Error) -> String {
    "Photeo could not open its private library folder.".to_owned()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(ScanControl::default())
        .invoke_handler(tauri::generate_handler![
            start_scan,
            cancel_scan,
            library_page
        ])
        .run(tauri::generate_context!())
        .expect("error while running Photeo");
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn generated_fixture_becomes_a_library_item() {
        let record: MediaRecord = serde_json::from_str(include_str!(
            "../../../../contracts/fixtures/media-record/valid/image-whatsapp-filename-date.json"
        ))
        .expect("golden MediaRecord");
        let item = library_item(&record);
        assert_eq!(item.kind, "image");
        assert!(item.filename.contains("WA"));
        assert!(item.captured_at.is_some());
    }

    #[test]
    fn search_uses_contract_fields_without_touching_original_bytes() {
        let record: MediaRecord = serde_json::from_str(include_str!(
            "../../../../contracts/fixtures/media-record/valid/image-beach-sunset.json"
        ))
        .expect("golden MediaRecord");
        let item = library_item(&record);
        let haystack = search_text(&record, &item);
        assert!(haystack.contains("sunset"));
    }

    #[test]
    fn desktop_job_runs_the_real_resumable_ingest_pipeline() {
        let temporary = tempdir().expect("temporary library");
        let source = temporary.path().join("source");
        let output = temporary.path().join("output");
        let checkpoint = temporary.path().join("jobs").join("scan.json");
        fs::create_dir_all(&source).expect("source directory");
        fs::write(source.join("empty.jpg"), []).expect("hostile input");
        let roots = vec![source.to_string_lossy().into_owned()];
        let locator = source_locator_digest(&[source]).expect("source locator");
        let mut job = load_or_create_job(&checkpoint, &roots, &locator).expect("desktop job");
        let report = execute_scan_batch(
            &mut job,
            &output,
            &CheckpointStore::new(&checkpoint),
            Some(SCAN_BATCH_SIZE),
        )
        .expect("scan completes");
        assert!(report.complete);
        assert_eq!(report.processed, 1);
        assert_eq!(report.quarantined, 1);
        assert_eq!(read_library(&output, "").expect("library").len(), 1);
    }
}
