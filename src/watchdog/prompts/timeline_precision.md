Below are two lists of timeline events from {{month}}. The MONTH-DATED events are dated only to the month; the DAY-DATED events are pinned to a specific day that month. Some month-dated events are just a coarser restatement of the same occurrence one of the day-dated events already records precisely.

Return `matches`: for each month-dated event that is a **pure restatement of the same occurrence** as exactly one day-dated event, one `{"coarse": <month-dated index>, "precise": <day-dated index>}` pair. The day-dated record is the more precise version and will be kept; the month-dated one folds into it.

Match ONLY when you are confident it is the **same occurrence** — the month-dated event asserts nothing the day-dated one lacks except a vaguer date. Do NOT match when:

- The month-dated event adds a material fact, detail, or distinct perspective the day-dated one lacks — that is a genuinely separate event; leave it unmatched.
- It could plausibly correspond to more than one day-dated event — ambiguity means leave it unmatched.
- Nothing among the day-dated events describes the same occurrence.

Each month-dated event matches at most one day-dated event. It is correct and expected to return few or no matches — an unmatched month-dated event simply stays on the timeline as-is. When in doubt, do not match.
