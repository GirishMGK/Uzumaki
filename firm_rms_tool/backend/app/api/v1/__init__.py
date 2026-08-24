from fastapi import APIRouter

from app.api.v1 import (
    admin_import,
    allocations,
    auth,
    capacity,
    clients,
    dashboards,
    engagements,
    independence,
    masters,
    me,
    non_availability,
    reports,
    resource_requests,
    scenarios,
    scheduler,
    staff,
    timesheets,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(masters.offices_router, prefix="/offices", tags=["masters"])
api_router.include_router(masters.departments_router, prefix="/departments", tags=["masters"])
api_router.include_router(masters.skills_router, prefix="/skills", tags=["masters"])
api_router.include_router(masters.client_groups_router, prefix="/client-groups", tags=["masters"])
api_router.include_router(staff.router, prefix="/staff", tags=["staff"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(engagements.router, prefix="/engagements", tags=["engagements"])
api_router.include_router(allocations.router, prefix="/allocations", tags=["allocations"])
api_router.include_router(non_availability.router, prefix="/non-availability", tags=["non-availability"])
api_router.include_router(admin_import.router, prefix="/admin/import", tags=["import"])
api_router.include_router(scheduler.router, prefix="/scheduler", tags=["scheduler"])
api_router.include_router(capacity.router, prefix="/capacity", tags=["capacity"])
api_router.include_router(dashboards.router, prefix="/dashboards", tags=["dashboards"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(independence.router, prefix="/independence-declarations", tags=["independence"])
api_router.include_router(resource_requests.router, prefix="/resource-requests", tags=["resource-requests"])
api_router.include_router(timesheets.router, prefix="/timesheets", tags=["timesheets"])
api_router.include_router(scenarios.router, prefix="/scenarios", tags=["scenarios"])
api_router.include_router(me.router, prefix="/me", tags=["me"])
