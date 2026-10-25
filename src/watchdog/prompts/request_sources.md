Each numbered item below is a place a reporter could get documents from, as an investigation's document requests name it: a registry, an archive, an office, a court, a company. The same place is often named in different ways: by its official name, by a description of it, by the department that holds the records, or by a service that resells them. The reporter wants one list of requests per place, so that one letter or one search covers them all.

For each item, give a short `label` that names the place it means. Give items that name the same place the same label, written exactly the same way; give an item that names a place no other item does a label of its own.

Return `answers`: one `{"item": <number>, "label": "..."}` per item.

- A label is the place's own name where an item gives it ("Land Registry Office"), at most a few words, with no explanation.
- Give two items the same label only when a request to one is a request to the other: the same office, registry or company, under another name or a description of it, or a service that gives access to that same registry's records.
- Never join different places: two courts, two registries in different places, a ministry and an agency under it, a company and its lawyer, a city and one of its departments when the requests are addressed to the department, a public registry and a private company that also holds the records.

When you are not sure two items name the same place, give them different labels. A wrong grouping sends a request to the wrong place; a missed one costs the reporter one extra list.
