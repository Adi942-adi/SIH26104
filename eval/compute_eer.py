import argparse
import csv
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from sklearn.metrics import roc_curve


def compute_eer(scores, labels):
    """Return (EER, threshold) for scores whose positive class is bona fide."""
    score_values = np.asarray(scores, dtype=float)
    label_values = np.asarray(labels, dtype=int)

    if score_values.ndim != 1 or label_values.ndim != 1:
        raise ValueError("scores and labels must be one-dimensional")
    if len(score_values) != len(label_values) or len(score_values) == 0:
        raise ValueError("scores and labels must be non-empty and equally sized")
    if not np.isin(label_values, (0, 1)).all() or len(np.unique(label_values)) != 2:
        raise ValueError("labels must include both 0 (spoof) and 1 (bona fide)")

    fpr, tpr, thresholds = roc_curve(
        label_values, score_values, pos_label=1, drop_intermediate=False
    )
    fnr = 1.0 - tpr

    # Keep the final point at each repeated FPR so interpolation is well-defined.
    unique_fpr, reversed_indices = np.unique(fpr[::-1], return_index=True)
    indices = len(fpr) - 1 - reversed_indices
    unique_fnr = fnr[indices]
    unique_thresholds = thresholds[indices]

    eer = brentq(lambda point: np.interp(point, unique_fpr, unique_fnr) - point, 0.0, 1.0)
    threshold = float(np.interp(eer, unique_fpr, unique_thresholds))
    return float(eer), threshold


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute EER from filename,score,label CSV data. The CSV carries "
                    "AASIST risk (score ~ 1 = spoof), so scores are inverted to the "
                    "bonafide-positive axis before the EER is computed."
    )
    parser.add_argument("csv_path", type=Path, help="CSV containing filename,score,label columns")
    args = parser.parse_args()

    scores = []
    labels = []
    with args.csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        required_columns = {"filename", "score", "label"}
        if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
            raise ValueError("CSV must contain filename,score,label columns")
        for row in reader:
            scores.append(1.0 - float(row["score"]))
            labels.append(int(row["label"]))

    eer, threshold = compute_eer(scores, labels)
    print(f"EER: {eer:.2%}")
    print(f"Threshold at EER: {threshold:.6f}")


if __name__ == "__main__":
    main()
