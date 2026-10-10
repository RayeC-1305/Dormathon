"""Invented synthetic MyInvois evidence only: no real invoices or live services.

Engine tests keep OCR, reconciliation, Store, artifacts, and wallet simulation real.
Only API retrieval, SDK extraction, and the live/demo model boundary are offline
substitutions; embedded, explicitly marked demo PDFs remain the commercial input.
"""
import asyncio
import hashlib
import ipaddress
import json
import shutil
import socket
import xml.etree.ElementTree as ET
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from agent_service import myinvois_client, orchestrator
from agent_service.myinvois_client import MyInvoisClient, MyInvoisConfig
from agent_service.sdk_runtime import SDKRuntime
from agent_service.store import Store
from scripts.verify_manifest import verify_manifest
from tools.ml_underwrite_tool import evaluate_credit_ml
from tools.myinvois_tool import A, B, D, MAX_DOCUMENT, bind_myinvois_trade, parse_myinvois_invoice
from tools.ocr_tool import parse_demo_packet
from tools.reconciliation_tool import EvidenceError


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ID = "Synthetic_opaque-001"
SELLER_TIN = "SYNTHETICSELLER"
BUYER_TIN = "SYNTHETICBUYER"
SOURCE = "evidence/myinvois-response-0.json"
EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
AGGREGATES = {
    "AccountingSupplierParty", "AccountingCustomerParty", "Party", "PartyIdentification",
    "InvoiceLine", "Price", "Item", "SellersItemIdentification", "ItemPriceExtension",
    "LegalMonetaryTotal", "TaxTotal", "TaxSubtotal", "AllowanceCharge", "PrepaidPayment",
    "Delivery", "Shipment", "FreightAllowanceCharge",
}


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    """Block external sockets while allowing asyncio's internal loopback socketpair."""
    for key in ("OUT_IN_OCR_ADAPTER", "OUT_IN_OCR_ADAPTER_SHA256", "OUT_IN_REPO_ROOT",
                "OUT_IN_MYINVOIS_ENABLED", "OUT_IN_MYINVOIS_CLIENT_ID",
                "OUT_IN_MYINVOIS_CLIENT_SECRET", "OUT_IN_MYINVOIS_ON_BEHALF_OF"):
        monkeypatch.delenv(key, raising=False)
    attempts = []

    def permitted(address):
        if not isinstance(address, tuple):
            return True  # Local AF_UNIX socketpairs do not use IP addresses.
        try:
            return ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            return address[0] == "localhost"

    def guard(function):
        def checked(self, address, *args, **kwargs):
            if not permitted(address):
                attempts.append(address)
                raise AssertionError("External networking is forbidden in synthetic tests")
            return function(self, address, *args, **kwargs)
        return checked

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
    original_dns = socket.getaddrinfo

    def local_dns(host, *args, **kwargs):
        if host is not None and not permitted((host, 0)):
            attempts.append(host)
            raise AssertionError("External DNS is forbidden in synthetic tests")
        return original_dns(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_dns)
    yield attempts
    assert attempts == [], "A test attempted an external network connection"


def scalar(value, **attributes):
    return [{"_": value, **attributes}]


def amount(value):
    return scalar(value, currencyID="MYR")


def synthetic_ubl(version="1.1"):
    """Fully invented ordinary invoice, deliberately matching the marked demo PDF."""
    party = lambda tin: [{"Party": [{"PartyIdentification": [{"ID": scalar(tin, schemeID="TIN")}]}]}]
    invoice = {
        "ID": scalar("DEMO-INV-001"),
        "IssueDate": scalar("2026-01-01"),
        "IssueTime": scalar("00:00:00Z"),
        "InvoiceTypeCode": scalar("01", listVersionID=version),
        "DocumentCurrencyCode": scalar("MYR"),
        "AccountingSupplierParty": party(SELLER_TIN),
        "AccountingCustomerParty": party(BUYER_TIN),
        "InvoiceLine": [{
            "ID": scalar("SYNTHETIC-LINE-1"),
            "InvoicedQuantity": scalar("100", unitCode="C62"),
            "LineExtensionAmount": amount("100000.00"),
            "Item": [{"Description": scalar("Synthetic demonstration goods"),
                      "SellersItemIdentification": [{"ID": scalar("DEMO-SKU-1")}]}],
            "Price": [{"PriceAmount": amount("1000.00"), "BaseQuantity": scalar("1")}],
            "ItemPriceExtension": [{"Amount": amount("100000.00")}],
        }],
        "TaxTotal": [{"TaxAmount": amount("0.00"),
                      "TaxSubtotal": [{"TaxAmount": amount("0.00")}]}],
        "LegalMonetaryTotal": [{
            "LineExtensionAmount": amount("100000.00"),
            "TaxExclusiveAmount": amount("100000.00"),
            "TaxInclusiveAmount": amount("100000.00"),
            "PayableAmount": amount("100000.00"),
        }],
    }
    return {"_D": D, "_A": A, "_B": B, "Invoice": [invoice]}


def xml_document(root):
    invoice = ET.Element("{" + D + "}Invoice")

    def populate(element, node):
        for key, value in node.items():
            if key == "_":
                element.text = str(value)
            elif isinstance(value, list):
                namespace = A if key in AGGREGATES else B
                for child in value:
                    populate(ET.SubElement(element, "{" + namespace + "}" + key), child)
            else:
                element.set(key, str(value))

    populate(invoice, root["Invoice"][0])
    return ET.tostring(invoice, encoding="unicode")


