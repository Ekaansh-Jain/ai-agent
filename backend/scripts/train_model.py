"""Train the IsolationForest anomaly model on HI-Small account profiles and persist it.

Unsupervised — labels are never used. Run once; the live engine + eval load the saved model.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nexus import anomaly  # noqa: E402
from nexus.config import Settings  # noqa: E402
from nexus.ingest import load_dataset  # noqa: E402
from nexus.profiles import build_profiles  # noqa: E402


def main():
    ds = load_dataset(Settings(variant="HI-Small"))
    profiles = build_profiles(ds.con)
    print(f"training IsolationForest on {len(profiles):,} account profiles (unsupervised) ...")
    model = anomaly.train(profiles)
    anomaly.save(model)
    scores = model.score_frame(profiles)
    print(f"saved -> {anomaly.MODEL_PATH}")
    print(f"anomaly score distribution: min={scores.min():.3f} "
          f"median={scores.median():.3f} p99={scores.quantile(0.99):.3f} max={scores.max():.3f}")


if __name__ == "__main__":
    main()
