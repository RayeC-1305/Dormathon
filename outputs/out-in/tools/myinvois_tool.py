"""Deterministic, deliberately narrow MyInvois invoice profile, never an LLM parser.

Supports invoice 01 v1.0/1.1, MYR, literal quantity/price and no allowances,
charges, prepayment or rounding adjustment. These are OUT&IN constraints, not
LHDN validity rules. Commercial PDFs remain independent SKU/shipping evidence.
"""
from __future__ import annotations
import copy
import json
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from tools.reconciliation_tool import EvidenceError, dec, money

D = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
A = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
B = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
MAX_DOCUMENT = 16 * 1024 * 1024


def check_json_structure(text):
    depth, tokens, quoted, escaped = 0, 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
            tokens += 1
        elif char in "[{":
            depth += 1
            tokens += 1
            if depth > 32:
                raise EvidenceError("JSON exceeds structural depth budget")
        elif char in "]}":
            depth -= 1
        elif char == ",":
            tokens += 1
        if tokens > 150000:
            raise EvidenceError("JSON exceeds structural token budget")


def strict_json(text):
    check_json_structure(text)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError("Duplicate JSON evidence key")
            result[key] = value
        return result
    def constant(_):
        raise EvidenceError("Non-finite JSON evidence")
    try:
        return json.loads(text, parse_float=str, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as exc:
        raise EvidenceError("Malformed structured JSON evidence") from exc


def _bounded(value, depth=0, budget=None):
    budget = budget if budget is not None else [0]
    budget[0] += 1
    if depth > 32 or budget[0] > 150000:
        raise EvidenceError("Structured invoice exceeds node/depth budget")
    if isinstance(value, dict):
        if any(not isinstance(k, str) or len(k) > 256 for k in value):
            raise EvidenceError("Invalid structured invoice key")
        for v in value.values():
            _bounded(v, depth + 1, budget)
    elif isinstance(value, list):
        for v in value:
            _bounded(v, depth + 1, budget)
    elif value is not None and type(value) not in (str, int, bool):
        raise EvidenceError("Untrusted floating-point structured invoice")


def _xml_node(element, depth=0, budget=None):
    budget = budget if budget is not None else [0]
    budget[0] += 1
    if depth > 32 or budget[0] > 50000:
        raise EvidenceError("XML invoice exceeds node/depth budget")
    if not element.tag.startswith(("{" + D + "}", "{" + A + "}", "{" + B + "}")):
        raise EvidenceError("Unrecognized UBL namespace")
    result = dict(element.attrib)
    if list(element):
        for child in element:
            if child.tag == "{urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2}UBLExtensions":
                # Raw signed bytes retained; no claim of independent LHDN certificate verification.
                continue
            name = child.tag.split("}", 1)[1]
            result.setdefault(name, []).append(_xml_node(child, depth + 1, budget))
    else:
        result["_"] = (element.text or "").strip()
    return result


def _nodes(node, key, required=False):
    result = node.get(key, [])
    if not isinstance(result, list) or any(not isinstance(v, dict) for v in result) or (required and not result):
        raise EvidenceError("Missing or malformed UBL field: " + key)
    return result


def _one(node, key, optional=False):
    result = _nodes(node, key)
    if optional and not result:
        return None
    if len(result) != 1:
        raise EvidenceError("UBL requires exactly one field: " + key)
    return result[0]


def _value(node, key, optional=False):
    item = _one(node, key, optional)
    if item is None:
        return None
    value = item.get("_")
    if type(value) not in (str, int):
        raise EvidenceError("Invalid UBL scalar: " + key)
    return str(value)


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or any(ord(c) < 32 for c in value):
        raise EvidenceError("Missing or invalid " + label)
    return value.strip()


def _amount(node, key, optional=False):
    item = _one(node, key, optional)
    if item is None:
        return Decimal(0)
    if item.get("currencyID") != "MYR":
        raise EvidenceError("Every monetary UBL field must declare MYR")
    value = money(item.get("_"), key)
    if value < 0:
        raise EvidenceError("Negative UBL amount is unsupported")
    return value


def _utc(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError()
        if parsed > datetime.now(timezone.utc):
            raise ValueError()
        return parsed
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise EvidenceError("Missing or invalid UTC " + label) from exc


def _no_adjustments(invoice):
    # Never discard supported-looking but unrepresentable adjustments.
    invoice_lines = _nodes(invoice, "InvoiceLine", True)
    for node in [invoice] + invoice_lines + [_one(line, "Price") for line in invoice_lines]:
        for charge in _nodes(node, "AllowanceCharge"):
            if _amount(charge, "Amount") != 0:
                raise EvidenceError("Nonzero allowances/charges require a reviewed extended mapping")
    for prepaid in _nodes(invoice, "PrepaidPayment"):
        if _amount(prepaid, "PaidAmount") != 0:
            raise EvidenceError("Prepayments require a reviewed extended mapping")
    for delivery in _nodes(invoice, "Delivery"):
        for shipment in _nodes(delivery, "Shipment"):
            for charge in _nodes(shipment, "FreightAllowanceCharge"):
                if _amount(charge, "Amount") != 0:
                    raise EvidenceError("Shipment freight requires a reviewed extended mapping")


def parse_myinvois_invoice(envelope: dict, source: str) -> dict:
    """Parse authenticated raw envelope; reject unsupported status/profile/precision."""
    if not isinstance(envelope, dict):
        raise EvidenceError("MyInvois response must be an object")
    if not isinstance(envelope.get("uuid"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", envelope["uuid"]):
        raise EvidenceError("Missing or invalid MyInvois document UUID")
    if envelope.get("status") != "Valid" or envelope.get("cancelDateTime") or envelope.get("rejectRequestDateTime"):
        raise EvidenceError("Only Valid, non-cancelled, non-rejected MyInvois invoices are eligible")
    validated = _utc(envelope.get("dateTimeValidated"), "validation time")
    issued = _utc(envelope.get("dateTimeIssued"), "issuance time")
    if validated < issued:
        raise EvidenceError("Validation precedes invoice issuance")
    document = envelope.get("document")
    if not isinstance(document, str) or not document.strip() or len(document.encode("utf-8")) > MAX_DOCUMENT:
        raise EvidenceError("Missing or oversized raw invoice document")
    try:
        if document.lstrip().startswith("<"):
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", document, re.I):
                raise EvidenceError("XML DTD/entities are forbidden")
            depth, nodes = 0, 0
            for event, element in ET.iterparse(io.StringIO(document), events=("start", "end")):
                if event == "start":
                    depth += 1
                    nodes += 1
                    if depth > 32 or nodes > 50000:
                        raise EvidenceError("XML invoice exceeds node/depth budget")
                else:
                    depth -= 1
                    element.clear()
            root = ET.fromstring(document)
            if root.tag != "{" + D + "}Invoice":
                raise EvidenceError("Expected UBL Invoice root")
            invoice = _xml_node(root)
            format_name = "xml"
        else:
            root = strict_json(document)
            _bounded(root)
            if not isinstance(root, dict) or any(root.get(k) != v for k, v in (("_D", D), ("_A", A), ("_B", B))):
                raise EvidenceError("Missing or invalid JSON UBL namespaces")
            invoice = _one(root, "Invoice")
            format_name = "json"
    except (ET.ParseError, RecursionError, UnicodeError) as exc:
        raise EvidenceError("Malformed UBL invoice") from exc
    code = _one(invoice, "InvoiceTypeCode")
    version = code.get("listVersionID")
    if code.get("_") != "01" or version not in ("1.0", "1.1"):
        raise EvidenceError("Only ordinary invoice 01 versions 1.0/1.1 are supported")
    if envelope.get("typeVersionNumber") != version or str(envelope.get("typeName", "")).lower() != "invoice":
        raise EvidenceError("API envelope and UBL document type/version disagree")
    if _value(invoice, "DocumentCurrencyCode") != "MYR":
        raise EvidenceError("Foreign-currency MyInvois invoices require verified FX; blocked")
    iid = _text(_value(invoice, "ID"), "invoice ID")
    if envelope.get("internalId") != iid:
        raise EvidenceError("API internalId and UBL invoice ID disagree")
    if _value(invoice, "IssueDate") != issued.date().isoformat():
        raise EvidenceError("API and UBL issuance date disagree")
    supplier = _one(_one(invoice, "AccountingSupplierParty"), "Party")
    tins = [_value(v, "ID") for v in _nodes(supplier, "PartyIdentification")
            if _one(v, "ID").get("schemeID") == "TIN"]
    if len(tins) != 1 or tins[0] != envelope.get("issuerTin"):
        raise EvidenceError("API and UBL issuer TIN disagree")
    buyer = _one(_one(invoice, "AccountingCustomerParty"), "Party")
    buyer_tins = [_value(v, "ID") for v in _nodes(buyer, "PartyIdentification")
                  if _one(v, "ID").get("schemeID") == "TIN"]
    if len(buyer_tins) != 1:
        raise EvidenceError("Structured invoice requires one buyer TIN for party binding")
    _no_adjustments(invoice)
    total = _one(invoice, "LegalMonetaryTotal")
    for field in ("AllowanceTotalAmount", "ChargeTotalAmount", "PayableRoundingAmount", "PrepaidAmount"):
        if _amount(total, field, True) != 0:
            raise EvidenceError("Nonzero monetary adjustments require reviewed extended mapping")
    lines, seen = [], set()
    with localcontext() as ctx:
        ctx.prec = 80
        for line in _nodes(invoice, "InvoiceLine", True):
            if len(lines) >= 5000:
                raise EvidenceError("Too many structured invoice lines")
            lid = _text(_value(line, "ID"), "line ID")
            if lid in seen:
                raise EvidenceError("Duplicate structured invoice line ID")
            seen.add(lid)
            quantity = dec(_value(line, "InvoicedQuantity"), "quantity")
            price_node = _one(line, "Price")
            if _value(price_node, "BaseQuantity", True) is not None and dec(_value(price_node, "BaseQuantity")) != 1:
                raise EvidenceError("Non-unit price base quantity is unsupported")
            price = _amount(price_node, "PriceAmount")
            extension = _amount(line, "LineExtensionAmount")
            if quantity <= 0 or money(quantity * price) != extension:
                raise EvidenceError("UBL line arithmetic is not exactly reconciled")
            item = _one(line, "Item")
            description = _text(_value(item, "Description"), "line description")
            identification = _one(item, "SellersItemIdentification", True)
            sku = _text(_value(identification, "ID"), "seller SKU") if identification else None
            item_extension = _one(line, "ItemPriceExtension", True)
            if item_extension is not None and _amount(item_extension, "Amount") != extension:
                raise EvidenceError("UBL item subtotal differs from quantity times price")
            lines.append({"myinvois_line_id": lid, "sku": sku, "quantity": str(quantity),
                          "unit_price": str(price), "description": description})
        subtotal = _amount(total, "LineExtensionAmount")
        excluding = _amount(total, "TaxExclusiveAmount")
        including = _amount(total, "TaxInclusiveAmount")
        payable = _amount(total, "PayableAmount")
        taxes = _nodes(invoice, "TaxTotal", True)
        tax = sum((_amount(t, "TaxAmount") for t in taxes), Decimal(0))
        for t in taxes:
            subtaxes = _nodes(t, "TaxSubtotal")
            if subtaxes and sum((_amount(s, "TaxAmount") for s in subtaxes), Decimal(0)) != _amount(t, "TaxAmount"):
                raise EvidenceError("UBL invoice tax subtotal arithmetic disagrees")
        if subtotal != sum((dec(l["quantity"]) * dec(l["unit_price"]) for l in lines), Decimal(0)) or excluding != subtotal or including != subtotal + tax or payable != including or payable <= 0:
            raise EvidenceError("UBL invoice totals do not have exact monetary parity")
        for field, expected in (("totalExcludingTax", excluding), ("totalDiscount", Decimal(0)), ("totalNetAmount", subtotal), ("totalPayableAmount", payable)):
            if money(envelope.get(field), field) != expected:
                raise EvidenceError("API summary and raw invoice amounts disagree: " + field)
    return {"invoice_id": iid, "currency": "MYR", "subtotal": str(subtotal), "total": str(payable),
            "tax": str(tax), "freight": "0", "discount": "0", "line_items": lines,
            "source": source, "source_kind": "lhdn_myinvois", "document_id": envelope["uuid"],
            "issuer_tin": tins[0], "buyer_tin": buyer_tins[0], "ubl_version": version, "document_format": format_name,
            "status": "Valid", "validated_at": validated.isoformat()}


def bind_myinvois_trade(structured: list, proposal: dict) -> dict:
    """API amounts are authoritative; PDF evidence supplies independently matched SKU/shipping.

    A PDF line binds by explicit MyInvois line ID or seller SKU, else by unique
    exact (description, quantity, price). No line index/HS code is invented as SKU.
    """
    if not isinstance(proposal, dict) or not isinstance(proposal.get("invoices"), list):
        raise EvidenceError("Missing commercial invoice proposal")
    pdfs = proposal["invoices"]
    if len(pdfs) != len(structured) or len({i["invoice_id"] for i in structured}) != len(structured):
        raise EvidenceError("One commercial invoice PDF record required per MyInvois invoice")
    bound = []
    for invoice in structured:
        matches = [p for p in pdfs if p.get("invoice_id") == invoice["invoice_id"]]
        if len(matches) != 1:
            raise EvidenceError("Commercial PDF invoice ID must match MyInvois uniquely")
        pdf = matches[0]
        for field in ("issuer_tin", "buyer_tin"):
            if pdf.get(field) != invoice[field]:
                raise EvidenceError("Commercial PDF/MyInvois party identity mismatch: " + field)
        for field in ("currency",):
            if pdf.get(field) != invoice[field]:
                raise EvidenceError("Commercial PDF currency differs from MyInvois")
        for field in ("subtotal", "total", "tax", "freight", "discount"):
            if money(pdf.get(field, "0"), field) != money(invoice[field], field):
                raise EvidenceError("Commercial PDF amount differs from MyInvois: " + field)
        remaining = list(pdf.get("line_items", []))
        if len(remaining) != len(invoice["line_items"]):
            raise EvidenceError("Commercial PDF/MyInvois line count mismatch")
        items = []
        for line in invoice["line_items"]:
            candidates = []
            for p in remaining:
                if p.get("myinvois_line_id"):
                    identity = p["myinvois_line_id"] == line["myinvois_line_id"]
                elif line["sku"]:
                    identity = p.get("sku") == line["sku"]
                else:
                    identity = p.get("description") == line["description"]
                identity = identity and (p.get("description") == line["description"] or (line["sku"] is not None and p.get("sku") == line["sku"]))
                if identity and dec(p.get("quantity")) == dec(line["quantity"]) and dec(p.get("unit_price")) == dec(line["unit_price"]):
                    candidates.append(p)
            if len(candidates) != 1:
                raise EvidenceError("Ambiguous or unmatched commercial PDF/MyInvois line")
            matched = candidates[0]
            if not matched.get("sku") or (line["sku"] and matched["sku"] != line["sku"]):
                raise EvidenceError("Missing or contradictory independently evidenced SKU")
            remaining.remove(matched)
            items.append({**copy.deepcopy(matched), **line, "sku": matched["sku"],
                          "sku_provenance": "commercial_invoice_pdf" if line["sku"] is None else "ubl_seller_identifier_and_pdf"})
        bound.append({**copy.deepcopy(pdf), **{k: v for k, v in invoice.items() if k not in ("source", "line_items")},
                      "source": pdf["source"], "structured_source": invoice["source"], "line_items": items})
    return {"invoices": bound, "deliveries": copy.deepcopy(proposal.get("deliveries", []))}