def synthetic_envelope(root=None, format_name="json", version="1.1"):
    root = synthetic_ubl(version) if root is None else root
    return {
        "uuid": DOCUMENT_ID, "internalId": "DEMO-INV-001", "issuerTin": SELLER_TIN,
        "status": "Valid", "typeName": "invoice", "typeVersionNumber": version,
        "dateTimeIssued": "2026-01-01T00:00:00Z",
        "dateTimeValidated": "2026-01-02T00:00:00+00:00",
        "totalExcludingTax": "100000.00", "totalDiscount": "0.00",
        "totalNetAmount": "100000.00", "totalPayableAmount": "100000.00",
        "document": json.dumps(root) if format_name == "json" else xml_document(root),
    }


def node_at(root, path):
    node = root["Invoice"][0]
    for field in path.split("/") if path else []:
        node = node[field][0]
    return node


@pytest.fixture
def commercial_proposal():
    invoices = json.loads((ROOT / "demo/invoice.json").read_text(encoding="utf-8"))
    for invoice in invoices:
        invoice.update(source="invoice.pdf", issuer_tin=SELLER_TIN, buyer_tin=BUYER_TIN,
                       regions=[{"page": 1, "bbox": [0, 0, 100, 100]}])
    return {"invoices": invoices, "deliveries": []}


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_valid_json_and_namespaced_xml_have_identical_mapping(version):
    json_result = parse_myinvois_invoice(synthetic_envelope(version=version), SOURCE)
    xml_result = parse_myinvois_invoice(synthetic_envelope(format_name="xml", version=version), SOURCE)
    assert json_result.pop("document_format") == "json"
    assert xml_result.pop("document_format") == "xml"
    assert json_result == xml_result
    assert json_result == {
        "invoice_id": "DEMO-INV-001", "currency": "MYR", "subtotal": "100000.00",
        "total": "100000.00", "tax": "0.00", "freight": "0", "discount": "0",
        "source": SOURCE, "source_kind": "lhdn_myinvois", "document_id": DOCUMENT_ID,
        "issuer_tin": SELLER_TIN, "buyer_tin": BUYER_TIN, "ubl_version": version,
        "status": "Valid", "validated_at": "2026-01-02T00:00:00+00:00",
        "line_items": [{"myinvois_line_id": "SYNTHETIC-LINE-1", "sku": "DEMO-SKU-1",
                        "quantity": "100", "unit_price": "1000.00",
                        "description": "Synthetic demonstration goods"}],
    }


@pytest.mark.parametrize("changes,match", [
    ({"status": "Cancelled"}, "Only Valid"),
    ({"status": "Rejected"}, "Only Valid"),
    ({"status": "Submitted"}, "Only Valid"),
    ({"status": "valid"}, "Only Valid"),
    ({"status": None}, "Only Valid"),
    ({"cancelDateTime": "2026-01-03T00:00:00Z"}, "Only Valid"),
    ({"rejectRequestDateTime": "2026-01-03T00:00:00Z"}, "Only Valid"),
    ({"dateTimeIssued": "2999-01-01T00:00:00Z"}, "UTC issuance"),
    ({"dateTimeValidated": "2999-01-02T00:00:00Z"}, "UTC validation"),
    ({"dateTimeIssued": "2026-01-01T00:00:00"}, "UTC issuance"),
    ({"dateTimeValidated": "2026-01-02T00:00:00+08:00"}, "UTC validation"),
    ({"dateTimeValidated": None}, "UTC validation"),
    ({"dateTimeIssued": "not-a-date"}, "UTC issuance"),
    ({"dateTimeValidated": "2025-12-31T00:00:00Z"}, "precedes"),
    ({"internalId": "SYNTHETIC-OTHER"}, "invoice ID disagree"),
    ({"issuerTin": "SYNTHETIC-OTHER"}, "issuer TIN disagree"),
    ({"typeName": "credit note"}, "type/version disagree"),
    ({"typeVersionNumber": "1.0"}, "type/version disagree"),
    ({"totalExcludingTax": "99999.99"}, "API summary"),
    ({"totalDiscount": "0.01"}, "API summary"),
    ({"totalNetAmount": "100000.01"}, "API summary"),
    ({"totalPayableAmount": "100000.001"}, "sub-cent"),
    ({"totalPayableAmount": 100000.0}, "not a float"),
])
def test_reject_invalid_api_envelope(changes, match):
    envelope = synthetic_envelope()
    envelope.update(changes)
    with pytest.raises(EvidenceError, match=match):
        parse_myinvois_invoice(envelope, SOURCE)


@pytest.mark.parametrize("envelope", [None, [], "synthetic"])
def test_reject_nonobject_envelope(envelope):
    with pytest.raises(EvidenceError, match="must be an object"):
        parse_myinvois_invoice(envelope, SOURCE)


def test_missing_uuid_is_a_controlled_evidence_error():
    envelope = synthetic_envelope()
    del envelope["uuid"]
    # Keep the desired fail-closed contract visible if runtime leaks KeyError.
    with pytest.raises(EvidenceError):
        parse_myinvois_invoice(envelope, SOURCE)


