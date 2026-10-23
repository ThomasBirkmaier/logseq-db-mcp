# API-Befunde: Was die HTTP-API eines Logseq-DB-Graphen wirklich tut

Gemessen am 2026-10-23 gegen den leeren DB-Graphen `SpikeTest`, lokal, unter 400 Entitäten; Zeiten gelten nur dafür. Quelle der Versuche F1 bis F11: `spike/spike.py` und `spike/nachprobe.py` (Anleitung in `spike/README.md`). I1 bis I9 kamen beim Bau des Servers dazu und sind durch `tests/live/` abgedeckt. Annahmen sind als **Annahme** markiert.

Nach einem Logseq-Update zuerst `uv run --no-editable pytest -m live` laufen lassen: Die Live-Tests prüfen genau diese Befunde.

## Datenform beim Lesen

Gelesen wird über `logseq.DB.datascriptQuery` mit `pull [*]`; dann hat jede Entität dieselbe Form, egal welche Editor-Methode sie sonst geliefert hätte:

- Kern-Attribute ohne Namespace: `id`, `uuid`, `title`, `ident`, `page`, `parent`, `order`, `tags`, `refs`, `cardinality`, `journal-day`, `closed-value-property`, `created-at`, `updated-at`.
- Alle anderen mit vollem Ident: `:logseq.property/type`, `:logseq.property.class/extends`, `:user.property/Status-AbCd1234`.
- Verweise als `{"id": N}`, Mehrfachwerte als Liste davon.
- Eingaben (`:in`) sind EDN-Strings: `'#uuid "…"'`, `'20310203'`, `'[1 2 3]'`.
- Art eines Knotens: Journal hat `journal-day`; Tag trägt den Tag `:logseq.class/Tag`, Property `:logseq.class/Property`; ein Block hat `page`, eine Seite nicht.
- Wert-Blöcke tragen `:logseq.property/created-from-property`; Auswahlwerte `closed-value-property`. Beide haben `page` und sind trotzdem keine Gliederungs-Blöcke.

## Befunde

