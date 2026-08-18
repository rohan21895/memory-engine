use std::{env, fs, path::PathBuf, process::ExitCode};

use memory_engine_contracts::{JobSpec, JobSpecJobType};
use memory_engine_ingest::{execute_scan, execute_video_proxy, CheckpointStore};

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
    store.recover(&mut job)?;
    let report = match job.job_type {
        JobSpecJobType::ScanSource => {
            serde_json::to_value(execute_scan(&mut job, &output_dir, &store)?)?
        }
        JobSpecJobType::GenerateVideoProxy => {
            let ffmpeg = env::var_os("MEMORY_ENGINE_FFMPEG")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("ffmpeg"));
            serde_json::to_value(execute_video_proxy(&mut job, &output_dir, &store, &ffmpeg)?)?
        }
        _ => return Err("job type is not handled by the ingest worker".into()),
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
