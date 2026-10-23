# CLAUDE.md

DB-nativer MCP-Server für Logseq-DB-Graphen, Python, über die lokale HTTP-API. Was er kann: `README.md`. Warum und wie: `docs/KONZEPT.md`. **Bevor du an Lesen oder Schreiben etwas änderst, lies `docs/API-BEFUNDE.md`**: Die API meldet oft Erfolg ohne Wirkung, und viele Methoden verhalten sich anders, als `LSPlugin.ts` sagt.

## Arbeitsweise

- Doku auf Deutsch, Code, Kommentare und Tool-Beschreibungen auf Englisch.
- Neue Erkenntnisse über die API gehören in `docs/API-BEFUNDE.md` (Tabelle, nächste freie Nummer `I…`) und in einen Live-Test.
- Gelesen wird nur über Datascript `pull [*]` (`graph.py`), nicht über `getBlock`/`getBlockProperties`: deren Formen sind uneinheitlich (API-BEFUNDE F4).
- Geschrieben wird nur über `writer.Writer`: erst alles prüfen, dann schreiben, dann zurücklesen. Nie einen Schreibaufruf wiederholen.

## Befehle

```bash
uv sync
uv run --no-editable pytest                 # offline
uv run --no-editable pytest -m live         # schreibt in SpikeTest; verweigert jeden anderen Graphen
uv run --no-editable python scripts/record_fixtures.py
```

`--no-editable` ist auf diesem Mac nötig (versteckte `.pth`-Dateien, siehe README). Tests laden aus `src/` (pytest `pythonpath`), Claude Desktop dagegen das installierte Paket: Ob dort der aktuelle Stand läuft, zeigt `diff -rq --exclude=__pycache__ src/logseq_db_mcp .venv/lib/python3.12/site-packages/logseq_db_mcp`. Live-Tests und Spike **nie gegen ReqSeq**; der produktive Graph des Nutzers ist ReqSeq, der Testgraph SpikeTest. Schreib-Experimente nur in SpikeTest und nur, wenn er offen ist (`logseq.App.getCurrentGraph`).
