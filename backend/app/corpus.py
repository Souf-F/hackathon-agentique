from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    source_name: str
    text: str


def load_corpus(corpus_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc_path in sorted(corpus_dir.glob("*.txt")):
        document_id = doc_path.stem
        paragraphs = [p.strip() for p in doc_path.read_text(encoding="utf-8").split("\n\n") if p.strip()]
        for i, paragraph in enumerate(paragraphs):
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}:{i}",
                    document_id=document_id,
                    source_name=doc_path.name,
                    text=paragraph,
                )
            )
    return chunks


def search(chunks: list[Chunk], query: str, k: int) -> list[Chunk]:
    query_words = set(query.lower().split())
    scored = [
        (len(query_words & set(chunk.text.lower().split())), chunk)
        for chunk in chunks
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for score, chunk in scored[:k] if score > 0] or chunks[:k]
