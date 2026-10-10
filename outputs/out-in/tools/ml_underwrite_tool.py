"""Scikit-learn inference with an explicit, hash-pinned model/feature contract.

NEVER load pickles from dossiers. Pickle is executable code; operator-reviewed
model artifacts are loaded only after hashes and sklearn version match. Demo
models use synthetic training data and are not calibrated for real borrowers.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import pickle
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from tools.reconciliation_tool import EvidenceError, dec, money

FEATURES = ["monthly_inflow", "monthly_outflow", "monthly_debt_service", "invoice_total", "cashflow_margin", "dsr"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EvidenceError("Cannot load reviewed ML adapter")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _checked(root: Path, relative: str, hashes: dict) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
        raise EvidenceError("Model/code asset missing or outside repository")
    if hashes.get(relative) != _sha(path):
        raise EvidenceError("Model/code hash differs from reviewed manifest")
    return path


def evaluate_credit_ml(financial_metrics: dict, model_dir: str | None = None,
                       mode: str = "live", requested_limit: str | None = None,
                       term_months: int = 12, annual_rate: str = "0.08") -> dict:
    import numpy as np
    import pandas as pd
    import sklearn
    from threadpoolctl import threadpool_limits
    if mode not in ("live", "demo"):
        raise EvidenceError("Mode must be live or demo")
    values = {key: dec(financial_metrics.get(key), key) for key in FEATURES}
    if values["monthly_inflow"] <= 0 or min(values[k] for k in FEATURES if k != "cashflow_margin") < 0:
        raise EvidenceError("Financial features contain invalid signs or zero income")
    computed_dsr = values["monthly_debt_service"] / values["monthly_inflow"]
    if abs(computed_dsr - values["dsr"]) > Decimal("1e-12"):
        raise EvidenceError("DSR feature differs from computed evidence")
    duration = dec(term_months, "loan duration")
    if duration != duration.to_integral_value() or not 1 <= duration <= 120:
        raise EvidenceError("Loan duration must be an integer in 1..120 months")
    term_months = int(duration)
    rate = dec(annual_rate, "annual rate")
    if not 0 <= rate <= 1:
        raise EvidenceError("Annual rate must be a decimal fraction in [0,1]")
    root = Path(os.environ.get("OUT_IN_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
    directory = Path(model_dir or root / "ml/models").resolve()
    manifest_path = directory / "model_manifest.json"
    if not manifest_path.is_file():
        raise EvidenceError("Reviewed model manifest is missing; run demo preparation or provide production models")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mode != "demo" and manifest.get("is_demo") is not False:
        raise EvidenceError("Synthetic demo models cannot be used for live inference")
    if manifest.get("sklearn_version") != sklearn.__version__:
        raise EvidenceError("scikit-learn version differs from training; reproduce the pinned environment")
    feature_names = manifest.get("feature_names")
    if (not isinstance(feature_names, list) or not feature_names
            or any(not isinstance(name, str) or not name.strip() for name in feature_names)
            or len(set(feature_names)) != len(feature_names)):
        raise EvidenceError("Manifest feature schema must be a nonempty ordered list of unique names")
    if mode == "demo" and feature_names != FEATURES:
        raise EvidenceError("Feature schema differs from demo financial features")
    hashes = manifest.get("sha256", {})
    for filename in ("credit_classifier.pkl", "credit_regressor.pkl"):
        path = directory / filename
        if not path.is_file() or path.is_symlink() or hashes.get(filename) != _sha(path):
            raise EvidenceError("Missing, unreviewed or changed model file")
    # Deliberate trusted-admin boundary. No uploaded filename reaches pickle.load.
    with (directory / "credit_classifier.pkl").open("rb") as handle:
        classifier = pickle.load(handle)
    with (directory / "credit_regressor.pkl").open("rb") as handle:
        regressor = pickle.load(handle)
    classes = list(getattr(classifier, "classes_", []))
    default = manifest.get("default_class")
    if "default_class" not in manifest or default not in classes:
        raise EvidenceError("Default class label is absent from classifier")
    if hasattr(classifier, "feature_names_in_") and list(classifier.feature_names_in_) != feature_names:
        raise EvidenceError("Classifier feature order mismatch")
    if hasattr(regressor, "feature_names_in_") and list(regressor.feature_names_in_) != feature_names:
        raise EvidenceError("Regressor feature order mismatch")
    if mode == "live":
        # Repository source interfaces were not supplied. Require explicit reviewed
        # adapters instead of guessing ml/feature_engineering.py or ml/predict.py.
        feature_path = _checked(root, "ml/feature_engineering.py", hashes)
        predict_path = _checked(root, "ml/predict.py", hashes)
        feature = _module(feature_path, "out_in_reviewed_features")
        prediction = _module(predict_path, "out_in_reviewed_prediction")
        if not callable(getattr(feature, "build_features", None)) or not callable(getattr(prediction, "infer_credit", None)):
            raise EvidenceError("Repository must implement build_features(metrics) and infer_credit(classifier,regressor,frame,default_class)")
        # Preserve explicit external feature values and their provenance; only the
        # six policy metrics are normalized here. Missing fields stay missing.
        adapter_metrics = {**financial_metrics, **{k: str(v) for k, v in values.items()}}
        frame = feature.build_features(adapter_metrics)
        if (not isinstance(frame, pd.DataFrame) or list(frame.columns) != feature_names
                or frame.shape != (1, len(feature_names))):
            raise EvidenceError("Reviewed feature adapter returned a different schema")
        if any(not pd.api.types.is_numeric_dtype(dtype)
               or pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_complex_dtype(dtype)
               for dtype in frame.dtypes):
            raise EvidenceError("Reviewed feature adapter must return finite numeric features")
        if not np.isfinite(frame.to_numpy(dtype=float, na_value=np.nan)).all():
            raise EvidenceError("Reviewed feature adapter must return finite numeric features")
        output = prediction.infer_credit(classifier, regressor, frame, default)
        probability, ceiling = float(output["default_probability"]), float(output["financing_ceiling"])
    else:
        frame = pd.DataFrame([[float(values[k]) for k in FEATURES]], columns=FEATURES)
        with threadpool_limits(limits=1):
            probability = float(classifier.predict_proba(frame)[0, classes.index(default)])
            ceiling = float(regressor.predict(frame)[0])
    if not np.isfinite(probability) or not 0 <= probability <= 1 or not np.isfinite(ceiling) or ceiling < 0:
        raise EvidenceError("ML returned nonfinite or invalid output; fail closed")
    requested = money(requested_limit or financial_metrics.get("requested_limit"), "requested limit")
    if requested <= 0:
        raise EvidenceError("Requested limit must be positive")
    predicted = Decimal(str(ceiling)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    support_cap = money(financial_metrics["invoice_total"]) * Decimal("0.8")
    # Include proposed debt in the DSR decision. Existing-only DSR is unsafe.
    per_unit_payment = Decimal(1) / Decimal(term_months) + rate / Decimal(12)
    capacity = max(Decimal(0), values["monthly_inflow"] * Decimal("0.45") - values["monthly_debt_service"])
    capacity_limit = (capacity / per_unit_payment).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    limit = min(requested, predicted, support_cap, capacity_limit).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    # Cent-conserving flat-interest schedule may have larger final installment.
    from tools.artifact_tool import _loan_model
    schedule = _loan_model({"principal": str(limit), "loan_cap": str(limit),
                            "term_months": term_months, "annual_rate": str(rate)})["repayments"]
    max_payment = max(Decimal(r["payment"]) for r in schedule)
    all_in_dsr = (values["monthly_debt_service"] + max_payment) / values["monthly_inflow"]
    risk_ok = Decimal(str(probability)) <= Decimal("0.12") and all_in_dsr <= Decimal("0.45") and limit > 0
    haircut = (requested * Decimal("0.50")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    proposed = min(haircut, predicted, support_cap, capacity_limit).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return {"default_probability": str(probability), "dsr": str(all_in_dsr), "existing_dsr": str(computed_dsr),
            "financing_ceiling": str(predicted), "requested_limit": str(requested),
            "approved_limit": str(limit) if risk_ok else "0.00", "proposed_limit": str(limit if risk_ok else proposed),
            "haircut_requested_limit": str(haircut) if not risk_ok else None,
            "passed": risk_ok, "review_required": not risk_ok,
            "gates": {"probability_le_0_12": probability <= 0.12, "all_in_dsr_le_0_45": all_in_dsr <= Decimal("0.45")},
            "model_hashes": {k: hashes[k] for k in ("credit_classifier.pkl", "credit_regressor.pkl")},
            "model_manifest_sha256": _sha(manifest_path), "is_demo_model": manifest.get("is_demo"),
            "feature_provenance": financial_metrics.get("provenance", "unspecified"),
            "policy": "max installment DSR; cap=min(requested,ML,80% invoices,DSR capacity); haircut never auto-approves"}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m tools.ml_underwrite_tool metrics.json")
    print(json.dumps(evaluate_credit_ml(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))), indent=2))
