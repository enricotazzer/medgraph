# 0011: The local app, read-only: the pages served on this machine (Phase 5a)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-21

## Context

ADR 0010 settled the shape of the app: the existing pages, served by FastAPI, for one user on this machine. Phase 5a is its first step. It serves what `make view` and `make concept-map` write as files, built from the stores on request. It does not ingest data and does not call a model; uploads and model jobs come in 5c.

## Decision

### What it serves (`medgraph/api/app.py`, `make app`)

| Path | What |
| --- | --- |
| `/` | the cohorts with a graph store and a Synthea manifest |
| `/cohorts/<cohort>/` | the patients. The first ones have a condition in the monitoring table or a filed lab report, as in `make view --list`. Flag counts come from the results `make flags` stored |
| `/cohorts/<cohort>/<patient>.html` | the patient's page |
| `/cohorts/<cohort>/concept-map.html` | the concept map |
| `/api/cohorts/<cohort>/patients/<patient>/view.json` | the data the patient's page embeds (ADR 0005) |

The paths follow the `views/` folder, so the concept map links to patient pages exactly as it does on disk.

- **One code path for the pages.** The pages are built from the stores on each request, by the same functions as the scripts. Those functions moved from the scripts into `src/` with no change in behaviour:
  - `medgraph/api/pages.py`: `patient_model`, `concept_model` and `patient_rows`;
  - `medgraph/agent/cache.py`: the explanation records;
  - `medgraph/cohort.py`: the manifest, the evaluation date, the banner, and the knowledge graph used only while current.

  `view.py` now separates the page's data (`page_model`) from its HTML (`render_page`).
- **Read-only.** The app opens stores with SQLite's `mode=ro` (`GraphStore(path, read_only=True)`): a missing file is an error, not a new empty store. `make view` and `make concept-map` now open stores the same way. No model is called: explanations come from the cache that `make explain` fills.
- **Synthea cohorts only.** A store is served only if its cohort has a Synthea manifest, so every page truthfully says its data are synthetic. Uploaded records, and whether this restriction changes, are for 5c.
- **The concept map** takes about 70 s for dev-1000, so it is built in a background thread. Until it is ready, a short page reloads itself every 5 s.
  - The map is kept in memory and rebuilt when the store's digest changes, or the knowledge graph's digest, or the reason it is not used.
  - A failed build shows a page saying so; the details go to the server's log.
  - The patient list (1.8 s for dev-1000) is kept per store digest.
- **`config_dir`** (setting `MEDGRAPH_CONFIG_DIR`) defaults to the repository's `configs/`. It is where the app finds the pins it checks the knowledge graph against.

### Security, for a server only this machine should reach

- **Network:** the server binds to 127.0.0.1 only.
- **DNS rebinding:** any `Host` other than `127.0.0.1` or `localhost` gets a 400, via Starlette's `TrustedHostMiddleware`. Without this check, a website whose name is made to resolve to 127.0.0.1 could read the pages from the user's own browser.
- **Headers:** every response, errors included, carries:
  - the pages' CSP (`default-src 'none'`, so no network requests) plus `frame-ancestors 'none'`;
  - `Referrer-Policy: no-referrer`;
  - `X-Content-Type-Options: nosniff`;
  - `Cache-Control: no-store`.
- **No forms yet:** `form-action 'none'` stays in place.
- **No access log**, since request paths name patients, and no `Server` header.
- **Paths:** cohorts and patients are matched only against names found in the data folder. Anything else is a 404, including `..` and encoded slashes.

### Telemetry and other outside requests

- **FastAPI 0.156 ships OpenTelemetry instrumentation** (`fastapi/telemetry/`) and requires `opentelemetry-api`.
  - By default it records traces, metrics, and logs that include exception messages. It sends them to whatever OpenTelemetry provider is configured.
  - It exports nothing unless three conditions all hold: `FASTAPI_OTEL_AUTO_CONFIGURE` is set, an OTLP endpoint is configured, and the OpenTelemetry SDK is installed. None of the three holds here.
  - The non-negotiables say to switch telemetry off anyway, so the app turns every part of it off (`NO_TELEMETRY`). A test checks this holds even when the environment asks for automatic configuration.
- **Documentation pages off:** FastAPI's `/docs`, `/redoc` and `/openapi.json` are disabled. Swagger UI loads its scripts from a CDN.

### Dependencies

- **Added:** `fastapi` 0.156.8 and `uvicorn` 0.54.0.
- **Brought along:** Starlette 1.9.0, `opentelemetry-api` 1.45.1, `click` and `annotated-doc`.
- **One test warning filtered:** Starlette's test client says using `httpx` with it is deprecated and recommends a package called `httpx2`. medgraph keeps `httpx`, which its Ollama client uses, and the lock pins Starlette.

## Checks

- `make check`: 763 passed, 46 skipped (opt-in).
- **App tests** (`tests/api/`, 23):
  - a served page is byte-identical to the file `make view` writes;
  - `view.json` equals the page's embedded data;
  - foreign `Host` headers are refused, and the security headers are on every response, including the 400 and the 404;
  - unknown and `..` paths are 404;
  - the concept map is built off the request and then served from memory, and a failed build says so;
  - serving changes no file in the data folder;
  - the telemetry is off.
- **On dev-1000** (`make app`, 2026-10-21):

  | Request | Size | Time |
  | --- | --- | --- |
  | patient list, first and cached | 244 kB | 1.85 s, then 4 ms |
  | patient pages: 23be53d1, cf418f8d, and the largest (7c9b01e3) | 1.4 MB, 0.8 MB, 16.9 MB | 95 ms, 35 ms, 1.8 s |
  | `view.json` (cf418f8d) | 303 kB | 26 ms |
  | concept map, once built (about a minute) | 3.7 MB | 22 ms |

  - **Same pages:** three served pages (23be53d1, cf418f8d, 2474b3ff) are byte-identical to `make view`'s.
  - **Page check:** the Phase 4b page check in Node (jsdom) passes on them, covering variant switching and citations.
  - **Host check:** a foreign `Host` got a 400.
  - **Store untouched:** the graph store's modification time did not change.

## Limitations

- **Any account on this machine can open the pages.** 127.0.0.1 is reachable by every local user and process, not just the person who started the server. For synthetic data that is acceptable. Before the app holds a real person's records (5c), this needs a decision, for example a secret token in the address, or a socket only the user can open.
- **The concept map lives in the server's memory,** and the first view after a start or a store change waits about a minute.
- **The patient list's flag counts** come from `make flags`, while each page evaluates the rules again as of the cohort's date. The two agree while the stored results are current.

## Consequences

- 5b (clinician and personal views, English and Italian) changes these pages, and the scripts and the app show the change together.
- Code in `src/` no longer depends on scripts for cohort files, explanation records or page assembly.
