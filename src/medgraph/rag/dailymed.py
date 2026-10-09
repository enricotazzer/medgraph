"""Drug labels from DailyMed (US FDA Structured Product Labeling), chosen by RxNorm code.

DailyMed lists many labels for one product: the application holder's, generic makers', and
repackagers' copies. medgraph takes one per RxCUI by a fixed rule (``SELECTION_RULE``):

1. Prefer the labelling of the approved application: marketing category NDA or BLA. Generic
   (ANDA) labels must match it.
2. Else an ANDA or an NDA authorized generic.
3. Else any label DailyMed maps to the RxCUI (for example OTC monograph products).

Within the first tier that has labels, the most recently published wins; ties go to the
higher version, then the lower set ID. This picks current labelling, not the "best" label:
for a generic it may be a repackager's copy of the manufacturer's text. The choice is pinned
(set ID, version, SHA-256) in a committed lock file, so later runs reproduce it; a newer
version is reported, never taken silently.

Copyright: NLM states that it "cannot guarantee the copyright status for any item"
(https://www.nlm.nih.gov/web_policies.html). Labels are stored locally, never committed, and
quoted in short passages with their set ID and version.
"""

import datetime as dt
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from medgraph.rag.fetch import Fetcher

DAILYMED = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
RXNAV = "https://rxnav.nlm.nih.gov/REST"
SELECTION_RULE = "labels-v1"
TIERS: tuple[tuple[str, tuple[str | None, ...]], ...] = (
    ("NDA or BLA", ("C73594", "C73585")),
    ("ANDA or NDA authorized generic", ("C73584", "C73605")),
    ("any marketing category", (None,)),
)
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
HL7 = "{urn:hl7-org:v3}"
LABELER = f"{HL7}author/{HL7}assignedEntity/{HL7}representedOrganization/{HL7}name"


# --- choosing a label ---------------------------------------------------------------------


@dataclass(frozen=True)
class Listing:
    setid: str
    version: int
    published: dt.date
    title: str


def _listing_date(text: str) -> dt.date:
    """DailyMed's "Sep 24, 2026"."""
    return dt.datetime.strptime(text, "%b %d, %Y").replace(tzinfo=dt.UTC).date()


def parse_listing(item: dict[str, Any]) -> Listing:
    return Listing(
        setid=str(item["setid"]),
        version=int(item["spl_version"]),
        published=_listing_date(str(item["published_date"])),
        title=str(item["title"]),
    )


def choose(listings: Iterable[Listing]) -> Listing:
    """The most recently published; then the higher version; then the lower set ID."""
    return min(listings, key=lambda x: (-x.published.toordinal(), -x.version, x.setid))


def list_labels(fetcher: Fetcher, rxcui: str, category: str | None) -> list[Listing]:
    params: dict[str, Any] = {"rxcui": rxcui, "pagesize": 100, "page": 1}
    if category is not None:
        params["marketing_category_code"] = category
    listings: list[Listing] = []
    while True:
        data = fetcher.json(f"{DAILYMED}/spls.json", params)
        listings.extend(parse_listing(item) for item in data.get("data", []))
        if params["page"] >= int(data.get("metadata", {}).get("total_pages") or 1):
            return listings
        params["page"] += 1


@dataclass(frozen=True)
class Selection:
    listing: Listing
    tier: str
    candidates: int  # labels in the chosen tier


def select_label(fetcher: Fetcher, rxcui: str) -> Selection | None:
    for tier, categories in TIERS:
        found = {x.setid: x for c in categories for x in list_labels(fetcher, rxcui, c)}
        if found:
            return Selection(choose(found.values()), tier, len(found))
    return None


# --- RxNorm -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RxNormInfo:
    status: str
    name: str | None
    tty: str | None
    ingredients: tuple[str, ...]


