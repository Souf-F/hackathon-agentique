"""Analyse de sécurité d'un passage.

Principe : on ne cherche pas des mots, on cherche une FONCTION. Un passage
devient suspect quand il s'adresse à l'agent pour modifier son autorité, son
rôle, ses outils, ou pour obtenir ses secrets.

Trois mécanismes distincts, tous explicables séparément :

1. SIGNAUX      — motifs de forme, chacun porteur d'un poids.
2. CIBLE        — à qui s'adresse l'injonction ? « ignorer le paragraphe 3 du
                  règlement » vise un artefact documentaire ; « ignore tes
                  instructions » vise l'agent. Seul le second compte.
3. ATTÉNUATION  — le déclencheur est-il cité ou décrit plutôt que porté ?
                  L'atténuation est PLAFONNÉE : elle ne s'applique que si un
                  seul type de signal est présent. Un texte pédagogique cite
                  une phrase ; il n'enchaîne pas plusieurs demandes
                  opérationnelles distinctes.

Ce module ne met rien en quarantaine. Il retourne un verdict ; la décision
appartient à l'application (cf. pipeline.py).

Palier 2 : signaux déterministes uniquement. Le second signal (classifieur
LLM) s'ajoutera derrière cette même signature.
"""

import re

from .models import InjectionVerdict

# --- Signaux : (poids, catégorie, libellé, motif) --------------------------

SIGNALS: list[tuple[float, str, str, re.Pattern]] = [
    (
        0.55,
        "authority_override",
        "injonction d'abandonner les consignes en place",
        re.compile(
            r"\b(ignor\w*|oubli\w*|disregard|forget|override|remplace\w*)\b"
            r"(?P<between>[^.!?\n]{0,40}?)"
            r"\b(instruction|consigne|r[eè]gle|prompt|directive|precede\w*|"
            r"pr[ée]c[ée]dent\w*|previous|above|syst[èe]me|system)\w*",
            re.I,
        ),
    ),
    (
        0.50,
        "role_redefinition",
        "tentative de redéfinition du rôle de l'agent",
        re.compile(
            r"\b(tu es|vous [êe]tes|you are|act as|agis comme|"
            r"comporte-toi|pretend)\b[^.!?\n]{0,30}?"
            r"\b(maintenant|d[ée]sormais|now|administrateur|admin|root|"
            r"developer|d[ée]veloppeur|dan|jailbreak)\b",
            re.I,
        ),
    ),
    (
        # Volontairement sous le seuil : « SYSTEM: » ouvre legitimement des
        # sections de documentation technique. Ce signal doit etre corrobore.
        0.35,
        "system_impersonation",
        "usurpation d'un message système",
        re.compile(
            r"(\[\s*(system|syst[èe]me|admin|important)[^\]]{0,30}\]|"
            r"<\s*(system|im_start)[^>]{0,20}>|"
            r"^\s*(system|syst[èe]me)\s*:)",
            re.I | re.M,
        ),
    ),
    (
        0.55,
        "secret_exfiltration",
        "demande d'exposition d'un secret ou du prompt système",
        re.compile(
            r"\b(affiche|montre|r[ée]v[èe]le|divulgue|print|reveal|show|"
            r"output|repeat|donne)\w*\b[^.!?\n]{0,40}?"
            r"\b(system prompt|prompt syst[èe]me|api[_ ]?key|cl[ée] api|"
            r"token|secret|credential|variable d'environnement|env)\w*",
            re.I,
        ),
    ),
    (
        0.55,
        "tool_invocation",
        "demande d'action externe (réseau, envoi, exécution)",
        re.compile(
            r"\b(envoie|transmets|poste|send|post|upload|ex[ée]cute|execute|"
            r"run|curl|fetch)\b[^.!?\n]{0,60}?"
            r"(https?://|\bwebhook\b|\bendpoint\b|\bshell\b|\bbash\b)",
            re.I,
        ),
    ),
    (
        0.25,
        "authority_override",
        "revendication de priorité sur les consignes existantes",
        re.compile(
            r"\b(ces instructions|cette consigne|this instruction|these rules)"
            r"[^.!?\n]{0,40}\b(priorit\w*|prevail|override|sup[ée]rieur\w*)",
            re.I,
        ),
    ),
]

# --- Cible : l'injonction vise-t-elle un artefact documentaire ? -----------
# « ignorer le paragraphe 3 du règlement précédent » parle du document.
# « ignore tes instructions précédentes » parle de l'agent.

SECOND_PERSON = re.compile(
    r"\b(tes|ton|ta|tu|toi|vos|votre|vous|your|you)\b", re.I
)

DOCUMENT_REFERENT = re.compile(
    r"\b(paragraphe|article|alin[ée]a|chapitre|section|clause|page|annexe|"
    r"avenant|contrat|r[èe]glement|cv|curriculum|version|[ée]dition|"
    r"formulaire|courrier)\w*",
    re.I,
)

# --- Atténuation : le déclencheur est-il décrit plutôt que porté ? ---------

