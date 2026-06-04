# ============================================================
# Prediction Layer API Wrapper
# For optimizer integration
# ============================================================
# Required final input files in the same folder:
# 1. real_time_scheduler_input.csv
# 2. plan_ahead_scheduler_input.csv
#
# This file supports BOTH usage styles:
#
# A) Direct Python function calls from optimizer code:
#    from prediction_api_updated import predict_memory, predict_cpu, predict_workload
#
#    mem = predict_memory(collection_id="123", requested_memory=512)
#    cpu = predict_cpu(collection_id="123", requested_cpu=2)
#    workload = predict_workload(tenant_id="tenant_1", hours=6)
#
# B) FastAPI HTTP wrapper:
#    pip install fastapi uvicorn pandas
#    uvicorn prediction_api_updated:app --reload
#
# Endpoints:
#    GET /predict/realtime?collection_id=123&tenant_id=tenant_1
#    GET /predict/memory?collection_id=123&requested_memory=512
#    GET /predict/cpu?collection_id=123&requested_cpu=2
#    GET /predict/workload?tenant_id=tenant_1&hours=6
# ============================================================

from pathlib import Path
from typing import Optional, Dict, Any, List

import pandas as pd

# FastAPI is optional. The direct Python functions work even if FastAPI is not installed.
try:
    from fastapi import FastAPI, Query
except ImportError:  # pragma: no cover
    FastAPI = None

    def Query(default=None, **kwargs):
        return default


# ------------------------------------------------------------
# 1. File paths
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

REAL_TIME_PATH = BASE_DIR / "real_time_scheduler_input.csv"
PLAN_AHEAD_PATH = BASE_DIR / "plan_ahead_scheduler_input.csv"

if not REAL_TIME_PATH.exists():
    raise FileNotFoundError(f"Missing file: {REAL_TIME_PATH}")

if not PLAN_AHEAD_PATH.exists():
    raise FileNotFoundError(f"Missing file: {PLAN_AHEAD_PATH}")


# ------------------------------------------------------------
# 2. Load and validate final prediction files
# ------------------------------------------------------------
real_time_df = pd.read_csv(REAL_TIME_PATH)
plan_ahead_df = pd.read_csv(PLAN_AHEAD_PATH)

REQUIRED_REAL_TIME_COLUMNS = {
    "collection_id", "tenant_id", "prediction_time", "pred_mem_mb", "pred_cpu_p95"
}
REQUIRED_PLAN_AHEAD_COLUMNS = {
    "tenant_id", "period_index", "u", "sigma2"
}

missing_real_time = REQUIRED_REAL_TIME_COLUMNS - set(real_time_df.columns)
missing_plan_ahead = REQUIRED_PLAN_AHEAD_COLUMNS - set(plan_ahead_df.columns)

if missing_real_time:
    raise ValueError(f"real_time_scheduler_input.csv is missing columns: {sorted(missing_real_time)}")

if missing_plan_ahead:
    raise ValueError(f"plan_ahead_scheduler_input.csv is missing columns: {sorted(missing_plan_ahead)}")

real_time_df["collection_id"] = real_time_df["collection_id"].astype(str)
real_time_df["tenant_id"] = real_time_df["tenant_id"].astype(str)
real_time_df["pred_mem_mb"] = pd.to_numeric(real_time_df["pred_mem_mb"], errors="coerce")
real_time_df["pred_cpu_p95"] = pd.to_numeric(real_time_df["pred_cpu_p95"], errors="coerce")

plan_ahead_df["tenant_id"] = plan_ahead_df["tenant_id"].astype(str)
plan_ahead_df["period_index"] = pd.to_numeric(plan_ahead_df["period_index"], errors="coerce").astype(int)
plan_ahead_df["u"] = pd.to_numeric(plan_ahead_df["u"], errors="coerce")
plan_ahead_df["sigma2"] = pd.to_numeric(plan_ahead_df["sigma2"], errors="coerce")

# Remove rows with missing prediction values after conversion.
# The final CSVs should already have no nulls, but this prevents broken API output.
real_time_df = real_time_df.dropna(subset=["pred_mem_mb", "pred_cpu_p95"])
plan_ahead_df = plan_ahead_df.dropna(subset=["u", "sigma2"])

VALID_PERIODS = [0, 6, 12, 18]


# ------------------------------------------------------------
# 3. Pre-compute fallback values
# ------------------------------------------------------------
tenant_realtime_medians = (
    real_time_df
    .groupby("tenant_id")[["pred_mem_mb", "pred_cpu_p95"]]
    .median()
)

global_pred_mem_mb = float(real_time_df["pred_mem_mb"].median())
global_pred_cpu_p95 = float(real_time_df["pred_cpu_p95"].median())

tenant_plan_medians = (
    plan_ahead_df
    .groupby("tenant_id")[["u", "sigma2"]]
    .median()
)

global_u = float(plan_ahead_df["u"].median())
global_sigma2 = float(plan_ahead_df["sigma2"].median())


