"""Measurement logging for trend analysis across test runs.

Tests record metrics (settling time, peak temperature, fault-trip latency,
...) into a MeasurementLog; the session-scoped `measurements` fixture in
conftest.py flushes it to a timestamped CSV under measurements/ once the
whole suite has finished. scripts/plot_trends.py later reads every CSV in
that directory to chart metrics across runs.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CSV_FIELDS = ["run_timestamp", "test", "metric", "value", "unit"]


@dataclass
class MeasurementLog:
    """Collects metric rows for one test run and flushes them to CSV."""

    run_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    _rows: list = field(default_factory=list, repr=False)

    def record(self, test_name: str, metric: str, value: float, unit: str) -> None:
        """Append one measurement row, stamped with this run's timestamp."""
        self._rows.append((self.run_timestamp, test_name, metric, value, unit))

    def flush(self, path: Path) -> None:
        """Write all recorded rows to a CSV file, creating parent dirs as needed."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            writer.writerows(self._rows)