@pytest.mark.parametrize("format_name", ["json", "xml"])
@pytest.mark.parametrize("path,attribute,value,match", [
    ("InvoiceTypeCode", "_", "02", "ordinary invoice"),
    ("InvoiceTypeCode", "listVersionID", "2.0", "ordinary invoice"),
    ("DocumentCurrencyCode", "_", "USD", "Foreign-currency"),
    ("IssueDate", "_", "2026-01-02", "issuance date disagree"),
    ("ID", "_", "", "invoice ID"),
    ("InvoiceLine/ID", "_", "x" * 301, "line ID"),
    ("InvoiceLine/Item/Description", "_", "", "line description"),
    ("InvoiceLine/InvoicedQuantity", "_", "0", "line arithmetic"),
    ("InvoiceLine/InvoicedQuantity", "_", "-100", "line arithmetic"),
    ("InvoiceLine/InvoicedQuantity", "_", "NaN", "out-of-range"),
    ("InvoiceLine/InvoicedQuantity", "_", "Infinity", "out-of-range"),
    ("InvoiceLine/InvoicedQuantity", "_", "100.000001", "sub-cent"),
    ("InvoiceLine/Price/BaseQuantity", "_", "100", "base quantity"),
    ("InvoiceLine/Price/PriceAmount", "_", "999.99", "line arithmetic"),
    ("InvoiceLine/Price/PriceAmount", "_", "1000.001", "sub-cent"),
    ("InvoiceLine/Price/PriceAmount", "_", "-1000", "Negative UBL"),
    ("InvoiceLine/LineExtensionAmount", "_", "100000.01", "line arithmetic"),
    ("InvoiceLine/ItemPriceExtension/Amount", "_", "99999.99", "item subtotal"),
    ("LegalMonetaryTotal/LineExtensionAmount", "_", "99999.99", "monetary parity"),
    ("LegalMonetaryTotal/TaxExclusiveAmount", "_", "99999.99", "monetary parity"),
    ("LegalMonetaryTotal/TaxInclusiveAmount", "_", "100000.01", "monetary parity"),
    ("LegalMonetaryTotal/PayableAmount", "_", "99999.99", "monetary parity"),
    ("TaxTotal/TaxSubtotal/TaxAmount", "_", "0.01", "tax subtotal arithmetic"),
])
def test_reject_invalid_ubl_profile_and_exact_arithmetic(format_name, path, attribute, value, match):
    root = synthetic_ubl()
    node_at(root, path)[attribute] = value
    with pytest.raises(EvidenceError, match=match):
        parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)


@pytest.mark.parametrize("format_name", ["json", "xml"])
@pytest.mark.parametrize("path", [
    "InvoiceLine/Price/PriceAmount", "InvoiceLine/LineExtensionAmount",
    "InvoiceLine/ItemPriceExtension/Amount", "TaxTotal/TaxAmount",
    "TaxTotal/TaxSubtotal/TaxAmount", "LegalMonetaryTotal/PayableAmount",
])
@pytest.mark.parametrize("currency", [None, "USD"])
def test_every_amount_requires_explicit_myr(format_name, path, currency):
    root = synthetic_ubl()
    node = node_at(root, path)
    if currency is None:
        del node["currencyID"]
    else:
        node["currencyID"] = currency
    with pytest.raises(EvidenceError, match="Every monetary UBL field"):
        parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)


@pytest.mark.parametrize("format_name", ["json", "xml"])
@pytest.mark.parametrize("party", ["AccountingSupplierParty", "AccountingCustomerParty"])
@pytest.mark.parametrize("defect", ["missing-tin", "duplicate-tin"])
def test_party_tin_must_be_unique(format_name, party, defect):
    root = synthetic_ubl()
    node = node_at(root, party + "/Party")
    if defect == "missing-tin":
        node["PartyIdentification"][0]["ID"][0]["schemeID"] = "SYNTHETIC-OTHER-SCHEME"
    else:
        node["PartyIdentification"].append(deepcopy(node["PartyIdentification"][0]))
    with pytest.raises(EvidenceError, match="TIN"):
        parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)


@pytest.mark.parametrize("format_name", ["json", "xml"])
@pytest.mark.parametrize("location,container,field", [
    ("", "AllowanceCharge", "Amount"),
    ("InvoiceLine", "AllowanceCharge", "Amount"),
    ("InvoiceLine/Price", "AllowanceCharge", "Amount"),
    ("", "PrepaidPayment", "PaidAmount"),
    ("LegalMonetaryTotal", None, "AllowanceTotalAmount"),
    ("LegalMonetaryTotal", None, "ChargeTotalAmount"),
    ("LegalMonetaryTotal", None, "PayableRoundingAmount"),
    ("LegalMonetaryTotal", None, "PrepaidAmount"),
    ("", "freight", "Amount"),
])
def test_nonzero_adjustments_never_disappear(format_name, location, container, field):
    root = synthetic_ubl()
    target = node_at(root, location)
    if container == "freight":
        target["Delivery"] = [{"Shipment": [{"FreightAllowanceCharge": [{field: amount("0.01")}]}]}]
    elif container:
        target[container] = [{field: amount("0.01")}]
    else:
        target[field] = amount("0.01")
    with pytest.raises(EvidenceError, match="reviewed extended mapping"):
        parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)


@pytest.mark.parametrize("format_name", ["json", "xml"])
def test_duplicate_line_ids_are_rejected(format_name):
    root = synthetic_ubl()
    root["Invoice"][0]["InvoiceLine"] *= 2
    with pytest.raises(EvidenceError, match="Duplicate structured invoice line ID"):
        parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)


