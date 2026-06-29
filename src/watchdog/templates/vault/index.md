# {name}

*Watchdog investigation vault — created {today}.*

> The tables below are live [Dataview](https://github.com/blacksmithgu/obsidian-dataview) queries. Install and enable the **Dataview** community plugin (Settings → Community plugins → turn on community plugins, then Browse → Dataview → Install → Enable) to render them; without it you'll see the raw query blocks. The tables refresh themselves as you ingest.

## Most-mentioned entities

The people, companies, and addresses appearing in the most documents — a quick read on who is central to the investigation.

```dataview
TABLE type AS "Type", length(appears_in) AS "Documents", date_last_updated AS "Updated"
FROM "entities"
SORT length(appears_in) DESC
LIMIT 20
```

## Recent documents

```dataview
TABLE document_type AS "Type", date_of_document AS "Dated", page_count AS "Pages"
FROM "documents"
SORT date_ingested DESC
LIMIT 10
```

## People

```dataview
TABLE aliases AS "Also known as", length(appears_in) AS "Documents"
FROM "entities/person"
SORT length(appears_in) DESC
```

## Companies

```dataview
TABLE aliases AS "Also known as", length(appears_in) AS "Documents"
FROM "entities/company"
SORT length(appears_in) DESC
```

## Possible duplicates

Documents flagged during chew as near-duplicates of an earlier one.

```dataview
TABLE near_duplicate_of AS "Near-duplicate of"
FROM "documents"
WHERE near_duplicate_of
```
