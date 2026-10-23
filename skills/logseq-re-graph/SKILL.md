---
name: "logseq-re-graph"
description: "Arbeitsregeln für die Spec des Requirements-Tools im Logseq-DB-Graph ReqSeq über den MCP-Server logseq-db: lesen, anlegen, Properties setzen, prüfen."
---

# Arbeit am RE-Graph in Logseq

Gilt für jede Arbeit an Thomas' Requirements-Spec im Logseq-DB-Graph **ReqSeq** über die Tools des MCP-Servers **logseq-db** (Repo `ThomasBirkmaier/logseq-db-mcp`).

## Zu Beginn jeder Session
- Prüfen, welcher Graph offen ist: `call_api` mit `logseq.App.getCurrentGraph`. Für die Spec muss es `logseq_db_ReqSeq` sein. `SpikeTest` ist der Testgraph: dort nichts für die Spec schreiben, in ReqSeq keine Experimente.
- Seite **Konventionen** lesen (`resolve` → `get_node` mit Tiefe 3). Sie regelt Schreibstil, Elemente, Status, IDs, Glossar und Verknüpfungen und hat Vorrang vor allem anderen.
- `get_schema` lesen: Tags mit Vererbung und Klassen-Properties, Properties mit Typ, erlaubten Klassen und Auswahlwerten.
- Bei Verbindungsfehlern („not reachable“): Thomas bitten, Logseq zu öffnen und den HTTP-API-Server über den 🔌-Button zu starten.

## Metamodell
- Tags (Klassen) anlegen oder umbenennen kann nur Thomas, in der Oberfläche.
- Alles andere am Metamodell kann ich, aber **nur nach Thomas' Okay**: `create_property` (Typ, Kardinalität, erlaubte Tags, Auswahlwerte, gleich an Tags hängen), `update_property` (umbenennen, erlaubte Tags, Auswahlwerte ergänzen), `update_tag` (Klassen-Properties, Vererbung). Erst mit `dry_run: true`, Plan zeigen, dann ausführen.
- Typ und Kardinalität einer Property lassen sich nicht ändern, Auswahlwerte nicht entfernen; das macht Thomas in der Oberfläche.

## Lesen
- Elemente einer Klasse: `find_objects` (mit Unterklassen; Filter per `where`, z. B. Requirement Status = Draft). Einzelner Knoten: `get_node`. Titel → UUID: `resolve`.
- Verweise kommen als `{uuid, title, id}`; `id` ist die ID des Ziels (z. B. N-007). Filter nehmen ID, Titel oder UUID.
- `get_backlinks` ist zuverlässig, auch für Verweise über Properties.
- Für Fragen, die kein Tool abdeckt: `query` (Datascript, nur lesend).

## Schreiben
- Neue Elemente mit `create_objects` anlegen: `as_page: true`, Tag und Properties in einem Aufruf, Text als `children`. Erst mit `dry_run: true`, dann echt.
- Verweis-Properties (Status, Derives from, Refines, Based on, Supersedes) per ID (`"N-007"`), Titel oder UUID setzen. Der Server prüft die erlaubten Klassen und lehnt Falsches ab.
- Werte ändern oder mit `null` entfernen: `update_objects`. Mehrfachwerte werden ersetzt, nicht ergänzt.
- Jeder Schreibaufruf meldet `verified` und `problems`. Bei `problems` oder einem Fehler anhalten und Thomas melden.
- Nächste freie ID: höchste vorhandene ID der Klasse per `find_objects` (sortiert nach ID) plus eins. IDs werden nie wiederverwendet.
- `[[Links]]` nur auf bestehende Seiten setzen. Der Server lehnt Links auf unbekannte Titel ab; das nicht mit `create_missing_links` umgehen, sondern zuerst den Glossarbegriff anlegen.
- Neuer Glossarbegriff: `create_objects` mit Tag Glossary Term, Definition als erster Block, Bereich (Fachlich oder Tool-Entwicklung), dazu nach Bedarf Synonyme, Oberbegriff, Siehe auch, Abgrenzung. Abgrenzung in beiden Richtungen setzen.

## Prüfen und Löschen
- `validate_objects` nach jeder größeren Änderung. Leer bleiben dürfen (`optional`): bei Decision Supersedes; bei Glossary Term alles außer Bereich.
- Eigene, gerade erzeugte Fehler darf ich sofort entfernen (`delete_nodes` mit `confirm: true`).
- `delete_nodes` legt Seiten nur in den Papierkorb; der Server blendet sie danach aus. Den Papierkorb leert nur Thomas.
- Inhaltliche Löschungen und Umstrukturierungen erst vorschlagen und nach Thomas' Okay ausführen. Requirements werden nicht gelöscht, sondern auf Obsolete gesetzt.
- Vor größeren Umbauten den Graph-Ordner `~/logseq/graphs/ReqSeq` nach `~/logseq/backups-manuell/` kopieren (nicht unter `~/logseq/graphs/`, sonst sieht Logseq die Kopie als Graph).

## Antworten an Thomas
- Kurz, auf Deutsch, per Du. Ergebnis zuerst, dann nur was er tun muss oder was auffällig war.
- Befunde, die für das spätere Requirements-Tool relevant sind (z. B. Schwächen des Werkzeugs), kurz als Befund benennen.
