import json
import re
from datetime import datetime
from typing import Dict, Any

DEFAULT_POLICY_V5 = {
    "policy_version": "v5.0",
    "document_id": "CORVO-PROC-POL-04",
    "effective_from": "2026-04-01",
    "valid_plants": ["PUN1", "CHK1"],
    "clubbing": {
        "enabled": True,
        "created_window_calendar_days": 14,
        "need_by_window_days": 7,
        "same_item": True,
        "same_plant": True
    },
    "valuation": {
        "po_history_lookback_days": 365
    },
    "preferred_supplier": {
        "po_lookback_days": 548,
        "recency_high_days": 90,
        "recency_mid_days": 365,
        "weights": {
            "has_contract": 18.0,
            "po_count_per_po": 4.0,
            "po_count_cap": 5,
            "recency": 25.0,
            "on_time_pct": 30.0,
            "quality_rating": 25.0
        }
    },
    "consolidation": {
        "enabled": True,
        "window_hours": 24,
        "same_supplier": True,
        "same_plant": True,
        "same_route": True
    },
    "plant_overrides": {},
    "category_overrides": {},
    "rules": [
        {
            "clause": "1.2",
            "name": "New Item Sourcing",
            "when": {"is_new_item": True},
            "decision": "SOURCING_REQUEST",
            "route": "NEW_ITEM_SOURCING",
            "suggest_count": 0
        },
        {
            "clause": "5.1",
            "name": "Capex Sourcing",
            "when": {"item_type": "Capex"},
            "decision": "SOURCING_REQUEST",
            "route": "CAPEX_SOURCING",
            "suggest_count": 5
        },
        {
            "clause": "5.2",
            "name": "Rate Contract PO",
            "when": {"has_usable_contract": True},
            "decision": "PO",
            "route": "CONTRACT_PO",
            "suggest_count": 0
        },
        {
            "clause": "5.3",
            "name": "Emergency PO",
            "when": {
                "is_emergency": True,
                "max_value_inr": 500000,
                "bought_from_active_in_365d": True
            },
            "decision": "PO",
            "route": "EMERGENCY_PO",
            "suggest_count": 0
        },
        {
            "clause": "5.4",
            "name": "Spot PO",
            "when": {
                "lt_value_inr": 200000,
                "bought_from_active_in_365d": True
            },
            "decision": "PO",
            "route": "SPOT_PO",
            "suggest_count": 0
        },
        {
            "clause": "5.5",
            "name": "Request for Quotation (RFQ)",
            "when": {"lt_value_inr": 1000000},
            "decision": "SOURCING_REQUEST",
            "route": "RFQ",
            "suggest_count": 3
        },
        {
            "clause": "5.6",
            "name": "Request for Proposal (RFP)",
            "when": {"min_value_inr": 1000000},
            "decision": "SOURCING_REQUEST",
            "route": "RFP",
            "suggest_count": 5
        }
    ]
}

def _parse_amount(num_str: str, unit_str: str = "") -> float:
    val = float(num_str.replace(",", "").strip())
    u = (unit_str or "").strip().lower()
    if u in ["lakh", "lakhs", "lac", "lacs"]:
        val *= 100000
    elif u in ["crore", "crores", "cr"]:
        val *= 10000000
    return val

