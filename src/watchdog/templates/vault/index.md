# {name}

*Watchdog investigation vault — created {today}.*

## Recent documents

```dataview
TABLE document_type, date_of_document
FROM "documents"
SORT date_ingested DESC
LIMIT 10
```

## Entities

```dataview
TABLE type, aliases
FROM "entities"
SORT date_last_updated DESC
```
