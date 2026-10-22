"""People's records in the local app (Phase 5c, ADR 0013): access, adding, replacing, deleting."""

import datetime as dt
import json
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medgraph.api import people as ppl
from medgraph.api.access import COOKIE, form_token, load_secret
from medgraph.api.app import HEADERS, create_app
from medgraph.api.pages import UPLOADED_BANNER
from medgraph.graph import view
from medgraph.settings import Settings

DATA = re.compile(r'<script type="application/json" id="medgraph-data">(.*?)</script>', re.DOTALL)
ORIGIN = {"origin": "http://127.0.0.1:8000"}


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEDGRAPH_APP_TOKEN_PATH", str(tmp_path / "home" / "app-token"))
    monkeypatch.chdir(tmp_path)  # no .env from the repository
    return Settings()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)
    client.cookies.set(COOKIE, app.state.secret)
    return client


def token(client: TestClient) -> str:
    return form_token(client.app.state.secret)  # type: ignore[attr-defined]


def bundle_bytes(fhir_fixture_dir: Path, name: str = "patient-a.json") -> bytes:
    return (fhir_fixture_dir / name).read_bytes()


def upload(
    client: TestClient, data: bytes, label: str = "Me", lang: str = "en", **extra: Any
) -> Any:
    return client.post(
        f"/people/upload?lang={lang}",
        data={"label": label, "form_token": token(client), **extra},
        files={"record": ("record.json", data, "application/json")},
        headers=ORIGIN,
    )


def only_person(settings: Settings) -> ppl.Person:
    (who,) = ppl.people(settings)
    return who


def page_model(html: str) -> dict[str, Any]:
    (data,) = DATA.findall(html)
    model: dict[str, Any] = json.loads(data)
    return model


# --- access -------------------------------------------------------------------------------------


def test_without_the_cookie_every_page_is_locked(settings: Settings) -> None:
    app = create_app(settings)
    anyone = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)
    for path in ("/", "/people/add", "/cohorts/x/", "/people/abcdefabcdef.html"):
        response = anyone.get(path)
        assert response.status_code == 401
        assert "This app is locked" in response.text
        for name, value in HEADERS.items():
            assert response.headers[name] == value
    assert "Questa app è bloccata" in anyone.get("/?lang=it").text
    assert anyone.get("/?token=wrong").status_code == 401


def test_the_link_sets_the_cookie_and_drops_the_key_from_the_address(settings: Settings) -> None:
    app = create_app(settings)
    browser = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)
    response = browser.get(f"/?lang=it&token={app.state.secret}")
    assert response.status_code == 303
    assert response.headers["location"] == "/?lang=it"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert app.state.secret not in response.headers["location"]
    assert browser.get("/").status_code == 200  # the client kept the cookie


def test_the_secret_is_private_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "home" / "app-token"
    secret = load_secret(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_secret(path) == secret
    path.chmod(0o644)
    assert load_secret(path) == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("short")
    assert load_secret(path) not in ("short", secret)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "http://evil.example"},
        {"origin": "null"},
        {"origin": "http://localhost:8000"},
    ],
)
def test_a_form_from_anywhere_else_is_refused(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path, headers: dict[str, str]
) -> None:
    response = client.post(
        "/people/upload",
        data={"label": "Me", "form_token": token(client)},
        files={"record": ("r.json", bundle_bytes(fhir_fixture_dir), "application/json")},
        headers=headers,  # the host is 127.0.0.1:8000, so localhost is another origin
    )
    assert response.status_code == 403
    assert ppl.people(settings) == []


