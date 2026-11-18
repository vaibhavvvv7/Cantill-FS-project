# DECISIONS.md — Engineering & Procurement Policy Judgment Log

## 1. Edge Cases & Silent Policy Scenarios in `sample_pr_batch.csv`

### 1.1 Zero-Quantity Requisition (`PR-26-00172/1`)
* **Observation:** `PR-26-00172/1` requests `Cutting wheel 14 inch` (`ITM-1020`) with `quantity = 0`.
* **Decision:** Route to `NOT_PROCESSED` (`decision = NOT_PROCESSED`, `route = NOT_PROCESSED`, `group_value_inr = 0`) and exclude from requirement clubbing (Section 2.2) so a zero-quantity line never shifts or anchors a 14-day clubbing window.

### 1.2 Out-of-Scope Plant (`PR-26-00173/1`)
* **Observation:** `PR-26-00173/1` specifies plant `PUN2`. Both `master_data/plants.csv` and the header of `Corvo_Procurement_Policy.pdf` v5.0 state that the policy applies only to `PUN1` (Bhosari) and `CHK1` (Chakan).
* **Decision:** Route to `NOT_PROCESSED` with explicit reason `"Plant 'PUN2' is outside policy scope (CHK1, PUN1)"`.

### 1.3 Free-Text `"urgent"` vs. Structured `urgency` Field
* **Observation:** Lines such as `PR-26-00018/1`, `PR-26-00023/1`, `PR-26-00139/1`, and `PR-26-00167/2` append `"- urgent"` in the free-text description while the `urgency` column is `Normal`.
* **Decision:** Strip conversational suffixes (`urgent`, `for maintenance`, `as per sample`, `repeat`) during item matching (Section 1.1) and trigger Emergency Rule 5.3 strictly from the formal `urgency == "Emergency"` column so requesters cannot bypass quote thresholds via free-text descriptions.

### 1.4 Blocked Supplier Holding an Unexpired Rate Contract (`RC-2026-005` / `S004`)
* **Observation:** `RC-2026-005` covers `ITM-1006` (`Aluminium plate 6061 10mm`), but supplier `S004` (`Metalink Alloys`) is marked `Blocked` in `supplier_master.csv`.
* **Decision:** Disqualify `RC-2026-005` under Clause 4.1 and Clause 5.7 for both direct PO execution and Clause 3.1(a) valuation fallback, falling back to `item_master.csv` standard price (₹365/kg), reproducing Worked Example 10 (₹80,300 → RFQ).

### 1.5 Duplicate Requisitions Raised Minutes Apart (`PR-26-00174/1` & `PR-26-00175/1`)
* **Observation:** Two identical PR lines for `ITM-1033` (`Servo drive 2kW`, Qty 2 each) at `PUN1` were created 4 minutes apart on `2026-11-02`.
* **Decision:** Following Section 2.2, both lines are clubbed into a single requirement of total quantity 4 (`group_value_inr = ₹1,76,826`), preventing split-PR limit circumvention.

## 2. Preferred Supplier Ranking Formula (Section 6.2 & 6.4)
For every Active supplier qualifying under Section 6.1 (at least 1 PO in the trailing 548 days or a contract starting on/before the requirement date):

Score = 18 * I(HasContract) + 4 * min(PO_Count_548d, 5) + 25 * Recency + 30 * (OnTimePct / 100) + 25 * (QualityRating / 5)

Where `Recency` is `1.0` if the most recent PO was within 90 days, `0.5` if within 365 days, and `0.0` otherwise. When topping up from category suppliers for sourcing events (Section 6.4), remaining Active category suppliers are ranked by `0.5 * (OnTimePct / 100) + 0.5 * (QualityRating / 5)`.
