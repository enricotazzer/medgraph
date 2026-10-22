"""The local app (Phase 5a, ADR 0011; people's records, Phase 5c, ADR 0013), on this machine.

``python -m medgraph.api`` (``make app``) serves, on 127.0.0.1 only:

- ``/``: the people added in the app, then the Synthea cohorts with a graph store;
- ``/people/add``: add a person, by uploading a FHIR bundle or entering a birth date and sex
  (posted to ``/people/upload`` and ``/people/enter``);
- ``/people/<id>.html``: that person's page; ``/people/<id>/delete`` deletes them;
- ``/cohorts/<cohort>/``: a cohort's patients, those worth a look first;
- ``/cohorts/<cohort>/<patient>.html``: the patient's page, as ``make view`` writes it;
- ``/cohorts/<cohort>/concept-map.html``: the concept map, as ``make concept-map`` writes it;
- ``/api/cohorts/<cohort>/patients/<patient>/view.json`` and ``/api/people/<id>/view.json``:
  the data a page embeds.

Cohort pages are built from their stores, opened read-only, and only cohorts with a Synthea
manifest are served, so their pages say the data are synthetic. People's records are kept in
``$MEDGRAPH_DATA_DIR/people/`` (:mod:`medgraph.api.people`); their checks run as of today. No
model is called: explanations come from the cache that ``make explain`` fills, for cohorts only.
The app's own pages take ``?lang=it`` for Italian.

A browser on this machine, used by the person who started the app, is the only client:

- **Host:** the server refuses any ``Host`` but 127.0.0.1 or localhost, so a website whose name
  is made to resolve to 127.0.0.1 cannot read a page (DNS rebinding).
- **Access:** every request needs the app's cookie, and a form also the app's origin and a form
  token (:mod:`medgraph.api.access`): other accounts on this machine can reach 127.0.0.1 too.
- **Headers:** every response carries the pages' Content Security Policy, which forbids every
  network request and lets forms post only to the app, and may not be framed or cached. The
  referrer is sent to the app only (``same-origin``): with none, browsers send ``Origin: null``
  on a form post, and the origin check could not work.
- **No telemetry:** FastAPI's OpenTelemetry instrumentation and its documentation pages (which
  load scripts from a CDN) are switched off.
"""

import datetime as dt
import json
import logging
import threading
from collections.abc import Callable
from html import escape
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.telemetry import TelemetryConfig
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from medgraph.api import people as ppl
from medgraph.api.access import Access, form_token, load_secret, same
from medgraph.api.forms import Form, FormError, parse_form, read_body
from medgraph.api.pages import (
    Lang,
    PatientRow,
    PersonRow,
    add_page,
    added_page,
    building_page,
    concept_model,
    delete_page,
    error_page,
    failed_page,
    home_page,
    locked_page,
    message_page,
    page_lang,
    patient_model,
    patient_rows,
    patients_page,
    person_model,
    replace_page,
    tr,
)
from medgraph.cohort import SYNTHETIC_BANNER, current_knowledge, evaluation_date, load_meta
from medgraph.graph.store import GraphStore, cohort_digest
from medgraph.graph.view import CSP, render_concept_map, render_page
from medgraph.ingest.files import iter_data_files
from medgraph.rules.flags import evaluate
from medgraph.settings import Settings

logger = logging.getLogger(__name__)

