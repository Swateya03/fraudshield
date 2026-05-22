"""
ml_pipeline/training/registry.py
──────────────────────────────────
ModelRegistry interface + LocalFileRegistry.

MVP:        LocalFileRegistry (versioned folders on disk)
Production: MLflowRegistry (swap one line)

Version layout:
  local_store/model_registry/
    v1.0.0/
      model.pkl
      metadata.json
    v1.1.0/
      model.pkl
      metadata.json
    current -> v1.1.0   (symlink to champion)
"""

import os
import json
import pickle
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List
from dataclasses import asdict

from fraudshield_core.models import ModelMetadata
from fraudshield_core.config import config


class ModelRegistry(ABC):

    @abstractmethod
    def save(self, model: object, metadata: ModelMetadata) -> str:
        """Save model + metadata. Returns version string."""
        ...

    @abstractmethod
    def load(self, version: str = "current") -> tuple:
        """Load model + metadata. Returns (model, metadata)."""
        ...

    @abstractmethod
    def promote(self, version: str) -> None:
        """Promote a version to champion (current)."""
        ...

    @abstractmethod
    def list_versions(self) -> List[ModelMetadata]:
        ...


class LocalFileRegistry(ModelRegistry):
    """
    Saves models as versioned folders on disk.
    Simple, no server needed, works for MVP.

    Production upgrade: swap to MLflowRegistry.
    Interface stays identical.
    """

    def __init__(self, base_path: str = None):
        self.base_path = base_path or config.MODEL_REGISTRY_PATH
        os.makedirs(self.base_path, exist_ok=True)

    def save(self, model: object, metadata: ModelMetadata) -> str:
        version_dir = os.path.join(self.base_path, metadata.version)
        os.makedirs(version_dir, exist_ok=True)

        # Save model artifact
        with open(os.path.join(version_dir, "model.pkl"), "wb") as f:
            pickle.dump(model, f)

        # Save metadata
        meta_dict = {
            "version":       metadata.version,
            "trained_at":    metadata.trained_at.isoformat(),
            "training_rows": metadata.training_rows,
            "fraud_rate":    metadata.fraud_rate,
            "auc_roc":       metadata.auc_roc,
            "precision":     metadata.precision,
            "recall":        metadata.recall,
            "f1_score":      metadata.f1_score,
            "threshold":     metadata.threshold,
            "feature_names": metadata.feature_names,
            "is_champion":   metadata.is_champion,
            "ks_statistic":  metadata.ks_statistic,
            "calibration":   metadata.calibration,
        }
        with open(os.path.join(version_dir, "metadata.json"), "w") as f:
            json.dump(meta_dict, f, indent=2)

        print(f"  [ok] Saved model {metadata.version} to {version_dir}")
        return metadata.version

    def load(self, version: str = "current") -> tuple:
        if version == "current":
            version = self._get_current_version()
            if not version:
                raise FileNotFoundError(
                    "No model registered. Run from project root: python scripts/train_model.py "
                    f"(registry path: {self.base_path})"
                )

        version_dir = os.path.join(self.base_path, version)
        if not os.path.exists(version_dir):
            raise FileNotFoundError(f"Model version {version} not found")

        with open(os.path.join(version_dir, "model.pkl"), "rb") as f:
            model = pickle.load(f)

        with open(os.path.join(version_dir, "metadata.json")) as f:
            meta_dict = json.load(f)

        metadata = ModelMetadata(
            version       = meta_dict["version"],
            trained_at    = datetime.fromisoformat(meta_dict["trained_at"]),
            training_rows = meta_dict["training_rows"],
            fraud_rate    = meta_dict["fraud_rate"],
            auc_roc       = meta_dict["auc_roc"],
            precision     = meta_dict["precision"],
            recall        = meta_dict["recall"],
            f1_score      = meta_dict["f1_score"],
            threshold     = meta_dict["threshold"],
            feature_names = meta_dict["feature_names"],
            is_champion   = meta_dict.get("is_champion", False),
            ks_statistic  = meta_dict.get("ks_statistic", 0.0),
            calibration   = meta_dict.get("calibration", "none"),
        )
        return model, metadata

    def promote(self, version: str) -> None:
        """Mark version as champion. Updates CURRENT pointer and saves previous champion to PREVIOUS."""
        # Save current champion as PREVIOUS (enables rollback)
        current = self._get_current_version()
        if current and current != version:
            previous_file = os.path.join(self.base_path, "PREVIOUS")
            with open(previous_file, "w") as f:
                f.write(current)

        # Mark old champion
        for v in self.list_versions():
            if v.is_champion:
                v_dir = os.path.join(self.base_path, v.version)
                meta_path = os.path.join(v_dir, "metadata.json")
                with open(meta_path) as f:
                    meta = json.load(f)
                meta["is_champion"] = False
                with open(meta_path, "w") as f:
                    json.dump(meta, f, indent=2)

        # Mark new champion
        v_dir     = os.path.join(self.base_path, version)
        meta_path = os.path.join(v_dir, "metadata.json")
        with open(meta_path) as f:
            meta = json.load(f)
        meta["is_champion"] = True
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        # Write current pointer file
        current_file = os.path.join(self.base_path, "CURRENT")
        with open(current_file, "w") as f:
            f.write(version)

        print(f"  [ok] Promoted {version} to champion")

    def get_previous_version(self) -> Optional[str]:
        """Return the version that was champion before the current one, if known."""
        previous_file = os.path.join(self.base_path, "PREVIOUS")
        if os.path.exists(previous_file):
            with open(previous_file) as f:
                return f.read().strip() or None
        return None

    def rollback(self) -> Optional[str]:
        """Promote the previous champion. Returns rolled-back version, or None if no previous exists."""
        previous = self.get_previous_version()
        if not previous:
            return None
        self.promote(previous)
        # Clear PREVIOUS so we don't double-rollback
        previous_file = os.path.join(self.base_path, "PREVIOUS")
        if os.path.exists(previous_file):
            os.remove(previous_file)
        return previous

    def list_versions(self) -> List[ModelMetadata]:
        versions = []
        for entry in os.scandir(self.base_path):
            if entry.is_dir():
                meta_path = os.path.join(entry.path, "metadata.json")
                if os.path.exists(meta_path):
                    with open(meta_path) as f:
                        meta = json.load(f)
                    versions.append(ModelMetadata(
                        version       = meta["version"],
                        trained_at    = datetime.fromisoformat(meta["trained_at"]),
                        training_rows = meta["training_rows"],
                        fraud_rate    = meta["fraud_rate"],
                        auc_roc       = meta["auc_roc"],
                        precision     = meta["precision"],
                        recall        = meta["recall"],
                        f1_score      = meta["f1_score"],
                        threshold     = meta["threshold"],
                        feature_names = meta["feature_names"],
                        is_champion   = meta.get("is_champion", False),
                        ks_statistic  = meta.get("ks_statistic", 0.0),
                        calibration   = meta.get("calibration", "none"),
                    ))
        return sorted(versions, key=lambda v: v.trained_at)

    def get_champion_version(self) -> Optional[str]:
        """Version id for the champion model (CURRENT file, else latest by trained_at)."""
        return self._get_current_version()

    def _get_current_version(self) -> Optional[str]:
        current_file = os.path.join(self.base_path, "CURRENT")
        if os.path.exists(current_file):
            with open(current_file) as f:
                return f.read().strip()
        # Fall back to latest
        versions = self.list_versions()
        return versions[-1].version if versions else None