@pytest.mark.parametrize("format_name", ["json", "xml"])
@pytest.mark.parametrize("field", ["ID", "InvoiceTypeCode", "DocumentCurrencyCode", "LegalMonetaryTotal"])
def test_required_singletons_cannot_be_duplicated(format_name, field):
    root = synthetic_ubl()
    root["Invoice"][0][field] *= 2
    with pytest.raises(EvidenceError, match="exactly one field"):
        parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)


@pytest.mark.parametrize("document", [None, "", " ", {}, "x" * (MAX_DOCUMENT + 1),
                                       "\u03a9" * (MAX_DOCUMENT // 2 + 1)],
                         ids=["missing", "empty", "whitespace", "object", "ascii-over-limit", "utf8-over-limit"])
def test_raw_document_presence_and_utf8_byte_limit(document):
    envelope = synthetic_envelope()
    envelope["document"] = document
    with pytest.raises(EvidenceError, match="Missing or oversized"):
        parse_myinvois_invoice(envelope, SOURCE)


@pytest.mark.parametrize("defect", ["duplicate-root", "duplicate-nested", "nan", "infinity", "malformed",
                                    "namespace", "array-root", "long-key", "boolean-money"])
def test_json_structure_is_strict(defect):
    root = synthetic_ubl()
    envelope = synthetic_envelope(root)
    if defect == "duplicate-root":
        envelope["document"] = envelope["document"].replace('"_D":', '"_D":"duplicate", "_D":', 1)
    elif defect == "duplicate-nested":
        envelope["document"] = envelope["document"].replace('"unitCode":', '"unitCode":"duplicate", "unitCode":', 1)
    elif defect in ("nan", "infinity"):
        envelope["document"] = envelope["document"][:-1] + ',"unused":' + ("NaN" if defect == "nan" else "Infinity") + "}"
    elif defect == "malformed":
        envelope["document"] = '{"Invoice":'
    elif defect == "array-root":
        envelope["document"] = "[]"
    else:
        if defect == "namespace":
            root["_B"] = "urn:synthetic:unknown"
        elif defect == "long-key":
            root["x" * 257] = "synthetic"
        else:
            node_at(root, "InvoiceLine/Price/PriceAmount")["_"] = True
        envelope["document"] = json.dumps(root)
    with pytest.raises(EvidenceError):
        parse_myinvois_invoice(envelope, SOURCE)


@pytest.mark.parametrize("defect,match", [
    ("dtd", "DTD/entities"), ("entity", "DTD/entities"),
    ("unknown-child-namespace", "Unrecognized UBL namespace"),
    ("unqualified-root", "Expected UBL Invoice root"), ("malformed", "Malformed UBL"),
])
def test_xml_rejects_entities_and_unknown_namespaces(defect, match):
    envelope = synthetic_envelope(format_name="xml")
    tree = ET.fromstring(envelope["document"])
    if defect == "dtd":
        envelope["document"] = '<!DOCTYPE Invoice [<!ENTITY synthetic "synthetic">]>' + envelope["document"]
    elif defect == "entity":
        envelope["document"] = '<!ENTITY synthetic "synthetic">' + envelope["document"]
    elif defect == "malformed":
        envelope["document"] = "<Invoice>"
    else:
        if defect == "unknown-child-namespace":
            ET.SubElement(tree, "{urn:synthetic:unknown}Unexpected")
        else:
            tree.tag = "Invoice"
        envelope["document"] = ET.tostring(tree, encoding="unicode")
    with pytest.raises(EvidenceError, match=match):
        parse_myinvois_invoice(envelope, SOURCE)


@pytest.mark.parametrize("defect,match", [
    ("json-depth", "depth budget"), ("json-token-budget", "token budget"),
    ("json-node-budget", "node/depth budget"), ("xml-depth", "node/depth budget"),
    ("xml-extension-depth", "node/depth budget"), ("xml-nodes", "node/depth budget"),
    ("line-budget", "Too many structured invoice lines"),
])
def test_resource_limits_apply_before_processing_untrusted_subtrees(defect, match):
    root = synthetic_ubl()
    envelope = synthetic_envelope(root)
    if defect == "json-depth":
        envelope["document"] = "[" * 33 + "0" + "]" * 33
    elif defect == "json-token-budget":
        root["unused-synthetic-budget"] = [0] * 150001
        envelope["document"] = json.dumps(root)
    elif defect == "json-node-budget":
        # Exactly 150000 scan tokens, but the root adds the 150001st parsed node.
        envelope["document"] = "[" + ",".join(["0"] * 150000) + "]"
    elif defect == "line-budget":
        template = root["Invoice"][0]["InvoiceLine"][0]
        root["Invoice"][0]["InvoiceLine"] = []
        # Minimal XML lines stay below the separate XML node budget.
        for index in range(5001):
            line = deepcopy(template)
            line.pop("ItemPriceExtension")
            line["Item"][0].pop("SellersItemIdentification")
            line["Price"][0].pop("BaseQuantity")
            line["ID"] = scalar("SYNTHETIC-" + str(index))
            root["Invoice"][0]["InvoiceLine"].append(line)
        envelope["document"] = xml_document(root)
    else:
        tree = ET.fromstring(xml_document(root))
        if defect == "xml-nodes":
            for _ in range(50001):
                ET.SubElement(tree, "{" + B + "}SyntheticUnused")
        else:
            parent = tree
            if defect == "xml-extension-depth":
                parent = ET.SubElement(tree, "{" + EXT + "}UBLExtensions")
            for _ in range(33):
                parent = ET.SubElement(parent, "{" + B + "}SyntheticUnused")
        envelope["document"] = ET.tostring(tree, encoding="unicode")
    with pytest.raises(EvidenceError, match=match):
        parse_myinvois_invoice(envelope, SOURCE)


def test_bounded_xml_extensions_are_retained_as_raw_not_used_for_mapping():
    envelope = synthetic_envelope(format_name="xml")
    expected = parse_myinvois_invoice(envelope, SOURCE)
    tree = ET.fromstring(envelope["document"])
    extension = ET.SubElement(tree, "{" + EXT + "}UBLExtensions")
    ET.SubElement(extension, "{urn:synthetic:signature-not-verified}Marker").text = "SYNTHETIC ONLY"
    envelope["document"] = ET.tostring(tree, encoding="unicode")
    assert parse_myinvois_invoice(envelope, SOURCE) == expected


@pytest.mark.parametrize("structured_sku,explicit_id", [(True, False), (True, True), (False, False), (False, True)])
def test_binding_preserves_pdf_citations_and_independent_sku(commercial_proposal, structured_sku, explicit_id):
    structured = parse_myinvois_invoice(synthetic_envelope(), SOURCE)
    if not structured_sku:
        structured["line_items"][0]["sku"] = None
    if explicit_id:
        commercial_proposal["invoices"][0]["line_items"][0]["myinvois_line_id"] = "SYNTHETIC-LINE-1"
    commercial_proposal["deliveries"] = [{"synthetic": True, "source": "packing_list.pdf"}]
    before = deepcopy((structured, commercial_proposal))
    result = bind_myinvois_trade([structured], commercial_proposal)
    invoice = result["invoices"][0]
    assert invoice["source"] == "invoice.pdf"
    assert invoice["structured_source"] == SOURCE
    assert invoice["regions"] == commercial_proposal["invoices"][0]["regions"]
    assert invoice["container_seal"] == "DEMO-SEAL-001"
    assert Decimal(invoice["total"]) == Decimal("100000")
    line = invoice["line_items"][0]
    assert line["sku"] == "DEMO-SKU-1"
    assert line["hs_code"] == "000000"
    assert line["sku_provenance"] == ("ubl_seller_identifier_and_pdf" if structured_sku else "commercial_invoice_pdf")
    assert (structured, commercial_proposal) == before
    result["deliveries"][0]["synthetic"] = False
    assert commercial_proposal["deliveries"][0]["synthetic"] is True


@pytest.mark.parametrize("format_name", ["json", "xml"])
def test_missing_ubl_seller_sku_uses_unique_pdf_goods_identity(commercial_proposal, format_name):
    root = synthetic_ubl()
    node_at(root, "InvoiceLine/Item").pop("SellersItemIdentification")
    structured = parse_myinvois_invoice(synthetic_envelope(root, format_name), SOURCE)
    assert structured["line_items"][0]["sku"] is None
    result = bind_myinvois_trade([structured], commercial_proposal)
    assert result["invoices"][0]["line_items"][0]["sku"] == "DEMO-SKU-1"
    assert result["invoices"][0]["line_items"][0]["sku_provenance"] == "commercial_invoice_pdf"


@pytest.mark.parametrize("field,value,match", [
    ("issuer_tin", "SYNTHETIC-OTHER", "party identity"),
    ("buyer_tin", None, "party identity"),
    ("invoice_id", "SYNTHETIC-UNKNOWN", "invoice ID"),
    ("currency", "USD", "currency"),
    ("subtotal", "99999.99", "amount"), ("total", "100000.01", "amount"),
    ("tax", "0.01", "amount"), ("freight", "0.01", "amount"),
    ("discount", "0.01", "amount"),
])
def test_binding_rejects_identity_and_amount_contradictions(commercial_proposal, field, value, match):
    commercial_proposal["invoices"][0][field] = value
    with pytest.raises(EvidenceError, match=match):
        bind_myinvois_trade([parse_myinvois_invoice(synthetic_envelope(), SOURCE)], commercial_proposal)


@pytest.mark.parametrize("defect", ["unknown-line-id", "goods-mismatch-with-explicit-id", "seller-sku-contradiction",
                                    "missing-sku", "quantity", "price", "missing-lines"])
def test_binding_line_ids_cannot_override_goods_evidence(commercial_proposal, defect):
    structured = parse_myinvois_invoice(synthetic_envelope(), SOURCE)
    pdf = commercial_proposal["invoices"][0]
    line = pdf["line_items"][0]
    if defect == "unknown-line-id":
        line["myinvois_line_id"] = "SYNTHETIC-UNKNOWN"
    elif defect == "goods-mismatch-with-explicit-id":
        structured["line_items"][0]["sku"] = None
        line.update(myinvois_line_id="SYNTHETIC-LINE-1", description="Different synthetic goods")
    elif defect == "seller-sku-contradiction":
        line.update(myinvois_line_id="SYNTHETIC-LINE-1", sku="SYNTHETIC-CONTRADICTORY-SKU")
    elif defect == "missing-sku":
        structured["line_items"][0]["sku"] = None
        line.pop("sku")
    elif defect in ("quantity", "price"):
        line["quantity" if defect == "quantity" else "unit_price"] = "99"
    else:
        pdf["line_items"] = []
    with pytest.raises(EvidenceError):
        bind_myinvois_trade([structured], commercial_proposal)


@pytest.mark.parametrize("defect", ["duplicate-structured-invoice", "duplicate-pdf-invoice", "ambiguous-goods",
                                    "duplicate-explicit-line-id"])
def test_binding_rejects_duplicate_and_ambiguous_identities(commercial_proposal, defect):
    structured = [parse_myinvois_invoice(synthetic_envelope(), SOURCE)]
    if defect == "duplicate-structured-invoice":
        structured *= 2
        commercial_proposal["invoices"] *= 2
    elif defect == "duplicate-pdf-invoice":
        structured.append({**deepcopy(structured[0]), "invoice_id": "SYNTHETIC-SECOND"})
        commercial_proposal["invoices"] *= 2
    else:
        lines = structured[0]["line_items"]
        lines.append({**deepcopy(lines[0]), "myinvois_line_id": "SYNTHETIC-LINE-2"})
        pdf_lines = commercial_proposal["invoices"][0]["line_items"]
        pdf_lines.append(deepcopy(pdf_lines[0]))
        if defect == "duplicate-explicit-line-id":
            for line in pdf_lines:
                line["myinvois_line_id"] = "SYNTHETIC-LINE-1"
        else:
            for line in lines:
                line["sku"] = None
    with pytest.raises(EvidenceError):
        bind_myinvois_trade(structured, commercial_proposal)


@pytest.mark.parametrize("proposal", [None, [], {}, {"invoices": {}}])
def test_binding_requires_commercial_invoice_list(proposal):
    with pytest.raises(EvidenceError, match="Missing commercial"):
        bind_myinvois_trade([], proposal)


class SyntheticMyInvoisClient:
    """In-memory API boundary; exact invented response bytes and matching provenance."""
    def __init__(self, envelope):
        self.response_bytes = ("\n" + json.dumps(envelope, indent=2) + "\n").encode("utf-8")
        self.provenance = {
            "response_sha256": hashlib.sha256(self.response_bytes).hexdigest(),
            "endpoint": f"/api/v1.0/documents/{DOCUMENT_ID}/raw",
            "environment": "sandbox", "auth_mode": "taxpayer", "content_type": "application/json",
            "fetched_at": "2026-01-02T00:00:00+00:00",
            "freshness": "synthetic offline one-shot snapshot; no live status assurance",
        }
        self.calls = []

    async def fetch_document(self, document_id):
        assert document_id == DOCUMENT_ID
        self.calls.append(document_id)
        return {"envelope": json.loads(self.response_bytes), "response_bytes": self.response_bytes,
                "provenance": deepcopy(self.provenance)}


@pytest.fixture
def engine_case(tmp_path, monkeypatch):
    def create(*, envelope=None, client=None, defect=None):
        store = Store(tmp_path / "synthetic-workspace")
        names = ("invoice.pdf", "packing_list.pdf", "statement.xlsx")
        sources = [{"name": name, "sha256": hashlib.sha256((ROOT / "demo/dossier" / name).read_bytes()).hexdigest()}
                   for name in names]
        terms = {"requested_limit": "40000.00", "duration_months": 12, "annual_rate": "0.08",
                 "monthly_debt_service": "1000.00", "myinvois_document_ids": [DOCUMENT_ID]}
        jid, _ = store.create("live", terms, sources)
        directory = store.workspace / "jobs" / jid
        inputs = directory / "inputs"
        inputs.mkdir(parents=True)
        for name in names:
            shutil.copyfile(ROOT / "demo/dossier" / name, inputs / name)
        if client is None:
            client = SyntheticMyInvoisClient(synthetic_envelope() if envelope is None else envelope)
        case = SimpleNamespace(store=store, jid=jid, directory=directory, client=client,
                               sdk_calls=[], model_calls=[], normalized_packets=[])

        async def offline_extract(runtime, evidence_paths, cycle=0, prior_errors=None):
            # Offline SDK substitution parses ACTUAL normalized, marked synthetic PDFs.
            case.sdk_calls.append((cycle, deepcopy(prior_errors)))
            packets = [json.loads(Path(path).read_text(encoding="utf-8")) for path in evidence_paths]
            case.normalized_packets.extend(deepcopy(packets))
            parsed = [parse_demo_packet(packet) for packet in packets]
            proposal = {"invoices": [p["data"] for p in parsed if p["kind"] == "invoice"],
                        "deliveries": [p["data"] for p in parsed if p["kind"] == "delivery"]}
            for invoice in proposal["invoices"]:
                invoice.update(issuer_tin=SELLER_TIN, buyer_tin=BUYER_TIN)
                if defect == "binding":
                    invoice["buyer_tin"] = "SYNTHETIC-MISMATCH"
            if defect in ("raw-mutation", "provenance-mutation"):
                name = "myinvois-response-0.json" if defect == "raw-mutation" else "myinvois-provenance-0.json"
                path = runtime.job_dir / "evidence" / name
                path.write_bytes(path.read_bytes() + b"\nSYNTHETIC MUTATION")
            return proposal

        def offline_demo_model(features, model_dir, mode, requested_limit, term_months, annual_rate):
            # Real reviewed demo-model evaluation, not fabricated probabilities/approval.
            assert mode == "live"
            case.model_calls.append(deepcopy(features))
            result = evaluate_credit_ml(features, str(ROOT / "demo/models"), "demo",
                                        requested_limit, term_months, annual_rate)
            assert result["is_demo_model"] is True
            return result

        monkeypatch.setattr(SDKRuntime, "extract", offline_extract)
        monkeypatch.setattr(orchestrator, "evaluate_credit_ml", offline_demo_model)
        case.engine = orchestrator.UnderwritingEngine(store, client)
        return case
    return create


def states(case):
    return [e["payload"]["state"] for e in case.store.events(case.jid) if e["type"] == "STATE_TRANSITION"]


def assert_no_underwriting_or_wallet(case):
    assert case.model_calls == []
    assert "UNDERWRITING" not in states(case)
    assert "SETTLING" not in states(case)
    with case.store.connect() as db:
        assert db.execute("SELECT * FROM wallet WHERE job_id=?", (case.jid,)).fetchone() is None
    assert not (case.directory / "artifacts").exists()


@pytest.mark.parametrize("version,format_name", [("1.0", "json"), ("1.1", "xml")])
def test_real_engine_completes_with_nine_verified_artifacts_and_raw_source_hashes(engine_case, version, format_name):
    case = engine_case(envelope=synthetic_envelope(version=version, format_name=format_name))
    asyncio.run(case.engine.run(case.jid))
    job = case.store.get(case.jid)
    assert job["state"] == "COMPLETED", job["error"]
    assert len(job["artifacts"]) == 9
    assert states(case) == ["QUEUED", "EXTRACTING", "RECONCILING", "UNDERWRITING", "SETTLING", "COMPLETED"]
    assert case.client.calls == [DOCUMENT_ID]
    assert len(case.sdk_calls) == len(case.model_calls) == 1
    assert {p["source"] for p in case.normalized_packets} == {"invoice.pdf", "packing_list.pdf"}
    assert all(p["backend"] == "local_pdfplumber_pdfium" for p in case.normalized_packets)
    assert all("SYNTHETIC DEMO" in " ".join(page["text"] for page in p["pages"]) for p in case.normalized_packets)
    assert job["metrics"]["is_demo_model"] is True
    assert job["settlement"] == "SIMULATED_ONLY"
    with case.store.connect() as db:
        wallet = db.execute("SELECT * FROM wallet WHERE job_id=?", (case.jid,)).fetchone()
    assert wallet["status"] == "SIMULATED_ONLY"
    assert Decimal(wallet["amount"]) == Decimal(job["metrics"]["approved_limit"])
    bundle = case.directory / "artifacts"
    assert {a["name"] for a in job["artifacts"]} == {p.name for p in bundle.iterdir()}
    manifest_bytes = (bundle / "verification_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert manifest_bytes == canonical
    key = serialization.load_pem_public_key((bundle / "demo_public_key.pem").read_bytes())
    key.verify((bundle / "verification_manifest.sig").read_bytes(), canonical)
    for entry in manifest["outputs"]:
        assert hashlib.sha256((bundle / entry["name"]).read_bytes()).hexdigest() == entry["sha256"]
    for entry in manifest["sources"]:
        assert hashlib.sha256((case.directory / entry["name"]).read_bytes()).hexdigest() == entry["sha256"]
    declared = {entry["name"]: entry["sha256"] for entry in manifest["sources"]}
    assert SOURCE in declared
    assert "evidence/myinvois-provenance-0.json" in declared
    assert {"inputs/invoice.pdf", "inputs/packing_list.pdf", "inputs/statement.xlsx"} <= declared.keys()
    assert (case.directory / SOURCE).read_bytes() == case.client.response_bytes
    provenance = json.loads((case.directory / "evidence/myinvois-provenance-0.json").read_bytes())
    assert provenance == case.client.provenance
    assert declared[SOURCE] == provenance["response_sha256"]
    assert len(job["myinvois_sources"]) == 2
    assert all(declared[entry["name"]] == entry["sha256"] for entry in job["myinvois_sources"])
    assert manifest["real_funds"] is False and manifest["risk"]["is_demo_model"] is True
    assert manifest["legal_status"] == "demo_attestation_not_legal_countersignature"
    verification = verify_manifest(bundle, source_dir=case.directory)
    assert verification["verified"] is True and verification["outputs_checked"] == 7
    assert verification["sources_checked"] == len(manifest["sources"])
    assert verification["manifest_signature"] == "verified"
    assert verification["borrower_attestation"] == verification["lender_attestation"] == "verified"
    pdf = (bundle / "Out_In_TNC_Agreement.pdf").read_bytes()
    for role in ("borrower", "lender"):
        key = serialization.load_pem_public_key((bundle / f"demo_{role}_public_key.pem").read_bytes())
        key.verify((bundle / f"{role}_attestation.sig").read_bytes(),
                   b"OUT_IN_DEMO_PDF_ATTESTATION_V1\x00" + role.encode() + b"\x00" + pdf)


def test_real_engine_binding_mismatch_quarantines_after_three_actual_reslices(engine_case):
    case = engine_case(defect="binding")
    asyncio.run(case.engine.run(case.jid))
    job = case.store.get(case.jid)
    assert job["state"] == "QUARANTINED", job["error"]
    assert states(case).count("SELF_HEALING") == 3
    assert states(case).count("RECONCILING") == 4
    assert [cycle for cycle, _ in case.sdk_calls] == [0, 1, 2, 3]
    assert all(errors[0]["gate"] == "myinvois_commercial_binding" for _, errors in case.sdk_calls[1:])
    assert case.client.calls == [DOCUMENT_ID]
    assert_no_underwriting_or_wallet(case)
    report = json.loads((case.directory / "quarantine/variance_report.json").read_bytes())
    assert report["self_heal_cycles"] == 3 and report["disbursement"] == "prohibited"
    assert report["variance"] is None and report["invoice_total"] is None
    assert report["errors"][0]["gate"] == "myinvois_commercial_binding"
    for cycle in (1, 2, 3):
        packets = [p for p in case.normalized_packets if p["cycle"] == cycle]
        assert len(packets) == 2
        assert all(p["crops"] and all(c["dpi"] == 240 + cycle * 40 for c in p["crops"]) for p in packets)
        assert all("local_crop" not in crop for p in packets for crop in p["crops"])
        assert list((case.directory / "crops" / f"cycle-{cycle}").glob("*.png"))


@pytest.mark.parametrize("defect", ["cancelled", "unsupported-type", "price-allowance"])
def test_real_engine_api_profile_failure_precedes_sdk_extraction(engine_case, defect):
    root = synthetic_ubl()
    if defect == "unsupported-type":
        node_at(root, "InvoiceTypeCode")["_"] = "02"
    elif defect == "price-allowance":
        node_at(root, "InvoiceLine/Price")["AllowanceCharge"] = [{"Amount": amount("1.00")}]
    envelope = synthetic_envelope(root)
    if defect == "cancelled":
        envelope["status"] = "Cancelled"
    case = engine_case(envelope=envelope)
    asyncio.run(case.engine.run(case.jid))
    assert case.store.get(case.jid)["state"] == "FAILED"
    assert case.sdk_calls == [] and case.normalized_packets == []
    assert states(case) == ["QUEUED", "EXTRACTING", "FAILED"]
    assert_no_underwriting_or_wallet(case)
    assert (case.directory / SOURCE).read_bytes() == case.client.response_bytes
    assert (case.directory / "evidence/myinvois-provenance-0.json").is_file()
    assert len(case.store.get(case.jid)["myinvois_sources"]) == 2
    assert not (case.directory / "normalized").exists()


@pytest.mark.parametrize("defect", ["raw-mutation", "provenance-mutation"])
def test_real_engine_mutated_api_snapshots_fail_before_underwriting(engine_case, defect):
    case = engine_case(defect=defect)
    asyncio.run(case.engine.run(case.jid))
    job = case.store.get(case.jid)
    assert job["state"] == "FAILED"
    assert "MyInvois source snapshot changed" in job["error"]
    assert job["artifacts"] == [] and len(case.sdk_calls) == 1
    assert_no_underwriting_or_wallet(case)


@pytest.mark.parametrize("stage", ["POST", "GET"])
def test_real_engine_api_network_error_never_reaches_sdk(engine_case, stage):
    requests = []

    def transport(request):
        requests.append(request.method)
        if request.method == stage:
            raise httpx.ConnectError("Invented offline transport failure", request=request)
        return httpx.Response(200, json={"access_token": "synthetic-offline-token",
                                        "token_type": "Bearer", "expires_in": 3600})

    client = MyInvoisClient(MyInvoisConfig("synthetic-client", "synthetic-secret"),
                           transport=httpx.MockTransport(transport))
    case = engine_case(client=client)
    asyncio.run(case.engine.run(case.jid))
    job = case.store.get(case.jid)
    assert job["state"] == "FAILED" and "failed safely" in job["error"]
    assert requests == (["POST"] if stage == "POST" else ["POST", "GET"])
    assert case.sdk_calls == []
    assert_no_underwriting_or_wallet(case)
    assert not (case.directory / "normalized").exists()


def test_real_engine_api_fetch_deadline_cancels_pending_request(engine_case, monkeypatch):
    monkeypatch.setattr(myinvois_client, "_FETCH_TIMEOUT_SECONDS", 0.05)
    state = SimpleNamespace(cancelled=False, requests=[])

    async def pending(request):
        state.requests.append(request.method)
        try:
            await asyncio.Future()
        finally:
            state.cancelled = True

    client = MyInvoisClient(MyInvoisConfig("synthetic-client", "synthetic-secret"),
                           transport=httpx.MockTransport(pending))
    case = engine_case(client=client)
    asyncio.run(case.engine.run(case.jid))
    job = case.store.get(case.jid)
    assert state.cancelled is True and state.requests == ["POST"]
    assert job["state"] == "FAILED" and "timed out" in job["error"]
    assert case.sdk_calls == []
    assert_no_underwriting_or_wallet(case)


def test_real_engine_caller_deadline_cancels_sdk_and_fails_closed(engine_case, monkeypatch):
    case = engine_case()
    state = SimpleNamespace(cancelled=False)

    async def scenario():
        started = asyncio.Event()

        async def pending_extract(runtime, evidence_paths, cycle=0, prior_errors=None):
            case.sdk_calls.append((cycle, prior_errors))
            started.set()
            try:
                await asyncio.Future()
            finally:
                state.cancelled = True

        monkeypatch.setattr(SDKRuntime, "extract", pending_extract)
        task = asyncio.create_task(case.engine.run(case.jid))
        try:
            await asyncio.wait_for(started.wait(), timeout=30)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=0.05)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert state.cancelled is True and len(case.sdk_calls) == 1
    job = case.store.get(case.jid)
    assert job["state"] == "FAILED" and "Execution interrupted" in job["error"]
    assert_no_underwriting_or_wallet(case)
