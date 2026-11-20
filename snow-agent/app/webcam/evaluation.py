"""Measure vision-classification accuracy against manually labelled webcam images."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.webcam.classifier import _CLASSIFICATIONS

TARGET_ACCURACY = 0.85


@dataclass
class EvaluationReport:
    total: int
    correct: int
    mismatches: list[tuple[str, str, str]] = field(default_factory=list)  # (image, expected, got)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def meets_target(self) -> bool:
        return self.total > 0 and self.accuracy >= TARGET_ACCURACY


def load_labels(path: str | Path) -> dict[str, str]:
    """Load ``{image filename: label}`` and reject labels outside the classification set."""
    labels = json.loads(Path(path).read_text(encoding="utf-8"))
    bad = {name: label for name, label in labels.items() if label not in _CLASSIFICATIONS}
    if bad:
        raise ValueError(f"Unknown labels: {bad}")
    return labels


async def evaluate(
    images_dir: str | Path,
    labels: dict[str, str],
    classify: Callable[[Path], Awaitable[str]],
) -> EvaluationReport:
    report = EvaluationReport(total=0, correct=0)
    for name, expected in sorted(labels.items()):
        got = await classify(Path(images_dir) / name)
        report.total += 1
        if got == expected:
            report.correct += 1
        else:
            report.mismatches.append((name, expected, got))
    return report
