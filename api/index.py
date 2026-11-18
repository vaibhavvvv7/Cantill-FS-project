import os
import sys
import io
import re
import json
import tempfile
import pandas as pd
from pypdf import PdfReader
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Body, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# Ensure both api/ directory and repository root are in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

try:
    from api.engine import CorvoEngine, parse_contract_pdf
    from api.policy_store import DEFAULT_POLICY_V5, parse_policy_amendment
except ImportError:
    from engine import CorvoEngine, parse_contract_pdf
    from policy_store import DEFAULT_POLICY_V5, parse_policy_amendment

app = FastAPI(title="Corvo Policy-Driven PR-to-PO Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

KIT_DIR = os.getenv("KIT_DIR", os.path.join(BASE_DIR, "Cantill_FullStack_Task_Kit"))
engine = CorvoEngine(KIT_DIR)

ACTIVE_POLICY = json.loads(json.dumps(DEFAULT_POLICY_V5))
POLICY_HISTORY = [json.loads(json.dumps(DEFAULT_POLICY_V5))]
PENDING_AMENDMENT = None
AUDIT_LOG = []
CURRENT_BATCH_DF = pd.read_csv(os.path.join(KIT_DIR, "pr_batches/sample_pr_batch.csv"))
CURRENT_RESULT = engine.process_batch(CURRENT_BATCH_DF, ACTIVE_POLICY)

@app.get("/")
@app.get("/api")
@app.get("/api/")
def health_check():
    return {
        "status": "healthy",
        "service": "Corvo Policy-Driven PR-to-PO Engine",
        "policy_version": ACTIVE_POLICY.get("policy_version", "v5.0"),
        "total_pr_lines": len(CURRENT_RESULT.get("decisions", []))
    }

@app.get("/api/state")
def get_state():
    return {
        "active_policy": ACTIVE_POLICY,
        "policy_history": POLICY_HISTORY,
        "pending_amendment": PENDING_AMENDMENT,
        "batch_result": CURRENT_RESULT,
        "contracts": engine.contract_reg_df.to_dict("records"),
        "items": engine.items_df.to_dict("records"),
        "audit_log": AUDIT_LOG
    }

@app.post("/api/process-batch")
async def process_batch_endpoint(file: UploadFile = File(None)):
    global CURRENT_BATCH_DF, CURRENT_RESULT
    if file is not None:
        content = await file.read()
        CURRENT_BATCH_DF = pd.read_csv(io.BytesIO(content))
    CURRENT_RESULT = engine.process_batch(CURRENT_BATCH_DF, ACTIVE_POLICY)
    return CURRENT_RESULT

@app.get("/api/export-decisions")
def export_decisions_csv():
    cols = [
        "pr_no", "pr_line", "resolved_item_code", "group_id", "po_id",
        "decision", "route", "supplier_id", "unit_price", "group_value_inr",
        "suggested_supplier_ids", "policy_version", "reason"
    ]
    out_df = pd.DataFrame(CURRENT_RESULT["decisions"])[cols]
    stream = io.StringIO()
    out_df.to_csv(stream, index=False)
    resp = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=decisions_{ACTIVE_POLICY['policy_version']}.csv"
    return resp

@app.get("/api/preferred-suppliers")
def preview_preferred_suppliers(item_code: str = Query("ITM-1048"), as_of: str = Query("2026-11-01")):
    return engine.get_preferred_suppliers(item_code, as_of, ACTIVE_POLICY)

@app.get("/api/export-preferred-suppliers")
def export_preferred_suppliers_csv(as_of: str = Query("2026-11-01")):
    rows = []
    for _, item in engine.items_df.iterrows():
        ic = item["item_code"]
        pref = engine.get_preferred_suppliers(ic, as_of, ACTIVE_POLICY)[:3]
        for p in pref:
            rows.append({
                "item_code": ic,
                "rank": p["rank"],
                "supplier_id": p["supplier_id"],
                "reference_price": p["reference_price"]
            })
    out_df = pd.DataFrame(rows)
    stream = io.StringIO()
    out_df.to_csv(stream, index=False)
    resp = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=preferred_suppliers_{as_of}.csv"
    return resp

@app.post("/api/policy/propose")
async def propose_policy_change(text: str = Body(..., embed=True)):
    global PENDING_AMENDMENT
    PENDING_AMENDMENT = parse_policy_amendment(text, ACTIVE_POLICY)
    return PENDING_AMENDMENT

@app.post("/api/policy/upload-doc")
async def upload_policy_doc(file: UploadFile = File(...)):
    global PENDING_AMENDMENT
    content = await file.read()
    if file.filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
    else:
        text = content.decode("utf-8", errors="ignore")
    PENDING_AMENDMENT = parse_policy_amendment(text, ACTIVE_POLICY)
    return PENDING_AMENDMENT

@app.post("/api/policy/approve")
def approve_policy_change():
    global ACTIVE_POLICY, PENDING_AMENDMENT, CURRENT_RESULT
    if not PENDING_AMENDMENT:
        raise HTTPException(status_code=400, detail="No pending policy amendment")
    ACTIVE_POLICY = PENDING_AMENDMENT["proposed_policy"]
    POLICY_HISTORY.append(json.loads(json.dumps(ACTIVE_POLICY)))
    AUDIT_LOG.append({
        "timestamp": datetime.now().isoformat(),
        "actor": "Head of Procurement",
        "action": f"Approved Policy {ACTIVE_POLICY['policy_version']} (effective {ACTIVE_POLICY['effective_from']})"
    })
    PENDING_AMENDMENT = None
    CURRENT_RESULT = engine.process_batch(CURRENT_BATCH_DF, ACTIVE_POLICY)
    return {"active_policy": ACTIVE_POLICY, "batch_result": CURRENT_RESULT}

@app.post("/api/contracts/upload-pdf")
async def upload_contract_pdf(file: UploadFile = File(...)):
    global CURRENT_RESULT
    content = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    parsed = parse_contract_pdf(tmp_path)
    os.unlink(tmp_path)
    if not parsed:
        raise HTTPException(status_code=400, detail="Could not parse Rate Contract PDF structure")
    engine.add_contract_from_parsed(parsed)
    CURRENT_RESULT = engine.process_batch(CURRENT_BATCH_DF, ACTIVE_POLICY)
    AUDIT_LOG.append({
        "timestamp": datetime.now().isoformat(),
        "actor": "Buyer",
        "action": f"Uploaded PDF Contract {parsed['register']['contract_id']}"
    })
    return {
        "contracts": engine.contract_reg_df.to_dict("records"),
        "batch_result": CURRENT_RESULT
    }

@app.post("/api/chat-intake")
def chat_intake(payload: dict = Body(...)):
    global CURRENT_BATCH_DF, CURRENT_RESULT
    msg = payload.get("message", "")
    draft = payload.get("draft", {})

    if not draft.get("item_code"):
        code = engine.resolve_item_code(None, msg, "")
        if code != "NEW":
            item_row = engine.items_df[engine.items_df["item_code"] == code].iloc[0]
            draft["item_code"] = code
            draft["description"] = item_row["description"]
            draft["uom"] = item_row["uom"]
        elif len(msg.strip()) > 3:
            draft["item_code"] = "NEW"
            draft["description"] = msg.strip()
            draft["uom"] = "NOS"

    qty_m = re.search(r'\b(\d+)\s*(?:nos|kg|ltr|mtr|pair|set|cyl|bkt|brl|ream|rol|job|mon|qtr)?\b', msg, re.I)
    if qty_m and not draft.get("quantity"):
        draft["quantity"] = int(qty_m.group(1))

    if re.search(r'\b(pun1|bhosari|pune)\b', msg, re.I):
        draft["plant"] = "PUN1"
    elif re.search(r'\b(chk1|chakan)\b', msg, re.I):
        draft["plant"] = "CHK1"

    date_m = re.search(r'(\d{4}-\d{2}-\d{2})', msg)
    if date_m:
        draft["need_by_date"] = date_m.group(1)

    if re.search(r'\b(emergency|breakdown)\b', msg, re.I):
        draft["urgency"] = "Emergency"
    elif not draft.get("urgency"):
        draft["urgency"] = "Normal"

    missing = [k for k in ["description", "quantity", "plant", "need_by_date"] if not draft.get(k)]
    if missing:
        return {
            "status": "NEED_INFO",
            "draft": draft,
            "reply": f"Matched item: {draft.get('item_code', 'Pending')} ({draft.get('description', '?')}). Please provide missing fields: {', '.join(missing)} (e.g. plant PUN1/CHK1, quantity, need-by YYYY-MM-DD)."
        }

    new_pr_no = f"PR-26-{len(CURRENT_BATCH_DF) + 200:05d}"
    new_row = {
        "pr_no": new_pr_no,
        "pr_line": 1,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "requester": "Chat Requester",
        "department": "Production",
        "plant": draft["plant"],
        "item_code": draft["item_code"] if draft["item_code"] != "NEW" else None,
        "description": draft["description"],
        "quantity": int(draft["quantity"]),
        "uom": draft.get("uom", "NOS"),
        "estimated_unit_price": float(draft.get("estimated_unit_price", 1500.0)),
        "need_by_date": draft["need_by_date"],
        "urgency": draft.get("urgency", "Normal")
    }
    CURRENT_BATCH_DF = pd.concat([CURRENT_BATCH_DF, pd.DataFrame([new_row])], ignore_index=True)
    CURRENT_RESULT = engine.process_batch(CURRENT_BATCH_DF, ACTIVE_POLICY)
    return {
        "status": "CREATED",
        "draft": {},
        "reply": f"PR {new_pr_no} created for {draft['quantity']} {draft.get('uom','NOS')} of {draft['description']} at {draft['plant']}!",
        "batch_result": CURRENT_RESULT
    }

@app.post("/api/override")
def buyer_override(payload: dict = Body(...)):
    pr_no = payload.get("pr_no")
    pr_line = int(payload.get("pr_line", 1))
    new_route = payload.get("route")
    comment = payload.get("comment", "").strip()
    if not comment:
        raise HTTPException(status_code=400, detail="Written override reason is required (Policy Section 8.1)")

    for row in CURRENT_RESULT["decisions"]:
        if row["pr_no"] == pr_no and int(row["pr_line"]) == pr_line:
            orig = row["route"]
            row["route"] = new_route
            row["decision"] = "PO" if "PO" in new_route else "SOURCING_REQUEST"
            row["reason"] = f"{row['reason']} | OVERRIDE ({orig} -> {new_route}) by Buyer: {comment}"
            AUDIT_LOG.append({
                "timestamp": datetime.now().isoformat(),
                "actor": "Buyer",
                "action": f"Overrode {pr_no}/{pr_line} from {orig} to {new_route}: {comment}"
            })
            break
    return CURRENT_RESULT
