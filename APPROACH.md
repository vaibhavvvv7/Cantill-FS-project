# APPROACH.md — Architecture & AI Tooling Disclosure

1. **Architecture:** FastAPI Python backend (`/api`) serving both REST endpoints and Vercel Serverless functions, coupled with a React + Vite + Tailwind CSS dark-mode glassmorphism workspace (`/src`).
2. **Policy-as-Data Design:** All thresholds, rule orderings, clubbing windows, PO consolidation windows, and plant/category overrides are stored as versioned JSON documents in `api/policy_store.py`, allowing natural-language or PDF policy amendments without code changes.
3. **AI Tool Usage:** AI assistance was used for scaffolding the React Tailwind components and regex extraction patterns for rate contract PDFs and natural-language policy amendments. All deterministic rule evaluation and supplier scoring formulas were verified against `Worked_Examples.pdf`.
