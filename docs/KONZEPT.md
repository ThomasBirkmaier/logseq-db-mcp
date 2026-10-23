# Konzept: DB-nativer MCP-Server für Logseq

Stand 2026-10-23: **Phase 1 und 2 umgesetzt** (Abschnitt 7.2), die Befunde der API stehen in [API-BEFUNDE.md](API-BEFUNDE.md). Ursprünglich verfasst vor dem Bau, als Arbeitstitel `logseq-db-mcp`. Grundlage: Code des Forks `ThomasBirkmaier/mcp-logseq` (ergut 1.10.0 + `call_api`), lesende Sonden gegen den Graphen `ReqSeq`, die Plugin-API in `logseq/logseq` (`libs/src/LSPlugin.ts`, master) und die Projekte unter „Quellen“. Annahmen sind als **Annahme** markiert. Was erst ein Spike gegen einen Testgraphen klärt, steht in Abschnitt 8.

---

## 1. Ziel und Abgrenzung

- **Nur DB-Graphen.** Kein Dateimodus, keine `key:: value`-Syntax, kein Markdown als Datenmodell.
- Die Begriffe des Servers sind die des DB-Graphen: **Knoten** (Seite oder Block), **Tag/Klasse** mit Vererbung und Klassen-Properties, **Property** mit Typ und Kardinalität, **typisierte Werte**.
- Generisch, nicht RE-spezifisch. Regeln des Requirements-Graphen (welche Klassen, welche Pflicht-Properties) bleiben im Skill `logseq-re-graph`. Der Server liefert nur die Werkzeuge, mit denen der Skill sie prüfen kann (`validate_objects`).
- Lokal, stdio zuerst. HTTP-Transport ist optional und kommt später.

## 2. Ausgangslage: was es schon gibt

| | Eingebauter Logseq-MCP | ergut / Fork | LoveSkylark/mcp-logseq-db |
|---|---|---|---|
| Ort | in Logseq, `http://127.0.0.1:12315/mcp` | Python, HTTP-API | Python, HTTP-API |
| DB-nativ | ja | nachgerüstet (`if db_mode`) | ja |
| Umfang | 6 Tools | 17 + `call_api` | etwa 50 |
| Schreiben | `upsertNodes`: nur `title`, `tags`, `page-id`; keine Property-Werte; laut Issues 16 s bis 140 s pro Aufruf | Markdown-Text, Properties als Text oder per Ident | mit Read-back-Prüfung; schreibbar laut README nur `plugin.property.<id>/*`, also **nicht** deine `user.property/*` |
| Reife | Version 0.1.0; offener PR #13622 baut ihn auf 53 Tools aus (Autor = LoveSkylark, ungeprüft, kein Review) | aktiv gepflegt, 720 Tests | 0 Sterne, Installation nur unter Windows erprobt, Lizenz unklar |

Gefunden in deiner Konfiguration: `LOGSEQ_API_URL` endet auf `/mcp`. ergut ignoriert den Pfad und spricht `/api` an. Der eingebaute MCP-Endpunkt weist `Host: localhost` ab („Invalid Host header“) und antwortet nur auf `127.0.0.1`.

**Folgerung:** Keines der drei passt. Der eingebaute Server ist zu knapp und schreibt zu langsam. ergut denkt in Markdown. mcp-logseq-db ist DB-nativ, aber unreif. Seine README sagt, deine Properties seien nicht beschreibbar; das hat der Spike widerlegt (API-BEFUNDE, F4). Die HTTP-API schreibt `user.property/*`-Werte, wenn man das volle Ident übergibt. **Damit trägt der eigene Server.**

Von mcp-logseq-db übernehmen wir **Ideen, keinen Code** (die Lizenz ist unklar): jeden Schreibvorgang zurücklesen und prüfen, Schreibaufrufe nie wiederholen, Links beim Import maskieren statt Stub-Seiten anzulegen, Titel nie stillschweigend auflösen.

## 3. Datenmodell

### 3.1 Begriffe

