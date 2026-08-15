use std::{collections::HashSet, path::PathBuf};

use memory_engine_contracts::JobErrorCode;
use serde::{Deserialize, Serialize};
use walkdir::WalkDir;

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ScanOptions {
    pub follow_symlinks: bool,
    pub include_hidden: bool,
    pub max_depth: usize,
}

impl Default for ScanOptions {
    fn default() -> Self {
        Self {
            follow_symlinks: false,
            include_hidden: false,
            max_depth: 32,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WalkEntry {
    pub path: PathBuf,
    pub cursor: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ScanIssue {
    pub code: JobErrorCode,
    pub message: String,
}

pub fn scan_paths(roots: &[PathBuf], options: ScanOptions) -> (Vec<WalkEntry>, Vec<ScanIssue>) {
    let mut entries = Vec::new();
    let mut issues = Vec::new();
    let mut seen = HashSet::new();

    for root in roots {
        let walker = WalkDir::new(root)
            .follow_links(options.follow_symlinks)
            .max_depth(options.max_depth)
            .sort_by_file_name()
            .into_iter()
            .filter_entry(|entry| options.include_hidden || !is_hidden(entry));

        for result in walker {
            match result {
                Ok(entry) if entry.file_type().is_file() => {
                    let path = entry.path().to_path_buf();
                    let cursor = path.to_string_lossy().into_owned();
                    if seen.insert(cursor.clone()) {
                        entries.push(WalkEntry { path, cursor });
                    }
                }
                Ok(_) => {}
                Err(error) => {
                    let code = if error.loop_ancestor().is_some() {
                        JobErrorCode::SymlinkLoop
                    } else if error
                        .io_error()
                        .is_some_and(|io| io.kind() == std::io::ErrorKind::PermissionDenied)
                    {
                        JobErrorCode::PermissionDenied
                    } else {
                        JobErrorCode::FileUnreadable
                    };
                    issues.push(ScanIssue {
                        code,
                        message: "source entry could not be walked; location redacted".to_owned(),
                    });
                }
            }
        }
    }
    entries.sort_by(|left, right| left.cursor.cmp(&right.cursor));
    (entries, issues)
}

fn is_hidden(entry: &walkdir::DirEntry) -> bool {
    entry.depth() > 0
        && entry
            .file_name()
            .to_str()
            .is_some_and(|name| name.starts_with('.'))
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn skips_hidden_files_by_default() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("visible.jpg"), b"x").expect("visible");
        fs::write(directory.path().join(".hidden.jpg"), b"x").expect("hidden");
        let (entries, issues) =
            scan_paths(&[directory.path().to_path_buf()], ScanOptions::default());
        assert!(issues.is_empty());
        assert_eq!(entries.len(), 1);
    }

    #[cfg(unix)]
    #[test]
    fn reports_symlink_loops_without_recursing_forever() {
        use std::os::unix::fs::symlink;

        let directory = tempdir().expect("tempdir");
        let nested = directory.path().join("nested");
        fs::create_dir(&nested).expect("nested");
        symlink(directory.path(), nested.join("loop")).expect("symlink");
        let options = ScanOptions {
            follow_symlinks: true,
            ..ScanOptions::default()
        };
        let (_, issues) = scan_paths(&[directory.path().to_path_buf()], options);
        assert!(issues
            .iter()
            .any(|issue| issue.code == JobErrorCode::SymlinkLoop));
    }

    #[test]
    fn walks_ten_thousand_files_deterministically() {
        let directory = tempdir().expect("tempdir");
        for index in 0..10_000 {
            fs::write(directory.path().join(format!("{index:05}.jpg")), b"x")
                .expect("fixture file");
        }
        let (entries, issues) =
            scan_paths(&[directory.path().to_path_buf()], ScanOptions::default());
        assert!(issues.is_empty());
        assert_eq!(entries.len(), 10_000);
        assert!(entries
            .windows(2)
            .all(|pair| pair[0].cursor < pair[1].cursor));
    }
}