def parse_policy_amendment(text: str, current_policy: Dict[str, Any]) -> Dict[str, Any]:
    updated = json.loads(json.dumps(current_policy))
    changes = []

    ver_m = re.search(r'version\s*v?([0-9]+\.[0-9]+)', text, re.I)
    if ver_m:
        updated["policy_version"] = f"v{ver_m.group(1)}"
    else:
        curr_v = float(updated.get("policy_version", "v5.0").replace("v", ""))
        updated["policy_version"] = f"v{curr_v + 0.1:.1f}"
    changes.append(f"Policy version updated to {updated['policy_version']}")

    eff_m = re.search(r'effective\s*(?:from|date)?\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})', text, re.I)
    if eff_m:
        raw_dt = eff_m.group(1).strip()
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
            try:
                updated["effective_from"] = datetime.strptime(raw_dt, fmt).strftime("%Y-%m-%d")
                changes.append(f"Effective date set to {updated['effective_from']}")
                break
            except Exception:
                pass

    spot_m = re.search(r'spot\s*(?:po|purchase|limit|threshold).*?(?:₹|inr|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|lacs)?', text, re.I)
    if spot_m:
        val = _parse_amount(spot_m.group(1), spot_m.group(2))
        for r in updated["rules"]:
            if r["route"] == "SPOT_PO":
                old = r["when"].get("lt_value_inr", 200000)
                r["when"]["lt_value_inr"] = val
                changes.append(f"Clause 5.4 (SPOT_PO) limit updated from ₹{old:,.0f} to ₹{val:,.0f}")

    emerg_m = re.search(r'emergency\s*(?:po|limit|threshold).*?(?:₹|inr|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|lacs)?', text, re.I)
    if emerg_m:
        val = _parse_amount(emerg_m.group(1), emerg_m.group(2))
        for r in updated["rules"]:
            if r["route"] == "EMERGENCY_PO":
                old = r["when"].get("max_value_inr", 500000)
                r["when"]["max_value_inr"] = val
                changes.append(f"Clause 5.3 (EMERGENCY_PO) limit updated from ₹{old:,.0f} to ₹{val:,.0f}")

    rfp_m = re.search(r'(?:rfp|rfq)\s*(?:limit|threshold|boundary).*?(?:₹|inr|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|lacs)?', text, re.I)
    if rfp_m:
        val = _parse_amount(rfp_m.group(1), rfp_m.group(2))
        for r in updated["rules"]:
            if r["route"] == "RFQ":
                r["when"]["lt_value_inr"] = val
            elif r["route"] == "RFP":
                r["when"]["min_value_inr"] = val
        changes.append(f"Clause 5.5/5.6 (RFQ / RFP threshold) updated to ₹{val:,.0f}")

    club_m = re.search(r'clubbing.*?(\d+)\s*(?:calendar\s*)?days', text, re.I)
    if club_m:
        days = int(club_m.group(1))
        old_d = updated["clubbing"]["created_window_calendar_days"]
        updated["clubbing"]["created_window_calendar_days"] = days
        changes.append(f"Section 2.2 Clubbing creation window changed from {old_d} to {days} calendar days")

    need_m = re.search(r'need[-\s]*by.*?(\d+)\s*days', text, re.I)
    if need_m:
        days = int(need_m.group(1))
        old_n = updated["clubbing"]["need_by_window_days"]
        updated["clubbing"]["need_by_window_days"] = days
        changes.append(f"Section 2.2 Clubbing need-by window changed from {old_n} to {days} days")

    consol_m = re.search(r'consolidat.*?(\d+)\s*hours', text, re.I)
    if consol_m:
        hrs = int(consol_m.group(1))
        old_h = updated["consolidation"]["window_hours"]
        updated["consolidation"]["window_hours"] = hrs
        changes.append(f"Section 7.1 PO consolidation window changed from {old_h} to {hrs} hours")

    for p_code in ["PUN1", "CHK1", "PUN2"]:
        if re.search(r'\b' + p_code + r'\b', text):
            if p_code not in updated["valid_plants"] and re.search(r'(?:add|include|apply|enable).*?' + p_code, text, re.I):
                updated["valid_plants"].append(p_code)
                changes.append(f"Added plant {p_code} to valid policy scope")
            p_spot = re.search(p_code + r'.*?spot.*?(?:₹|inr|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs)?', text, re.I)
            if p_spot:
                pval = _parse_amount(p_spot.group(1), p_spot.group(2))
                updated.setdefault("plant_overrides", {}).setdefault(p_code, {})["spot_limit"] = pval
                changes.append(f"Plant override for {p_code}: SPOT_PO limit set to ₹{pval:,.0f}")

    for cat in ["Safety", "IT", "Electrical", "Raw Material", "Fasteners", "Bearings", "Hydraulics", "Pneumatics", "Packaging", "Services", "Lubricants", "Welding", "Cutting Tools", "Capex"]:
        cat_m = re.search(cat + r'.*?(?:spot|po|rfq|rfp).*?(?:₹|inr|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs)?', text, re.I)
        if cat_m:
            cval = _parse_amount(cat_m.group(1), cat_m.group(2))
            updated.setdefault("category_overrides", {})[cat] = {"spot_limit": cval}
            changes.append(f"Category override for {cat}: threshold set to ₹{cval:,.0f}")

    return {"proposed_policy": updated, "changes": changes}