| Begriff | Im Graphen | Im Server |
|---|---|---|
| Knoten | Entität mit `:block/uuid`. Seite, Block, Journal, Tag und Property sind alles Knoten | `Node` |
| Tag / Klasse | Knoten mit Tag `Tag`, Ident `:user.class/…`, `extends`, Klassen-Properties | `Tag` |
| Property | Knoten mit Ident `:user.property/…`, Typ, Kardinalität `one`/`many`, optional erlaubte Klassen oder feste Auswahlwerte | `Property` |
| Wert | je nach Typ Literal oder Verweis auf einen Knoten | `Value` |
| Objekt | Knoten mit mindestens einem eigenen Tag, zum Beispiel ein `System Requirement` | `Node` mit `tags` |

In ReqSeq heute: 9 eigene Klassen, zum Beispiel `Requirements Engineering Element` mit den Unterklassen `Need`, `Use Case` und `System Requirement` (Klassen-Properties `RequirementType`, `Derivesfrom`, `Basedon`, `Rationale`), dazu `Decision` mit `DecisionStatus` und `Supersedes`.

### 3.2 Identität

- Intern immer **UUID**. `db/id` ist nur innerhalb eines Aufrufs gültig und wird nie ausgegeben.
- Titel werden nur über das ausdrückliche `resolve` in UUIDs übersetzt. Mehrdeutige Titel ergeben eine Liste mit Kandidaten, keine stille Wahl (Pages, Tags und Properties teilen sich einen Titelraum).
- Schreib-Tools nehmen nur UUIDs. Ausnahme: neu anzulegende Knoten im selben Aufruf, die per temporärer ID (`"tmp:…"`) referenziert werden, wie bei `upsertNodes`.

### 3.3 Ausgabeform (kompaktes JSON)

```json
{
  "uuid": "6ad36bc6-…",
  "kind": "block",
  "title": "Das System muss [[Need: Bremsen]] erfüllen",
  "page": {"uuid": "…", "title": "Systemanforderungen"},
  "tags": [{"uuid": "…", "title": "System Requirement"}],
  "properties": {
    "Requirement Type": {"type": "node", "value": {"uuid": "…", "title": "Funktional"}},
    "Derives from":     {"type": "node", "value": [{"uuid": "…", "title": "Need: Bremsen"}]},
    "Rationale":        {"type": "default", "value": "…"}
  },
  "children": []
}
```

- Properties erscheinen unter dem **Anzeigenamen**; das Ident (`:user.property/Basedon-HudGiQsf`) gibt es nur auf Wunsch (`include_idents`).
- Verweise im Titel (`[[uuid]]`) werden als `[[Titel]]` ausgegeben.
- Eingebaute Properties (`:logseq.property/*`) bleiben ausgeblendet, außer den nützlichen: Status/Priorität der Aufgaben, Journal-Datum, Alias und Icon.
- `children` gibt es nur bis zu der Tiefe, die der Aufruf anfordert, damit Antworten klein bleiben.

### 3.4 Typisierte Werte

Geschrieben wird immer mit `upsertBlockProperty(uuid, <volles Ident>, Wert, Optionen)`. Gespeichert wird so, wie der Spike es zeigt (F4):

| Property-Typ | Eingabe im Tool | Schreiben an Logseq | Gespeichert als | Ausgabe |
|---|---|---|---|---|
| `default` (Text) | String | String | **Wert-Block** (eigene Entität, Kind des Blocks, `created-from-property`) | String |
| `number` | Zahl | Zahl | ebenfalls Wert-Block | Zahl |
| `checkbox` | bool | bool | Literal | bool |
| `url` | String, geprüft | String | Wert-Block | String |
| `date` | ISO-Datum | Entitäts-ID der Journal-Seite, `entityId: true`; Journal vorher mit `createJournalPage` anlegen | Verweis aufs Journal (UUID `00000001-JJJJ-MMTT-…`) | ISO-Datum |
| `datetime` | ISO-Zeitpunkt; der Server rechnet um | Epoch-Millisekunden als Zahl; ein ISO-String wird abgelehnt („should be a datetime“) | Literal (ms) | ISO-Zeitpunkt |
| `node` | UUID(s), optional `{"title": …}` mit Prüfung der erlaubten Klassen | Entitäts-ID(s), `entityId: true` | Verweis | `{uuid, title}` |
| `node`, `many` | Liste | Liste von Entitäts-IDs | Liste von Verweisen | Liste |
| feste Auswahl (closed values) | Titel des Auswahlwerts | Entitäts-ID mit `entityId: true` oder der Titel als String; beides trifft den vorhandenen Auswahlwert, ohne einen neuen anzulegen | Verweis auf den Auswahlwert | Titel |

