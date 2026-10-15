import json

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine, SessionLocal
from .models import Invoice, TaskRun
from .tools import (
    list_invoices,
    read_invoice,
    search_invoice,
    create_invoice,
    verify_invoice,
)
from .gemini_agent import GeminiTaskAgent
from .agent import TaskAgent


# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="TaskPilot",
    description="Autonomous AI Task Worker",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskRequest(BaseModel):
    task: str
    mode: str="auto"


@app.get("/")
def root():
    return {
        "message": "TaskPilot is running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.get("/tools/invoices")
def get_invoices():
    return list_invoices()


@app.get("/tools/invoices/{filename}")
def get_invoice(filename: str):
    return read_invoice(filename)


@app.get("/tools/search/{invoice_number}")
def search_invoice_endpoint(invoice_number: str):
    return search_invoice(invoice_number)


@app.post("/tools/invoices")
def create_invoice_endpoint(invoice: dict):
    return create_invoice(
        vendor=invoice["vendor"],
        invoice_number=invoice["invoice_number"],
        invoice_date=invoice["invoice_date"],
        due_date=invoice["due_date"],
        amount=invoice["amount"]
    )


@app.get("/tools/verify/{invoice_number}")
def verify_invoice_endpoint(invoice_number: str):
    return verify_invoice(
        invoice_number=invoice_number,
        expected_vendor="Acme Corporation",
        expected_invoice_date="2026-10-01",
        expected_due_date="2026-11-01",
        expected_amount=84500
    )


@app.post("/tasks")
def run_task(request: TaskRequest):
    from fastapi import HTTPException

    if request.mode not in {"auto", "gemini", "local"}:
        raise HTTPException(
            status_code=400,
            detail="mode must be 'auto', 'gemini', or 'local'"
        )

    db = SessionLocal()

    # Save the task before executing it.
    task_run = TaskRun(
        task=request.task,
        status="running",
        execution_mode=request.mode,
    )

    try:
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        try:
            if request.mode == "local":
                result = TaskAgent().run(request.task)
                result["execution_mode"] = "local"

            else:
                result = GeminiTaskAgent().run(request.task)

                if result.get("status") == "failed" and request.mode == "auto":
                    error = str(result.get("error", "")).upper()

                    if any(term in error for term in (
                        "429", "RESOURCE_EXHAUSTED",
                        "QUOTA", "UNAVAILABLE"
                    )):
                        result = TaskAgent().run(request.task)
                        result["execution_mode"] = "local_fallback"
                        result["fallback_reason"] = (
                            "Gemini was unavailable or its quota was exhausted."
                        )
                    else:
                        result["execution_mode"] = "gemini"
                else:
                    result["execution_mode"] = "gemini"

        except Exception as exc:
            result = {
                "task": request.task,
                "status": "failed",
                "message": "An unexpected error occurred during task execution.",
                "error": str(exc),
                "steps": [],
                "execution_mode": (
                    "local" if request.mode == "local" else "gemini"
                ),
            }

        # Attach the history ID to the result.
        result["task_run_id"] = task_run.id

        # Persist the completed or failed result.
        task_run.status = result.get("status", "failed")
        task_run.execution_mode = result.get("execution_mode")
        task_run.result_json = json.dumps(result, default=str)

        db.commit()

        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

@app.get("/tasks/history")
def get_task_history():
    db = SessionLocal()

    try:
        task_runs = (
            db.query(TaskRun)
            .order_by(TaskRun.created_at.desc())
            .limit(50)
            .all()
        )

        return [
            {
                "id": run.id,
                "task": run.task,
                "status": run.status,
                "execution_mode": run.execution_mode,
                "created_at": (
                    run.created_at.isoformat()
                    if run.created_at
                    else None
                ),
                "result": (
                    json.loads(run.result_json)
                    if run.result_json
                    else None
                ),
            }
            for run in task_runs
        ]

    finally:
        db.close()