def test_a_form_without_its_token_is_refused(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    response = client.post(
        "/people/upload",
        data={"label": "Me", "form_token": "x"},
        files={"record": ("r.json", bundle_bytes(fhir_fixture_dir), "application/json")},
        headers=ORIGIN,
    )
    assert response.status_code == 403
    assert ppl.people(settings) == []


def test_forms_may_post_only_to_the_app() -> None:
    assert "form-action 'self'" in HEADERS["content-security-policy"]
    assert HEADERS["referrer-policy"] == "same-origin"  # "no-referrer" sends Origin: null


# --- adding -------------------------------------------------------------------------------------


def test_an_uploaded_bundle_is_stored_in_its_own_folder_and_served(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    data = bundle_bytes(fhir_fixture_dir)
    response = upload(client, data, label="Me")
    assert response.status_code == 200
    assert "Me added" in response.text
    who = only_person(settings)
    assert ppl.PERSON_ID.fullmatch(who.id)
    assert who.source == "upload"
    assert who.patient_id == "pat-a"
    assert sorted(p.name for p in who.folder.iterdir()) == [
        "graph.sqlite",
        "person.json",
        "record.json",
    ]
    assert (who.folder / "record.json").read_bytes() == data  # byte for byte
    home = client.get("/").text
    assert f'href="people/{who.id}.html#lang=en"' in home
    assert "pat-a" not in home  # the record's own ID stays in its folder
    page = client.get(f"/people/{who.id}.html")
    assert page.status_code == 200
    model = page_model(page.text)
    assert model["person"]["label"] == "Me"
    assert model["banner"] == UPLOADED_BANNER
    assert model["rules"]["as_of"] == dt.datetime.now(dt.UTC).astimezone().date().isoformat()
    assert client.get(f"/api/people/{who.id}/view.json").json()["person"]["label"] == "Me"


def test_an_upload_never_touches_the_temporary_folder(
    client: TestClient, fhir_fixture_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = tmp_path / "tmp"
    spool.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spool))
    big = json.loads(bundle_bytes(fhir_fixture_dir))
    big["padding"] = "x" * (3 * 2**20)  # larger than the 1 MB frameworks keep in memory
    assert upload(client, json.dumps(big).encode()).status_code == 200
    assert list(spool.iterdir()) == []


def test_the_same_file_again_changes_nothing(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    data = bundle_bytes(fhir_fixture_dir)
    upload(client, data)
    response = upload(client, data, label="Again")
    assert "Already here" in response.text
    assert only_person(settings).label == "Me"


def test_a_new_record_for_the_same_patient_asks_before_replacing(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    upload(client, bundle_bytes(fhir_fixture_dir))
    before = only_person(settings)
    newer = json.loads(bundle_bytes(fhir_fixture_dir))
    newer["entry"] = newer["entry"][:5]
    data = json.dumps(newer).encode()

    asked = upload(client, data, label="Other")
    assert "A record for this patient is already here" in asked.text
    (key,) = re.findall(r'name="key" value="([0-9a-f]{32})"', asked.text)
    keep = {"form_token": token(client), "key": key, "choice": "keep"}
    kept = client.post("/people/upload/choice", data=keep, headers=ORIGIN)
    assert "Nothing changed" in kept.text
    assert only_person(settings) == before
    assert list((settings.people_dir / ".pending").iterdir()) == []
    # The upload was discarded: the same key no longer works.
    again = client.post("/people/upload/choice", data=keep | {"choice": "replace"}, headers=ORIGIN)
    assert "no longer waiting" in again.text

    asked = upload(client, data, label="Other")
    (key,) = re.findall(r'name="key" value="([0-9a-f]{32})"', asked.text)
    replace = {"form_token": token(client), "key": key, "choice": "replace"}
    replaced = client.post("/people/upload/choice", data=replace, headers=ORIGIN)
    assert "Me: record replaced" in replaced.text
    after = only_person(settings)
    assert (after.id, after.label) == (before.id, "Me")
    assert after.record_sha256 != before.record_sha256
    assert (after.folder / "record.json").read_bytes() == data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not json", "The file is not JSON"),
        (b"[1, 2]", "not a FHIR Bundle"),
        (b'{"resourceType": "Patient", "id": "x"}', "not a FHIR Bundle"),
        (b"", "Choose a file to upload"),
    ],
)
def test_a_file_that_is_not_one_record_is_refused_and_says_why(
    client: TestClient, settings: Settings, data: bytes, message: str
) -> None:
    response = upload(client, data)
    assert response.status_code == 400
    assert message in response.text
    assert ppl.people(settings) == []


def test_a_bundle_with_two_patients_is_refused(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    a = json.loads(bundle_bytes(fhir_fixture_dir))
    b = json.loads(bundle_bytes(fhir_fixture_dir, "patient-b.json"))
    a["entry"] += b["entry"]
    response = upload(client, json.dumps(a).encode())
    assert response.status_code == 400
    assert "expected one Patient resource, found 2" in response.text


def test_a_file_over_the_limit_is_refused(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ppl, "MAX_RECORD_BYTES", 1000)
    response = upload(client, bundle_bytes(fhir_fixture_dir))
    assert response.status_code == 400
    assert "The file is larger than" in response.text
    assert ppl.people(settings) == []


def test_errors_are_shown_in_the_page_language(client: TestClient) -> None:
    response = upload(client, b"not json", lang="it")
    assert '<html lang="it">' in response.text
    assert "Il file non è JSON" in response.text


def test_labels_are_required_short_and_escaped(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    assert upload(client, bundle_bytes(fhir_fixture_dir), label="  ").status_code == 400
    assert upload(client, bundle_bytes(fhir_fixture_dir), label="x" * 61).status_code == 400
    label = '<script>alert("x")</script>'
    assert upload(client, bundle_bytes(fhir_fixture_dir), label=label).status_code == 200
    who = only_person(settings)
    for path in ("/", f"/people/{who.id}/delete"):
        assert "<script>alert" not in client.get(path).text
    page = client.get(f"/people/{who.id}.html").text
    assert page_model(page)["person"]["label"] == label  # data, shown with textContent
    assert "<script>alert" not in page


def test_a_person_entered_by_hand_gets_a_minimal_record(
    client: TestClient, settings: Settings
) -> None:
    form = {"label": "Mum", "birth_date": "1950-03-04", "gender": "female"}
    response = client.post(
        "/people/enter", data=form | {"form_token": token(client)}, headers=ORIGIN
    )
    assert response.status_code == 200
    who = only_person(settings)
    assert who.source == "entered"
    record = json.loads((who.folder / "record.json").read_text())
    (entry,) = record["entry"]
    patient = entry["resource"]
    assert patient["meta"]["source"] == ppl.ENTERED
    assert (patient["birthDate"], patient["gender"]) == ("1950-03-04", "female")
    assert "entered on" in client.get("/").text


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"birth_date": "2999-01-01", "gender": "female"}, "between 1900-01-01 and today"),
        ({"birth_date": "1899-12-31", "gender": "female"}, "between 1900-01-01 and today"),
        ({"birth_date": "4/3/1950", "gender": "female"}, "as a date"),
        ({"birth_date": "1950-03-04", "gender": "woman"}, "listed options"),
    ],
)
def test_a_hand_entered_person_is_checked(
    client: TestClient, settings: Settings, form: dict[str, str], message: str
) -> None:
    data = form | {"label": "Mum", "form_token": token(client)}
    response = client.post("/people/enter", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert message in response.text
    assert ppl.people(settings) == []


def test_checks_held_back_by_unread_records_show_on_the_list(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    record = json.loads(bundle_bytes(fhir_fixture_dir))
    record["entry"].append(
        {
            "fullUrl": "urn:uuid:icd9",
            "resource": {
                "resourceType": "Condition",
                "id": "icd9",
                "subject": {"reference": "urn:uuid:pat-a"},
                "code": {
                    "coding": [{"system": "http://hl7.org/fhir/sid/icd-9-cm", "code": "585.3"}]
                },
            },
        }
    )
    upload(client, json.dumps(record).encode())
    who = only_person(settings)
    assert "checks not done" in client.get("/").text
    model = page_model(client.get(f"/people/{who.id}.html").text)
    assert model["personal"]["unchecked"]["records"][0]["node"] == "condition:icd9"


# --- deleting -----------------------------------------------------------------------------------


def test_deleting_a_person_removes_everything_of_theirs(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    upload(client, bundle_bytes(fhir_fixture_dir), label="Me")
    upload(client, bundle_bytes(fhir_fixture_dir, "patient-b.json"), label="Other")
    who = next(p for p in ppl.people(settings) if p.label == "Me")
    confirm = client.get(f"/people/{who.id}/delete")
    assert str(who.folder) in confirm.text
    assert "does not wipe the drive" in confirm.text
    response = client.post(
        f"/people/{who.id}/delete", data={"form_token": token(client)}, headers=ORIGIN
    )
    assert "The record of Me was deleted." in response.text
    assert not who.folder.exists()
    assert client.get(f"/people/{who.id}.html").status_code == 404
    assert [p.label for p in ppl.people(settings)] == ["Other"]
    # Nothing in the data folder names the deleted person or holds their record.
    for path in settings.data_dir.rglob("*"):
        assert who.id not in str(path)
        if path.is_file():
            assert b"pat-a" not in path.read_bytes(), path


def test_deleting_needs_the_form(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    upload(client, bundle_bytes(fhir_fixture_dir))
    who = only_person(settings)
    response = client.post(f"/people/{who.id}/delete", data={"confirm": "1"}, headers=ORIGIN)
    assert response.status_code == 403  # a form, but without the form token
    assert client.post(f"/people/{who.id}/delete", headers=ORIGIN).status_code == 400
    assert who.folder.exists()


@pytest.mark.parametrize("person_id", ["..", "abc", "ABCDEFABCDEF", "abcdefabcdef0"])
def test_only_well_formed_ids_name_a_person(client: TestClient, person_id: str) -> None:
    assert client.get(f"/people/{person_id}.html").status_code == 404


# --- interrupted writes and waiting uploads ------------------------------------------------------


def test_an_interrupted_replace_is_undone_and_a_half_added_record_dropped(
    client: TestClient, settings: Settings, fhir_fixture_dir: Path
) -> None:
    upload(client, bundle_bytes(fhir_fixture_dir))
    who = only_person(settings)
    root = settings.people_dir
    who.folder.rename(root / f".old-{who.id}")  # stopped between the two renames of replace
    (root / f".new-{who.id}").mkdir()
    (root / ".new-0123456789ab").mkdir()  # stopped while adding someone
    ppl.recover(settings)
    assert ppl.people(settings) == [who]
    assert sorted(p.name for p in root.iterdir()) == [who.id]


def test_a_waiting_upload_is_dropped_after_an_hour(settings: Settings) -> None:
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    key = ppl.stash(settings, "Me", b"{}", start)
    ppl.clean_pending(settings, start + dt.timedelta(minutes=59))
    assert (settings.people_dir / ".pending" / key).exists()
    ppl.clean_pending(settings, start + dt.timedelta(minutes=61))
    assert ppl.take(settings, key) is None


def test_every_app_page_key_exists_in_both_languages() -> None:
    pages = (Path(ppl.__file__).parent / "pages.py").read_text(encoding="utf-8")
    app = (Path(ppl.__file__).parent / "app.py").read_text(encoding="utf-8")
    used = set(re.findall(r'"(app\.[\w.]+)"', pages + app))
    used |= {
        f"app.err.{key}"
        for key in re.findall(r'AddError\("([\w.]+)"', app + Path(ppl.__file__).read_text())
    }
    keys = set(view.strings()["en"])
    assert used - keys == set()
    assert view.strings()["en"]["banner.uploaded"] == UPLOADED_BANNER
