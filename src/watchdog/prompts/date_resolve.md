Each numbered item below is a date as a document printed it, which code could not turn into a calendar date on its own, with the fact it dates and the date the document itself bears. For each item, say what calendar date or period the printed date names.

Return `answers`: one `{"item": <number>, "start": "...", "end": "..."}` per item.

- A single date: `start` is it, `end` is "".
- A period ("April to June 2024", "Q1 2020", "2019–2021", "du 3 au 7 juin 2019"): `start` is its first day, month or year and `end` its last, at the same precision the wording gives.
- Nothing: when the wording does not name a date you can place with confidence, `start` and `end` are both "". This is the right answer whenever you are unsure.

Write dates as `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, and keep only the precision the wording gives: "spring 2019" is `2019`, "mai 2020" is `2020-05`. Never add a day or a month the wording does not state.

A date relative to something else ("the following Tuesday", "three days later", "last March") may be placed only when the fact or the document's date says what it is relative to; otherwise answer nothing. Never place a date from today's date.

When an item says its figures can be read two ways, answer one of the two readings only when the fact, the wording or the document's date shows which one is meant (a weekday that fits only one, a sequence that only one keeps in order); otherwise answer nothing.

Do not correct a date that looks wrong. Read it as printed.
