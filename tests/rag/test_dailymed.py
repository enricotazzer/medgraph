"""Choosing one DailyMed label per RxCUI, reading its identity, and the lock file."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from medgraph.rag.dailymed import (
    TIERS,
    LabelLock,
    Listing,
    LockedDrug,
    LockedLabel,
    choose,
    parse_listing,
    rxnorm_info,
    select_label,
    spl_meta,
)
from medgraph.rag.fetch import Fetcher

SPL = Path(__file__).parents[1] / "fixtures" / "spl" / "minimal.xml"


def listing(setid: str, version: int, published: str) -> Listing:
    return Listing(setid, version, dt.date.fromisoformat(published), f"LABEL {setid}")


def test_parse_listing_reads_dailymed_dates() -> None:
    item = {"setid": "abc", "spl_version": 9, "published_date": "Sep 21, 2026", "title": "X"}
    assert parse_listing(item) == Listing("abc", 9, dt.date(2026, 9, 21), "X")


def test_choose_prefers_recent_then_higher_version_then_lower_setid() -> None:
    old = listing("a", 30, "2025-01-01")
    new = listing("b", 2, "2026-09-24")
    assert choose([old, new]) == new
    same_day_v3 = listing("c", 3, "2026-09-24")
    assert choose([new, same_day_v3]) == same_day_v3
    twin = listing("0", 3, "2026-09-24")
    assert choose([same_day_v3, twin]) == twin
    assert choose(reversed([old, new, same_day_v3, twin])) == twin


def fake_dailymed(by_category: dict[str | None, list[dict[str, Any]]], page_size: int = 2) -> Any:
    """A transport that serves spls.json from ``by_category``, paginated, and RxNav history."""
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if request.url.path.endswith("historystatus.json"):
            return httpx.Response(
                200,
                json={
                    "rxcuiStatusHistory": {
                        "metaData": {"status": "Active"},
                        "attributes": {"name": "lisinopril 10 MG Oral Tablet", "tty": "SCD"},
                        "definitionalFeatures": {
                            "ingredientAndStrength": [
                                {"activeIngredientName": "lisinopril"},
                                {"activeIngredientName": "lisinopril"},
                            ]
                        },
                    }
                },
            )
        items = by_category.get(params.get("marketing_category_code"), [])
        page = int(params["page"])
        chunk = items[(page - 1) * page_size : page * page_size]
        pages = max(1, -(-len(items) // page_size))
        return httpx.Response(200, json={"data": chunk, "metadata": {"total_pages": pages}})

    return httpx.MockTransport(handler), calls


def item(setid: str, version: int, published: str) -> dict[str, Any]:
    return {"setid": setid, "spl_version": version, "published_date": published, "title": setid}


def test_select_label_takes_the_first_tier_with_labels() -> None:
    nda = TIERS[0][1][0]
    anda = TIERS[1][1][0]
    transport, _ = fake_dailymed(
        {
            None: [item("repack", 2, "Sep 24, 2026"), item("brand", 2, "May 05, 2025")],
            anda: [item("repack", 2, "Sep 24, 2026")],
            nda: [item("brand", 2, "May 05, 2025")],
        }
    )
    selection = select_label(Fetcher(transport=transport, delay_s=0), "314076")
    assert selection is not None
    assert (selection.listing.setid, selection.tier, selection.candidates) == (
        "brand",
        "NDA or BLA",
        1,
    )


def test_select_label_falls_back_and_follows_pages() -> None:
    anda = TIERS[1][1][0]
    many = [item(f"g{i}", i, f"Jan 0{i}, 2026") for i in range(1, 6)]
    transport, calls = fake_dailymed({None: many, anda: many}, page_size=2)
    selection = select_label(Fetcher(transport=transport, delay_s=0), "860975")
    assert selection is not None
    assert (selection.listing.setid, selection.tier, selection.candidates) == (
        "g5",
        "ANDA or NDA authorized generic",
        5,
    )
    assert {c["page"] for c in calls if c.get("marketing_category_code") == anda} == {
        "1",
        "2",
        "3",
    }


def test_select_label_returns_none_without_labels() -> None:
    transport, _ = fake_dailymed({})
    assert select_label(Fetcher(transport=transport, delay_s=0), "197378") is None


def test_rxnorm_info_deduplicates_ingredients() -> None:
    transport, _ = fake_dailymed({})
    info = rxnorm_info(Fetcher(transport=transport, delay_s=0), "314076")
    assert (info.status, info.tty, info.ingredients) == ("Active", "SCD", ("lisinopril",))


def test_spl_meta_reads_identity_from_the_document() -> None:
    meta = spl_meta(SPL.read_bytes())
    assert meta.setid == "11111111-2222-3333-4444-555555555555"
    assert meta.version == 7
    assert meta.effective == dt.date(2025, 1, 2)
    assert meta.document_type == "HUMAN PRESCRIPTION DRUG LABEL"
    assert meta.title.startswith("EXAMPLINE (exampline) tablets")
    assert meta.labeler == "Example Pharma, Inc."
    assert meta.approvals == ("NDA000001",)
    assert meta.name == "Examplix (exampline)"
    with pytest.raises(ValueError, match="not an SPL"):
        spl_meta(b'<document xmlns="urn:hl7-org:v3"/>')


def test_lock_round_trips_and_lists_each_label_once() -> None:
    label = LockedLabel(
        setid="s",
        version=2,
        published=dt.date(2025, 5, 5),
        effective=dt.date(2025, 1, 2),
        title="T",
        labeler="L",
        document_type="HUMAN PRESCRIPTION DRUG LABEL",
        approvals=("NDA019777",),
        sha256="0" * 64,
        bytes=10,
    )
    drugs = tuple(
        LockedDrug(
            rxcui=rxcui,
            name=rxcui,
            patients=1,
            rxnorm_status="Active",
            tty="SCD",
            ingredients=("lisinopril",),
            label=label if rxcui != "3" else None,
            tier="NDA or BLA" if rxcui != "3" else None,
            candidates=1 if rxcui != "3" else 0,
            reason=None if rxcui != "3" else "DailyMed maps no label to this RxCUI",
        )
        for rxcui in ("1", "2", "3")
    )
    lock = LabelLock(rule="labels-v1", selected_on=dt.date(2026, 10, 8), cohort="c", drugs=drugs)
    again = LabelLock.model_validate_json(json.dumps(lock.model_dump(mode="json")))
    assert again == lock
    assert list(lock.labels()) == ["s"]
    assert label.path == "s/v2.xml"
