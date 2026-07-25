# Drop AMLworld files here

Place the IBM AMLworld files in this folder. For development you only need the HI-Small set:

- `HI-Small_Trans.csv`
- `HI-Small_Patterns.txt`
- `HI-Small_accounts.csv`

(Add `LI-Small_*` later for false-positive evaluation. Medium/Large are unused.)

Then run the Phase 1 profile:

```bash
cd backend
.venv/bin/python -m nexus.ingest --variant HI-Small
```

These files are git-ignored (too large for the repo).
