"""Retrieval over manuals and past maintenance logs, with citations.

This is a dependency-free TF-IDF retriever so the system runs and is testable anywhere. In
production the same `search` contract is served by Vertex AI RAG Engine / Vertex AI Search
(see docs/ARCHITECTURE.md). Hinglish and Devanagari terms are expanded to English so a Hindi
voice query such as "machine garam ho raha hai" finds the heat dissipation section.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from saathi.models import Citation

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TOKEN_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)
_FAILURE_TAG_RE = re.compile(r"\(([A-Z]{3})\)\s*$")

_STOPWORDS = frozenset(
    "a an the and or of to in on at is are was be it this that for with from by as if "
    "not no do does did has have had can may will should hai ho raha rahi kya hain ki ka ke "
    "ko se me mein par aur".split()
)

# Hinglish (romanised Hindi) and Devanagari -> English technical terms.
_GLOSSARY = {
    "garam": "hot heat overheating temperature",
    "garmi": "hot heat temperature",
    "गरम": "hot heat overheating temperature",
    "गर्म": "hot heat overheating temperature",
    "thanda": "cold cool",
    "awaaz": "noise sound",
    "awaz": "noise sound",
    "आवाज": "noise sound",
    "आवाज़": "noise sound",
    "shor": "noise",
    "pankha": "fan cooling",
    "पंखा": "fan cooling",
    "tel": "oil lubrication",
    "तेल": "oil lubrication",
    "kharab": "fault failure",
    "खराब": "fault failure",
    "band": "stopped stop shutdown",
    "बंद": "stopped stop shutdown",
    "dhuan": "smoke burning smell",
    "dhuaan": "smoke burning smell",
    "pani": "coolant water",
    "paani": "coolant water",
    "ghis": "wear worn",
    "ghisa": "wear worn",
    "ghisna": "wear worn",
    "tool": "tool",
    "kaampna": "vibration",
    "jhatka": "vibration jerk",
    "speed": "speed rpm",
    "bijli": "power electrical drive",
    "बिजली": "power electrical drive",
    "fuse": "fuse drive power",
}

# Terse maintenance-log abbreviations -> full words.
_ABBREVIATIONS = {
    "brg": "bearing",
    "tmp": "temperature",
    "temp": "temperature",
    "grs": "grease lubrication",
    "lube": "lubrication",
    "clg": "coolant",
    "clnt": "coolant",
    "conc": "concentration",
    "vib": "vibration",
    "mc": "machine",
    "rad": "radiator",
    "hmi": "hmi display",
}


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def expand_text(text: str) -> list[str]:
    """Tokenise, expand Hinglish and abbreviations, drop stopwords, stem."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        expansion = _GLOSSARY.get(raw) or _ABBREVIATIONS.get(raw)
        words = [raw] + (expansion.split() if expansion else [])
        for word in words:
            if word not in _STOPWORDS and len(word) > 1:
                tokens.append(_stem(word))
    return tokens


@dataclass
class Chunk:
    chunk_id: str
    source: str
    heading: str
    text: str
    kind: str  # manual | incident
    failure_mode: str | None = None
    tokens: list[str] = field(default_factory=list, repr=False)


@dataclass
class Hit:
    chunk: Chunk
    score: float

    def to_citation(self, max_chars: int = 360) -> Citation:
        text = " ".join(self.chunk.text.split())
        excerpt = text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"
        return Citation(
            source=self.chunk.source,
            heading=self.chunk.heading,
            kind=self.chunk.kind,  # type: ignore[arg-type]
            score=round(self.score, 3),
            excerpt=excerpt,
        )


def load_manual_chunks(manual_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(manual_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for block in re.split(r"(?m)^## ", text)[1:]:
            heading, _, body = block.partition("\n")
            heading = heading.strip()
            tag = _FAILURE_TAG_RE.search(heading)
            chunks.append(
                Chunk(
                    chunk_id=f"{path.stem}#{re.sub(r'[^a-z0-9]+', '-', heading.lower()).strip('-')}",
                    source=path.name,
                    heading=heading,
                    text=f"{heading}. {body.strip()}",
                    kind="manual",
                    failure_mode=tag.group(1) if tag else None,
                )
            )
    return chunks


def load_incident_chunks(csv_path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for i, row in enumerate(csv.DictReader(handle), start=1):
            text = f"{row['problem']}. Action: {row['action']}"
            chunks.append(
                Chunk(
                    chunk_id=f"log#{i}",
                    source="maintenance_logs.csv",
                    heading=f"{row['date']} {row['machine_id']}: {row['problem']}",
                    text=f"{text} ({row['machine_id']}, {row['date']})",
                    kind="incident",
                )
            )
    return chunks


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk]):
        if not chunks:
            raise ValueError("KnowledgeBase needs at least one chunk")
        self._chunks = chunks
        for chunk in chunks:
            chunk.tokens = expand_text(chunk.text)
        n_docs = len(chunks)
        doc_freq: Counter[str] = Counter()
        for chunk in chunks:
            doc_freq.update(set(chunk.tokens))
        self._idf = {t: math.log((1 + n_docs) / (1 + df)) + 1 for t, df in doc_freq.items()}
        self._vectors = [self._vector(c.tokens) for c in chunks]

    @classmethod
    def from_default_data(cls) -> KnowledgeBase:
        chunks = load_manual_chunks(DATA_DIR / "manuals")
        chunks += load_incident_chunks(DATA_DIR / "maintenance_logs.csv")
        return cls(chunks)

    def _vector(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        vec = {t: (1 + math.log(c)) * self._idf.get(t, 0.0) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def search(
        self, query: str, k: int = 3, kind: str | None = None, min_score: float = 0.12
    ) -> list[Hit]:
        if k < 1:
            raise ValueError("k must be positive")
        q_vec = self._vector(expand_text(query))
        if not q_vec:
            return []
        hits = []
        for chunk, vec in zip(self._chunks, self._vectors, strict=True):
            if kind and chunk.kind != kind:
                continue
            score = sum(w * vec.get(t, 0.0) for t, w in q_vec.items())
            if score >= min_score:
                hits.append(Hit(chunk, score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def manual_for_failure_mode(self, code: str) -> Chunk | None:
        for chunk in self._chunks:
            if chunk.kind == "manual" and chunk.failure_mode == code:
                return chunk
        return None

    @staticmethod
    def extract_steps(chunk: Chunk) -> list[str]:
        """Numbered corrective actions from a manual section, in order."""
        steps: list[str] = []
        in_actions = False
        for line in chunk.text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("corrective actions"):
                in_actions = True
                continue
            match = re.match(r"^\d+\.\s+(.*)$", stripped)
            if in_actions and match:
                steps.append(match.group(1))
        return steps
