# Robot Agentic — agent de diagnostic

Agent d'intelligence artificielle qui remonte à la **cause racine** d'une panne de machine à partir de ses quatre sources de données : **signaux**, **journal**, **codes d'erreur** et **documentation**. Il formule des hypothèses, les vérifie par de petits tests encadrés sur la machine, puis remet un rapport : la cause, les preuves, la réparation.

Phase 1 : validation sur **URSim**, le simulateur officiel Universal Robots (UR3e). Phase 2 : bras Waveshare RoArm-M2-S (mode actif) et pince ampèremétrique MotorScope (mode passif).

## Architecture

Un **cœur** identique pour toutes les machines, et un **connecteur** par machine. Brancher une nouvelle machine revient à écrire un connecteur qui respecte `core/interfaces.py` : l'agent ne change pas.

```
src/robot_agentic/
├── core/                  le cœur, indépendant de la machine
│   ├── interfaces.py      contrat Connector : signals(), logs(), faults(), docs(), tools()
│   ├── events.py          types communs : Sample, LogLine, FaultCode, Alert, ToolSpec
│   ├── recorder.py        lit les flux en continu, enregistre chaque test (avant, pendant, après)
│   ├── baseline.py        référence de bon fonctionnement, par test
│   ├── checks.py          détection déterministe : alertes, écarts à la référence, coupures
│   └── safety.py          garde-fous : bornes des tests, accord de l'opérateur, code PIN
├── agent/
│   ├── graph.py           graphe LangGraph : surveiller → accord → enquêter → rapport
│   ├── tools.py           outils de l'agent (communs + ceux du connecteur)
│   ├── modes.py           loop (surveillance) · check (contrôle ponctuel)
│   ├── context.py         données envoyées au LLM : écarts, graphiques, valeurs complètes
│   ├── llm.py             appels à Claude, coût et journal des appels
│   └── prompts/system.md  consignes de l'agent
├── connectors/
│   ├── ursim/             simulateur UR : temps réel (30003), primaire (30001), dashboard (29999), URScript (30002), journal
│   ├── fake/              machine simulée en Python, pour les tests
│   ├── roarm/             bras RoArm-M2-S (phase 2)
│   └── motorscope/        pince + ESP32 (phase 2)
├── docs/                  indexation et recherche dans les manuels (BM25)
└── hmi/cli.py             interface terminal
tools/
├── fault_injection/       pannes provoquées sur URSim + scénarios attendus
└── healthcheck.py         vérifie que tout est prêt
benchmarks/run.py          diagnostics justes, temps, coût sur les scénarios
tests/                     tests automatiques (faux connecteur, faux LLM : sans réseau ni coût)
```

### Ce que l'agent reçoit des signaux

Après chaque test, l'agent reçoit **l'enregistrement complet** sous trois formes :

1. les **écarts à la référence** calculés de façon déterministe, pour orienter sa lecture ;
2. un **graphique** des voies qui s'écartent, superposées à la référence : les LLM repèrent mieux une anomalie sur une image que dans une suite de nombres ([Zhou & Yu, ICLR 2025](https://arxiv.org/abs/2410.05440)) ;
3. les **valeurs**, en tableau, au rythme le plus fin que permet le budget `RECORDING_TOKEN_BUDGET` (environ 120 Hz pour un test de 4 s). L'outil `get_recording` redonne une voie précise à pleine résolution (500 Hz).

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # puis renseigner ANTHROPIC_API_KEY
```

URSim (Docker) :

```bash
docker run -d --name ursim -e ROBOT_MODEL=UR3 \
  -p 5900:5900 -p 6080:6080 -p 29999:29999 -p 30001-30004:30001-30004 \
  universalrobots/ursim_e-series
# interface PolyScope : http://localhost:6080/vnc.html
```

Documentation : déposer les manuels (PDF) dans `manuals/ursim/`, par exemple le *Service Manual* et la liste des codes d'erreur de Universal Robots. Ils ne sont pas versionnés.

## Utilisation

```bash
.venv/bin/python -m tools.healthcheck --api        # tout est prêt ?
.venv/bin/robot-agentic --yes baseline --standard  # références (machine en bon état)
.venv/bin/robot-agentic check                      # contrôle ponctuel
.venv/bin/robot-agentic check "le bras force à la montée"
.venv/bin/robot-agentic loop                       # surveillance continue
```

`--yes` accepte automatiquement les tests : à réserver au simulateur. Sur une vraie machine, chaque test est soumis à l'opérateur (et à `OPERATOR_PIN` s'il est défini).

### Pannes provoquées et benchmarks

```bash
.venv/bin/python -m tools.fault_injection.inject list
.venv/bin/python -m tools.fault_injection.inject wrong_payload
.venv/bin/python -m tools.fault_injection.inject wrong_payload --revert
.venv/bin/python -m benchmarks.run                 # tous les scénarios (appels payants)
```

| Panne | Ce qui se passe | Comment l'agent est alerté |
|---|---|---|
| `wrong_payload` | charge déclarée à 2 kg, outil vide | aucune alerte : contrôle ponctuel |
| `script_error` | programme URScript invalide | journal (erreur de syntaxe) |
| `protective_stop` | arrêt de protection | code d'erreur C209 |
| `controller_crash` | processus du contrôleur tué | flux coupés |
| `freeze` | contrôleur gelé 8 s | coupure du flux de signaux |

### Tests

```bash
.venv/bin/python -m pytest
```

## Données produites

Dans `data/` (non versionné) : enregistrements (`recordings/`), références (`baselines/`), rapports (`reports/`), journal des appels à Claude avec tokens et coût (`llm_calls.jsonl`).