Bei Text, Zahl und URL hängt der Wert als Block unter dem Block, der die Property trägt. Die Gliederungs-Tools blenden solche Wert-Blöcke deshalb aus, außer `include_value_blocks` ist gesetzt. Ein Datum aus der Oberfläche liefert `getBlockProperties` als nackte ID (`216`), ein über die API gesetztes als Text („Jan 15th, 2030“). Gelesen wird deshalb über Datascript und nicht über `getBlockProperties`.

Die Kardinalität `many` erwartet immer eine Liste. Gesetzt wird der Wert nur, wenn er zum Typ der Property passt. Eine Prüfung im Server erspart Fehlschreibungen, die die API stumm schlucken würde.

### 3.5 Schema-Cache

`SchemaCache` liest Tags und Properties einmal (`getAllTags`, `getAllProperties`) und hält die Zuordnungen Name ↔ Ident ↔ UUID, Typ, Kardinalität, erlaubte Klassen, Auswahlwerte und die Vererbung. Neu geladen wird nach jedem eigenen Schema-Schreibvorgang und wenn ein Name nicht gefunden wird. **Annahme:** Ein Zeitstempel oder eine `tx-id` erlaubt eine billige Prüfung auf Aktualität; `listTags` des eingebauten Servers liefert `tx-id`.

## 4. Werkzeugliste

Etwa 25 Tools, in snake_case. **R** = lesend, **W** = schreibend (fällt bei `--read-only` weg). Phase siehe Abschnitt 7.

### Schema

| Tool | | Zweck | Phase |
|---|---|---|---|
| `get_schema` | R | Eigene Tags mit Vererbung und Klassen-Properties, Properties mit Typ, Kardinalität, Auswahl; optional eingebaute | 1 |
| `create_tag` | W | **Nicht möglich** (API-BEFUNDE F10): Die API legt nur `plugin.class`-Tags an; Tags legt man in der Oberfläche an | – |
| `update_tag` | W | Klassen-Properties und Vererbung; über `:logseq.property.class/properties` direkt (I16), weil `addTagProperty` scheitert | 3, erledigt (0.3.0) |
| `create_property` | W | Property mit Typ, Kardinalität, erlaubten Tags, Auswahlwerten, über `upsertNodes` als `user.property` | 3, erledigt (0.3.0) |
| `update_property` | W | Umbenennen (I20), erlaubte Tags (I17), Auswahlwerte ergänzen | 3, erledigt (0.3.0) |
| `update_property` | W | Name, erlaubte Klassen, Auswahlwerte ändern. Typwechsel nur mit `force` | 3 |

### Objekte (Knoten mit Tag)

| Tool | | Zweck | Phase |
|---|---|---|---|
| `find_objects` | R | Objekte eines Tags, **inklusive Unterklassen**, gefiltert nach Property-Werten (gleich, enthält, leer/nicht leer, Verweis auf), sortiert, begrenzt; intern Datascript | 1 |
| `create_objects` | W | Ein oder mehrere Objekte: Tag(s), Titel, Properties, Ort (eigene Seite oder Block unter einem Knoten); mit `dry_run` | 2 |
| `update_objects` | W | Properties setzen/leeren, Titel ändern; mehrere Objekte in einem Aufruf | 2 |
| `tag_nodes` / `untag_nodes` | W | Tags hinzufügen/entfernen | 2 |
| `validate_objects` | R | Prüft Objekte eines Tags gegen das Schema: fehlende Klassen-Properties, falscher Typ, Verweis auf eine unerlaubte Klasse, verwaiste Verweise | 3 |

### Gliederung (Seiten und Blöcke)