| Nr. | Thema | Befund |
|---|---|---|
| F1 | `upsertProperty` mit Schema | Typ und Kardinalität wirken. Ident `:plugin.property._test_plugin/<Schlüssel>`; der Titel ist der Schlüssel, `opts.name` wird **nicht** übernommen. Rund 20 ms je Property |
| F2 | `upsertNodes` (Logseqs eigener MCP, `/mcp`) | Legt `:user.property/<Name>-<Suffix>` an, wie die Oberfläche. 30 ms bis 50 ms. `/mcp` weist `Host: localhost` ab, `127.0.0.1` geht |
| F4 | **Werte in `user.property` schreiben** | **Geht** mit `upsertBlockProperty(uuid, <volles Ident>, Wert, Optionen)`, für Properties aus der Oberfläche und aus `upsertNodes`. Text, Zahl, Checkbox, URL, Datum, datetime, Verweis, Mehrfachverweis und Auswahl bestätigt |
| F4 | Speicherform | Text und URL als Wert-Block mit dem Text im `title`, Zahl als Wert-Block mit `:logseq.property/value`; Checkbox und datetime als Literal; Datum, Verweise und Auswahl als Verweis |
| F4 | Korrektur | „Spike UI Date“ war eine **datetime**-Property; der Spike schrieb dort die Zahl 216 als Zeitstempel. Der Lesevergleich über `getBlockProperties` hat das verdeckt. Deshalb liest der Server nur über Datascript |
| F5 | Tags über die API | `createTag` legt `:plugin.class._test_plugin/…` an. `tagProperties` beim Anlegen, `addTagExtends` und `addBlockTag` wirken. `getTagObjects` nimmt Ident oder UUID, nicht den Namen („Not a tag“), und liefert auch Objekte der Unterklassen |
| F5 | Klassen in Datascript | `[?b :block/tags ?t]` mit `:in $ [?t …]` und der Menge aller Untertags (in Python aus `extends` berechnet, beliebig tief) |
| F6 | `insertBatchBlock` | Übernimmt Kinder und Properties, aber Property-Schlüssel landen im Plugin-Namespace; `keepUUID` mit `properties.id` wirkt nicht (`id` wird eine Text-Property). Der Server fügt deshalb einzeln mit `insertBlock` + `customUUID` ein |
| F7 | `moveBlock` | Ohne Optionen hinter das Ziel; `before: true` vor das Ziel; `children: true` als **erstes** Kind. Dieselbe Verschiebung zweimal: keine Änderung, kein Fehler |
| F8 | **Erfolg ohne Wirkung** | `updateBlock` auf eine unbekannte UUID, `insertBlock`/`insertBatchBlock` mit Anker `null`: alle `null`, HTTP 200. Ein unbekannter Property-Schlüssel legt still eine neue Plugin-Property an |
| F9 | Antwortzeiten | Datascript 10 ms bis 25 ms; `pull [*]` über alle Blöcke 22 ms |
| F10 | Tags über `upsertNodes` | Scheitert **immer** („The Imported EDN has 1 validation error(s)“, ohne Details), auch für einen einzelnen Tag mit schlichtem Titel. Der Dry-Run meldet trotzdem Erfolg. Fehler kommen als Text „API Error: …“, nicht mit `isError` |
| F10 | Seite → Klasse | `addBlockTag(<Seite>, #Tag)` abgelehnt („Can't set tag with built-in #Tag“). Über die API gibt es **keinen Weg zu `user.class`** |
| F10 | `addTagProperty` | Scheitert an API-Klassen mit jeder Kombination aus UUID, Ident und Name („Not a valid tag“) |
| F11 | `datetime` | Nur Epoch-Millisekunden; ein ISO-String wird abgelehnt („should be a datetime“) |
| F11 | Auswahlwerte | `addPropertyValueChoices(property, [UUIDs])` will UUIDs vorhandener Knoten, keine Texte. Die Knoten selbst werden zu Auswahlwerten. Auswählen per Entitäts-ID (`entityId: true`) oder per Titel, ohne dass ein neuer Wert entsteht |
| I1 | Auswahlwerte sind keine Seiten mehr | Eine Seite, die mit `addPropertyValueChoices` zum Auswahlwert wurde, bekommt `page` und `parent` = die Property. Sie taucht danach nicht mehr als Seite auf und kann kein Verweisziel mehr sein |
| I2 | `renamePage` | Erwartet im DB-Graphen die **UUID** der Seite, nicht den alten Titel („Invalid page uuid“) |
| I3 | `moveBlock` mehrfach mit `before` | Zwei Blöcke nacheinander `before` dasselbe Ziel: Der zweite landet **vor dem ersten**, nicht zwischen erstem und Ziel. Der Server verschiebt deshalb nur den ersten relativ zum Ziel und jeden weiteren hinter seinen Vorgänger |
| I4 | `createPage` | Legt trotz `createFirstBlock: false` einen leeren Block an, sichtbar nach dem ersten Einfügen. Der Server entfernt ihn bei Seiten, die er selbst angelegt hat |
| I5 | `insertBlock` auf eine Seite | `insertBlock(<Seiten-UUID>, Text, {sibling: false, customUUID})` hängt ans Ende der obersten Ebene an, auch bei leerer Seite |
| I6 | Links im Text | `[[titel]]` verlinkt die bestehende Seite, auch bei anderer Groß-/Kleinschreibung, und wird als `[[<uuid>]]` gespeichert. `[[<uuid>]]` geht ebenso. Ein unbekannter Titel würde eine leere Seite anlegen (**Annahme**, nicht ausprobiert); der Server lehnt ihn ab, außer mit `create_missing_links` |
| I7 | `logseq.App.search` | Volltext ohne Groß-/Kleinschreibung über Blöcke inklusive Wert-Blöcke; Treffer mit Markierungen `$pfts_…>$…$<pfts_…$` im Titel. Der Server lädt die Treffer per ID neu und ordnet Wert-Blöcke ihrem Träger zu |
| I8 | Datascript-Funktionen | `clojure.string/includes?` und `starts-with?` funktionieren, achten aber auf Groß-/Kleinschreibung; `missing?` und `get-else` funktionieren |
| I10 | Verweise als Text (ReqSeq) | In ReqSeq sind alle 49 Werte von „Requirement Status“ und „Decision Status“ (Typ `node`) **Text-Wert-Blöcke, deren Text die UUID des Ziels ist**, kein einziger ein echter Verweis; alle Ziele existieren. **Ursache in SpikeTest nachgestellt** (`tests/live/test_text_refs.py`): `upsertBlockProperty(uuid, ident, "<uuid>")` ohne `entityId` legt bei einer `node`-Property ohne Typprüfung einen Text-Wert-Block an. Solche Werte erzeugen keine `:block/refs`, also keine Backlinks. Der Server liest sie als `{uuid, title, stored_as_text: true}`. **Reparatur:** `update_objects` mit demselben Wert schreibt einen echten Verweis; den alten Wert-Block lässt Logseq liegen, der Server löscht ihn, sobald nichts mehr auf ihn zeigt. **In ReqSeq am 2026-10-23 repariert** mit `scripts/repair_text_refs.py` (Sicherung vorher: `~/logseq/backups-manuell/ReqSeq_2026-10-23_vor-Statusreparatur`): 49 Werte, alle zurückgelesen; danach 0 Text-Verweise, 0 verwaiste Wert-Blöcke, Backlinks auf den Statusseiten vorhanden |
| I11 | `upsertNodes` mit `property-classes` | Scheitert mit der UUID einer API-Klasse („An unexpected error occurred building the import“) und mit ihrem Namen („2 validation error(s)“). Erlaubte Klassen lassen sich über die API also nicht setzen |
| I12 | `pull` auf gelöschte Entität | Liefert weiterhin `{"id": N}` statt nichts. Der Server wertet nur Entitäten mit `uuid` als vorhanden |
| I13 | `deletePage` = Papierkorb | Im DB-Graphen löscht `deletePage` nicht: Die Seite bekommt `:logseq.property/deleted-at` und behält Tags, Properties, Kinder und Verweise; Datascript findet sie weiter, ihre Verweise zählen als Backlinks, ihr Titel bleibt belegt. Bemerkt beim ersten Schreibtest in ReqSeq. Der Server blendet Seiten im Papierkorb und Blöcke darauf in allen Listen aus; `get_node` per UUID zeigt sie mit `recycled: true`. Endgültig löschen geht nur in der Logseq-Oberfläche. `removeBlock` für Blöcke löscht dagegen wirklich |
| I14 | Verweis-Filter | `find_objects` vergleicht Verweise mit UUID oder Titel des Ziels, nicht mit dessen `ID`-Property: „Derives from = N-007“ findet nichts, „= Abdeckung einer Auswahl von Requirements“ schon Seit 0.2.0 tragen Verweise die `id` ihres Ziels (`LOGSEQ_ID_PROPERTY`), und Filter und Eingaben nehmen sie an |
| I15 | `addTagProperty` an `user.class` | Scheitert auch an Tags aus der Oberfläche („Not a valid tag“), ebenso `removeTagProperty` (`spike/nachprobe_ui_tag.py`) |
| I16 | **Klassen-Properties direkt setzen** | **Geht:** `upsertBlockProperty(<Tag-UUID>, ":logseq.property.class/properties", [Property-IDs], {entityId: true})` hängt Properties an einen Tag, an `user.class` wie an API-Klassen. Es ergänzt nur; `removeBlockProperty(<Tag>, ":logseq.property.class/properties")` leert die Liste. Eine bestimmte Menge setzen heißt also: leeren, dann neu setzen. Das Schema des Servers sieht sie danach als Klassen-Properties |
| I17 | **Erlaubte Klassen setzen** | **Geht:** `setPropertyNodeTags(<Property-UUID>, [Tag-Entitäts-IDs])` setzt `:logseq.property/classes` einer node-Property. Korrigiert I11, das nur `upsertNodes` betraf |
| I18 | `upsertNodes` mit `edit` | Für Seiten, Tags und Properties nicht unterstützt („Editing a page, tag or property isn't supported yet“); nur `add` geht |
| I19 | Vererbung an `user.class` | `addTagExtends` und `removeTagExtends` wirken auch an Tags aus der Oberfläche |
| I20 | Property umbenennen | `renamePage(<Property-UUID>, <neuer Titel>)` benennt eine Property um; das Ident bleibt, Werte bleiben erhalten |
| I21 | Tags über die API | `createTag` legt `:plugin.class._test_plugin/…` an (F5). In der Oberfläche lässt sich so ein Tag wie ein eigener bearbeiten und umbenennen (von Thomas in SpikeTest geprüft); Umbenennen behält das Ident. `renamePage` mit der Tag-UUID benennt um, auch bei `user.class`. Klassen-Properties sind eine Menge: Die angegebene Reihenfolge bleibt nicht erhalten |
| I9 | Plugin-Kennung | Alles, was über die HTTP-API angelegt wird, landet unter `_test_plugin`. **Annahme:** Die Kennung kommt vom HTTP-API-Server selbst; ändert Logseq sie, wechselt der Namespace neuer API-Properties |

## Was daraus im Server folgt

- Werte immer mit vollem Ident aus dem Schema-Cache schreiben; ein unbekannter Name ist ein Fehler, bevor irgendetwas geschrieben wird (F8).
- Jeder Schreibvorgang wird vorher vollständig geprüft, dann einzeln ausgeführt und danach über Datascript zurückgelesen (`verified`, `problems`). Schreibaufrufe werden nie wiederholt.
- Blöcke einzeln mit `insertBlock` und eigener UUID einfügen (F6, I5); Properties und Tags danach setzen.
- Mehrfachwerte ersetzen statt ergänzen: erst `removeBlockProperty`, dann setzen.
- Schema über die API: Properties anlegen (`upsertNodes`, F2), umbenennen (I20), an Tags hängen (I16), erlaubte Klassen setzen (I17), Vererbung (I19), Auswahlwerte (F11). Nur Tags als `user.class` anlegen geht nicht (F10).
