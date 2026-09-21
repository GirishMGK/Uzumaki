"""System info and resource monitoring endpoints."""

from __future__ import annotations

import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from fcmr_core.config import settings

router = APIRouter()


@router.get("/system/info")
async def get_system_info():
    """Return current system state and DuckDB configuration."""
    try:
        vm = psutil.virtual_memory()
        cpu_count = psutil.cpu_count(logical=True)
        cpu_freq = psutil.cpu_freq()

        return JSONResponse(
            {
                "hardware": {
                    "tier": settings.hw_tier,
                    "total_ram_gb": round(vm.total / 1024**3, 1),
                    "cpu_cores": cpu_count,
                    "cpu_freq_ghz": round(cpu_freq.current / 1000, 2) if cpu_freq else None,
                },
                "duckdb": {
                    "memory_limit": settings.duckdb_memory_limit,
                    "threads": settings.duckdb_threads,
                    "temp_directory": str(settings.data_dir / "duckdb_spill"),
                },
                "paths": {
                    "data_dir": str(settings.data_dir),
                    "logs_dir": str(settings.logs_dir),
                },
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system/usage")
async def get_resource_usage():
    """Return current RAM and CPU usage."""
    try:
        vm = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.5)

        # Estimate DuckDB spill usage
        spill_dir = settings.data_dir / "duckdb_spill"
        spill_size_mb = 0
        if spill_dir.exists():
            try:
                spill_size_mb = sum(
                    f.stat().st_size for f in spill_dir.rglob("*") if f.is_file()
                ) // (1024 * 1024)
            except Exception:
                pass

        return JSONResponse(
            {
                "ram": {
                    "total_gb": round(vm.total / 1024**3, 1),
                    "used_gb": round(vm.used / 1024**3, 1),
                    "available_gb": round(vm.available / 1024**3, 1),
                    "percent": vm.percent,
                },
                "cpu": {
                    "percent": cpu_percent,
                    "count": psutil.cpu_count(logical=True),
                },
                "disk_spill_mb": spill_size_mb,
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system/logs")
async def get_recent_logs(lines: int = 100):
    """Return recent log lines from processing.log."""
    try:
        log_file = settings.logs_dir / "processing.log"
        if not log_file.exists():
            return JSONResponse({"logs": [], "total_lines": 0})

        with open(log_file) as f:
            all_lines = f.readlines()

        # Return the last N lines
        recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return JSONResponse(
            {
                "logs": [line.rstrip() for line in recent],
                "total_lines": len(all_lines),
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
