import os
from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse, FileResponse

from core.models import ExtractionRequest
from core.job_manager import jobs, create_job, get_job, cleanup_jobs
from core.pipeline import run_extraction
from core.config import MAX_CONCURRENT_JOBS

router = APIRouter(prefix="/api")

@router.post("/extract")
async def start_extraction(req: ExtractionRequest, background_tasks: BackgroundTasks):
    """Start an extraction job in the background."""
    cleanup_jobs()

    running_jobs = sum(1 for item in jobs.values() if item.get("status") == "running")
    if running_jobs >= MAX_CONCURRENT_JOBS:
        return JSONResponse(
            status_code=429,
            content={"error": "Too many running extraction jobs. Please try again shortly."},
        )

    import uuid
    job_id = str(uuid.uuid4())[:8]
    create_job(job_id)

    background_tasks.add_task(run_extraction, job_id, req)
    return {"jobId": job_id, "status": "running"}

@router.get("/status/{job_id}")
async def job_status(job_id: str, logOffset: int = Query(default=0, ge=0)):
    """Poll the current status of a running job."""
    cleanup_jobs()
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    all_logs = job["logs"]
    logs = all_logs[logOffset:] if logOffset else all_logs
    return {
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "logs": logs,
        "nextLogOffset": len(all_logs),
        "emailsFound": job["emailsFound"],
        "videosSearched": job["videosSearched"],
        "error": job["error"],
    }

@router.get("/download/{job_id}")
async def download_file(job_id: str):
    """Download the generated Excel file for a completed job."""
    cleanup_jobs()
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if job["status"] != "completed":
        return JSONResponse(status_code=400, content={"error": "Job not completed yet"})
    if not job["filePath"] or not os.path.exists(job["filePath"]):
        return JSONResponse(status_code=404, content={"error": "File not found"})

    filepath = job["filePath"]
    filename = os.path.basename(filepath)
    return FileResponse(
        path=filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        headers={
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