| Tool | | Zweck | Phase |
|---|---|---|---|
| `resolve` | R | Titel → UUID-Kandidaten mit Art (Seite, Tag, Property, Block) | 1 |
| `get_node` | R | Seite oder Block mit Properties, Tags und Kindern bis Tiefe n | 1 |
| `get_backlinks` | R | Knoten, die auf einen Knoten verweisen, auch über Property-Werte | 1 |
| `create_page` | W | Seite anlegen, optional mit Tags und Properties | 2 |
| `insert_blocks` | W | Blockbaum als JSON einfügen; Position: erstes/letztes Kind, vor/nach | 2 |
| `update_block` | W | Text eines Blocks ändern | 2 |
| `move_blocks` | W | Blöcke verschieben, Reihenfolge bleibt erhalten | 2 |
| `rename_page` | W | Seite umbenennen | 2 |
| `delete_nodes` | W | Löschen, nur mit `confirm: true`; meldet vorher, was an Verweisen verloren geht | 2 |

### Journale

| Tool | | Zweck | Phase |
|---|---|---|---|
| `get_journal` | R | Journal eines Datums (Standard: heute) | 1 |
| `add_to_journal` | W | Blöcke an ein Journal anhängen, legt es bei Bedarf an (`createJournalPage`) | 2 |

### Suche und Abfrage

| Tool | | Zweck | Phase |
|---|---|---|---|
| `search` | R | Volltextsuche, Treffer als kompakte Knoten | 1 |
| `query` | R | Datascript-Abfrage, nur lesend. Ergebnis begrenzt; `pull` mit Standardmuster, damit Antworten lesbar bleiben | 1 |

### Notausgang

| Tool | | Zweck | Phase |
|---|---|---|---|
| `call_api` | W | Übernommen aus dem Fork, unverändert in Sperrliste und Zugriffsregel | 1 |

**Bewusst nicht dabei:** Markdown-Import ganzer Seiten (kommt höchstens später als `import_markdown`, mit maskierten Links), Vektorsuche (sie braucht Dateien, ein DB-Graph liegt in SQLite) und Namespace-Werkzeuge (Namespaces spielen im DB-Graphen eine andere Rolle; **Annahme**, nicht vertieft).

## 5. Schreibsicherheit

- **Read-back:** Jeder Schreibvorgang liest das Ergebnis zurück und meldet `verified: true/false`. Die API meldet oft Erfolg ohne Wirkung (Spike F6, F8): `updateBlock` auf eine unbekannte UUID, `insertBlock` ohne Anker und `insertBatchBlock` ohne Anker antworten alle mit `null` und HTTP 200.
- **Ziele vor dem Schreiben prüfen:** Jede UUID wird vorher gelesen. Property-Schlüssel kommen nur aus dem `SchemaCache`, denn ein unbekannter Schlüssel legt stillschweigend eine neue Property `:plugin.property._test_plugin/<Schlüssel>` an (F8).
- **Keine Wiederholung von Schreibaufrufen.** Nach einem Timeout wird nicht erneut geschrieben, sondern der Zustand gelesen und gemeldet.
- **`dry_run`** bei allen zusammengesetzten Schreib-Tools: Der Server prüft Schema, Typen und Ziele und zeigt den Plan, ohne zu schreiben.
- **Nicht atomar:** Die HTTP-API kennt keine Transaktionen. Mehrteilige Schreibvorgänge melden bei einem Abbruch genau, was schon geschrieben ist.
- **Zugriffsregeln, DB-nativ statt Namespaces:**
  - `--read-only`
  - `LOGSEQ_WRITE_TAGS`: Schreiben nur an Objekten dieser Tags
  - `LOGSEQ_WRITE_PAGES`: Schreiben nur auf diesen Seiten
  - `LOGSEQ_HIDE_TAGS`: Objekte dieser Tags werden nie gelesen
  - Für `call_api` gilt weiter: gesperrt, sobald eine Regel gesetzt ist.
- **Audit-Zeile** je Schreibvorgang (Tool, UUIDs, nie Inhalte), wie im Fork.

## 6. Was wir aus dem Fork übernehmen

ergut steht unter MIT-Lizenz; übernommene Teile behalten den Copyright-Hinweis (LICENSE + NOTICE).

