"""Evaluate webcam vision accuracy against labelled images.

Usage:
    python -m scripts.eval_webcam data/webcam_eval/images data/webcam_eval/labels.json

``labels.json`` maps image filenames (inside the images directory) to one of
snow_good, snow_poor, no_snow, camera_offline. Exit code 1 when accuracy is
below the 85% target, so this can gate prompt changes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.clients.openai_client import OpenAIClient
from app.settings import settings
from app.webcam.classifier import _SYSTEM_PROMPT
from app.webcam.evaluation import TARGET_ACCURACY, evaluate, load_labels


async def _run(images_dir: str, labels_file: str) -> int:
    labels = load_labels(labels_file)
    client = OpenAIClient()

    async def classify(path: Path) -> str:
        response = await client.complete_vision(
            system_prompt=_SYSTEM_PROMPT,
            user_text="Resort webcam image.",
            image_path=path,
            model=settings.openai_model_chat,
        )
        return str(response.get("classification"))

    report = await evaluate(images_dir, labels, classify)
    print(f"{report.correct}/{report.total} correct ({report.accuracy:.0%}); target {TARGET_ACCURACY:.0%}")
    for name, expected, got in report.mismatches:
        print(f"  MISMATCH {name}: expected {expected}, got {got}")
    return 0 if report.meets_target else 1


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(_run(sys.argv[1], sys.argv[2])))


if __name__ == "__main__":
    main()
