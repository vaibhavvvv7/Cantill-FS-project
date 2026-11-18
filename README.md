# Corvo Engineering — Policy-Driven PR-to-PO Engine

Production full-stack web application built for the **Cantill.ai Full-Stack Task** implementing **Corvo Procurement Policy v5.0** (with Policy-as-Data architecture for dynamic policy amendments).

---

## 🚀 Live Deployment on Vercel

This repository is pre-configured for zero-config deployment on Vercel using `vercel.json` rewrites, combining a Vite + React SPA frontend with Python Serverless Functions in `/api`.

### Option A: Deploy via GitHub (Recommended)

1. **Initialize Git & Push to GitHub:**
   ```bash
   git init
   git add .
   git commit -m "feat: Corvo policy-driven PR-to-PO engine"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo-name>.git
   git push -u origin main
   ```

2. **Import to Vercel:**
   * Go to [vercel.com/new](https://vercel.com/new).
   * Select your GitHub repository.
   * **Project Settings:**
     * **Framework Preset:** `Vite` (auto-detected)
     * **Root Directory:** `./`
     * **Build Command:** `npm run build`
     * **Output Directory:** `dist`
   * Click **Deploy**.

Vercel will automatically:
* Build the React frontend into `dist/`.
* Deploy `api/index.py` as Python serverless functions with bundled CSV master data and PDF rate contracts.
* Route `/api/*` to the FastAPI backend and all other paths to the React SPA with full client-side routing support.

### Option B: Deploy via Vercel CLI

```bash
npm install -g vercel
vercel login
vercel --prod
```

---

## 💻 Local Development

### 1. Backend (FastAPI)
```bash
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
```
Verify the API: [http://127.0.0.1:8000/api](http://127.0.0.1:8000/api) (returns health check and total PR lines).

### 2. Frontend (React + Vite)
In a separate terminal:
```bash
npm install
npm run dev
```
Open [http://localhost:5173](http://localhost:5173). Requests to `/api/*` are automatically proxied to the backend on port 8000.

### 3. Docker (Alternative)
```bash
docker compose up --build
```
Access the application at [http://localhost:8000](http://localhost:8000).

---

## 📊 Blind Test & Submission Files

The `submission/` folder contains generated CSV exports matching the required schemas:
* `submission/sample_run_decisions.csv`: Evaluated 186 lines from `sample_pr_batch.csv` with clubbed `group_id`, consolidated `po_id`, routes, rates, and policy reasons.
* `submission/sample_preferred_suppliers.csv`: Top 3 preferred suppliers per item as of `2026-11-01`.

During the 2-hour blind test:
* **Run 1:** Export decisions from the web UI or `/api/export-decisions`.
* **Run 2:** Upload the amendment in the *Policy Amendment Studio*, approve it, and re-export decisions.

---

## 🏛 Architecture & Design Documents

* [DECISIONS.md](DECISIONS.md): Engineering judgment log covering edge cases (zero-quantity PRs, out-of-scope plants, blocked suppliers holding active rate contracts, rapid duplicate PRs, and the preferred supplier scoring formula).
* [APPROACH.md](APPROACH.md): System architecture, Policy-as-Data design, and AI tooling disclosure.
