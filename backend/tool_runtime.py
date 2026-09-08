"""Runtime capability-bound des outils exposes au modele."""

import json
import time
import uuid
from datetime import datetime, timezone

from .db import connect
from .tools import search_evidence

MAX_QUERY_CHARS = 2000
MAX_K = 12


class ToolRuntime:
    """Expose uniquement les outils lecture seule pour un corpus donne."""

    def __init__(self, corpus_id: str):
        self.corpus_id = corpus_id
        self.traces: list[dict] = []
        self.returned_chunk_ids: set[str] = set()
        self.returned_chunks: dict[str, str] = {}
        self._registry = {"search_evidence": self._search_evidence}

    def definitions(self) -> list[dict]:
        return [{
            "name": "search_evidence",
            "description": "Recherche des passages admissibles dans le corpus courant.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
                    "k": {"type": "integer", "minimum": 1, "maximum": MAX_K},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }]

    def execute(self, tool: str, arguments: object) -> dict:
        call_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            handler = self._registry.get(tool)
            if handler is None:
                result = self._error("UNKNOWN_TOOL", "L'outil demandé n'est pas disponible.")
            else:
                result = handler(arguments)
            status = result["status"]
        except Exception:
            result = self._error("TOOL_EXECUTION_ERROR", "La recherche n'a pas pu être exécutée.")
            status = "error"

        duration_ms = round((time.perf_counter() - started) * 1000)
        trace = {
            "call_id": call_id,
            "tool": tool,
            "arguments": arguments if isinstance(arguments, dict) else {},
            "status": status,
            "duration_ms": duration_ms,
            "result": self._trace_result(result),
        }
        self.traces.append(trace)
        self._persist(trace)
        return result

    def _search_evidence(self, arguments: object) -> dict:
        if not isinstance(arguments, dict) or set(arguments) - {"query", "k"}:
            return self._error("INVALID_ARGUMENTS", "Les arguments de recherche sont invalides.")
        query = arguments.get("query")
        k = arguments.get("k", 5)
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            return self._error("INVALID_ARGUMENTS", "La requête doit contenir entre 1 et 2000 caractères.")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
            return self._error("INVALID_ARGUMENTS", "k doit être compris entre 1 et 12.")

        chunks = search_evidence(self.corpus_id, query.strip(), k)
        items = [{
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "text": chunk.text,
            "page": chunk.page,
        } for chunk in chunks]
        self.returned_chunk_ids.update(item["chunk_id"] for item in items)
        self.returned_chunks.update({item["chunk_id"]: item["document_id"] for item in items})
        return {"status": "ok", "count": len(items), "items": items}

    def _persist(self, trace: dict) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO tool_calls (call_id, corpus_id, timestamp, tool, arguments, status, duration_ms, result)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace["call_id"], self.corpus_id,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    trace["tool"], json.dumps(trace["arguments"]), trace["status"],
                    trace["duration_ms"], json.dumps(trace["result"]),
                ),
            )

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"status": "error", "error": {"code": code, "message": message}}

    @staticmethod
    def _trace_result(result: dict) -> dict:
        if result["status"] == "error":
            return {"error": result["error"]}
        return {
            "count": result["count"],
            "items": [{
                "chunk_id": item["chunk_id"],
                "document_id": item["document_id"],
                "preview": item["text"][:160],
            } for item in result["items"]],
        }
