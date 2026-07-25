# Structure

Backend and frontend are fully separate.

```
nexus/
├── backend/            # all Python
│   └── nexus/          # importable package (no notebooks in core)
│       ├── config.py
│       ├── schemas.py
│       ├── ingest.py
│       ├── ground_truth.py   # parsed Patterns; HELD OUT from agent/detectors
│       └── ...               # tools, engine added in later phases
├── frontend/           # React + Vite + TS (added later)
├── data/raw/           # AMLworld CSVs + *_Patterns.txt + Accounts file
└── .kiro/
```

Rules:
- Python package lives under `backend/nexus/`, never at repo root.
- `data/` stays at repo root, shared, git-ignored for raw files.
- Frontend never imports backend code; they talk over the FastAPI HTTP boundary only.