HOSTS = ("127.0.0.1", "localhost")
HEADERS = {
    "content-security-policy": (
        CSP.replace("form-action 'none'", "form-action 'self'") + "; frame-ancestors 'none'"
    ),
    "referrer-policy": "same-origin",
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
SMALL_FORM = 16 * 1024  # bytes: every form but an upload
UPLOAD_FORM = ppl.MAX_RECORD_BYTES + 1024 * 1024  # the file, the label and the form's framing


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

    def get(
        self, cohort: str, key: tuple[str, ...], build: Callable[[], str]
    ) -> str | Literal[False] | None:
        """The page if built for ``key``, False if that build failed (until ``key`` changes);
        otherwise None, and a build is started if none is running."""
        with self._lock:
            built = self._built.get(cohort)
            if built is not None and built[0] == key:
                return built[1] if built[1] is not None else False
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


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def today() -> dt.date:
    """Today on this machine's clock: the date people's checks run as of."""
    return now().astimezone().date()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    secret = load_secret(settings.app_token_path)
    forms = form_token(secret)
    ppl.recover(settings)
    app = FastAPI(
        title="medgraph",
        telemetry=NO_TELEMETRY,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Starlette runs the middleware added last first: headers, then the host, then access.
    app.add_middleware(Access, secret=secret, locked=lambda lang: locked_page(page_lang(lang)))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(HOSTS), www_redirect=False)
    app.add_middleware(SecurityHeaders)
    app.state.secret = secret
    concept_maps = ConceptMaps()
    app.state.concept_maps = concept_maps
    rows_cache: dict[tuple[str, str], list[PatientRow]] = {}
    person_rows: dict[tuple[str, str, dt.date], PersonRow] = {}
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

    def person_of(person_id: str) -> ppl.Person:
        who = ppl.person(settings, person_id)
        if who is None:
            raise HTTPException(404, "no such person")
        return who

    def person_page_model(who: ppl.Person) -> dict[str, Any]:
        return person_model(
            who.load_graph(),
            today(),
            label=who.label,
            source=who.source,
            added=who.added.astimezone().date(),
        )

    def row_of(who: ppl.Person) -> PersonRow:
        key = (who.id, who.record_sha256, today())
        if key not in person_rows:
            rules = evaluate(who.load_graph(), key[2])
            person_rows[key] = PersonRow(
                id=who.id,
                label=who.label,
                source=who.source,
                added=who.added.astimezone().date(),
                flags=len(rules.flags),
                unchecked=len(rules.unchecked),
            )
        return person_rows[key]

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

    async def form_of(request: Request, limit: int) -> Form:
        """The posted form, if it carries the form token; otherwise 403."""
        try:
            body = await read_body(request.stream(), request.headers.get("content-length"), limit)
            form = parse_form(request.headers.get("content-type", ""), body)
        except FormError as exc:
            status = 413 if str(exc) == "too large" else 400
            raise HTTPException(status, f"form not read: {exc}") from exc
        if not same(form.get("form_token"), forms):
            raise HTTPException(403, "this form did not come from the app")
        return form

    def added(lang: Lang, who: ppl.Person, checked: ppl.Checked, replaced: bool) -> HTMLResponse:
        page = added_page(
            lang,
            who.id,
            who.label,
            checked.graph.number_of_nodes(),
            dict(checked.issues()),
            replaced,
        )
        return HTMLResponse(page)

    def not_added(lang: Lang, error: ppl.AddError) -> HTMLResponse:
        message = _text(lang, f"app.err.{error.key}", **error.values)
        return HTMLResponse(error_page(lang, message, back="add"), status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index(lang: str | None = None) -> HTMLResponse:
        cohorts = []
        for cohort in served_cohorts(settings):
            with store_of(cohort) as store:
                cohorts.append((cohort, len(store.digests())))
        rows = [row_of(who) for who in ppl.people(settings)]
        return HTMLResponse(home_page(page_lang(lang), rows, cohorts))

    @app.get("/people/add", response_class=HTMLResponse)
    def add_form(lang: str | None = None) -> HTMLResponse:
        page = add_page(
            page_lang(lang),
            forms,
            str(settings.people_dir),
            ppl.MAX_RECORD_BYTES // 2**20,
            today(),
        )
        return HTMLResponse(page)

    @app.post("/people/upload", response_class=HTMLResponse)
    async def upload(request: Request, lang: str | None = None) -> HTMLResponse:
        language = page_lang(lang)
        form = await form_of(request, UPLOAD_FORM)
        try:
            label = ppl.clean_label(form.get("label"))
            _, data = form.files.get("record", ("", b""))
            if not data:
                raise ppl.AddError("nofile")
            checked = await run_in_threadpool(ppl.check_record, data)
        except ppl.AddError as exc:
            return not_added(language, exc)
        here = ppl.same_patient(settings, checked)
        if here is not None:
            if here.record_sha256 == checked.sha256:
                text = message_page(
                    language,
                    "app.same.title",
                    _text(language, "app.same.text", label=here.label),
                    up="../",
                )
                return HTMLResponse(text)
            key = ppl.stash(settings, label, data, now())
            page = replace_page(language, forms, key, here.label, here.added.astimezone().date())
            return HTMLResponse(page)
        who = await run_in_threadpool(ppl.add, settings, label, checked, "upload", now())
        return added(language, who, checked, replaced=False)

    @app.post("/people/upload/choice", response_class=HTMLResponse)
    async def upload_choice(request: Request, lang: str | None = None) -> HTMLResponse:
        language = page_lang(lang)
        form = await form_of(request, SMALL_FORM)
        stashed = ppl.take(settings, form.get("key"))
        if stashed is None:
            expired = _text(language, "app.expired.text")
            return HTMLResponse(message_page(language, "app.expired.title", expired, up="../"))
        _, data = stashed
        try:
            checked = await run_in_threadpool(ppl.check_record, data)
        except ppl.AddError as exc:
            return not_added(language, exc)
        here = ppl.same_patient(settings, checked)
        if here is None:  # deleted meanwhile
            return not_added(language, ppl.AddError("gone"))
        if form.get("choice") != "replace":
            kept = _text(language, "app.kept.text", label=here.label)
            return HTMLResponse(message_page(language, "app.kept.title", kept, up="../"))
        who = await run_in_threadpool(ppl.replace, settings, here, checked, now())
        return added(language, who, checked, replaced=True)

    @app.post("/people/enter", response_class=HTMLResponse)
    async def enter(request: Request, lang: str | None = None) -> HTMLResponse:
        language = page_lang(lang)
        form = await form_of(request, SMALL_FORM)
        try:
            label = ppl.clean_label(form.get("label"))
            try:
                born = dt.date.fromisoformat(form.get("birth_date"))
            except ValueError as exc:
                raise ppl.AddError("birth.format") from exc
            data = ppl.entered_record(born, form.get("gender"), today(), now())
            checked = await run_in_threadpool(ppl.check_record, data)
        except ppl.AddError as exc:
            return not_added(language, exc)
        who = await run_in_threadpool(ppl.add, settings, label, checked, "entered", now())
        return added(language, who, checked, replaced=False)

    @app.get("/people/{person_id}.html", response_class=HTMLResponse)
    def person_page(person_id: str) -> HTMLResponse:
        return HTMLResponse(render_page(person_page_model(person_of(person_id))))

    @app.get("/api/people/{person_id}/view.json")
    def person_json(person_id: str) -> Response:
        model = person_page_model(person_of(person_id))
        body = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
        return Response(body, media_type="application/json")

    @app.get("/people/{person_id}/delete", response_class=HTMLResponse)
    def delete_form(person_id: str, lang: str | None = None) -> HTMLResponse:
        who = person_of(person_id)
        return HTMLResponse(delete_page(page_lang(lang), forms, who.label, str(who.folder)))

    @app.post("/people/{person_id}/delete", response_class=HTMLResponse)
    async def delete(request: Request, person_id: str, lang: str | None = None) -> HTMLResponse:
        language = page_lang(lang)
        await form_of(request, SMALL_FORM)
        who = person_of(person_id)
        await run_in_threadpool(ppl.delete, settings, who)
        text = _text(language, "app.deleted.text", label=who.label)
        return HTMLResponse(message_page(language, "app.deleted.title", text, up="../../"))

    @app.get("/cohorts/{cohort}/", response_class=HTMLResponse)
    def patients(cohort: str, lang: str | None = None) -> HTMLResponse:
        with store_of(cohort) as store:
            key = (cohort, store_key(store))
            if key not in rows_cache:
                rows_cache[key] = patient_rows(store)
        return HTMLResponse(patients_page(cohort, rows_cache[key], page_lang(lang)))

    @app.get("/cohorts/{cohort}/concept-map.html", response_class=HTMLResponse)
    def concept_map_page(cohort: str, lang: str | None = None) -> HTMLResponse:
        with store_of(cohort) as store:
            store_digest = store_key(store)
            patients = len(store.digests())
        # Rebuilt when the store changes, or the knowledge graph (or whether it is current).
        knowledge, why = current_knowledge(settings, cohort)
        key = (store_digest, knowledge.digest() if knowledge is not None else str(why))
        html = concept_maps.get(cohort, key, lambda: build_concept_map(cohort))
        if html is False:
            return HTMLResponse(failed_page(cohort, page_lang(lang)))
        if html is None:
            return HTMLResponse(building_page(cohort, patients, page_lang(lang)))
        return HTMLResponse(html)

    @app.get("/cohorts/{cohort}/{patient}.html", response_class=HTMLResponse)
    def patient_page(cohort: str, patient: str) -> HTMLResponse:
        return HTMLResponse(render_page(model_of(cohort, patient)))

    @app.get("/api/cohorts/{cohort}/patients/{patient}/view.json")
    def view_json(cohort: str, patient: str) -> Response:
        body = json.dumps(model_of(cohort, patient), ensure_ascii=False, separators=(",", ":"))
        return Response(body, media_type="application/json")

    return app


def _text(lang: Lang, key: str, **values: str) -> str:
    """A message's text, with plain-text ``values`` escaped."""
    return tr(lang, key, **{k: escape(v) for k, v in values.items()})
