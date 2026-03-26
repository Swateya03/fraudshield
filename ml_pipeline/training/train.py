"""
ml_pipeline/training/train.py
──────────────────────────────
XGBoost model training with MLflow tracking.
Saves model artifact to local registry.

Run: python scripts/train_model.py
"""

import os
import pickle
import json
from datetime import datetime
from typing import Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    roc_auc_score, precision_score, recall_score,
    f1_score, precision_recall_curve
)
import xgboost as xgb
import mlflow
import mlflow.xgboost

from fraudshield_core.config import config
from fraudshield_core.models import FeatureVector, ModelMetadata


def train(X: pd.DataFrame, y: pd.Series,
          version: str = None) -> Tuple[object, ModelMetadata]:
    """
    Train XGBoost classifier. Track with MLflow. Save to registry.

    Returns (model, metadata).
    """
    version = version or f"v{datetime.utcnow().strftime('%Y%m%d_%H%M')}"

    # ── Split ─────────────────────────────────────────────────
    X_train, X_temp, y_train, y_temp = train_test_split(
        X, y, test_size=0.30, random_state=42, stratify=y
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_temp, y_temp, test_size=0.50, random_state=42, stratify=y_temp
    )

    print(f"  Train: {len(X_train):,}  Val: {len(X_val):,}  Test: {len(X_test):,}")

    # ── MLflow tracking ───────────────────────────────────────
    from pathlib import Path as _Path
    _uri = config.MLFLOW_TRACKING_URI
    if not _uri.startswith(("http", "file:", "databricks", "sqlite", "postgresql")):
        _uri = _Path(_uri).resolve().as_uri()   # converts C:\... → file:///C:/...
    mlflow.set_tracking_uri(_uri)
    mlflow.set_experiment("fraudshield-cnp-detection")

    with mlflow.start_run(run_name=version):

        # ── Train ─────────────────────────────────────────────
        # Class imbalance: fraud is ~8% — use scale_pos_weight
        neg_count  = (y_train == 0).sum()
        pos_count  = (y_train == 1).sum()
        scale_pos  = neg_count / max(pos_count, 1)

        model = xgb.XGBClassifier(
            n_estimators      = 300,
            max_depth         = 6,
            learning_rate     = 0.05,
            subsample         = 0.8,
            colsample_bytree  = 0.8,
            scale_pos_weight  = scale_pos,
            use_label_encoder = False,
            eval_metric       = "logloss",
            random_state      = 42,
            verbosity         = 0,
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        # ── Evaluate ──────────────────────────────────────────
        y_prob = model.predict_proba(X_test)[:, 1]
        auc    = roc_auc_score(y_test, y_prob)

        # Find threshold that maximises F1
        precisions, recalls, thresholds = precision_recall_curve(y_test, y_prob)
        f1_scores  = 2 * (precisions * recalls) / (precisions + recalls + 1e-8)
        best_idx   = f1_scores.argmax()
        threshold  = float(thresholds[best_idx])

        # Guard: perfectly separable synthetic data returns threshold ~1.0
        # which causes the API to never block anything.
        # Fall back to 15th percentile of fraud scores on the test set.
        if threshold >= 0.90:
            threshold = float(np.percentile(y_prob[y_test == 1], 15))
            threshold = round(max(0.30, min(threshold, 0.89)), 4)
            print(f"  [threshold] PR fallback applied → {threshold:.4f}")

        y_pred = (y_prob >= threshold).astype(int)
        precision  = precision_score(y_test, y_pred, zero_division=0)
        recall     = recall_score(y_test, y_pred, zero_division=0)
        f1         = f1_score(y_test, y_pred, zero_division=0)

        print(f"\n  Results:")
        print(f"    AUC-ROC:   {auc:.4f}")
        print(f"    Precision: {precision:.4f}  (at threshold {threshold:.2f})")
        print(f"    Recall:    {recall:.4f}")
        print(f"    F1:        {f1:.4f}")

        # Log to MLflow
        mlflow.log_params({
            "version": version,
            "n_estimators": 300,
            "max_depth": 6,
            "threshold": threshold,
            "train_size": len(X_train),
            "fraud_rate": float(y.mean()),
        })
        mlflow.log_metrics({
            "auc_roc":   auc,
            "precision": precision,
            "recall":    recall,
            "f1":        f1,
        })
        mlflow.xgboost.log_model(model, "model")

        # ── Build metadata ────────────────────────────────────
        metadata = ModelMetadata(
            version       = version,
            trained_at    = datetime.utcnow(),
            training_rows = len(X_train),
            fraud_rate    = float(y.mean()),
            auc_roc       = round(auc, 4),
            precision     = round(precision, 4),
            recall        = round(recall, 4),
            f1_score      = round(f1, 4),
            threshold     = round(threshold, 4),
            feature_names = FeatureVector.FEATURE_NAMES,
            is_champion   = False,
        )

    return model, metadata
