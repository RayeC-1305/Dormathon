"""Generate explicitly synthetic demo dossiers/models, never real lending data.
Run from source root: python scripts/prepare_demo.py
"""
from __future__ import annotations
import hashlib
import json
import sys
import pickle
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import numpy as np
    import pandas as pd
    import sklearn
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from openpyxl import Workbook
    from tools.ml_underwrite_tool import FEATURES
    demo = ROOT / "demo"
    models, dossier = demo / "models", demo / "dossier"
    models.mkdir(parents=True, exist_ok=True)
    dossier.mkdir(parents=True, exist_ok=True)
    expected_dossier = {"invoice.pdf", "packing_list.pdf", "statement.xlsx"}
    if any(p.name not in expected_dossier or p.is_symlink() or p.is_junction() or not p.is_file()
           for p in dossier.iterdir()):
        raise RuntimeError("Demo dossier must contain only the three generated regular fixture files")
    for directory in (demo, models, dossier):
        if directory.is_symlink() or directory.is_junction():
            raise RuntimeError("Refusing linked demo directory")
    for path in [demo / "dossier.zip", demo / "invoice.json",
                 models / "credit_classifier.pkl", models / "credit_regressor.pkl", models / "model_manifest.json"]:
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("Refusing linked generated demo target")
    rng = np.random.default_rng(42)
    income = rng.uniform(20000, 300000, 600)
    debt = income * rng.uniform(0, .8, 600)
    expense = income * rng.uniform(.1, .95, 600)
    data = pd.DataFrame({"monthly_inflow":income,"monthly_outflow":expense,"monthly_debt_service":debt,
                         "invoice_total":income,"cashflow_margin":(income-expense)/income,"dsr":debt/income})[FEATURES]
    labels = ((data["dsr"] > .35) | (data["cashflow_margin"] < .15)).astype(int)
    classifier = RandomForestClassifier(n_estimators=48, max_depth=6, random_state=42, n_jobs=1).fit(data, labels)
    regressor = RandomForestRegressor(n_estimators=48,max_depth=8,random_state=42,n_jobs=1).fit(data, income*.65)
    hashes = {}
    for name, model in [("credit_classifier.pkl", classifier),("credit_regressor.pkl",regressor)]:
        path = models / name
        path.write_bytes(pickle.dumps(model,protocol=5))
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (models / "model_manifest.json").write_text(json.dumps({"is_demo":True,"training_data":"synthetic generated seed=42; not calibrated for real credit",
        "default_class":1,"feature_names":FEATURES,"sklearn_version":sklearn.__version__,"sha256":hashes},indent=2),encoding="utf-8")
    invoice = {"invoice_id":"DEMO-INV-001","currency":"MYR","subtotal":"100000.00","total":"100000.00",
               "tax":"0.00","freight":"0.00","discount":"0.00","container_seal":"DEMO-SEAL-001","incoterms":"FOB",
               "line_items":[{"sku":"DEMO-SKU-1","description":"Synthetic demonstration goods","quantity":"100","unit_price":"1000.00","hs_code":"000000"}]}
    delivery = {"invoice_id":"DEMO-INV-001","container_seal":"DEMO-SEAL-001","incoterms":"FOB",
                "line_items":[{"sku":"DEMO-SKU-1","quantity":"100"}]}
    for name, kind, record in [("invoice.pdf","invoice",invoice),("packing_list.pdf","delivery",delivery)]:
        path = dossier / name
        pdf = canvas.Canvas(str(path), pagesize=A4)
        y = 795
        pdf.setFont("Helvetica-Bold",12)
        pdf.drawString(40,y,"OUT&IN SYNTHETIC DEMO - NOT REAL TRADE EVIDENCE")
        y -= 32
        payload = "OUT_IN_DEMO_JSON_BEGIN\n" + json.dumps({"is_demo":True,"kind":kind,"data":record},indent=2) + "\nOUT_IN_DEMO_JSON_END"
        pdf.setFont("Courier",8)
        for line in payload.splitlines():
            if y < 45:
                pdf.showPage()
                y = 795
                pdf.setFont("Courier",8)
            pdf.drawString(40,y,line)
            y -= 12
        pdf.save()
    book = Workbook()
    sheet = book.active
    sheet.title = "BankLedger"
    sheet.append(["transaction_id","invoice_id","currency","inflow","outflow","date"])
    sheet.append(["DEMO-BANK-001","DEMO-INV-001","MYR","100000.00","0.00","2026-10-01"])
    sheet.append(["DEMO-BANK-002","","MYR","0.00","30000.00","2026-10-02"])
    sheet.freeze_panes = "A2"
    for column in "ABCDEF":
        sheet.column_dimensions[column].width=23
    book.save(dossier / "statement.xlsx")
    with zipfile.ZipFile(demo / "dossier.zip","w",compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(expected_dossier):
            archive.write(dossier / name, name)
    (demo / "invoice.json").write_text(json.dumps([invoice],indent=2),encoding="utf-8")
    print("Generated synthetic invoice PDF, delivery PDF, bank XLSX and hash-pinned demo ML models.")
    print("No real borrower data, calibrated default probability, legal signatures or funds.")


if __name__ == "__main__":
    main()
