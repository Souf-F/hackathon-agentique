"""Mesure honnête du coût d'un run : tokens, appels, durée, coût estimé.

Une seule source de vérité : l'accumulateur est alimenté à chaque réponse
fournisseur reçue (appel initial, appels après tool, tour final, réparation
JSON) puis exposé tel quel dans la réponse non-stream, l'événement SSE
`done` et le journal (`run_completed`).

Règles d'honnêteté :
- si une réponse ne fournit pas de bloc `usage` (client mock), on ne remplit
  PAS des zéros : `usage_available = False` et les compteurs restent à None ;
- le pricing est centralisé ici ; un modèle inconnu donne
  `estimated_cost_usd = None` + `pricing_status = "unknown_model"` — on
  n'applique jamais silencieusement un tarif à un autre modèle ;
- le coût est calculé en Decimal, arrondi uniquement pour l'affichage (6 dp).
"""

import os
import time
from decimal import Decimal, ROUND_HALF_UP

# Tarifs Anthropic officiels (USD par million de tokens). Le cache n'est pas
# tarifié ici : seuls input/output ont un tarif publié retenu.
PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
}

_COST_QUANTUM = Decimal("0.000001")


def pricing_for(model: str | None) -> tuple[Decimal, Decimal] | None:
    """Retourne (input_rate, output_rate) si le modèle est connu, sinon None."""
    if not model:
        return None
    lowered = model.lower()
    for name, rates in PRICING.items():
        if name in lowered:
            return rates
    return None


def _as_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


class UsageAccumulator:
    """Additionne l'usage de chaque réponse fournisseur d'un run."""

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.model_calls = 0
        self.tool_calls = 0
        self.input_tokens: int | None = 0
        self.output_tokens: int | None = 0
        self.cache_creation_input_tokens: int | None = 0
        self.cache_read_input_tokens: int | None = 0
        self.usage_available = True
        self._started = time.perf_counter()

    def record(self, response: object) -> None:
        """Enregistre UNE réponse fournisseur. Jamais de tokens inventés."""
        self.model_calls += 1
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            self._mark_unavailable()
            return
        values = {
            "input_tokens": _as_non_negative_int(usage.get("input_tokens")),
            "output_tokens": _as_non_negative_int(usage.get("output_tokens")),
            "cache_creation_input_tokens": _as_non_negative_int(
                usage.get("cache_creation_input_tokens", 0)),
            "cache_read_input_tokens": _as_non_negative_int(
                usage.get("cache_read_input_tokens", 0)),
        }
        if values["input_tokens"] is None or values["output_tokens"] is None:
            self._mark_unavailable()
            return
        if self.input_tokens is not None:
            self.input_tokens += values["input_tokens"] or 0
        if self.output_tokens is not None:
            self.output_tokens += values["output_tokens"] or 0
        if self.cache_creation_input_tokens is not None:
            self.cache_creation_input_tokens += values["cache_creation_input_tokens"] or 0
        if self.cache_read_input_tokens is not None:
            self.cache_read_input_tokens += values["cache_read_input_tokens"] or 0

    def _mark_unavailable(self) -> None:
        self.usage_available = False
        self.input_tokens = None
        self.output_tokens = None
        self.cache_creation_input_tokens = None
        self.cache_read_input_tokens = None

    def set_tool_calls(self, n: int) -> None:
        self.tool_calls = max(0, int(n))

    def snapshot(self) -> dict:
        """Photographie immuable : la même est exposée partout."""
        duration_ms = int((time.perf_counter() - self._started) * 1000)
        rates = pricing_for(self.model)
        if not self.usage_available:
            cost, pricing_status = None, "unavailable"
        elif rates is None:
            cost, pricing_status = None, "unknown_model"
        else:
            total = (Decimal(self.input_tokens or 0) * rates[0]
                     + Decimal(self.output_tokens or 0) * rates[1]) / Decimal(1_000_000)
            cost = float(total.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP))
            pricing_status = "ok"
        return {
            "model": self.model,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "duration_ms": duration_ms,
            "estimated_cost_usd": cost,
            "usage_available": self.usage_available,
            "pricing_status": pricing_status,
        }


def unavailable(model: str | None = None) -> dict:
    """Métrique honnête quand aucun usage réel n'existe (run mocké)."""
    acc = UsageAccumulator(model=model)
    acc._mark_unavailable()
    return acc.snapshot()
