# logseq-db-mcp

MCP-Server für **Logseq-DB-Graphen** (SQLite-Graphen von Logseq 2.x). Er arbeitet in den Begriffen des DB-Graphen statt in Markdown:

- **Knoten:** Seiten, Blöcke, Journale
- **Tags (Klassen):** mit Vererbung und Klassen-Properties
- **Properties:** mit Typ, Kardinalität und Auswahlwerten
- **Typisierte Werte:** Text, Zahl, Datum, Verweis und so weiter

Er spricht mit der lokalen HTTP-API von Logseq. Für Dateigraphen (Markdown) ist er nicht gedacht.

Warum es ihn gibt und wie er aufgebaut ist: [docs/KONZEPT.md](docs/KONZEPT.md). Was die API wirklich tut und wo sie stillschweigend scheitert: [docs/API-BEFUNDE.md](docs/API-BEFUNDE.md).

## Einrichten

1. In Logseq unter *Settings → Features* den **HTTP APIs server** einschalten, starten und einen Token anlegen.
2. In `~/Library/Application Support/Claude/claude_desktop_config.json` eintragen und Claude Desktop neu starten:

```json
{
  "mcpServers": {
    "logseq-db": {
      "command": "uv",
      "args": ["run", "--directory", "/Pfad/zu/Logseq DB MCP", "--no-editable", "logseq-db-mcp"],
      "env": {
        "LOGSEQ_API_TOKEN": "…",
        "LOGSEQ_API_URL": "http://127.0.0.1:12315"
      }
    }
  }
}
```

`--no-editable` ist auf macOS nötig: Python 3.12.13 überspringt `.pth`-Dateien, die macOS als „hidden“ markiert, und eine editierbare Installation findet ihr Paket dann nicht. Nach Codeänderungen muss Claude Desktop neu starten; `uv run` baut das Paket dann neu, weil `src/**/*.py` in `[tool.uv] cache-keys` steht. Ohne diesen Eintrag liefe der alte Stand weiter, weil uv nur auf `pyproject.toml` achtet.

`--read-only` (an `args` anhängen) bietet nur die lesenden Tools an.

### Umgebungsvariablen

| Variable | Bedeutung |
|---|---|
| `LOGSEQ_API_TOKEN` | Pflicht. Token des HTTP-API-Servers |
| `LOGSEQ_API_URL` | Standard `http://127.0.0.1:12315`; ein Pfad wird ignoriert, `localhost` wird zu `127.0.0.1` |
| `LOGSEQ_HIDE_TAGS` | Kommaliste. Knoten mit diesen Tags, oder auf Seiten mit diesen Tags, werden nie gelesen oder geschrieben. `query` ist dann gesperrt |
| `LOGSEQ_WRITE_PAGES`, `LOGSEQ_WRITE_TAGS` | Kommalisten. Sobald eine gesetzt ist, wird nur auf diesen Seiten oder an Knoten mit diesen Tags geschrieben |
| `LOGSEQ_ENABLE_CALL_API` | `true` schaltet `call_api` frei; gesperrt, solange eine Zugriffsregel gesetzt ist |
| `LOGSEQ_API_CONNECT_TIMEOUT`, `LOGSEQ_API_READ_TIMEOUT` | Sekunden, Standard 3 und 15 |
| `LOGSEQ_LOG_LEVEL`, `LOGSEQ_LOG_FILE` | Logging nach stderr, optional zusätzlich in eine Datei. Inhalte werden nie geloggt, nur Toolnamen, Argumentnamen und bei Schreibvorgängen die UUIDs |

## Tools

**Lesen**

| Tool | Zweck |
|---|---|
| `get_schema` | Tags mit Vererbung, eigenen und geerbten Klassen-Properties und Objektzahl; Properties mit Typ, Kardinalität, erlaubten Tags und Auswahlwerten |
| `resolve` | Titel → UUIDs (Seiten, Journale, Tags, Properties, auf Wunsch Blöcke), exakte Treffer zuerst |
| `get_node` | Knoten mit Tags, typisierten Properties und Gliederung bis `depth` |
| `find_objects` | Objekte eines Tags inklusive Untertags, mit Filtern (`eq`, `ne`, `contains`, `empty`, `not_empty`, `gt`, `gte`, `lt`, `lte`), Sortierung und Seiten |
| `get_backlinks` | Wer verweist auf einen Knoten: Links im Text, Property-Werte, Tags |
| `get_journal` | Journal eines Datums (Standard heute) |
| `search` | Volltext ohne Groß-/Kleinschreibung; Treffer in Property-Werten liefern den Block, der den Wert trägt |
| `query` | Datascript, nur lesend |

