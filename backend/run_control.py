"""Registre des runs Palier 4 : états, kill switch, annulation coopérative.

Un run_id opaque est créé pour chaque exécution agentique. Les statuts
minimaux sont : running, stop_requested, stopped, completed, failed
(plus interrupted après un redémarrage, voir journal.mark_interrupted_runs).

Le kill switch est une CAPACITÉ OPÉRATEUR (endpoint HTTP), jamais un tool
LLM. `request_stop` journalise immédiatement stop_requested et arme un
threading.Event que la boucle async consulte avant chaque appel et pendant
chaque appel bloquant (poll 50 ms + cancel de la Task).
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

TERMINAL_STATUSES = {"stopped", "completed", "failed", "interrupted"}

_lock = threading.Lock()
_runs: dict[str, "RunRecord"] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class RunRecord:
    run_id: str
    corpus_id: str
    status: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    failure_code: str | None = None
    stop_requested_at: str | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)


def _reset_for_tests() -> None:
    with _lock:
        _runs.clear()


def create_run(corpus_id: str, run_id: str | None = None) -> RunRecord:
    with _lock:
        if run_id is not None and run_id in _runs:
            # Réutilisation (ex : run pré-marqué stop_requested avant le run) :
            # on ne réinitialise jamais un état existant.
            return _runs[run_id]
        record = RunRecord(
            run_id=run_id or uuid.uuid4().hex[:12],
            corpus_id=corpus_id,
            started_at=_now(),
        )
        _runs[record.run_id] = record
        return record


def get(run_id: str) -> RunRecord | None:
    with _lock:
        return _runs.get(run_id)


def request_stop(run_id: str) -> tuple[RunRecord | None, bool, str]:
    """Demande l'arrêt. Retourne (record, already_terminal, timestamp).

    Idempotent : un second appel ne rejournalise pas stop_requested et ne
    casse jamais un run déjà terminal (il retourne son état).
    """
    from . import journal as journal_mod

    with _lock:
        record = _runs.get(run_id)
    if record is None:
        # Run inconnu du registre (ex: processus redémarré) : on l'enregistre
        # comme stoppé pour rester idempotent sans effet destructeur.
        record = RunRecord(run_id=run_id, corpus_id="", status="stopped", started_at=_now(), finished_at=_now())
        with _lock:
            _runs[run_id] = record
        return record, True, record.finished_at or _now()
    with _lock:
        if record.status in TERMINAL_STATUSES:
            return record, True, record.finished_at or _now()
        if record.status == "stop_requested":
            return record, False, record.stop_requested_at or _now()
        record.status = "stop_requested"
        record.stop_requested_at = _now()
        record.stop_event.set()
        timestamp = record.stop_requested_at
    journal_mod.append(
        run_id, "stop_requested", status="stop_requested",
        data={"corpus_id": record.corpus_id},
    )
    return record, False, timestamp


def mark_terminal(run_id: str, status: str, failure_code: str | None = None) -> RunRecord | None:
    with _lock:
        record = _runs.get(run_id)
        if record is None:
            return None
        if record.status in TERMINAL_STATUSES:
            return record
        record.status = status
        record.finished_at = _now()
        record.failure_code = failure_code
        return record


def snapshot(run_id: str) -> dict | None:
    with _lock:
        record = _runs.get(run_id)
        if record is None:
            return None
        return {
            "run_id": record.run_id,
            "corpus_id": record.corpus_id,
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "failure_code": record.failure_code,
        }
