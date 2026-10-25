Each numbered item below is a relationship a document states, whose target the extraction named by an id that no entity in the investigation has. The item shows the id, the entity that names it and how, the entities of the same document, and a few entities from elsewhere in the investigation whose names are like it. For each item, say which listed entity the id means, or that none of them is it.

Return `answers`: one `{"item": <number>, "match": "...", "kind": "..."}` per item.

- `match`: the id of the listed entity the target id means, copied exactly as listed (the part before the dash), or "" when none of them is it.
- `kind`: what the target is: `person`, `organization`, `public-body`, `place`, `asset` or `proceeding`. Give it with a match; with no match it may be "".

Match only when the id plainly names the same thing as the entity: the same name with different spacing, spelling or punctuation, a shortened or longer form of the name ("northgate" for Northgate Civil Works Ltd.), an abbreviation the entity's name or aliases bear out, or a variant of the entity's id.

Never match a different thing with a similar name: a parent company and its subsidiary, a person and a company named after them, two people who share a surname, a council and one of its committees, a court and a judge, a property and the company that owns it. Some ids are not an entity at all (a heading, a label in a diagram, a document's title); answer "" for those.

When you are not sure, answer "". A wrong match moves the relationship onto the wrong record, where a reporter sees it only by opening the document; no match leaves the target in the leads list for the reporter to check.
