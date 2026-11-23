"""Document library: writes documents to disk, reads them back and splits them into searchable chunks.

- Every article is one chunk; the chunk identifier has the form "<document_id>#mN" and does not change when the
  document is regenerated.
- Every chunk carries the roles of its document. A role sees only the chunks of documents that list that role among
  their roles; filtering is done before ranking, and a chunk that is not visible enters no score.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sayman.documents.synthetic import Document, Article, search_text


@dataclass(frozen=True)
class Chunk:
    parca_id: str
    belge_id: str
    belge_baslik: str
    madde_baslik: str
    roller: tuple[str, ...]
    metin: str          # the article's own text; the citation check looks at this
    arama_metni: str    # document title + party + article title + text; search looks at this


def to_chunks(documents: list[Document]) -> list[Chunk]:
    return [Chunk(m.parca_id, b.belge_id, b.baslik, m.baslik, tuple(b.roller), m.metin, search_text(b, m))
            for b in documents for m in b.maddeler]


def can_see(role: str, roles: tuple[str, ...]) -> bool:
    return role in roles


def write_documents(documents: list[Document], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for b in documents:
            f.write(json.dumps(asdict(b), ensure_ascii=False) + "\n")


def read_documents(path: Path) -> list[Document]:
    documents = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            d["roller"] = tuple(d["roller"])
            d["maddeler"] = tuple(Article(**m) for m in d["maddeler"])
            documents.append(Document(**d))
    return documents
