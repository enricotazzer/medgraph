"""The local app (Phase 5a, ADR 0011): the cohorts' pages, served on this machine, read-only.

``python -m medgraph.api`` (``make app``) serves, on 127.0.0.1 only:

- ``/``: the Synthea cohorts with a graph store (``make graphs``);
- ``/cohorts/<cohort>/``: its patients, those worth a look first;
- ``/cohorts/<cohort>/<patient>.html``: the patient's page, as ``make view`` writes it;
- ``/cohorts/<cohort>/concept-map.html``: the concept map, as ``make concept-map`` writes it;
- ``/api/cohorts/<cohort>/patients/<patient>/view.json``: the data the patient's page embeds.

The paths follow the views folder, so the pages link to each other as they do as files. Pages
are built from the stores on request; the stores are opened read-only, nothing is written and no
model is called: explanations come from the cache that ``make explain`` fills. Only cohorts with
a Synthea manifest are served, so every page says its data are synthetic.

A browser on this machine is the only client. The server refuses any ``Host`` but 127.0.0.1 or
localhost, so a website whose name is made to resolve to 127.0.0.1 cannot read a page (DNS
rebinding). Every response carries the pages' Content Security Policy, which forbids every
network request, and may not be framed, cached or sent a referrer. FastAPI's OpenTelemetry
instrumentation and its documentation pages (which load scripts from a CDN) are switched off.
"""

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.telemetry import TelemetryConfig
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from medgraph.api.pages import (
    PatientRow,
    building_page,
    cohorts_page,
    concept_model,
    failed_page,
    patient_model,
    patient_rows,
    patients_page,
)
from medgraph.cohort import SYNTHETIC_BANNER, current_knowledge, evaluation_date, load_meta
from medgraph.graph.store import GraphStore, cohort_digest
from medgraph.graph.view import CSP, render_concept_map, render_page
from medgraph.ingest.files import iter_data_files
from medgraph.settings import Settings

logger = logging.getLogger(__name__)

HOSTS = ("127.0.0.1", "localhost")
HEADERS = {
    "content-security-policy": f"{CSP}; frame-ancestors 'none'",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
}
# Every part of FastAPI's OpenTelemetry instrumentation, off (CLAUDE.md: no telemetry).
NO_TELEMETRY: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}


class SecurityHeaders:
    """Adds :data:`HEADERS` to every response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ConceptMaps:
    """Each cohort's concept map, built off the request (about a minute for dev-1000) and kept
    until the store or the knowledge graph it was built from changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._built: dict[str, tuple[tuple[str, ...], str | None]] = {}  # html, or None: failed
        self._building: dict[str, threading.Thread] = {}

    def get(self, cohort: str, key: tuple[str, ...], build: Callable[[], str]) -> str | None:
        """The page if built for ``key``; otherwise None, and a build is started if none is
        running. A failed build gives :func:`failed_page` until ``key`` changes."""
        with self._lock:
            built = self._built.get(cohort)
            if built is not None and built[0] == key:
                return built[1] if built[1] is not None else failed_page(cohort)
            if cohort not in self._building:
                thread = threading.Thread(
                    target=self._build, args=(cohort, key, build), daemon=True
                )
                self._building[cohort] = thread
                thread.start()
        return None

    def wait(self, cohort: str) -> None:
        """Wait for a running build (for tests)."""
        with self._lock:
            thread = self._building.get(cohort)
        if thread is not None:
            thread.join()

    def _build(self, cohort: str, key: tuple[str, ...], build: Callable[[], str]) -> None:
        html: str | None
        try:
            html = build()
        except Exception:
            logger.exception("concept map of %s failed", cohort)
            html = None
        with self._lock:
            self._built[cohort] = (key, html)
            del self._building[cohort]


def served_cohorts(settings: Settings) -> list[str]:
    """Cohorts with a graph store and a Synthea manifest, by name."""
    if not settings.graphs_dir.is_dir():
        return []
    return [
        path.stem
        for path in iter_data_files(settings.graphs_dir, "*.sqlite")
        if path.parent == settings.graphs_dir
        and (settings.synthea_dir / path.stem / "MANIFEST.json").is_file()
    ]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="medgraph",
        telemetry=NO_TELEMETRY,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(HOSTS), www_redirect=False)
    app.add_middleware(SecurityHeaders)  # added last, so it wraps the host check too
    concept_maps = ConceptMaps()
    app.state.concept_maps = concept_maps
    rows_cache: dict[tuple[str, str], list[PatientRow]] = {}
    reported: set[str] = set()

    def store_of(cohort: str) -> GraphStore:
        if cohort not in served_cohorts(settings):
            raise HTTPException(404, "no such cohort")
        return GraphStore(settings.graphs_dir / f"{cohort}.sqlite", read_only=True)

    def store_key(store: GraphStore) -> str:
        return store.meta().get("store_digest") or cohort_digest(store.digests())

    def report_once(why: str | None) -> None:
        if why is not None and why not in reported:
            reported.add(why)
            logger.warning("%s", why)

    def model_of(cohort: str, patient: str) -> dict[str, Any]:
        with store_of(cohort) as store:
            if patient not in store.digests():
                raise HTTPException(404, "no such patient")
            graph = store.load(patient)
        as_of = evaluation_date(load_meta(settings.synthea_dir / cohort), None)
        model, why = patient_model(settings, cohort, graph, as_of)
        report_once(why)
        return model

    def build_concept_map(cohort: str) -> str:
        with store_of(cohort) as store:
            patients = list(store.digests())
            model, why = concept_model(settings, cohort, (store.load(p) for p in patients))
            store_digest = store_key(store)
        report_once(why)
        return render_concept_map(
            model,
            cohort=cohort,
            store_digest=store_digest,
            banner=SYNTHETIC_BANNER,
            pages=set(patients),
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        cohorts = []
        for cohort in served_cohorts(settings):
            with store_of(cohort) as store:
                cohorts.append((cohort, len(store.digests())))
        return HTMLResponse(cohorts_page(cohorts))

    @app.get("/cohorts/{cohort}/", response_class=HTMLResponse)
    def patients(cohort: str) -> HTMLResponse:
        with store_of(cohort) as store:
            key = (cohort, store_key(store))
            if key not in rows_cache:
                rows_cache[key] = patient_rows(store)
        return HTMLResponse(patients_page(cohort, rows_cache[key]))

    @app.get("/cohorts/{cohort}/concept-map.html", response_class=HTMLResponse)
    def concept_map_page(cohort: str) -> HTMLResponse:
        with store_of(cohort) as store:
            store_digest = store_key(store)
            patients = len(store.digests())
        # Rebuilt when the store changes, or the knowledge graph (or whether it is current).
        knowledge, why = current_knowledge(settings, cohort)
        key = (store_digest, knowledge.digest() if knowledge is not None else str(why))
        html = concept_maps.get(cohort, key, lambda: build_concept_map(cohort))
        return HTMLResponse(html if html is not None else building_page(cohort, patients))

    @app.get("/cohorts/{cohort}/{patient}.html", response_class=HTMLResponse)
    def patient_page(cohort: str, patient: str) -> HTMLResponse:
        return HTMLResponse(render_page(model_of(cohort, patient)))

    @app.get("/api/cohorts/{cohort}/patients/{patient}/view.json")
    def view_json(cohort: str, patient: str) -> Response:
        body = json.dumps(model_of(cohort, patient), ensure_ascii=False, separators=(",", ":"))
        return Response(body, media_type="application/json")

    return app
