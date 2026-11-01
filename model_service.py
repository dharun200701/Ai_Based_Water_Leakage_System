"""Dataset validation, reproducible model training, and evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

FEATURES = [
    "Pressure_PSI",
    "Flow_Rate_Lmin",
    "Temperature_C",
    "Vibration_mms",
    "RPM",
    "Operational_Hours_h",
]
TARGET = "Leakage_Flag"


def load_and_train(dataset_path: str | Path) -> tuple[RandomForestClassifier, dict[str, Any]]:
    path = Path(dataset_path)
    if not path.is_file():
        raise ValueError(f"Training dataset was not found: {path.name}")
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as error:
        raise ValueError(f"Training dataset could not be read: {error}") from error
    missing_columns = [column for column in FEATURES + [TARGET] if column not in frame.columns]
    if missing_columns:
        raise ValueError("Dataset is missing required columns: " + ", ".join(missing_columns))

    data = frame[FEATURES + [TARGET]].copy()
    for column in FEATURES + [TARGET]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    missing_summary = {
        column: int(data[column].isna().sum())
        for column in FEATURES + [TARGET]
    }
    data = data.dropna()
    if len(data) < 10:
        raise ValueError("At least 10 complete numeric dataset rows are required to train.")
    if not set(data[TARGET].unique()).issubset({0, 1}):
        raise ValueError("Leakage_Flag must contain only binary 0/1 labels.")
    distribution = {str(int(label)): int(count) for label, count in data[TARGET].value_counts().items()}
    if len(distribution) != 2 or min(data[TARGET].value_counts()) < 2:
        raise ValueError("The training dataset must contain at least two examples of both target classes.")

    x_train, x_test, y_train, y_test = train_test_split(
        data[FEATURES], data[TARGET].astype(int), test_size=0.2,
        random_state=42, stratify=data[TARGET].astype(int),
    )
    model = RandomForestClassifier(
        n_estimators=100, random_state=42, class_weight=None,
    )
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)
    class_index = list(model.classes_).index(1)
    metrics: dict[str, Any] = {
        "dataset_filename": path.name,
        "source_rows": int(len(frame)),
        "usable_rows": int(len(data)),
        "missing_values": missing_summary,
        "class_distribution": distribution,
        "features": FEATURES,
        "target": TARGET,
        "train_rows": int(len(x_train)),
        "test_rows": int(len(x_test)),
        "metrics": {
            "accuracy": float(accuracy_score(y_test, predictions)),
            "precision": float(precision_score(y_test, predictions, zero_division=0)),
            "recall": float(recall_score(y_test, predictions, zero_division=0)),
            "f1": float(f1_score(y_test, predictions, zero_division=0)),
            "roc_auc": float(roc_auc_score(y_test, probabilities[:, class_index])),
            "confusion_matrix": confusion_matrix(y_test, predictions, labels=[0, 1]).tolist(),
            "classification_report": classification_report(
                y_test, predictions, labels=[0, 1], target_names=["Normal", "Leak"],
                output_dict=True, zero_division=0,
            ),
        },
        "limitations": [
            "Metrics are from a single stratified holdout split and may not represent field performance.",
            "Training examples are dataset-derived; manual readings are not live sensor measurements.",
            "Model probability is an uncalibrated classifier score, not a real-world leak probability.",
        ],
    }
    return model, metrics
