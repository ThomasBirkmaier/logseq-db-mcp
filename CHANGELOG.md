# Changelog

## [0.1.0] - 2026-10-23

Erste Version: Phase 1 (Lesen) und Phase 2 (Schreiben) aus `docs/KONZEPT.md`.

### Added

- Lesende Tools: `get_schema`, `resolve`, `get_node`, `find_objects` (inklusive Untertags, Filter, Sortierung), `get_backlinks`, `get_journal`, `search`, `query`
- Schreibende Tools: `create_page`, `insert_blocks`, `update_block`, `move_blocks`, `rename_page`, `delete_nodes`, `create_objects`, `update_objects`, `tag_nodes`, `untag_nodes`, `add_to_journal`
- `call_api` als opt-in Notausgang (`LOGSEQ_ENABLE_CALL_API`), übernommen aus dem Fork von mcp-logseq
- Typisierte Property-Werte in beide Richtungen: Text, Zahl, Checkbox, URL, Datum, datetime, Verweis, Mehrfachwerte, Auswahlwerte
- Jeder Schreibvorgang wird vorher geprüft und danach zurückgelesen (`verified`, `problems`); `dry_run` für zusammengesetzte Tools
- Zugriffsregeln nach Tags und Seiten: `LOGSEQ_HIDE_TAGS`, `LOGSEQ_WRITE_PAGES`, `LOGSEQ_WRITE_TAGS`; `--read-only`
- Offline-Tests mit aufgezeichneten Antworten aus SpikeTest, Live-Tests gegen SpikeTest
