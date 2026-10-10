You are tidying the relationship labels of an investigation's entity graph. Each document named a relationship between two entities in its own words, so the same relationship can arrive under several wordings. Below, each numbered pair (P1, P2, …) is one entity relating to another, in one direction: the first entity is the subject, the second the object, so "P1: Ada Brook (person) → Fenwick Lowe LLP (organization)" with the wording "lawyer at" reads "Ada Brook is lawyer at Fenwick Lowe LLP". Each wording is numbered and shows how many documents used it.

Your job, for each pair: find the wordings that describe exactly the same relationship between these two entities, and group them. For each group, pick as `canonical` the number of the member wording that a reader would find clearest; you cannot invent a new wording. Give a short `reason`.

Group wordings only when they say the same thing in different words:

- "lawyer at" and "counsel with" for a person and a law firm: the same relationship (a lawyer working at the firm). Group them.
- "owner of" and "registered owner of" for a company and a property: the same relationship. Group them.
- "chief executive of" and "CEO of": the same relationship. Group them.
- A rank or title within the same role, at the same body, with nothing in the wordings marking a change: "senior partner at" and "partner at", or "lead counsel for" and "counsel for", usually name the same relationship. Group them; each document's own wording stays visible under the group, so the rank is not lost.

Never group wordings that describe different relationships, even when they are close:

- Different positions or ranks: "partner at" and "articling student at"; "director of" and "president of"; "chair of" and "member of".
- Opposite sides or directions: "counsel for" and "counsel against"; "lender to" and "borrower from"; "buyer of" and "seller of".
- A different scope or strength: "subsidiary of" and "affiliate of"; "shareholder of" and "owner of"; "employee of" and "contractor to".
- A change over time that the wording marks: "former director of" and "director of"; "interim chair of" and "chair of".

When you are not sure two wordings name the same relationship, keep them apart. A wrongly joined pair hides a distinction the reporter needs, and the reporter can only see it by opening the documents; a missed grouping costs one extra line on the screen.

Return `pairs`: one entry for each pair that has at least one group, with `pair` (the pair's number, 1 for P1) and `groups` (each with `labels`, the numbers of two or more of that pair's wordings, `canonical`, one of those numbers, and `reason`). A wording belongs to at most one group. Leave out pairs with nothing to group; an empty `pairs` list is a correct answer.
