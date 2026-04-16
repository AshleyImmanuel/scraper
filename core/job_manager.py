from datetime import datetime, timezone
from core.config import JOB_RETENTION_SECONDS, MAX_STORED_JOBS, MAX_JOB_LOG_LINES

# Global job state
jobs: dict[str, dict] = {}

def get_job(job_id: str) -> dict | None:
    return jobs.get(job_id)

def create_job(job_id: str) -> dict:
    jobs[job_id] = {
        "status": "running",
        "progress": 0,
        "total": 0,
        "logs": ["Job created - starting extraction pipeline..."],
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "finishedAt": None,
        "filePath": None,
        "emailsFound": 0,
        "videosSearched": 0,
        "error": None,
    }
    return jobs[job_id]

def cleanup_jobs():
    """Remove stale or excess jobs from memory."""
    now = datetime.now(timezone.utc)
    stale_ids = []

    for job_id, job in jobs.items():
        if job.get("status") not in {"completed", "failed"}:
            continue
        timestamp = job.get("finishedAt") or job.get("startedAt")
        try:
            ts = datetime.fromisoformat(timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if (now - ts).total_seconds() > JOB_RETENTION_SECONDS:
            stale_ids.append(job_id)

    for job_id in stale_ids:
        jobs.pop(job_id, None)

    if len(jobs) <= MAX_STORED_JOBS:
        return

    overflow = len(jobs) - MAX_STORED_JOBS
    evictable_jobs = [
        (job_id, job)
        for job_id, job in jobs.items()
        if job.get("status") in {"completed", "failed"}
    ]
    ordered_jobs = sorted(
        evictable_jobs,
        key=lambda item: item[1].get("finishedAt") or item[1].get("startedAt") or "",
    )
    for old_job_id, _ in ordered_jobs[:overflow]:
        jobs.pop(old_job_id, None)

def log_to_job(job_id: str, message: str):
    """Append a timestamped log entry to the job."""
    job = jobs.get(job_id)
    if not job:
        return
        
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    job["logs"].append(f"[{ts}] {message}")
    
    if len(job["logs"]) > MAX_JOB_LOG_LINES:
        del job["logs"][: len(job["logs"]) - MAX_JOB_LOG_LINES]
        
    try:
        print(f"[{job_id}] {message}")
    except UnicodeEncodeError:
        print(f"[{job_id}] {message.encode('ascii', errors='replace').decode('ascii')}")
