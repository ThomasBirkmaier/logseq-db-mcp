# Spike: Was tut die HTTP-API eines DB-Graphen wirklich?

Phase 0 aus [docs/KONZEPT.md](../docs/KONZEPT.md), Abschnitt 8; Befunde in [docs/API-BEFUNDE.md](../docs/API-BEFUNDE.md). Das Skript `spike.py` führt die Versuche F1 bis F9 aus und schreibt einen Bericht. Es schreibt **nur in einen leeren Testgraphen**:

- Der Name des offenen Graphen muss mit `--graph` übereinstimmen.
- `ReqSeq` wird immer abgewiesen.
- Bei mehr als 30 eigenen Seiten bricht es ab.
- Alles, was es anlegt, heißt `Spike <Lauf-ID> …`. Gelöscht wird nichts.

## Vorbereitung in Logseq

1. Einen neuen **DB-Graphen** anlegen, zum Beispiel `SpikeTest`, und öffnen. Der HTTP-API-Server muss laufen; der Token ist derselbe wie für ReqSeq.
2. **Wichtig für die Kernfrage:** Im Testgraphen in der Oberfläche vier Properties anlegen. Die Namen müssen genau so lauten, der Typ steht in Klammern:
   - `Spike UI Text` (Text)
   - `Spike UI Number` (Number)
   - `Spike UI Node` (Node)
   - `Spike UI Date` (Date)

   Nur so wird geprüft, ob in der Oberfläche angelegte Properties (`user.property/*`) über die API beschreibbar sind. Fehlen sie, läuft der Spike trotzdem, beantwortet diese Frage aber nur indirekt.
3. Den Namen des Graphen durchgeben.

## Lauf

```bash
.venv/bin/python -I spike/spike.py --graph SpikeTest
```

`--preflight` prüft nur lesend die Verbindung, in jedem Graphen.

## Ergebnis

`spike/out/<Zeitstempel>/` (nicht versioniert):

- `report.md`: eine Tabelle je Versuch, Ergebnis `ja`, `nein` oder `unklar`, dazu Details
- `calls.jsonl`: jeder Aufruf mit Argumenten, Antwort und Dauer; wird später zu Fixtures für `tests/contract/`

Die Befunde wandern ausgewertet nach `docs/API-BEFUNDE.md` des neuen Repos.

| Versuch | Frage |
|---|---|
| F0 | Umgebung: Graph, DB-Modus, Erreichbarkeit von `/api` und `/mcp` (nur lesend) |
| F1 | `upsertProperty` mit Schema: welcher Namespace (`user.` oder `plugin.`), wirkt das Schema? |
| F2 | `upsertNodes` des eingebauten MCP: Namespace und Dauer |
| F3 | Properties aus der Oberfläche gefunden? |
| F4 | **Kernfrage:** Werte je Herkunft, Schlüsselform und Typ schreiben und zurücklesen |
| F5 | `createTag`, `tagProperties`, `addTagExtends`, `addBlockTag`, `getTagObjects` mit Unterklassen, Datascript |
| F6 | `insertBatchBlock` im DB-Graphen, `keepUUID`, `customUUID`, Aufwand für 20 Blöcke |
| F7 | `moveBlock`: before, children, No-op |
| F8 | Meldet die API Fehler oder Erfolg ohne Wirkung? |
| F9 | Antwortzeiten von Datascript |
| F10 | `upsertNodes`: Tags als `user.class`, `class-extends`, `class-properties`, Werte |
| F11 | `datetime`-Werte und feste Auswahlwerte |

`--only F5,F6` führt nur einzelne Versuche aus. `nachprobe.py` enthält die Nachproben zu F10/F11 (schlichter Tag, Seite → Klasse, Auswahlwerte aus Knoten-UUIDs).
