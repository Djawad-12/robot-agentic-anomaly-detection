"""Les outils de l'agent.

Deux familles :
- les outils communs à toutes les machines (état, journal, codes d'erreur, documentation,
  enregistrements, rapport final), définis ici ;
- les actions propres à la machine, déclarées par le connecteur (`Connector.tools()`).
  Chacune passe par les garde-fous (`core.safety`) et est enregistrée : l'agent reçoit en
  retour l'écart à la référence, un graphique et les valeurs complètes du test.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ..core import checks
from ..core.baseline import Baseline
from ..core.events import Level, ToolSpec
from ..core.interfaces import Connector
from ..core.recorder import Recorder, Recording
from ..core.safety import SafetyPolicy, SafetyRefusal
from ..docs.indexer import build
from ..docs.search import DocSearch
from . import context

log = logging.getLogger(__name__)

LEVELS = [lvl.value for lvl in Level]

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "description": "La cause racine, en une ou deux phrases."},
        "confidence": {"type": "string", "enum": ["faible", "moyenne", "élevée"]},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "Les faits mesurés qui appuient la conclusion."},
        "repair": {"type": "array", "items": {"type": "string"}, "description": "Les actions de réparation, dans l'ordre."},
        "references": {"type": "array", "items": {"type": "string"}, "description": "Passages de la documentation cités."},
        "tests_done": {"type": "array", "items": {"type": "string"}, "description": "Identifiants des enregistrements utilisés."},
    },
    "required": ["root_cause", "confidence", "evidence", "repair", "references", "tests_done"],
    "additionalProperties": False,
}

ToolResult = str | list[dict[str, Any]]


def test_key(name: str, args: dict[str, Any]) -> str:
    """Identifie un test pour retrouver sa référence, ex. "joint_test:delta_deg=5:joint=1".

    Les arguments sont triés par nom : le même test donne toujours la même clé.
    """
    return ":".join([name, *(f"{k}={args[k]}" for k in sorted(args))])


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Définition d'un outil commun au format de l'API (objet JSON fermé)."""
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False},
    }


