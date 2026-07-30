"""Evidence is unioned across every identifier a citation supplies — offline (injected
lookups). No keys, no network.

The retrieval chain used to stop at the first resolver that established existence, so a
reference carrying BOTH a DOI and an arXiv id only ever hit Crossref. ACL Anthology DOIs are
the case that exposed the cost: Crossref resolves them with exact venue, page and author
metadata but serves NO abstract, so APPLICATION fell back to model memory on a paper whose
abstract was one call away — and a memory-only judgement is not a verification.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "vendored_cite_check",
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "rapier" / "verify" / "_vendor" / "cite_check.py",
)
cc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cc)


def _citation(**ref):
    base = {"type": "journal", "title": "T", "authors": ["Doe, J."], "year": 2023,
            "doi": None, "arxiv_id": None, "urls": []}
    base.update(ref)
    return {"id": "c1", "is_citation": True, "raw_text": "Doe 2023, T", "reference": base,
            "claims": ["a claim"]}


@pytest.fixture
def no_network(monkeypatch):
    for name in ("lookup_crossref_search", "lookup_openlibrary", "fetch_url"):
        monkeypatch.setattr(cc, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError(name)))


def test_arxiv_supplements_a_doi_that_resolved_without_an_abstract(monkeypatch, no_network):
    """The ACL Anthology shape: DOI gives precise metadata, arXiv gives the abstract."""
    monkeypatch.setattr(cc, "lookup_crossref_doi",
                        lambda doi: (200, {"title": "T", "authors": ["Doe, J."], "year": 2023,
                                           "doi": doi, "abstract": None}))
    monkeypatch.setattr(cc, "lookup_arxiv",
                        lambda aid: (200, {"title": "T", "authors": ["Doe, J."],
                                           "abstract": "the retrieved abstract"}))
    ev = cc.retrieve_one(_citation(doi="10.18653/v1/2023.emnlp-main.398", arxiv_id="2305.14627"))

    assert ev["existence_status"] == "exists"
    assert ev["application_evidence_available"] is True
    assert ev["application_evidence_basis"] == "retrieved_abstract"
    assert ev["application_evidence_text"] == "the retrieved abstract"
    # existence came from the DOI; arXiv is labelled as the evidence supplement it was
    bases = [lk["match_basis"] for lk in ev["lookups"]]
    assert "doi_direct" in bases
    assert "arxiv_id_evidence_supplement" in bases


def test_arxiv_is_not_called_when_the_doi_already_carried_an_abstract(monkeypatch, no_network):
    monkeypatch.setattr(cc, "lookup_crossref_doi",
                        lambda doi: (200, {"title": "T", "authors": [], "year": 2023,
                                           "doi": doi, "abstract": "crossref abstract"}))
    monkeypatch.setattr(cc, "lookup_arxiv",
                        lambda aid: pytest.fail("arXiv called despite an abstract in hand"))
    ev = cc.retrieve_one(_citation(doi="10.1145/3571730", arxiv_id="2202.03629"))

    assert ev["application_evidence_text"] == "crossref abstract"


def test_supplement_miss_does_not_read_as_doubt_about_existence(monkeypatch, no_network):
    """arXiv running purely for an abstract says nothing about whether the work exists."""
    monkeypatch.setattr(cc, "lookup_crossref_doi",
                        lambda doi: (200, {"title": "T", "authors": [], "year": 2023,
                                           "doi": doi, "abstract": None}))
    monkeypatch.setattr(cc, "lookup_arxiv", lambda aid: (200, None))
    ev = cc.retrieve_one(_citation(doi="10.1/x", arxiv_id="2305.14627"))

    assert ev["existence_status"] == "exists"
    assert ev["application_evidence_available"] is False
    miss = [lk for lk in ev["lookups"] if lk["source"] == "arxiv"][0]
    assert miss["match_basis"] == "evidence_supplement_unavailable"


def test_arxiv_still_establishes_existence_when_no_doi_resolved(monkeypatch, no_network):
    monkeypatch.setattr(cc, "lookup_arxiv",
                        lambda aid: (200, {"title": "T", "authors": [], "abstract": "abs"}))
    ev = cc.retrieve_one(_citation(arxiv_id="2310.13548"))

    assert ev["existence_status"] == "exists"
    assert [lk["match_basis"] for lk in ev["lookups"]] == ["arxiv_id"]
    assert ev["application_evidence_available"] is True
