"""Lightweight metrics registry exposing Prometheus text format at ``/metrics``.

The architecture is metrics-*ready*: instrumentation points exist throughout
the job engine and service layers. Swapping in ``prometheus_client`` later
requires only replacing this module's internals.
"""

from __future__ import annotations

import threading
from typing import Dict, Tuple


def _label_key(labels: Dict[str, str]) -> str:
    return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


class Counter:
    def __init__(self, name: str, documentation: str) -> None:
        self.name = name
        self.documentation = documentation
        self._values: Dict[str, float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} counter"]
        with self._lock:
            for key, value in sorted(self._values.items()):
                rendered = f"{self.name}{{{key}}} {value}" if key else f"{self.name} {value}"
                lines.append(rendered)
        return lines


class Histogram:
    _DEFAULT_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600)

    def __init__(
        self, name: str, documentation: str, buckets: Tuple[float, ...] = _DEFAULT_BUCKETS
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.buckets = buckets
        self._counts: Dict[Tuple[Tuple[str, str], ...], Dict[float, int]] = {}
        self._sums: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._totals: Dict[Tuple[Tuple[str, str], ...], int] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            bucket_counts = self._counts.setdefault(key, {})
            cumulative_seen = False
            for bound in self.buckets:
                if value <= bound and not cumulative_seen:
                    bucket_counts[bound] = bucket_counts.get(bound, 0) + 1
                    cumulative_seen = True
                else:
                    bucket_counts.setdefault(bound, bucket_counts.get(bound, 0))
            # Ensure monotonic cumulative counts across buckets
            running = 0
            for bound in self.buckets:
                current = bucket_counts.get(bound, 0)
                if current > running:
                    running = current
                bucket_counts[bound] = running
            self._sums[key] = self._sums.get(key, 0.0) + value
            self._totals[key] = self._totals.get(key, 0) + 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key, bucket_counts in self._counts.items():
                label_pairs = list(key)
                cumulative = 0
                for bound in self.buckets:
                    cumulative = bucket_counts.get(bound, cumulative)
                    labels = label_pairs + [("le", str(bound))]
                    rendered_labels = ",".join(f'{k}="{v}"' for k, v in labels)
                    lines.append(f"{self.name}_bucket{{{rendered_labels}}} {cumulative}")
                labels_inf = label_pairs + [("le", "+Inf")]
                rendered_inf = ",".join(f'{k}="{v}"' for k, v in labels_inf)
                lines.append(f"{self.name}_bucket{{{rendered_inf}}} {self._totals.get(key, 0)}")
                base_labels = ",".join(f'{k}="{v}"' for k, v in label_pairs)
                suffix = f"{{{base_labels}}}" if base_labels else ""
                lines.append(f"{self.name}_sum{suffix} {self._sums.get(key, 0.0)}")
                lines.append(f"{self.name}_count{suffix} {self._totals.get(key, 0)}")
        return lines


jobs_total = Counter("infraops_jobs_total", "Total number of automation jobs by status.")
stage_failures_total = Counter(
    "infraops_stage_failures_total", "Failed pipeline stages by stage key."
)
vcenter_api_errors_total = Counter(
    "infraops_vcenter_api_errors_total", "Errors returned by the VMware service layer."
)
guest_op_failures_total = Counter(
    "infraops_guest_operation_failures_total", "Failed Windows guest operations."
)
app_install_failures_total = Counter(
    "infraops_application_install_failures_total", "Failed application installations."
)
http_requests_total = Counter(
    "infraops_http_requests_total", "HTTP requests processed by the API."
)
job_duration_seconds = Histogram(
    "infraops_job_duration_seconds", "End-to-end duration of completed provisioning jobs."
)


def render_metrics() -> str:
    collectors = (
        jobs_total,
        stage_failures_total,
        vcenter_api_errors_total,
        guest_op_failures_total,
        app_install_failures_total,
        http_requests_total,
    )
    lines: list[str] = []
    for collector in collectors:
        lines.extend(collector.render())
    lines.extend(job_duration_seconds.render())
    return "\n".join(lines) + "\n"
