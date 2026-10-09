from pathlib import Path

from medgraph.normalize.analytes import BY_KEY
from medgraph.rag.documents import load_documents
from medgraph.rag.quotes import locator_pages
from medgraph.rules.monitoring import MONITORING, monitoring_links

DOCUMENTS = Path(__file__).parents[2] / "configs" / "knowledge" / "documents.yaml"


def test_every_link_is_cited_and_names_a_registered_analyte() -> None:
    stored = {d.id for d in load_documents(DOCUMENTS)}
    for code, (display, links) in MONITORING.items():
        assert code.isdigit()
        assert display
        assert links
        for link in links:
            assert link.analyte in BY_KEY
            assert link.source
            assert link.locator
            assert link.quote
            assert link.locator in link.citation
            assert link.stored in stored
            assert locator_pages(link.locator)


def test_ckd_stages_link_gfr_albuminuria_and_creatinine() -> None:
    for code in ("431855005", "431856006", "433144002", "431857002"):
        assert {link.analyte for link in monitoring_links(code)} == {
            "egfr",
            "urine_acr",
            "creatinine",
        }
    assert [link.analyte for link in monitoring_links("271737000")] == ["hemoglobin"]


def test_codes_outside_the_table_get_no_link() -> None:
    assert monitoring_links("46177005") == ()  # end-stage renal disease: deliberately not yet
    assert monitoring_links("38341003") == ()  # hypertension: out of scope
    assert monitoring_links(None) == ()
