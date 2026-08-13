"""Run traces (brief section 5 agent hygiene): every run, its steps, and
per-step cost/token/latency — inspectable end to end."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import require_api_key
from app.db.base import get_db
from app.db.models import Run, Step
from app.schemas.api import RunOut, StepOut

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[RunOut])
def list_runs(limit: int = 50, db: Session = Depends(get_db)):
    return db.execute(select(Run).order_by(Run.id.desc()).limit(limit)).scalars().all()


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/{run_id}/steps", response_model=list[StepOut])
def get_run_steps(run_id: int, db: Session = Depends(get_db)):
    return db.execute(select(Step).where(Step.run_id == run_id).order_by(Step.id)).scalars().all()
