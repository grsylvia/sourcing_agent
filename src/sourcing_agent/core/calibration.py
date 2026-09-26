"""Cost calibration: logs actual run costs and fits actual = intercept + slope × estimate by least squares."""

# Run log format.
import json
# Standard error of the fit.
import math
# Fit record.
from dataclasses import dataclass
# File paths.
from pathlib import Path

# Logged passes needed before the fit gets an intercept (fewer fit a ratio through the origin).
MIN_POINTS_FOR_INTERCEPT = 3


# A fitted calibration line.
@dataclass
class Fit:
    # Fixed cost per pass, USD (0 for a ratio fit).
    intercept: float
    # Actual dollars per estimated dollar.
    slope: float
    # Passes the fit used.
    n: int
    # Share of variance explained, or None when it cannot be computed.
    r2: float | None
    # Typical prediction error, USD, or None with too few passes.
    error: float | None
    # True for a ratio through the origin (too few passes or a non-positive slope).
    ratio: bool = False

    def predict(self, estimate: float) -> float:
        """Return the calibrated cost for an estimate, never below zero."""
        # Point on the fitted line.
        return max(0.0, self.intercept + self.slope * estimate)


def append_run(path: Path, record: dict) -> None:
    """Append one pass record to the run log."""
    # One JSON object per line.
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_runs(path: Path) -> list[dict]:
    """Read every pass record, skipping unreadable lines."""
    # No log yet.
    if not path.exists():
        return []
    # Parsed records.
    records = []
    # One record per non-blank line.
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A damaged line is ignored.
            continue
    # All readable records.
    return records


def fit_line(points: list[tuple[float, float]]) -> Fit | None:
    """Fit actual cost against estimated cost; None when no usable points."""
    # Only points with a positive estimate carry information.
    points = [(x, y) for x, y in points if x > 0]
    # Number of points.
    n = len(points)
    # Nothing to fit.
    if n == 0:
        return None
    # Means of estimate and actual.
    mx, my = sum(x for x, _ in points) / n, sum(y for _, y in points) / n
    # Spread of the estimates.
    sxx = sum((x - mx) ** 2 for x, _ in points)
    # Ordinary least squares with intercept once there are enough varied points.
    if n >= MIN_POINTS_FOR_INTERCEPT and sxx > 0:
        slope = sum((x - mx) * (y - my) for x, y in points) / sxx
        intercept = my - slope * mx
        params = 2
    else:
        slope, intercept, params = 0.0, 0.0, 1
    # Fall back to a ratio through the origin for too few points or a non-positive slope.
    if slope <= 0:
        slope = sum(x * y for x, y in points) / sum(x * x for x, _ in points)
        intercept, params = 0.0, 1
    # Residual sum of squares.
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    # Total sum of squares around the mean.
    ss_tot = sum((y - my) ** 2 for _, y in points)
    # Variance explained, when the actuals vary.
    r2 = 1 - ss_res / ss_tot if n >= MIN_POINTS_FOR_INTERCEPT and ss_tot > 0 else None
    # Standard error of the regression, when degrees of freedom remain.
    error = math.sqrt(ss_res / (n - params)) if n > params else None
    # The fitted line.
    return Fit(intercept, slope, n, r2, error, params == 1)
