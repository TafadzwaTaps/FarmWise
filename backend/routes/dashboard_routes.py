"""routes/dashboard_routes.py — Executive dashboard summary.
Route: GET /farms/{farm_id}/dashboard-summary
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends

import crud
from crud.dashboard import dashboard_summary
from core.auth import require_farm_role

router = APIRouter(prefix="/farms/{farm_id}", tags=["Dashboard"])


@router.get("/dashboard-summary")
def get_dashboard_summary(
    farm_id: str,
    period_start: date | None = None,
    period_end: date | None = None,
    _member: dict = Depends(require_farm_role()),
):
    """Defaults to the last 30 days if no period is given — the dashboard's
    landing view, not a custom report (that's a separate, later feature)."""
    end = period_end or date.today()
    start = period_start or (end - timedelta(days=30))
    return dashboard_summary(farm_id, start.isoformat(), end.isoformat())
