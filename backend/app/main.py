import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

from app.corpus import Chunk, load_corpus, search
from app.llm import answer_query

load_dotenv()

CORPUS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "demo_corpus"

app = FastAPI(title="La Taupe")
chunks: list[Chunk] = []


@app.on_event("startup")
def startup() -> None:
    global chunks
    chunks = load_corpus(CORPUS_DIR)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks_loaded": len(chunks)}


class QueryRequest(BaseModel):
    question: str


@app.post("/query")
def query(request: QueryRequest) -> dict:
    top_k = int(os.environ.get("RETRIEVAL_TOP_K", 5))
    evidence = search(chunks, request.question, top_k)
    return answer_query(request.question, evidence)
