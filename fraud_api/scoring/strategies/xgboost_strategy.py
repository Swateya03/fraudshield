"""
fraud_api/scoring/strategies/xgboost_strategy.py
──────────────────────────────────────────────────
XGBoost scoring strategy.
Loads model from local registry.
Uses SHAP for explainable reason codes.
"""

import os
import json
import pickle
from typing import Optional

from fraud_api.scoring.strategies.base import ScoringStrategy
from fraudshield_core.models import FeatureVector
from fraudshield_core.config import config


class XGBoostStrategy(ScoringStrategy):
    """
    Production ML strategy.
    Loads trained XGBoost model from model registry.
    Falls back to RuleBasedStrategy via circuit breaker
    if model file not found or inference fails.
    """
    name = "xgboost_v1"

    def __init__(self, model_path: str = None):
        self._model_path = model_path or os.path.join(
            config.MODEL_REGISTRY_PATH,
            config.CURRENT_MODEL_VERSION
        )
        self._model        = None
        self._explainer    = None
        self._model_loaded = False
        self._load_model()

    def _load_model(self) -> None:
        """
        Load model artifact from registry.
        Silent fail — circuit breaker will catch this.
        """
        model_file = os.path.join(self._model_path, "model.pkl")
        if not os.path.exists(model_file):
            print(f"  [XGBoostStrategy] Model not found at {model_file}")
            print(f"  Run: python scripts/train_model.py to train first")
            return
        try:
            with open(model_file, "rb") as f:
                self._model = pickle.load(f)
            # Load SHAP explainer for reason codes
            try:
                import shap
                self._explainer = shap.TreeExplainer(self._model)
            except Exception:
                pass  # SHAP optional — reason codes will be empty
            self._model_loaded = True
            print(f"  [XGBoostStrategy] Loaded model from {self._model_path}")
        except Exception as e:
            print(f"  [XGBoostStrategy] Failed to load model: {e}")

    def is_ready(self) -> bool:
        return self._model_loaded

    def score(self, features: FeatureVector) -> dict:
        """
        Returns fraud probability + SHAP-based reason codes.
        If model not loaded, raises — circuit breaker catches this
        and switches to RuleBasedStrategy.
        """
        if not self._model_loaded:
            raise RuntimeError("XGBoost model not loaded")

        import numpy as np

        # Build feature vector in exact order model was trained on
        X = np.array([features.to_model_input()])

        # Get probability
        prob = float(self._model.predict_proba(X)[0][1])

        # Get SHAP values for explainability
        reason_codes = []
        if self._explainer is not None:
            try:
                shap_values = self._explainer.shap_values(X)
                # shap_values[1] = fraud class contributions
                contributions = shap_values[1][0] if isinstance(shap_values, list) \
                                else shap_values[0]
                feature_names = FeatureVector.FEATURE_NAMES

                # Top 4 features by absolute contribution
                ranked = sorted(
                    zip(feature_names, contributions),
                    key=lambda x: abs(x[1]),
                    reverse=True
                )[:4]

                reason_codes = [
                    {"code": name, "contribution": round(float(val), 4)}
                    for name, val in ranked
                    if abs(val) > 0.01
                ]
            except Exception:
                pass  # SHAP failure is non-critical

        return {
            "score":        round(prob, 4),
            "reason_codes": reason_codes,
        }

    def reload(self, new_path: str = None) -> None:
        """Hot-reload a new model version. No restart needed."""
        if new_path:
            self._model_path = new_path
        self._model_loaded = False
        self._load_model()