| Übernehmen | Quelle im Fork | Anpassung |
|---|---|---|
| Tool-Basisklasse, zentrale Zugriffsprüfung vor `_run`, Audit-Zeile | `tools/base.py`, `access.py` (`AccessPolicy`) | Policies neu: Tag- und Seitenregeln statt Namespaces |
| Dispatch: jsonschema-Prüfung, Fehler als lesbares Ergebnis statt Protokollfehler | `server.py` (`_dispatch_tool_call`, `_error_result`) | unverändert |
| Settings: lazy, geprüft, beim Import ohne Seiteneffekte | `settings.py` | ohne `db_mode` |
| HTTP-Client | `logseq.py` (`_call`, `call_api`) | als `client.py`, nur das Gerüst |
| Auflösung der Property-Idents, Batch-Auflösung von Titeln | `logseq.py` (`resolve_property_ident`, `_resolve_idents_batch`, `_resolve_titles_batch`, `get_blocks_db_properties`) | wandert in `SchemaCache` und `model.py` |
| `[[uuid]]` → `[[Titel]]` | `tools/base.py` (`_UUID_REF_PATTERN`, `_resolve_block_refs`) | `refs.py`, in beide Richtungen |
| `call_api` samt Gate und Tests | `tools/api.py`, `access.RawApiCall`, `tests/unit/test_call_api.py` | unverändert |
| CLI mit `--read-only`; Logging nach stderr; optional Log-Datei | `__init__.py` | Optionen ausgedünnt |
| Testmuster: HTTP-API mit `responses` mocken; Tabelle, die jedem Tool seine Zugriffsregel vorschreibt | `tests/unit/test_access_policy_coverage.py` | übernommen |
| Später: HTTP-Transport mit Bearer-Token | `transport/` | erst Phase 3 |

**Nicht übernommen:** `parser.py` (Markdown → Blöcke), alle Zweige für den Dateimodus, `vector/`, `namespace.py`, `find_pages_by_property`, `query` (Logseq-DSL), Schichten für Rückwärtskompatibilität (Re-Exports für `patch()`-Ziele).

## 7. Projektstruktur und Phasen

### 7.1 Struktur

Repo `ThomasBirkmaier/logseq-db-mcp` (privat), Ordner `Coding/Logseq DB MCP`. Umgesetzt, gegenüber dem Entwurf leicht umbenannt:

```
logseq-db-mcp/
├── pyproject.toml          # uv, hatchling; Abhängigkeiten: mcp, requests, jsonschema
├── README.md  CHANGELOG.md  LICENSE  NOTICE  CLAUDE.md
├── docs/
│   ├── KONZEPT.md          # dieses Dokument
│   └── API-BEFUNDE.md      # was die HTTP-API wirklich tut
├── src/logseq_db_mcp/
│   ├── __init__.py         # CLI: --read-only, --log-file, --log-level
│   ├── server.py           # Registry, Dispatch, Fehler im Ergebnis, Audit-Zeile
│   ├── settings.py
│   ├── client.py           # /api und /mcp, EDN-Eingaben, keine Wiederholung
│   ├── schema.py           # Schema, SchemaCache
│   ├── graph.py            # Entitäten, Gliederung, JSON-Form (statt model.py)
│   ├── values.py           # Typumwandlung je Property-Typ, ein und aus
│   ├── refs.py             # [[uuid]] ↔ [[Titel]], Linkprüfung
│   ├── access.py           # Hide-Tags, Write-Seiten/-Tags, call_api-Sperre
│   ├── writer.py           # prüfen, schreiben, zurücklesen (statt verify.py)
│   └── tools/  base.py  read.py  write.py  raw.py
├── tests/
│   ├── unit/               # ohne Logseq: Werte, Filter, Server, Snapshots aus fixtures/
│   ├── live/               # Marker "live": schreibt in SpikeTest, verweigert andere Graphen
│   └── fixtures/           # aufgezeichnete echte Antworten
├── scripts/record_fixtures.py
└── spike/                  # Phase 0
```

### 7.2 Phasen

