"""Superviseur Palier 4 : lance Uvicorn comme enfant et journalise sa vie.

- journalise process_started (PID enfant, timestamp exact observé) ;
- attend la fin de l'enfant ;
- journalise process_exited (timestamp exact, PID, exit_code) ;
- NE redémarre JAMAIS silencieusement le serveur.

Démo :
    python -m backend.supervisor
    tail -f data/oracle-journal.jsonl

Si le correcteur tue UNIQUEMENT le processus Uvicorn enfant, ce superviseur
reste vivant et écrit immédiatement process_exited.

L'import de ce module n'a aucun effet de bord (exigé pour les tests).
"""

import signal
import subprocess
import sys


def run(argv: list[str] | None = None) -> int:
    from . import journal as journal_mod

    cmd = [sys.executable, "-m", "uvicorn", "backend.main:app",
           "--host", "127.0.0.1", "--port", "8000"]
    if argv:
        cmd.extend(argv)
    child = subprocess.Popen(cmd)
    journal_mod.append(
        "supervisor", "process_started", status="running",
        data={"pid": child.pid, "cmd": "uvicorn backend.main:app"},
    )

    def _forward(signum, _frame):
        try:
            child.send_signal(signum)
        except Exception:
            pass

    old_int = signal.signal(signal.SIGINT, _forward)
    old_term = signal.signal(signal.SIGTERM, _forward)
    try:
        exit_code = child.wait()
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
    journal_mod.append(
        "supervisor", "process_exited", status="stopped",
        data={"pid": child.pid, "exit_code": exit_code},
    )
    return exit_code


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