def rxnorm_info(fetcher: Fetcher, rxcui: str) -> RxNormInfo:
    history = fetcher.json(f"{RXNAV}/rxcui/{rxcui}/historystatus.json")["rxcuiStatusHistory"]
    attributes = history.get("attributes") or {}
    features = history.get("definitionalFeatures") or {}
    ingredients = sorted(
        {
            str(x["activeIngredientName"])
            for x in features.get("ingredientAndStrength") or ()
            if x.get("activeIngredientName")
        }
    )
    return RxNormInfo(
        status=str((history.get("metaData") or {}).get("status", "unknown")),
        name=attributes.get("name"),
        tty=attributes.get("tty"),
        ingredients=tuple(ingredients),
    )


# --- SPL documents ------------------------------------------------------------------------


def spl_url(setid: str) -> str:
    return f"{DAILYMED}/spls/{setid}.xml"


@dataclass(frozen=True)
class SplMeta:
    setid: str
    version: int
    effective: dt.date
    document_type: str
    title: str
    labeler: str
    approvals: tuple[str, ...]  # application numbers, such as NDA019777
    name: str  # product name, with the generic name when different: "Zestril (lisinopril)"


def _text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _product_name(root: ET.Element) -> str:
    for product in root.iter(f"{HL7}manufacturedProduct"):
        inner = product.find(f"{HL7}manufacturedProduct")
        if inner is None:
            inner = product.find(f"{HL7}manufacturedMedicine")
        if inner is None:
            continue
        name = _text(inner.find(f"{HL7}name"))
        generic = _text(inner.find(f"{HL7}asEntityWithGeneric/{HL7}genericMedicine/{HL7}name"))
        if name and generic and generic.lower() != name.lower():
            return f"{name} ({generic.lower()})"
        return name or generic
    return ""


def spl_meta(xml: bytes) -> SplMeta:
    """Identity of one SPL document. The XML comes from NLM; ElementTree resolves no
    external entities, and the expat it uses limits entity expansion."""
    root = ET.fromstring(xml)
    setid = root.find(f"{HL7}setId")
    version = root.find(f"{HL7}versionNumber")
    effective = root.find(f"{HL7}effectiveTime")
    code = root.find(f"{HL7}code")
    if setid is None or version is None or effective is None or code is None:
        raise ValueError("not an SPL document: set ID, version, date or type missing")
    approvals = sorted(
        {
            str(i.get("extension"))
            for approval in root.iter(f"{HL7}approval")
            for i in approval.findall(f"{HL7}id")
            if i.get("extension")
        }
    )
    return SplMeta(
        setid=str(setid.get("root")),
        version=int(str(version.get("value"))),
        effective=dt.date.fromisoformat(str(effective.get("value"))[:8]),
        document_type=str(code.get("displayName", "")),
        title=_text(root.find(f"{HL7}title")),
        labeler=_text(root.find(LABELER)),
        approvals=tuple(approvals),
        name=_product_name(root),
    )


# --- the lock file ------------------------------------------------------------------------


class LockedLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    setid: str
    version: int
    published: dt.date
    effective: dt.date
    title: str
    labeler: str
    document_type: str
    approvals: tuple[str, ...]
    sha256: str
    bytes: int

    @property
    def path(self) -> str:
        """Relative to the knowledge store's labels folder."""
        return f"{self.setid}/v{self.version}.xml"


class LockedDrug(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rxcui: str
    name: str  # as the cohort records it
    patients: int
    rxnorm_status: str
    tty: str | None
    ingredients: tuple[str, ...]
    label: LockedLabel | None
    tier: str | None  # the selection tier the label came from
    candidates: int  # labels DailyMed lists in that tier
    reason: str | None = None  # why there is no label


class LabelLock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str
    selected_on: dt.date
    cohort: str
    drugs: tuple[LockedDrug, ...]

    def labels(self) -> dict[str, LockedLabel]:
        """Each distinct label once, by set ID."""
        return {d.label.setid: d.label for d in self.drugs if d.label is not None}
