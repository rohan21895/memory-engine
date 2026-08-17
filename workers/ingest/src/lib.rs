//! Local-only, resumable media ingest.
//!
//! The scan executor consumes generated contract types and opens each still image once:
//! the same byte buffer feeds BLAKE3, metadata extraction, pHash, and thumbnail creation.

mod format;
mod gopro;
mod job;
mod media;
mod metadata;
mod phash;
mod video;
mod walker;

pub use job::{
    execute_scan, execute_scan_batch, source_locator_digest, CheckpointStore, ScanReport,
};
pub use media::{ingest_file, IngestError, IngestedFile};
pub use video::{execute_video_proxy, VideoProxyError, VideoProxyReport};
pub use walker::{scan_paths, ScanIssue, ScanOptions, WalkEntry};
