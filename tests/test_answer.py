"""Les quatre chemins de la génération, dont les trois modes d'échec.

Sans ces tests, une clé invalide ressemble à une absence de clé et on croit
l'intégration fonctionnelle alors qu'elle est cassée.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import answer as answer_module  # noqa: E402
from backend.answer import LLMUnavailable, answer_query  # noqa: E402
from backend.models import EvidenceChunk  # noqa: E402

EVIDENCE = [EvidenceChunk("c1", "d1", "Camille Martin, cinq ans de Python.")]


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_sans_cle_mode_extractif(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a = answer_query("Quelle expérience Python ?", EVIDENCE)
    assert a.mode == "extractive"
    assert a.citations[0].chunk_id == "c1"


def test_avec_cle_mode_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(
        200, {"content": [{"type": "text", "text": "Cinq ans, selon c1."}]}))
    a = answer_query("Quelle expérience Python ?", EVIDENCE)
    assert a.mode == "llm"
    assert "Cinq ans" in a.text


def test_cle_invalide_leve_une_erreur(monkeypatch):
    """401 ne doit PAS ressembler à une absence de clé."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-invalide")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(
        401, text='{"error":"authentication_error"}'))
    with pytest.raises(LLMUnavailable) as exc:
        answer_query("Quelle expérience Python ?", EVIDENCE)
    assert "401" in str(exc.value)


def test_timeout_leve_une_erreur(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def _boom(*a, **k):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(LLMUnavailable):
        answer_query("Quelle expérience Python ?", EVIDENCE)


def test_json_illisible_leve_une_erreur(monkeypatch):
    """200 avec un corps non parsable ne doit pas devenir un 500."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class _Broken(_Response):
        def json(self):
            raise ValueError("bad json")

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Broken(200))
    with pytest.raises(LLMUnavailable) as exc:
        answer_query("Quelle expérience Python ?", EVIDENCE)
    assert "malformée" in str(exc.value)


def test_reponse_malformee_leve_une_erreur(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(200, {"content": []}))
    with pytest.raises(LLMUnavailable):
        answer_query("Quelle expérience Python ?", EVIDENCE)


def test_aucune_preuve_ne_declenche_aucun_appel(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("appel inattendu"))
    a = answer_query("question sans réponse", [])
    assert a.citations == []


def test_le_prompt_delimite_les_donnees():
    rendered = answer_module._render_evidence(EVIDENCE)
    assert rendered.startswith("<DONNEES>") and rendered.endswith("</DONNEES>")
    assert "chunk_id=c1" in rendered