class Toolbox:
    """Exécute les outils de l'agent sur une machine, et garde trace de ce qui a été fait.

    Attributes:
        report: rapport remis par `submit_report` (None tant que l'enquête n'est pas finie).
        used_recordings: identifiants des enregistrements produits pendant l'enquête.

    Args:
        connector: la machine.
        recorder: lecteur des flux, déjà démarré.
        safety: garde-fous appliqués à chaque action de la machine.
        baselines_dir: dossier des références.
        docs_cache: dossier du cache d'indexation de la documentation.
        budget_tokens: budget d'un tableau de valeurs envoyé à l'agent.
    """

    def __init__(
        self, connector: Connector, recorder: Recorder, safety: SafetyPolicy, baselines_dir: Path, docs_cache: Path, budget_tokens: int
    ) -> None:
        """Indexe la documentation de la machine (une seule fois, grâce au cache)."""
        self.connector = connector
        self.recorder = recorder
        self.safety = safety
        self.baselines_dir = baselines_dir
        self.budget = budget_tokens
        self.machine_tools: dict[str, ToolSpec] = {t.name: t for t in connector.tools()}
        docs = connector.docs()
        self.docs = DocSearch(build(docs, docs_cache)) if docs else None
        self.report: dict[str, Any] | None = None
        self.used_recordings: list[str] = []
        log.info("boîte à outils prête : %d action(s) machine, %d document(s)", len(self.machine_tools), len(docs))

    def definitions(self) -> list[dict[str, Any]]:
        """Définitions de tous les outils, au format de l'API : communs, puis ceux de la machine."""
        common = [
            _tool("get_status", "État actuel de la machine (mode, sécurité, programme).", {}),
            _tool(
                "get_recent_logs",
                "Lignes du journal des dernières `seconds` secondes, au niveau `min_level` ou plus grave.",
                {"seconds": {"type": "number"}, "min_level": {"type": "string", "enum": LEVELS}},
                ["seconds", "min_level"],
            ),
            _tool(
                "get_recent_faults",
                "Codes d'erreur émis pendant les dernières `seconds` secondes.",
                {"seconds": {"type": "number"}},
                ["seconds"],
            ),
            _tool(
                "search_docs",
                "Cherche dans la documentation de la machine (manuels). Renvoie les passages avec leur page, à citer dans le rapport.",
                {"query": {"type": "string"}},
                ["query"],
            ),
            _tool("list_baselines", "Tests pour lesquels une référence de bon fonctionnement existe.", {}),
            _tool(
                "get_recording",
                "Valeurs d'un enregistrement en CSV. `recording_id` : un identifiant renvoyé par un test, ou "
                '"latest:N" pour les N dernières secondes des flux (sans bouger la machine). `channels` vide : '
                "toutes les voies qui varient. `hz` : rythme voulu (0 = le plus fin possible dans le budget).",
                {"recording_id": {"type": "string"}, "channels": {"type": "array", "items": {"type": "string"}}, "hz": {"type": "number"}},
                ["recording_id", "channels", "hz"],
            ),
            _tool(
                "plot_recording",
                "Graphique des voies demandées (8 au plus), superposé à la référence quand elle existe.",
                {"recording_id": {"type": "string"}, "channels": {"type": "array", "items": {"type": "string"}}},
                ["recording_id", "channels"],
            ),
            {
                "name": "submit_report",
                "description": (
                    "Termine l'enquête et remet le rapport final. À appeler une seule fois, quand la cause est établie "
                    "ou que les tests possibles sont épuisés."
                ),
                "input_schema": REPORT_SCHEMA,
                "strict": True,
            },
        ]
        machine = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in self.machine_tools.values()]
        return common + machine

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Exécute un outil demandé par l'agent.

        Returns:
            Le contenu du `tool_result` : du texte, ou une liste de blocs (texte et images).
            Un refus des garde-fous est rendu comme un texte commençant par « REFUSÉ », pour
            que l'agent puisse s'adapter.

        Raises:
            ValueError: si l'outil n'existe pas.
            KeyError, FileNotFoundError: si un enregistrement ou une voie demandés n'existent pas.
        """
        log.info("outil %s %s", name, json.dumps(args, ensure_ascii=False))

        if name in self.machine_tools:
            spec = self.machine_tools[name]
            try:
                self.safety.check(spec, args)
            except SafetyRefusal as exc:
                return f"REFUSÉ : {exc}"
            key = test_key(spec.name, args)
            rec, outcome = self.recorder.record(lambda: self.connector.call_tool(spec.name, args), label=key, meta={"test": key})
            self.used_recordings.append(rec.id)
            return self.describe_test(rec, outcome)

        match name:
            case "get_status":
                return json.dumps(self.connector.snapshot(), ensure_ascii=False)

            case "get_recent_logs":
                rec = self.recorder.latest(float(args["seconds"]))
                floor = LEVELS.index(args.get("min_level", "INFO"))
                lines = [
                    f"{time.strftime('%H:%M:%S', time.localtime(x.t))} {x.level.value} [{x.source}] {x.text}"
                    for x in rec.logs
                    if LEVELS.index(x.level.value) >= floor
                ]
                return "\n".join(lines[-200:]) or "aucune ligne à ce niveau sur cette période"

            case "get_recent_faults":
                rec = self.recorder.latest(float(args["seconds"]))
                lines = [f"{time.strftime('%H:%M:%S', time.localtime(f.t))} {f.code} {f.level.value} {f.message}" for f in rec.faults]
                return "\n".join(lines) or "aucun code d'erreur sur cette période"

            case "search_docs":
                if self.docs is None:
                    return "aucune documentation disponible pour cette machine"
                hits = self.docs.search(args["query"], k=4)
                return "\n\n".join(f"[{p.cite()}]\n{p.text[:1200]}" for _, p in hits) or "aucun passage trouvé"

            case "list_baselines":
                return "\n".join(Baseline.available(self.baselines_dir, self.connector.name)) or "aucune référence enregistrée"

            case "get_recording":
                rec = self._load(args["recording_id"])
                names = args.get("channels") or context.informative_channels(rec)
                if args.get("hz"):
                    text = context.to_csv(rec, names, float(args["hz"]))
                    if context.estimate_tokens(text) <= self.budget:
                        return text
                text, rate = context.csv_within_budget(rec, names, self.budget)
                return f"# {rec.id}, {rec.label}, {rec.duration:.1f} s, rythme {rate:.0f} Hz (origine {rec.rate_hz:.0f} Hz)\n{text}"

            case "plot_recording":
                rec = self._load(args["recording_id"])
                names = args.get("channels") or context.informative_channels(rec)[:8]
                image = context.plot(rec, names, self._reference_recording(rec))
                return [{"type": "text", "text": f"graphique de {rec.id}"}, context.image_block(image)]

            case "submit_report":
                self.report = dict(args)
                log.info("rapport remis : %s (confiance %s)", self.report.get("root_cause"), self.report.get("confidence"))
                return "rapport reçu"

        raise ValueError(f"outil inconnu : {name}")

    def describe_test(self, rec: Recording, outcome: str) -> list[dict[str, Any]]:
        """Ce que l'agent reçoit après un test.

        Trois blocs : (1) un texte avec le bilan, les écarts à la référence, les coupures du
        flux, les codes d'erreur et les avertissements du journal pendant le test ; (2) le
        graphique des voies les plus en écart (ou des voies qui varient, sans référence) ;
        (3) les valeurs complètes des voies qui varient, au rythme permis par le budget.
        """
        parts = [f"Enregistrement {rec.id} ({rec.duration:.1f} s, {len(rec.t)} points à {rec.rate_hz:.0f} Hz) : {outcome}"]
        baseline = Baseline.load(self.baselines_dir, self.connector.name, rec.meta.get("test", ""))
        deviations = checks.compare(rec, baseline) if baseline else []
        if baseline is None:
            parts.append("Pas de référence pour ce test : comparer aux autres articulations ou aux valeurs attendues.")
        elif deviations:
            parts.append("Écarts par rapport à la référence :\n" + "\n".join("- " + d.describe() for d in deviations[:12]))
        else:
            parts.append("Aucun écart supérieur à 15 % par rapport à la référence.")
        gaps = checks.stream_gaps(rec)
        if gaps:
            parts.append(f"Interruptions du flux de signaux : {[(round(a, 2), round(b, 2)) for a, b in gaps[:10]]} (début s, durée s)")
        if rec.faults:
            parts.append("Codes d'erreur pendant le test :\n" + "\n".join(f"- {f.code} {f.message}" for f in rec.faults))
        warnings = [x for x in rec.logs if x.level in (Level.WARNING, Level.ERROR, Level.FATAL)]
        if warnings:
            parts.append("Journal (avertissements et erreurs) :\n" + "\n".join(f"- {x.level.value} {x.text}" for x in warnings[:30]))
        log.info(
            "test %s : %s ; %d écart(s), %d coupure(s), %d code(s) d'erreur", rec.id, outcome, len(deviations), len(gaps), len(rec.faults)
        )

        blocks: list[dict[str, Any]] = [{"type": "text", "text": "\n\n".join(parts)}]
        if len(rec.t):
            to_plot = [d.channel for d in deviations[:6]] or context.informative_channels(rec)[:6]
            blocks.append(context.image_block(context.plot(rec, to_plot, self._reference_recording(rec))))
            csv, rate = context.csv_within_budget(rec, context.informative_channels(rec), self.budget)
            blocks.append({"type": "text", "text": f"Valeurs complètes (voies qui varient, {rate:.0f} Hz) :\n{csv}"})
        return blocks

    def _load(self, recording_id: str) -> Recording:
        """Relit un enregistrement, ou crée et sauvegarde celui des N dernières secondes pour "latest:N"."""
        if recording_id.startswith("latest:"):
            rec = self.recorder.latest(float(recording_id.split(":", 1)[1]), label="flux récents")
            rec.save(self.recorder.directory)
            self.used_recordings.append(rec.id)
            return rec
        return self.recorder.load(recording_id)

    def _reference_recording(self, rec: Recording) -> Recording | None:
        """Enregistrement de la référence du même test, pour la tracer ; None s'il n'y en a pas."""
        baseline = Baseline.load(self.baselines_dir, self.connector.name, rec.meta.get("test", ""))
        if baseline is None:
            return None
        try:
            return self.recorder.load(baseline.recording_id)
        except FileNotFoundError:
            log.warning("enregistrement de référence %s introuvable", baseline.recording_id)
            return None

    def capture_baseline(self, name: str, args: dict[str, Any]) -> Baseline:
        """Enregistre la référence d'un test, sur une machine que l'on sait en bon état.

        Raises:
            SafetyRefusal: si le test sort des bornes ou si l'opérateur refuse.
        """
        spec = self.machine_tools[name]
        self.safety.check(spec, args)
        key = test_key(name, args)
        rec, _ = self.recorder.record(lambda: self.connector.call_tool(name, args), label=f"référence {key}", meta={"test": key})
        baseline = Baseline.from_recording(rec, key)
        baseline.save(self.baselines_dir)
        return baseline