**Schreiben** (fallen bei `--read-only` weg)

| Tool | Zweck |
|---|---|
| `create_page` | Seite mit Tags, Properties und erster Gliederung |
| `insert_blocks` | Blockbaum einfügen: `last_child`, `first_child`, `before`, `after` |
| `update_block` | Text eines Blocks ersetzen |
| `move_blocks` | Blöcke samt Kindern verschieben, Reihenfolge bleibt |
| `rename_page` | Seite umbenennen |
| `delete_nodes` | Blöcke oder Seiten löschen; ohne `confirm: true` nur Bericht, was verloren ginge |
| `create_objects` | Objekte (Knoten mit Tags und Properties) als Seiten oder Blöcke anlegen; meldet leere Klassen-Properties |
| `update_objects` | Titel und Properties setzen oder mit `null` entfernen, mehrere Knoten je Aufruf |
| `tag_nodes`, `untag_nodes` | Tags setzen oder entfernen |
| `add_to_journal` | Blöcke an ein Journal anhängen, legt es bei Bedarf an |
| `call_api` | Notausgang: jede Methode der Plugin-API (opt-in) |

**Werte** werden so angegeben:
- Text, Zahl, `true`/`false`
- Datum als `YYYY-MM-DD`, Zeitpunkt als ISO-Zeit
- Auswahlwerte über ihren Titel
- Verweise über UUID oder Titel
- Listen bei Kardinalität `many`; `null` entfernt den Wert

Der Server prüft jeden Wert gegen das Schema, bevor er schreibt. Bei Verweis-Properties mit erlaubten Tags prüft er auch, ob das Ziel einen davon trägt.

### Wie geschrieben wird

Logseqs API kennt keine Transaktionen und antwortet oft mit `null`, auch wenn nichts passiert ist. Deshalb läuft jeder Schreibvorgang in drei Schritten:

1. **Erst alles prüfen:** Tags, Properties, Werte, Links und Zugriff.
2. **Dann Aufruf für Aufruf schreiben.** Scheitert einer, nennt die Fehlermeldung, was schon geschrieben war.
3. **Zurücklesen.** Das Ergebnis enthält `verified` und `problems`.

Zusammengesetzte Tools kennen `dry_run`. `[[Links]]` auf Seiten, die es nicht gibt, werden abgelehnt, außer mit `create_missing_links: true`. Schreibaufrufe werden nie wiederholt.

### Grenzen

- **Tags und Properties anlegen oder ändern geht nur in der Logseq-Oberfläche.** Über die API entstehen nur Klassen im Plugin-Namespace, und an diese lassen sich später keine Properties hängen (API-BEFUNDE F10). Phase 3 mit `create_property` und `validate_objects` ist noch nicht gebaut.
- Der Server arbeitet mit dem Graphen, der in Logseq gerade offen ist.

## Entwickeln

```bash
uv sync
uv run --no-editable pytest                 # Offline-Tests, ohne Logseq
uv run --no-editable pytest -m live         # gegen Logseq, nur mit dem Testgraphen SpikeTest offen
uv run --no-editable python scripts/record_fixtures.py   # Snapshots neu aufzeichnen (nach einem Live-Lauf)
```

Die Live-Tests schreiben in den Graphen `SpikeTest` und brechen ab, wenn ein anderer Graph offen ist. Sie prüfen die Befunde aus `docs/API-BEFUNDE.md`; nach jedem Logseq-Update zuerst laufen lassen.

## Lizenz

MIT. Teile des Gerüsts stammen aus [ergut/mcp-logseq](https://github.com/ergut/mcp-logseq) (MIT), siehe `NOTICE`.
