use std::{env, fs, path::PathBuf, process::ExitCode};

use memory_engine_contracts::JobSpec;
use memory_engine_ingest::{execute_scan, CheckpointStore};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("ingest failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args_os().skip(1);
    let job_path = PathBuf::from(
        args.next()
            .ok_or("usage: memory-engine-ingest <job-spec.json> <output-dir> <checkpoint.json>")?,
    );
    let output_dir = PathBuf::from(args.next().ok_or("missing output directory")?);
    let checkpoint_path = PathBuf::from(args.next().ok_or("missing checkpoint path")?);
    if args.next().is_some() {
        return Err("too many arguments".into());
    }

    let mut job: JobSpec = serde_json::from_slice(&fs::read(job_path)?)?;
    let store = CheckpointStore::new(checkpoint_path);
    let report = execute_scan(&mut job, &output_dir, &store)?;
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
