"""Journal durable Palier 4 : JSON Lines append-only, indépendant de SQLite.

Si le fichier SQLite disparaît, ce journal doit encore pouvoir écrire que
SQLite a disparu. Il ne dépend donc d'aucun module applicatif dangereux à
l'import (pas de db, pas d'agent). Chaque ligne est un événement autonome :

  event_id, run_id, seq, timestamp (UTC ISO8601 ms), type, status, data

seq est strictement croissant à l'intérieur d'un run, y compris après un
redémarrage du processus (recalculé depuis le fichier si besoin).
"""

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

TERMINAL_EVENTS = {"run_completed", "run_failed", "run_stopped", "run_interrupted"}

MAX_RECENT_LIMIT = 500
DEFAULT_RECENT_LIMIT = 100

_lock = threading.Lock()
_seq: dict[str, int] = {}


def journal_path() -> Path:
    raw = os.getenv("ORACLE_JOURNAL_PATH", "./data/oracle-journal.jsonl")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _reset_for_tests() -> None:
    with _lock:
        _seq.clear()


def _max_seq_on_disk(run_id: str) -> int:
    best = 0
    try:
        with open(journal_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                if evt.get("run_id") == run_id:
                    try:
                        seq = int(evt.get("seq", 0))
                    except (TypeError, ValueError):
                        continue
                    best = max(best, seq)
    except FileNotFoundError:
        pass
    return best


def _next_seq(run_id: str) -> int:
    current = _seq.get(run_id, 0)
    if current == 0:
        current = _max_seq_on_disk(run_id)
    nxt = current + 1
    _seq[run_id] = nxt
    return nxt


# Noms de champs secrets : correspondance EXACTE ou suffixe explicite, jamais
# sous-chaîne (un matching flou rédigerait des compteurs légitimes comme
# input_tokens — régression constatée en vérification live).
_SECRET_KEYS = {
    "api_key", "apikey", "api-key", "x-api-key", "authorization",
    "secret", "token", "credential", "credentials", "password", "passwd",
    "access_token", "refresh_token", "client_secret", "api_secret",
    "auth_token", "id_token", "bearer",
}
_SECRET_SUFFIXES = (
    "_api_key", "_apikey", "-api-key", "_access_token", "_refresh_token",
    "_client_secret", "_api_secret", "_auth_token", "_password", "_passwd",
    "_secret", "_credential", "_credentials", "authorization",
)


def _is_secret_key(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SECRET_KEYS or lowered.endswith(_SECRET_SUFFIXES)

# Valeurs ressemblant à des secrets, même sous un nom de champ innocent
# (ex : secret collé dans une requête). On ne protège pas que les noms.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[a-f0-9]{64}\b"),  # condensat hexadécimal type clé / empreinte brute
]


def _redact_secret_values(text: str) -> str:
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _scrub(value):
    if isinstance(value, dict):
        return {
            str(k)[:120]: ("[redacted]" if _is_secret_key(str(k)) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value[:50]]
    if isinstance(value, str):
        return _redact_secret_values(value)[:2000]
    return value


def _truncate_args(arguments: object) -> object:
    """Borne ce qui est journalisé : jamais de texte intégral de document."""
    if not isinstance(arguments, dict):
        return {}
    out: dict = {}
    for key in ("query", "q", "question"):
        if key in arguments and isinstance(arguments[key], str):
            out[key] = arguments[key][:200]
    if "k" in arguments and isinstance(arguments["k"], int):
        out["k"] = arguments["k"]
    for key, value in arguments.items():
        if key in out:
            continue
        if isinstance(value, (int, float, bool)):
            out[str(key)[:64]] = value
        elif isinstance(value, str):
            out[str(key)[:64]] = value[:200]
    return out


def _journal_tool_result(trace: dict) -> dict:
    """Projection sûre d'une trace tool : compteurs + ids, jamais de texte."""
    result = trace.get("result", {}) if isinstance(trace, dict) else {}
    items = result.get("items", []) if isinstance(result, dict) else []
    chunk_ids = []
    if isinstance(items, list):
        for item in items[:25]:
            if isinstance(item, dict) and isinstance(item.get("chunk_id"), str):
                chunk_ids.append(item["chunk_id"][:64])
    out = {
        "call_id": str(trace.get("call_id", ""))[:32],
        "tool": str(trace.get("tool", ""))[:64],
        "status": trace.get("status"),
        "duration_ms": trace.get("duration_ms"),
    }
    if isinstance(result, dict) and result.get("status") == "error":
        err = result.get("error", {})
        out["error"] = {
            "code": str(err.get("code", "TOOL_ERROR"))[:64],
            "message": str(err.get("message", ""))[:300],
        }
    else:
        out["count"] = result.get("count", len(chunk_ids)) if isinstance(result, dict) else len(chunk_ids)
        out["chunk_ids"] = chunk_ids
    return out


def append(run_id: str, type: str, status: str | None = None, data: dict | None = None) -> dict:
    """Ajoute un événement et le persiste immédiatement (fsync)."""
    safe_data = _scrub(data or {})
    # Les traces tool arrivent déjà projetées par l'agent ; double sécurité :
    if type == "tool_result" and isinstance(safe_data, dict) and "trace" in safe_data:
        trace = safe_data["trace"]
        if isinstance(trace, dict):
            safe_data = {**safe_data, "trace": _journal_tool_result(trace)}
    with _lock:
        seq = _next_seq(run_id)
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "run_id": run_id,
            "seq": seq,
            "timestamp": _now(),
            "type": type,
            "status": status,
            "data": safe_data,
        }
        line = json.dumps(event, ensure_ascii=False)
        path = journal_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        return event


def events_for_run(run_id: str) -> list[dict]:
    events = []
    try:
        with open(journal_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                if evt.get("run_id") == run_id:
                    events.append(evt)
    except FileNotFoundError:
        return []
    events.sort(key=lambda e: (int(e.get("seq", 0)), e.get("timestamp", "")))
    return events


def recent(limit: int = DEFAULT_RECENT_LIMIT) -> list[dict]:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_RECENT_LIMIT
    limit = max(1, min(limit, MAX_RECENT_LIMIT))
    events = []
    try:
        with open(journal_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        return []
    return events[-limit:]


def find_incomplete_runs() -> list[dict]:
    """Runs avec run_started mais sans événement terminal."""
    started: dict[str, dict] = {}
    terminal: set[str] = set()
    try:
        with open(journal_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                rid = evt.get("run_id")
                if not rid:
                    continue
                if evt.get("type") == "run_started":
                    started[rid] = evt
                elif evt.get("type") in TERMINAL_EVENTS:
                    terminal.add(rid)
    except FileNotFoundError:
        return []
    out = []
    for rid, evt in started.items():
        if rid not in terminal:
            run_events = events_for_run(rid)
            last_ts = run_events[-1].get("timestamp") if run_events else evt.get("timestamp")
            out.append({"run_id": rid, "started_event": evt, "last_event_timestamp": last_ts})
    return out


def mark_interrupted_runs() -> list[dict]:
    """Au démarrage : journalise run_interrupted pour chaque run orphelin."""
    marked = []
    for item in find_incomplete_runs():
        evt = append(
            item["run_id"],
            "run_interrupted",
            status="failed",
            data={
                "reason": "process_restart_detected",
                "last_event_timestamp": item["last_event_timestamp"],
                "restart_detection_timestamp": _now(),
            },
        )
        marked.append(evt)
    return marked
