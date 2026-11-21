"""Evaluation metrics (FR-09.3, FR-09.4, NFR-09.3)."""

from __future__ import annotations

import numpy as np


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-6) -> float:
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p) >= 0.5) == (np.asarray(y) == 1)))


def mae(pred: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(pred) - np.asarray(y))))


def calibration_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict]:
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    out = []
    for b in range(n_bins):
        m = idx == b
        if m.any():
            out.append({"bin_low": round(edges[b], 2), "bin_high": round(edges[b + 1], 2), "n": int(m.sum()),
                        "mean_predicted": round(float(p[m].mean()), 4),
                        "observed_rate": round(float(y[m].mean()), 4)})
    return out


def expected_calibration_error(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    bins = calibration_bins(p, y, n_bins)
    n = sum(b["n"] for b in bins)
    return float(sum(b["n"] / n * abs(b["mean_predicted"] - b["observed_rate"]) for b in bins)) if n else float("nan")


def binary_report(p: np.ndarray, y: np.ndarray) -> dict:
    return {"n": int(len(p)), "brier": round(brier(p, y), 5), "log_loss": round(log_loss(p, y), 5),
            "accuracy": round(accuracy(p, y), 4), "ece": round(expected_calibration_error(p, y), 4)}
