import os
import re
import glob
import pandas as pd
from pypdf import PdfReader
from typing import Dict, Any, List, Optional, Tuple

ABBREV_MAP = {
    "brg": "bearing",
    "pneu": "pneumatic",
    "hyd": "hydraulic",
    "rod": "electrode",
}

def clean_tokens(text: str) -> set:
    t = str(text).lower()
    t = re.sub(r'\b(as per sample|for maintenance|urgent|repeat)\b', ' ', t)
    t = t.replace('24"', '24 inch')
    for k, v in ABBREV_MAP.items():
        t = re.sub(r'\b' + re.escape(k) + r'\b', v, t)
    return set(re.findall(r'[a-z0-9]+', t))

def parse_contract_pdf(pdf_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(pdf_path):
        return None
    try:
        reader = PdfReader(pdf_path)
        full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return None

    cid_m = re.search(r'Contract No\.\s*(RC-\d{4}-\d+)', full_text)
    sup_m = re.search(r'and\s+([^,]+),\s*[^\(]+\(Supplier ID\s*(S\d+)\)', full_text)
    term_m = re.search(r'valid from\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})', full_text)
    cap_m = re.search(r'shall not exceed INR\s*([\d,]+)', full_text)

    if not (cid_m and sup_m and term_m and cap_m):
        return None

    cid = cid_m.group(1).strip()
    supplier_name = sup_m.group(1).strip()
    supplier_id = sup_m.group(2).strip()
    start_date = term_m.group(1).strip()
    end_date = term_m.group(2).strip()
    value_cap = float(cap_m.group(1).replace(",", ""))

    lines = []
    for line in full_text.splitlines():
        m = re.match(r'^\s*\d+\s+(ITM-\d+)\s+(.+?)\s+([A-Z]{2,4})\s+([\d,]+\.\d{2})\s*$', line.strip())
        if m:
            lines.append({
                "contract_id": cid,
                "item_code": m.group(1),
                "description": m.group(2).strip(),
                "uom": m.group(3).strip(),
                "rate": float(m.group(4).replace(",", ""))
            })

    return {
        "register": {
            "contract_id": cid,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "start_date": start_date,
            "end_date": end_date,
            "value_cap": value_cap,
            "consumed_value": 0.0,
            "source": "PDF_ONLY"
        },
        "lines": lines,
        "raw_text": full_text
    }

class CorvoEngine:
    def __init__(self, kit_dir: str):
        self.kit_dir = kit_dir
        self.plants_df = pd.read_csv(os.path.join(kit_dir, "master_data/plants.csv"))
        self.items_df = pd.read_csv(os.path.join(kit_dir, "master_data/item_master.csv"))
        self.suppliers_df = pd.read_csv(os.path.join(kit_dir, "master_data/supplier_master.csv"))
        self.po_df = pd.read_csv(os.path.join(kit_dir, "master_data/po_history.csv"))
        self.po_df["po_dt"] = pd.to_datetime(self.po_df["po_date"])

        reg_df = pd.read_csv(os.path.join(kit_dir, "contracts/contract_register.csv"))
        reg_df["source"] = "REGISTER"
        lines_df = pd.read_csv(os.path.join(kit_dir, "contracts/contract_lines.csv"))

        self.contract_pdfs: Dict[str, str] = {}
        pdf_regs = []
        pdf_lines = []
        known_cids = set(reg_df["contract_id"].unique())

        for pdf_file in sorted(glob.glob(os.path.join(kit_dir, "contracts/pdf/*.pdf"))):
            parsed = parse_contract_pdf(pdf_file)
            if parsed:
                cid = parsed["register"]["contract_id"]
                self.contract_pdfs[cid] = parsed["raw_text"]
                if cid not in known_cids:
                    pdf_regs.append(parsed["register"])
                    pdf_lines.extend(parsed["lines"])

        if pdf_regs:
            reg_df = pd.concat([reg_df, pd.DataFrame(pdf_regs)], ignore_index=True)
        if pdf_lines:
            lines_df = pd.concat([lines_df, pd.DataFrame(pdf_lines)], ignore_index=True)

        self.contract_reg_df = reg_df
        self.contract_lines_df = lines_df

    def add_contract_from_parsed(self, parsed: Dict[str, Any]):
        cid = parsed["register"]["contract_id"]
        self.contract_pdfs[cid] = parsed["raw_text"]
        if cid not in set(self.contract_reg_df["contract_id"]):
            self.contract_reg_df = pd.concat([self.contract_reg_df, pd.DataFrame([parsed["register"]])], ignore_index=True)
            self.contract_lines_df = pd.concat([self.contract_lines_df, pd.DataFrame(parsed["lines"])], ignore_index=True)

    def resolve_item_code(self, item_code_val: Any, description: str, uom: str = "") -> str:
        if pd.notna(item_code_val) and str(item_code_val).strip() in set(self.items_df["item_code"]):
            return str(item_code_val).strip()
        q_tokens = clean_tokens(description)
        best_code = "NEW"
        best_score = 0.0
        for _, ir in self.items_df.iterrows():
            i_tokens = clean_tokens(ir["description"])
            if not i_tokens:
                continue
            inter = len(q_tokens & i_tokens)
            score = inter / len(i_tokens)
            if uom and str(ir["uom"]).upper() == str(uom).upper():
                score += 0.1
            if score > best_score and inter >= 2:
                best_score = score
                best_code = ir["item_code"]
        return best_code if best_score >= 0.65 else "NEW"

    def get_preferred_suppliers(self, item_code: str, as_of_date_str: str, policy: Dict[str, Any]) -> List[Dict[str, Any]]:
        if item_code == "NEW":
            return []
        as_of = pd.to_datetime(str(as_of_date_str)[:10])
        pref_cfg = policy.get("preferred_supplier", {})
        lookback = pref_cfg.get("po_lookback_days", 548)
        cutoff_548 = as_of - pd.Timedelta(days=lookback)

        pos = self.po_df[
            (self.po_df["item_code"] == item_code) &
            (self.po_df["po_dt"] >= cutoff_548) &
            (self.po_df["po_dt"] <= as_of)
        ].sort_values(["po_dt", "po_number"], ascending=[False, False])

        c_item = self.contract_lines_df[self.contract_lines_df["item_code"] == item_code].merge(
            self.contract_reg_df, on="contract_id"
        )
        c_item["start_dt"] = pd.to_datetime(c_item["start_date"])
        c_valid = c_item[c_item["start_dt"] <= as_of].sort_values(["start_dt", "contract_id"], ascending=[False, False])

        cand_sids = set(pos["supplier_id"].unique()) | set(c_valid["supplier_id"].unique())
        w = pref_cfg.get("weights", {})
        results = []

        for sid in cand_sids:
            s_rows = self.suppliers_df[self.suppliers_df["supplier_id"] == sid]
            if s_rows.empty or s_rows.iloc[0]["status"] != "Active":
                continue
            s_row = s_rows.iloc[0]
            s_pos = pos[pos["supplier_id"] == sid]
            po_count = len(s_pos)

            if po_count > 0:
                last_po_dt = s_pos.iloc[0]["po_dt"]
                days_ago = (as_of - last_po_dt).days
                if days_ago <= pref_cfg.get("recency_high_days", 90):
                    recency = 1.0
                elif days_ago <= pref_cfg.get("recency_mid_days", 365):
                    recency = 0.5
                else:
                    recency = 0.0
                ref_price = float(s_pos.iloc[0]["unit_price"])
                ref_source = f"PO {s_pos.iloc[0]['po_number']}"
            else:
                recency = 0.0
                s_c = c_valid[c_valid["supplier_id"] == sid]
                ref_price = float(s_c.iloc[0]["rate"])
                ref_source = f"Contract {s_c.iloc[0]['contract_id']}"

            has_contract = sid in set(c_valid["supplier_id"].unique())
            on_time = float(s_row["on_time_pct"])
            quality = float(s_row["quality_rating"])

            score = (
                (w.get("has_contract", 18.0) if has_contract else 0.0)
                + min(po_count, w.get("po_count_cap", 5)) * w.get("po_count_per_po", 4.0)
                + recency * w.get("recency", 25.0)
                + (on_time / 100.0) * w.get("on_time_pct", 30.0)
                + (quality / 5.0) * w.get("quality_rating", 25.0)
            )
            results.append({
                "item_code": item_code,
                "supplier_id": sid,
                "supplier_name": s_row["name"],
                "po_count_548d": po_count,
                "recency": recency,
                "has_contract": has_contract,
                "on_time_pct": on_time,
                "quality_rating": quality,
                "reference_price": round(ref_price, 2),
                "reference_source": ref_source,
                "score": round(score, 2)
            })

        results.sort(key=lambda x: (-x["score"], x["supplier_id"]))
        for idx, r in enumerate(results):
            r["rank"] = idx + 1
        return results

    def suggest_for_sourcing(self, item_code: str, as_of_date_str: str, count_needed: int, policy: Dict[str, Any]) -> List[str]:
        if item_code == "NEW" or count_needed <= 0:
            return []
        pref = self.get_preferred_suppliers(item_code, as_of_date_str, policy)
        sids = [p["supplier_id"] for p in pref]
        if len(sids) >= count_needed:
            return sids[:count_needed]

        item_row = self.items_df[self.items_df["item_code"] == item_code].iloc[0]
        cat = item_row["category"]
        cat_sups = self.suppliers_df[
            self.suppliers_df["categories"].str.contains(cat, regex=False, na=False) &
            (self.suppliers_df["status"] == "Active") &
            (~self.suppliers_df["supplier_id"].isin(sids))
        ].copy()
        cat_sups["cat_score"] = 0.5 * (cat_sups["on_time_pct"] / 100.0) + 0.5 * (cat_sups["quality_rating"] / 5.0)
        cat_sups = cat_sups.sort_values(["cat_score", "supplier_id"], ascending=[False, True])
        for sid in cat_sups["supplier_id"]:
            sids.append(sid)
            if len(sids) >= count_needed:
                break
        return sids

    def process_batch(self, pr_df: pd.DataFrame, policy: Dict[str, Any]) -> Dict[str, Any]:
        df = pr_df.copy()
        df["resolved_item_code"] = df.apply(
            lambda r: self.resolve_item_code(r.get("item_code"), r.get("description", ""), r.get("uom", "")),
            axis=1
        )
        df["created_dt"] = pd.to_datetime(df["created_at"])
        df["need_by_dt"] = pd.to_datetime(df["need_by_date"])

        df_sorted = df.sort_values(["created_dt", "pr_no", "pr_line"]).copy()
        valid_plants = set(policy.get("valid_plants", ["PUN1", "CHK1"]))
        club_cfg = policy.get("clubbing", {})

        requirements: List[Dict[str, Any]] = []
        not_processed_lines: List[Dict[str, Any]] = []
        req_counter = 1

        for _, row in df_sorted.iterrows():
            qty = float(row["quantity"]) if pd.notna(row["quantity"]) else 0.0
            plant = str(row["plant"]).strip()

            if qty <= 0:
                not_processed_lines.append({
                    "row": row,
                    "reason": f"{policy['policy_version']} Validation: Quantity is {int(qty)} (must be > 0)"
                })
                continue

            if plant not in valid_plants:
                not_processed_lines.append({
                    "row": row,
                    "reason": f"{policy['policy_version']} Validation: Plant '{plant}' is outside policy scope ({', '.join(sorted(valid_plants))})"
                })
                continue

            ic = row["resolved_item_code"]
            if ic == "NEW" or not club_cfg.get("enabled", True):
                requirements.append({
                    "group_id": f"REQ-{req_counter:04d}",
                    "item_code": ic,
                    "plant": plant,
                    "first_created_dt": row["created_dt"],
                    "first_need_dt": row["need_by_dt"],
                    "lines": [row]
                })
                req_counter += 1
                continue

            joined = False
            for req in requirements:
                if req["item_code"] == ic and req["plant"] == plant:
                    c_days = (row["created_dt"].date() - req["first_created_dt"].date()).days
                    n_days = abs((row["need_by_dt"].date() - req["first_need_dt"].date()).days)
                    if 0 <= c_days <= club_cfg.get("created_window_calendar_days", 14) and n_days <= club_cfg.get("need_by_window_days", 7):
                        req["lines"].append(row)
                        joined = True
                        break

            if not joined:
                requirements.append({
                    "group_id": f"REQ-{req_counter:04d}",
                    "item_code": ic,
                    "plant": plant,
                    "first_created_dt": row["created_dt"],
                    "first_need_dt": row["need_by_dt"],
                    "lines": [row]
                })
                req_counter += 1

        consumed_tracker: Dict[str, float] = {
            r["contract_id"]: float(r["consumed_value"])
            for _, r in self.contract_reg_df.iterrows()
        }
        active_suppliers = set(
            self.suppliers_df[self.suppliers_df["status"] == "Active"]["supplier_id"]
        )

        for req in requirements:
            ic = req["item_code"]
            total_qty = sum(float(l["quantity"]) for l in req["lines"])
            req["total_qty"] = total_qty
            req_dt = pd.to_datetime(req["first_created_dt"].strftime("%Y-%m-%d"))
            req["req_date_str"] = req["first_created_dt"].strftime("%Y-%m-%d")
            req["is_emergency"] = any(str(l["urgency"]).strip().lower() == "emergency" for l in req["lines"])

            if ic == "NEW":
                est_price = float(req["lines"][0]["estimated_unit_price"]) if pd.notna(req["lines"][0]["estimated_unit_price"]) else 0.0
                req["unit_price"] = ""
                req["group_value_inr"] = round(total_qty * est_price)
                req["decision"] = "SOURCING_REQUEST"
                req["route"] = "NEW_ITEM_SOURCING"
                req["supplier_id"] = ""
                req["suggested_supplier_ids"] = ""
                req["clause"] = "1.2"
                req["reason"] = f"{policy['policy_version']} Clause 1.2: Unmatched new item; valued at requester estimate ₹{est_price:,.2f}"
                continue

            item_row = self.items_df[self.items_df["item_code"] == ic].iloc[0]
            item_type = str(item_row["item_type"]).strip()
            item_cat = str(item_row["category"]).strip()
            std_price = float(item_row["standard_price"])

            c_matches = self.contract_lines_df[self.contract_lines_df["item_code"] == ic].merge(
                self.contract_reg_df, on="contract_id"
            )
            applicable_contracts = []
            for _, cr in c_matches.iterrows():
                s_dt = pd.to_datetime(cr["start_date"])
                e_dt = pd.to_datetime(cr["end_date"])
                if s_dt <= req_dt <= e_dt and cr["supplier_id"] in active_suppliers:
                    applicable_contracts.append(cr)

            applicable_contracts.sort(key=lambda x: (float(x["rate"]), x["contract_id"]))

            usable_contract = None
            for cr in applicable_contracts:
                cid = cr["contract_id"]
                rate = float(cr["rate"])
                cap = float(cr["value_cap"])
                rem = cap - consumed_tracker.get(cid, 0.0)
                if total_qty * rate <= rem:
                    usable_contract = cr
                    break

            cutoff_365 = req_dt - pd.Timedelta(days=365)
            pos_365 = self.po_df[
                (self.po_df["item_code"] == ic) &
                (self.po_df["po_dt"] >= cutoff_365) &
                (self.po_df["po_dt"] <= req_dt)
            ].sort_values(["po_dt", "po_number"], ascending=[False, False])

            pos_365_active = pos_365[pos_365["supplier_id"].isin(active_suppliers)]
            bought_active_365 = len(pos_365_active) > 0

            if usable_contract is not None:
                val_unit_price = float(usable_contract["rate"])
                val_source = f"Contract {usable_contract['contract_id']} rate ₹{val_unit_price:,.2f}"
            elif len(applicable_contracts) > 0:
                val_unit_price = float(applicable_contracts[0]["rate"])
                val_source = f"Lowest applicable contract {applicable_contracts[0]['contract_id']} rate ₹{val_unit_price:,.2f} (ceiling exceeded)"
            elif len(pos_365) > 0:
                val_unit_price = float(pos_365.iloc[0]["unit_price"])
                val_source = f"Most recent PO {pos_365.iloc[0]['po_number']} price ₹{val_unit_price:,.2f}"
            else:
                val_unit_price = std_price
                val_source = f"Item master standard price ₹{val_unit_price:,.2f}"

            group_val = round(total_qty * val_unit_price)

            if item_type.lower() == "capex":
                sugs = self.suggest_for_sourcing(ic, req["req_date_str"], 5, policy)
                req["decision"] = "SOURCING_REQUEST"
                req["route"] = "CAPEX_SOURCING"
                req["supplier_id"] = ""
                req["unit_price"] = ""
                req["group_value_inr"] = group_val
                req["suggested_supplier_ids"] = ";".join(sugs)
                req["clause"] = "5.1"
                req["reason"] = f"{policy['policy_version']} Clause 5.1: Capex item ({val_source})"
            elif usable_contract is not None:
                cid = usable_contract["contract_id"]
                rate = float(usable_contract["rate"])
                consumed_tracker[cid] = consumed_tracker.get(cid, 0.0) + (total_qty * rate)
                req["decision"] = "PO"
                req["route"] = "CONTRACT_PO"
                req["supplier_id"] = usable_contract["supplier_id"]
                req["unit_price"] = round(rate, 2)
                req["group_value_inr"] = round(total_qty * rate)
                req["suggested_supplier_ids"] = ""
                req["clause"] = "5.2"
                req["reason"] = f"{policy['policy_version']} Clause 5.2: Active Rate Contract {cid} with {usable_contract['supplier_id']}"
            else:
                emerg_limit = 500000
                spot_limit = 200000
                rfq_limit = 1000000
                for r in policy.get("rules", []):
                    if r["route"] == "EMERGENCY_PO":
                        emerg_limit = r["when"].get("max_value_inr", 500000)
                    elif r["route"] == "SPOT_PO":
                        spot_limit = r["when"].get("lt_value_inr", 200000)
                    elif r["route"] == "RFQ":
                        rfq_limit = r["when"].get("lt_value_inr", 1000000)

                p_ovr = policy.get("plant_overrides", {}).get(req["plant"], {})
                if "spot_limit" in p_ovr:
                    spot_limit = p_ovr["spot_limit"]
                c_ovr = policy.get("category_overrides", {}).get(item_cat, {})
                if "spot_limit" in c_ovr:
                    spot_limit = c_ovr["spot_limit"]

                pref_list = self.get_preferred_suppliers(ic, req["req_date_str"], policy)

                if req["is_emergency"] and group_val <= emerg_limit and bought_active_365 and pref_list:
                    top_sup = pref_list[0]
                    ref_p = top_sup["reference_price"]
                    req["decision"] = "PO"
                    req["route"] = "EMERGENCY_PO"
                    req["supplier_id"] = top_sup["supplier_id"]
                    req["unit_price"] = round(ref_p, 2)
                    req["group_value_inr"] = round(total_qty * ref_p)
                    req["suggested_supplier_ids"] = ""
                    req["clause"] = "5.3"
                    req["reason"] = f"{policy['policy_version']} Clause 5.3: Emergency PO <= ₹{emerg_limit:,.0f}; 1st preferred {top_sup['supplier_id']} ({top_sup['reference_source']})"
                elif group_val < spot_limit and bought_active_365 and pref_list:
                    top_sup = pref_list[0]
                    ref_p = top_sup["reference_price"]
                    req["decision"] = "PO"
                    req["route"] = "SPOT_PO"
                    req["supplier_id"] = top_sup["supplier_id"]
                    req["unit_price"] = round(ref_p, 2)
                    req["group_value_inr"] = round(total_qty * ref_p)
                    req["suggested_supplier_ids"] = ""
                    req["clause"] = "5.4"
                    req["reason"] = f"{policy['policy_version']} Clause 5.4: Spot PO < ₹{spot_limit:,.0f}; 1st preferred {top_sup['supplier_id']} ({top_sup['reference_source']})"
                elif group_val < rfq_limit:
                    sugs = self.suggest_for_sourcing(ic, req["req_date_str"], 3, policy)
                    req["decision"] = "SOURCING_REQUEST"
                    req["route"] = "RFQ"
                    req["supplier_id"] = ""
                    req["unit_price"] = ""
                    req["group_value_inr"] = group_val
                    req["suggested_supplier_ids"] = ";".join(sugs)
                    req["clause"] = "5.5"
                    req["reason"] = f"{policy['policy_version']} Clause 5.5: RFQ (< ₹{rfq_limit:,.0f}); valued via {val_source}"
                else:
                    sugs = self.suggest_for_sourcing(ic, req["req_date_str"], 5, policy)
                    req["decision"] = "SOURCING_REQUEST"
                    req["route"] = "RFP"
                    req["supplier_id"] = ""
                    req["unit_price"] = ""
                    req["group_value_inr"] = group_val
                    req["suggested_supplier_ids"] = ";".join(sugs)
                    req["clause"] = "5.6"
                    req["reason"] = f"{policy['policy_version']} Clause 5.6: RFP (>= ₹{rfq_limit:,.0f}); valued via {val_source}"

        open_pos: List[Dict[str, Any]] = []
        po_counter = 1
        consol_hrs = policy.get("consolidation", {}).get("window_hours", 24)

        for req in requirements:
            if req["decision"] != "PO":
                req["po_id"] = ""
                continue
            joined_po = None
            for po in open_pos:
                if (
                    po["supplier_id"] == req["supplier_id"]
                    and po["plant"] == req["plant"]
                    and po["route"] == req["route"]
                ):
                    hrs = (req["first_created_dt"] - po["first_created_dt"]).total_seconds() / 3600.0
                    if 0 <= hrs <= consol_hrs:
                        joined_po = po
                        break
            if joined_po:
                req["po_id"] = joined_po["po_id"]
            else:
                new_po_id = f"PO-{po_counter:04d}"
                po_counter += 1
                req["po_id"] = new_po_id
                open_pos.append({
                    "po_id": new_po_id,
                    "supplier_id": req["supplier_id"],
                    "plant": req["plant"],
                    "route": req["route"],
                    "first_created_dt": req["first_created_dt"]
                })

        line_map: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for req in requirements:
            for l in req["lines"]:
                key = (str(l["pr_no"]), int(l["pr_line"]))
                line_map[key] = {
                    "pr_no": str(l["pr_no"]),
                    "pr_line": int(l["pr_line"]),
                    "created_at": str(l["created_at"]),
                    "plant": str(l["plant"]),
                    "description": str(l["description"]),
                    "quantity": int(l["quantity"]),
                    "uom": str(l["uom"]),
                    "need_by_date": str(l["need_by_date"]),
                    "urgency": str(l["urgency"]),
                    "resolved_item_code": req["item_code"],
                    "group_id": req["group_id"],
                    "po_id": req["po_id"],
                    "decision": req["decision"],
                    "route": req["route"],
                    "supplier_id": req["supplier_id"],
                    "unit_price": req["unit_price"] if req["unit_price"] != "" else "",
                    "group_value_inr": req["group_value_inr"],
                    "suggested_supplier_ids": req["suggested_supplier_ids"],
                    "policy_version": policy["policy_version"],
                    "reason": req["reason"]
                }

        for np_item in not_processed_lines:
            l = np_item["row"]
            key = (str(l["pr_no"]), int(l["pr_line"]))
            line_map[key] = {
                "pr_no": str(l["pr_no"]),
                "pr_line": int(l["pr_line"]),
                "created_at": str(l["created_at"]),
                "plant": str(l["plant"]),
                "description": str(l["description"]),
                "quantity": int(l["quantity"]),
                "uom": str(l["uom"]),
                "need_by_date": str(l["need_by_date"]),
                "urgency": str(l["urgency"]),
                "resolved_item_code": l["resolved_item_code"],
                "group_id": "",
                "po_id": "",
                "decision": "NOT_PROCESSED",
                "route": "NOT_PROCESSED",
                "supplier_id": "",
                "unit_price": "",
                "group_value_inr": 0,
                "suggested_supplier_ids": "",
                "policy_version": policy["policy_version"],
                "reason": np_item["reason"]
            }

        ordered_rows = []
        for _, orig_row in pr_df.iterrows():
            key = (str(orig_row["pr_no"]), int(orig_row["pr_line"]))
            ordered_rows.append(line_map[key])

        return {
            "policy_version": policy["policy_version"],
            "total_lines": len(ordered_rows),
            "total_requirements": len(requirements),
            "total_pos": po_counter - 1,
            "decisions": ordered_rows
        }