# ------------------------------------------------------------
# 4. Direct Python functions for optimizer code
# ------------------------------------------------------------
def health_check() -> Dict[str, Any]:
    """Return basic status information."""
    return {
        "status": "ok",
        "message": "Prediction Layer API/direct wrapper is running.",
        "real_time_rows": int(len(real_time_df)),
        "plan_ahead_rows": int(len(plan_ahead_df)),
        "available_plan_periods": sorted(plan_ahead_df["period_index"].unique().tolist()),
    }


def predict_realtime(
    collection_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Direct Python function for real-time scheduler.

    Returns both memory and CPU prediction.

    Fallback logic:
    1. Known collection_id -> collection-level prediction.
    2. Unknown collection_id + known tenant_id -> tenant median fallback.
    3. Unknown tenant/no tenant history -> global median fallback.
    """

    # Case 1: known collection_id
    if collection_id is not None:
        collection_id = str(collection_id)
        match = real_time_df[real_time_df["collection_id"] == collection_id]

        if not match.empty:
            row = match.iloc[0]
            return {
                "source": "collection_prediction",
                "collection_id": row["collection_id"],
                "tenant_id": row["tenant_id"],
                "prediction_time": row["prediction_time"],
                "pred_mem_mb": float(row["pred_mem_mb"]),
                "pred_cpu_p95": float(row["pred_cpu_p95"]),
            }

    # Case 2: tenant median fallback
    if tenant_id is not None:
        tenant_id = str(tenant_id)

        if tenant_id in tenant_realtime_medians.index:
            row = tenant_realtime_medians.loc[tenant_id]
            return {
                "source": "tenant_median_fallback",
                "collection_id": collection_id,
                "tenant_id": tenant_id,
                "prediction_time": None,
                "pred_mem_mb": float(row["pred_mem_mb"]),
                "pred_cpu_p95": float(row["pred_cpu_p95"]),
            }

    # Case 3: global fallback
    return {
        "source": "global_median_fallback",
        "collection_id": collection_id,
        "tenant_id": tenant_id,
        "prediction_time": None,
        "pred_mem_mb": global_pred_mem_mb,
        "pred_cpu_p95": global_pred_cpu_p95,
    }


def predict_memory(
    collection_id: Optional[str] = None,
    requested_memory: Optional[float] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Direct Python function expected by optimizer-style code.

    Example:
        predict_memory(collection_id="123", requested_memory=512)
        predict_memory(collection_id=None, requested_memory=512, tenant_id="tenant_1")

    requested_memory is accepted for compatibility. The returned prediction comes
    from the prediction file or fallback values, not directly from requested_memory.
    """

    result = predict_realtime(collection_id=collection_id, tenant_id=tenant_id)

    return {
        "source": result["source"],
        "collection_id": result["collection_id"],
        "tenant_id": result["tenant_id"],
        "requested_memory": requested_memory,
        "pred_mem_mb": float(result["pred_mem_mb"]),
    }


def predict_cpu(
    collection_id: Optional[str] = None,
    requested_cpu: Optional[float] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Direct Python function expected by optimizer-style code.

    Example:
        predict_cpu(collection_id="123", requested_cpu=2)
        predict_cpu(collection_id=None, requested_cpu=2, tenant_id="tenant_1")

    Note: The output field is named pred_cpu_p95 because that is the required
    optimizer column name, but the model target is P90 CPU peak.
    """

    result = predict_realtime(collection_id=collection_id, tenant_id=tenant_id)

    return {
        "source": result["source"],
        "collection_id": result["collection_id"],
        "tenant_id": result["tenant_id"],
        "requested_cpu": requested_cpu,
        "pred_cpu_p95": float(result["pred_cpu_p95"]),
    }


def predict_workload(
    tenant_id: Optional[str] = None,
    hours: Optional[int] = None,
    declared_job_count: int = 1,
) -> Dict[str, Any]:
    """
    Direct Python function for plan-ahead scheduler.

    Expected planning periods: 0, 6, 12, 18.

    Examples:
        predict_workload(tenant_id="tenant_1", hours=6)
        predict_workload(tenant_id="tenant_1")
        predict_workload()  # full batch for all tenants and all periods

    Fallback logic:
    1. Known tenant + known period -> tenant-period prediction.
    2. Known tenant + missing period -> tenant median u and default sigma2.
    3. Unknown tenant -> global median demand scaled by declared_job_count.
    """

    if hours is not None:
        hours = int(hours)
        if hours not in VALID_PERIODS:
            return {
                "error": "Invalid hours value.",
                "valid_hours": VALID_PERIODS,
            }

    # Case 1: full batch for all tenants and all periods
    if tenant_id is None and hours is None:
        records = plan_ahead_df[
            ["tenant_id", "period_index", "u", "sigma2"]
        ].to_dict(orient="records")

        for record in records:
            record["period_index"] = int(record["period_index"])
            record["u"] = float(record["u"])
            record["sigma2"] = float(record["sigma2"])

        return {
            "source": "batch_plan_ahead_predictions",
            "records": records,
        }

    # Case 2: one specific tenant
    if tenant_id is not None:
        tenant_id = str(tenant_id)
        tenant_rows = plan_ahead_df[plan_ahead_df["tenant_id"] == tenant_id]

        # Known tenant
        if not tenant_rows.empty:
            if hours is not None:
                matched = tenant_rows[tenant_rows["period_index"] == hours]

                if not matched.empty:
                    row = matched.iloc[0]
                    return {
                        "source": "tenant_period_prediction",
                        "tenant_id": tenant_id,
                        "period_index": int(row["period_index"]),
                        "u": float(row["u"]),
                        "sigma2": float(row["sigma2"]),
                    }

                # Known tenant but sparse/missing period fallback
                median_row = tenant_plan_medians.loc[tenant_id]
                fallback_u = float(median_row["u"])
                fallback_sigma2 = float((0.20 * fallback_u) ** 2)

                return {
                    "source": "tenant_period_fallback",
                    "tenant_id": tenant_id,
                    "period_index": hours,
                    "u": fallback_u,
                    "sigma2": fallback_sigma2,
                }

            # Return all periods for known tenant
            records = tenant_rows[
                ["tenant_id", "period_index", "u", "sigma2"]
            ].to_dict(orient="records")

            for record in records:
                record["period_index"] = int(record["period_index"])
                record["u"] = float(record["u"])
                record["sigma2"] = float(record["sigma2"])

            return {
                "source": "tenant_plan_ahead_predictions",
                "tenant_id": tenant_id,
                "records": records,
            }

        # Unknown tenant fallback
        job_count = max(int(declared_job_count or 1), 1)
        fallback_u = float(global_u * job_count)
        fallback_sigma2 = float((0.20 * fallback_u) ** 2)

        if hours is not None:
            return {
                "source": "global_fallback_unknown_tenant",
                "tenant_id": tenant_id,
                "period_index": hours,
                "u": fallback_u,
                "sigma2": fallback_sigma2,
            }

        records = [
            {
                "tenant_id": tenant_id,
                "period_index": h,
                "u": fallback_u,
                "sigma2": fallback_sigma2,
            }
            for h in VALID_PERIODS
        ]

        return {
            "source": "global_fallback_unknown_tenant",
            "tenant_id": tenant_id,
            "records": records,
        }

    # Case 3: hours only, all tenants for that period
    period_rows = plan_ahead_df[plan_ahead_df["period_index"] == hours]
    records = period_rows[
        ["tenant_id", "period_index", "u", "sigma2"]
    ].to_dict(orient="records")

    for record in records:
        record["period_index"] = int(record["period_index"])
        record["u"] = float(record["u"])
        record["sigma2"] = float(record["sigma2"])

    return {
        "source": "batch_period_predictions",
        "period_index": hours,
        "records": records,
    }


# ------------------------------------------------------------
# 5. Optional FastAPI endpoints
# ------------------------------------------------------------
if FastAPI is not None:
    app = FastAPI(
        title="Prediction Layer API",
        description="Wrapper API for real-time and plan-ahead optimizer inputs.",
        version="1.1.0",
    )

    @app.get("/")
    def api_health_check() -> Dict[str, Any]:
        return health_check()

    @app.get("/predict/realtime")
    def api_predict_realtime(
        collection_id: Optional[str] = Query(default=None),
        tenant_id: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        return predict_realtime(collection_id=collection_id, tenant_id=tenant_id)

    @app.get("/predict/memory")
    def api_predict_memory(
        collection_id: Optional[str] = Query(default=None),
        tenant_id: Optional[str] = Query(default=None),
        requested_memory: Optional[float] = Query(default=None),
    ) -> Dict[str, Any]:
        return predict_memory(
            collection_id=collection_id,
            requested_memory=requested_memory,
            tenant_id=tenant_id,
        )

    @app.get("/predict/cpu")
    def api_predict_cpu(
        collection_id: Optional[str] = Query(default=None),
        tenant_id: Optional[str] = Query(default=None),
        requested_cpu: Optional[float] = Query(default=None),
    ) -> Dict[str, Any]:
        return predict_cpu(
            collection_id=collection_id,
            requested_cpu=requested_cpu,
            tenant_id=tenant_id,
        )

    @app.get("/predict/workload")
    def api_predict_workload(
        tenant_id: Optional[str] = Query(default=None),
        hours: Optional[int] = Query(default=None),
        declared_job_count: int = Query(default=1),
    ) -> Dict[str, Any]:
        return predict_workload(
            tenant_id=tenant_id,
            hours=hours,
            declared_job_count=declared_job_count,
        )

else:
    app = None


# ------------------------------------------------------------
# 6. Simple manual test
# ------------------------------------------------------------
if __name__ == "__main__":
    print(health_check())
    print(predict_workload())