| Phase | Inhalt | Stand |
|---|---|---|
| **0 Spike** | Offene Punkte gegen den Testgraphen SpikeTest klären | erledigt, `docs/API-BEFUNDE.md` |
| **1 Lesen** | Gerüst, Client, Schema-Cache, Graph; Tools `get_schema`, `resolve`, `get_node`, `find_objects`, `get_backlinks`, `get_journal`, `search`, `query`, `call_api` | **erledigt** |
| **2 Schreiben** | Werte, Read-back, Zugriffsregeln; `create_page`, `insert_blocks`, `update_block`, `move_blocks`, `rename_page`, `delete_nodes`, `create_objects`, `update_objects`, `tag_nodes`, `untag_nodes`, `add_to_journal` | **erledigt**; ersetzt den Fork in Claude Desktop |
| **3 Schema und Prüfung** | `validate_objects`, Verweise per ID, Schema-Tools | **erledigt**: `validate_objects` und Verweise per ID (0.2.0), `create_property`, `update_property`, `update_tag` (0.3.0); HTTP-Transport, `import_markdown` offen |

## 8. Offene Punkte und Risiken

Die Punkte des Spikes (`user.property` schreibbar, typisierte Werte, `insertBatchBlock`, Tags, `moveBlock`, Antwortzeiten) sind in Abschnitt 9 beantwortet. Offen bleiben:

1. ~~Klassen aus der Oberfläche (`user.class`) über die API ändern~~: geklärt (API-BEFUNDE I15 bis I20). Bis auf das Anlegen eines Tags lässt sich das ganze Schema über die API pflegen; die Schema-Tools dafür sind noch nicht gebaut.
2. Dauer von `upsertNodes` auf größeren Graphen (Issues: 16 s bis 140 s; im Testgraphen 30 ms bis 50 ms). Betrifft nur `create_property`.
3. ~~Leerer erster Block von `createPage`~~: erledigt, der Server entfernt ihn bei eigenen Seiten (API-BEFUNDE I4).
4. **API-Drift:** Der DB-Modus ist Beta, und `LSPlugin.ts` auf master enthält mehr als das veröffentlichte `@logseq/libs` auf npm. Gegenmittel: Contract-Tests und ein Live-Skript, das nach jedem Logseq-Update läuft.
5. **Konkurrenz durch Logseq selbst:** Wird PR #13622 übernommen, wächst der eingebaute Server stark. Der Client ist deshalb eine eigene Schicht (`client.py`), damit er notfalls auf den eingebauten MCP als Rückgrat umsteigen kann.
6. Die Plugin-Kennung `_test_plugin` stammt vermutlich vom HTTP-API-Server und nicht aus einer Einstellung (**Annahme**). Ändert Logseq sie, wechselt der Namespace aller über die API angelegten Properties.

## 9. Befunde

Die Befunde des Spikes und des Baus stehen gepflegt in [API-BEFUNDE.md](API-BEFUNDE.md), samt den Folgen für den Server.

## Quellen

- [ergut/mcp-logseq](https://github.com/ergut/mcp-logseq) (MIT), Fork: [ThomasBirkmaier/mcp-logseq](https://github.com/ThomasBirkmaier/mcp-logseq)
- [LoveSkylark/mcp-logseq-db](https://github.com/LoveSkylark/mcp-logseq-db): DB-natives Python-Projekt, Ideen zu Read-back und Grenzen der API
- [logseq/logseq PR #13622](https://github.com/logseq/logseq/pull/13622): Ausbau des eingebauten MCP-Servers (offen)
- [logseq/logseq Issue #13372](https://github.com/logseq/logseq/issues/13372): `upsertNodes` dauert 60 s bis 140 s auf großen Graphen
- [e-zz/logseq Issue #4](https://github.com/e-zz/logseq/issues/4): `upsertNodes` nimmt nur `title`, `tags`, `page-id`
- [logseq/db-test Issue #1455](https://github.com/logseq/db-test/issues/1455): `upsertNodes` kann Page Tags nicht schreiben
- Plugin-API: `libs/src/LSPlugin.ts` in [logseq/logseq](https://github.com/logseq/logseq) (master)
