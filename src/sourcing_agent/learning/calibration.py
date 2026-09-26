"""Calibration: corrects assumption-based estimates with the regression fitted on logged passes."""

# Estimator type.
from typing import Callable
# File paths.
from pathlib import Path

# Cost ranges.
from ..core.pricing import Range, money
# Estimated-vs-actual regression.
from .regression import Fit, fit_line
# Logged passes.
from .runlog import load_runs

# Prices a pass from its shape: (shape, model, batch) -> cost range. Supplied by the worker that logged the pass.
Estimator = Callable[[list[tuple[int, int]], str, bool], Range]


def clean_passes(records: list[dict]) -> list[dict]:
    """Passes with no errored rows (errored rows cut a pass short, so its cost teaches the wrong thing)."""
    # Keep only complete passes.
    return [r for r in records if not r["error_rows"]]


def training_points(records: list[dict], estimator: Estimator) -> list[tuple[float, float]]:
    """(estimate midpoint under today's assumptions, actual cost) for each clean pass."""
    # Re-estimating from the stored shape keeps old passes usable after assumptions change.
    return [(estimator([tuple(c) for c in r["shape"]], r["model"], r["batch"]).mid, r["actual_cost"]) for r in clean_passes(records)]


def calibrate(log_path: Path, estimator: Estimator) -> Fit | None:
    """Fit the logged passes; None before any clean pass."""
    # Least-squares line over the training points.
    return fit_line(training_points(load_runs(log_path), estimator))


def compare_line(model: str, actual: float, estimate: Range, fit: Fit | None, error_rows: int) -> str:
    """One line comparing a pass's actual cost with its estimate and the calibrated prediction."""
    # Calibrated prediction from passes logged before this one.
    calibrated = f", calibrated ${fit.predict(estimate.mid):.2f}" if fit else ""
    # Errored passes are logged but left out of the fit.
    note = f" ({error_rows} errored rows: excluded from calibration)" if error_rows else ""
    # The comparison.
    return f"Actual vs estimate ({model}): ${actual:.2f} vs {money(estimate)}{calibrated}{note}"
