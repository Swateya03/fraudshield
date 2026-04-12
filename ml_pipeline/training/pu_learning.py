"""
ml_pipeline/training/pu_learning.py
────────────────────────────────────
Elkan-Noto two-step Positive-Unlabeled (PU) Learning.

Step 1: Train an initial XGBoost on labeled data (fraud=1, everything else=0).
Step 2: Score unlabeled data; assign soft negative labels with confidence weights.
Step 3: Retrain on combined (labeled + weighted unlabeled) dataset.

This addresses the sparse-label bias where most negatives are actually unlabeled,
not confirmed legitimate.
"""

from datetime import datetime
from typing import Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score, precision_recall_curve
from scipy.stats import ks_2samp
import xgboost as xgb
import mlflow

from fraudshield_core.config import config
from fraudshield_core.models import FeatureVector, ModelMetadata


def pu_train(
    X: pd.DataFrame,
    y_partial: pd.Series,
    groups: np.ndarray,
    is_labeled: np.ndarray,
    version: str = None,
) -> Tuple[object, ModelMetadata]:
    """
    PU Learning: two-step Elkan-Noto approach.
    Returns (calibrated_model, metadata).
    """
    version = version or f"pu_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"

    # ── Split: use only labeled data for evaluation ───────────
    labeled_mask = is_labeled
    X_labeled  = X[labeled_mask]
    y_labeled  = y_partial[labeled_mask]
    g_labeled  = groups[labeled_mask]

    gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
    train_idx, test_idx = next(gss.split(X_labeled, y_labeled, g_labeled))
    X_test_eval = X_labeled.iloc[test_idx]
    y_test_eval = y_labeled.iloc[test_idx]

    print(f"  PU Learning | Total: {len(X):,} | Labeled: {labeled_mask.sum():,} | "
          f"Held-out test: {len(X_test_eval):,}")

    # ── Step 1: Initial model on all data (fraud=1, rest=0) ──
    neg_count = (y_partial == 0).sum()
    pos_count = (y_partial == 1).sum()
    scale_pos = neg_count / max(pos_count, 1)

    model_init = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        use_label_encoder=False, eval_metric="logloss",
        random_state=42, verbosity=0,
    )
    model_init.fit(X, y_partial, verbose=False)

    # ── Step 2: Score unlabeled, assign soft negatives ────────
    unlabeled_mask = ~is_labeled
    X_unlabeled = X[unlabeled_mask]
    probs_unlabeled = model_init.predict_proba(X_unlabeled)[:, 1]

    confidence_weights = 1.0 - probs_unlabeled
    soft_labels = (probs_unlabeled >= 0.5).astype(int)

    # ── Step 3: Retrain on combined dataset ───────────────────
    X_combined = pd.concat([X, X_unlabeled], ignore_index=True)
    y_combined = pd.concat([y_partial, pd.Series(soft_labels, index=X_unlabeled.index)], ignore_index=True)
    weights = np.concatenate([
        np.ones(len(X)),
        confidence_weights,
    ])

    model_final = xgb.XGBClassifier(
        n_estimators=300, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        use_label_encoder=False, eval_metric="logloss",
        random_state=42, verbosity=0,
    )
    model_final.fit(X_combined, y_combined, sample_weight=weights, verbose=False)

    # ── Calibrate ─────────────────────────────────────────────
    X_cal = X_labeled.iloc[train_idx]
    y_cal = y_labeled.iloc[train_idx]
    calibrated = CalibratedClassifierCV(model_final, cv="prefit", method="sigmoid")
    calibrated.fit(X_cal, y_cal)

    # ── Evaluate on held-out labeled test set ─────────────────
    y_prob = calibrated.predict_proba(X_test_eval)[:, 1]
    auc = roc_auc_score(y_test_eval, y_prob)

    fraud_scores = y_prob[y_test_eval == 1]
    legit_scores = y_prob[y_test_eval == 0]
    ks_stat = float(ks_2samp(fraud_scores, legit_scores).statistic) if len(fraud_scores) > 0 and len(legit_scores) > 0 else 0.0

    precisions, recalls, thresholds = precision_recall_curve(y_test_eval, y_prob)
    f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-8)
    best_idx  = f1_scores.argmax()
    threshold = float(thresholds[best_idx])

    if threshold >= 0.90:
        threshold = float(np.percentile(y_prob[y_test_eval == 1], 15))
        threshold = round(max(0.30, min(threshold, 0.89)), 4)
        print(f"  [threshold] PR fallback applied -> {threshold:.4f}")

    y_pred = (y_prob >= threshold).astype(int)
    prec   = precision_score(y_test_eval, y_pred, zero_division=0)
    rec    = recall_score(y_test_eval, y_pred, zero_division=0)
    f1     = f1_score(y_test_eval, y_pred, zero_division=0)

    print(f"\n  PU Results:")
    print(f"    AUC-ROC:      {auc:.4f}")
    print(f"    KS Statistic: {ks_stat:.4f}")
    print(f"    Precision:    {prec:.4f}  (at threshold {threshold:.2f})")
    print(f"    Recall:       {rec:.4f}")
    print(f"    F1:           {f1:.4f}")

    metadata = ModelMetadata(
        version       = version,
        trained_at    = datetime.utcnow(),
        training_rows = len(X_combined),
        fraud_rate    = float(y_partial.mean()),
        auc_roc       = round(auc, 4),
        precision     = round(prec, 4),
        recall        = round(rec, 4),
        f1_score      = round(f1, 4),
        threshold     = round(threshold, 4),
        feature_names = FeatureVector.FEATURE_NAMES,
        is_champion   = False,
        ks_statistic  = round(ks_stat, 4),
        calibration   = "platt",
    )

    return calibrated, metadata