REPORTING_CUES = re.compile(
    r"\b(exemple|example|consiste [àa]|il s'agit|typiquement|souvent|"
    r"attaque|technique|d[ée]crit|illustre|comme celle-ci|par exemple|"
    r"est une|sont des|appel[ée]e?s?|nomm[ée]e?s?|c'est-[àa]-dire)\b",
    re.I,
)

# Un ordre d'exécution qui introduit une citation ANNULE l'atténuation :
# « exécute exactement cette instruction : "…" » porte l'instruction citée.
EXECUTION_DIRECTIVE = re.compile(
    r"\b(ex[ée]cute|applique|suis|ob[ée]is|effectue|reproduis|"
    r"execute|apply|follow|obey|perform)\b[^\n]{0,60}?[\"«“']",
    re.I,
)

QUOTE_SPAN = re.compile(r"[\"«»“”'`]([^\"«»“”'`\n]{10,200})[\"«»“”'`]")

ATTENUATION = 0.4
CONFIDENCE_CAP = 0.95


def _normalize(text: str) -> str:
    """`_` et `-` deviennent des espaces.

    Sans cela, la frontière de mot échoue dans
    `ignore_previous_instructions.txt` et un nom de fichier hostile passe
    entre les mailles (cf. MENACES T07). La longueur est préservée pour que
    les offsets d'extrait restent valides.
    """
    return re.sub(r"[_\-]", " ", text)


def _sentence_around(text: str, start: int, end: int) -> str:
    lo = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    dot, nl = text.find(".", end), text.find("\n", end)
    candidates = [i for i in (dot, nl) if i != -1]
    hi = min(candidates) if candidates else len(text)
    return text[lo:hi]


def _targets_document(text: str, start: int, end: int) -> bool:
    """L'injonction porte-t-elle sur un artefact du document plutôt que sur l'agent ?

    « ignorer les versions antérieures de ce CV » désigne un objet du monde
    décrit par le document. « ignore tes instructions » désigne l'agent. Le
    départage se fait sur la phrase entière : la présence d'un référent
    documentaire sans marque de deuxième personne indique une consigne
    interne au document, pas une adresse à l'agent.
    """
    sentence = _sentence_around(text, start, end)
    return bool(
        DOCUMENT_REFERENT.search(sentence) and not SECOND_PERSON.search(sentence)
    )


def _is_quoted(text: str, start: int, end: int) -> bool:
    for m in QUOTE_SPAN.finditer(text):
        if m.start(1) <= start and end <= m.end(1):
            return True
    return False


def _described_near(text: str, start: int, end: int, window: int = 150) -> bool:
    """Un marqueur de description entoure-t-il le déclencheur ?

    La fenêtre est locale : chercher le marqueur dans tout le passage
    laisserait un attaquant désamorcer son injection en écrivant
    « par exemple » dans un paragraphe voisin.
    """
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    return bool(REPORTING_CUES.search(text[lo:hi]))


def _excerpt(text: str, start: int, end: int, pad: int = 60) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{text[lo:hi].strip()}{suffix}"


def analyze_chunk(text: str, threshold: float = 0.5) -> InjectionVerdict:
    """Analyse un passage et retourne un verdict. Aucun effet de bord."""
    probe = _normalize(text)
    hits: list[dict] = []
    ignored: list[str] = []

    for weight, category, label, pattern in SIGNALS:
        m = pattern.search(probe)
        if not m:
            continue

        if "between" in m.groupdict() and _targets_document(probe, m.start(), m.end()):
            ignored.append(f"{label} — vise un élément du document, pas l'agent")
            continue

        hits.append({
            "weight": weight,
            "category": category,
            "label": label,
            "excerpt": _excerpt(text, m.start(), m.end()),
            "start": m.start(),
            "end": m.end(),
        })

    if not hits:
        reason = "; ".join(ignored) if ignored else "aucun signal comportemental détecté"
        return InjectionVerdict(False, None, None, reason, 0.0)

    hits.sort(key=lambda h: h["weight"], reverse=True)
    top = hits[0]
    score = sum(h["weight"] for h in hits)
    distinct = {h["category"] for h in hits}

    quoted = _is_quoted(probe, top["start"], top["end"])
    described = _described_near(probe, top["start"], top["end"])
    forced = bool(EXECUTION_DIRECTIVE.search(probe))

    note = ""
    if quoted or described:
        if forced:
            note = " — atténuation refusée : citation introduite par un ordre d'exécution"
        elif len(distinct) >= 2:
            note = (" — atténuation refusée : plusieurs demandes opérationnelles "
                    "distinctes, ce n'est pas une citation")
        else:
            score *= ATTENUATION
            note = " — atténué : le déclencheur est cité ou décrit, pas adressé"

    confidence = min(round(score, 2), CONFIDENCE_CAP)
    reason = top["label"]
    if len(hits) > 1:
        reason += f" (+{len(hits) - 1} autre(s) signal(aux))"
    reason += note

    return InjectionVerdict(
        suspicious=confidence >= threshold,
        category=top["category"],
        excerpt=top["excerpt"],
        reason=reason,
        confidence=confidence,
    )
