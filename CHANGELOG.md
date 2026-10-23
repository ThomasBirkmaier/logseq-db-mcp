# Changelog

## [0.4.0] - 2026-10-23

### Added

- `create_tag`: Tags anlegen, mit Vererbung und Klassen-Properties. Das Ident liegt im Plugin-Namensraum; in der Oberfläche verhalten sich solche Tags wie eigene (API-BEFUNDE I21)
- `update_tag` kann Tags umbenennen (`title`), auch Tags aus der Oberfläche

### Changed

- Die Aussage „Tags anlegen geht nicht“ war zu streng: Es geht, nur nicht im Namensraum `user.class`. README, Konzept und Skill korrigiert

## [0.3.0] - 2026-10-23

### Added

- Schema-Tools: `create_property` (als `user.property`, mit Typ, Kardinalität, erlaubten Tags, Auswahlwerten und Zuordnung zu Tags), `update_property` (umbenennen, erlaubte Tags, Auswahlwerte ergänzen), `update_tag` (Klassen-Properties und Vererbung, auch bei Tags aus der Oberfläche). Grundlage: API-BEFUNDE I15 bis I20; nur Tags anlegen bleibt in der Oberfläche

## [0.2.1] - 2026-10-23

### Fixed

- Der Schema-Cache lädt sich neu, sobald ein Knoten eine Property trägt, die er noch nicht kennt. Vorher blieben Properties, die nach dem Start angelegt wurden (in der Oberfläche oder über einen anderen Client), unsichtbar, bis `get_schema` aufgerufen wurde; auch `validate_objects` übersah sie. Bemerkt beim Glossar-Schema in ReqSeq

## [0.2.0] - 2026-10-23

### Added

- `validate_objects`: Objekte einer Klasse gegen das Schema prüfen (leere Klassen-Properties, falsche Typen, unerlaubte Tags, verwaiste und gelöschte Ziele, als Text gespeicherte Verweise, doppelte IDs); `optional` nimmt Klassen-Properties aus, die leer bleiben dürfen (Logseq kennt keine Pflichtfelder)
- Verweise über die ID-Property (`LOGSEQ_ID_PROPERTY`, Standard `ID`): Ausgabe mit `id`, Angabe als `"N-007"`, Filter in `find_objects`
- `scripts/repair_text_refs.py`: als Text gespeicherte Verweise reparieren

### Fixed

- Als Text gespeicherte Verweise (UUID im Text-Wert-Block) werden als Verweis gelesen; Überschreiben räumt den alten Wert-Block weg
- Seiten im Papierkorb (`deletePage`) und Blöcke darauf erscheinen nicht mehr in Listen und Suchen
- Das installierte Paket wird bei Codeänderungen neu gebaut (`[tool.uv] cache-keys`)

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
