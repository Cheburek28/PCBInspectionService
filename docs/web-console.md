# Web console (`/ui`)

A small built-in web page for trying parameters on your own photos and browsing every inspection —
from the console and from client stations alike. It is meant for engineers tuning the service, not for
the production line.

## Enable

Set a password (the console answers 404 without one):

```bash
python -c "import secrets; print(secrets.token_urlsafe(18))"   # generate
echo "PCBIS_UI_PASSWORD=<generated>" >> .env                   # dev compose and compose.prod.yaml read .env
docker compose up -d
```

Open `http://localhost:8000/ui` (production: `https://<domain>/ui`). Login sets a signed cookie for
`PCBIS_UI_SESSION_HOURS` (12 h). Changing the password logs everybody out. There are no user accounts.

## Pages

| Page | What it does |
|---|---|
| **Сравнение** (`/ui/`) | Upload a reference and a photo, set parameters (empty = server default), choose the mask, run |
| **История** (`/ui/inspections`) | Every inspection, newest first; filter by source (console / API stations), status, product |
| **Проверка** (`/ui/inspections/{id}`) | Photo with numbered boxes; switch to reference, inspected-area mask, aligned photo or heatmap; click a box → reference vs. photo crop, accept / reject; quality metrics, timings and the parameters used; **re-run** with other parameters |

Each console run creates its own session (`client_meta.source = "web-ui"`, station `web-ui`), so it never
supersedes or mixes with inspections from production clients. A re-run uses the same reference and photo
(no re-upload) and links back to the original. Verdicts given in the console are stored like any other
feedback (operator `web-ui`) and appear in dataset exports.
