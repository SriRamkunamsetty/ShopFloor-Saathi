from __future__ import annotations

import pytest

from saathi.tools.knowledge import KnowledgeBase, expand_text


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_default_data()


def top_heading(kb, query, kind="manual"):
    hits = kb.search(query, k=1, kind=kind)
    return hits[0].chunk.heading if hits else None


@pytest.mark.parametrize(
    "query",
    [
        "machine is overheating and the cooling fan seems slow",
        "machine garam ho raha hai",  # Hinglish
        "मशीन गरम हो रही है",  # Devanagari
    ],
)
def test_heat_queries_find_heat_dissipation_section(kb, query):
    assert top_heading(kb, query) == "Heat dissipation failure (HDF)"


def test_wear_and_power_queries(kb):
    assert top_heading(kb, "tool ghis gaya finish kharab burrs") == "Tool wear failure (TWF)"
    assert (
        top_heading(kb, "drive overload alarm fuse burning smell")
        == "Power and drive failure (PWF)"
    )


def test_abbreviations_in_logs_are_expanded(kb):
    hits = kb.search("bearing noisy lubrication", kind="incident")
    assert any("brg" in h.chunk.text for h in hits)
    assert "bear" in expand_text("brg noisy")  # "brg" -> "bearing" -> stem "bear"


def test_gibberish_returns_nothing_instead_of_guessing(kb):
    assert kb.search("zzqx wvvk plorb") == []
    assert kb.search("") == []


def test_kind_filter_and_citation_shape(kb):
    hits = kb.search("coolant pump leaking", kind="incident")
    assert hits and all(h.chunk.kind == "incident" for h in hits)
    citation = hits[0].to_citation(max_chars=60)
    assert citation.source == "maintenance_logs.csv" and len(citation.excerpt) <= 60


def test_extract_steps_are_ordered_and_complete(kb):
    chunk = kb.manual_for_failure_mode("HDF")
    steps = kb.extract_steps(chunk)
    assert len(steps) == 5 and steps[0].startswith("Stop the cycle")


def test_k_must_be_positive(kb):
    with pytest.raises(ValueError):
        kb.search("fan", k=0)
