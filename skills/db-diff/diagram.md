# The schema diagram

One format, used in three places: `/db-diff`'s report, the preview of the "Approve" option in every schema approval question, and the changes `~/.agents/hooks/schema-guard.py` records when Aakif picks that option. The hook reads this diagram, not the question's wording, so what he approves is exactly the diagram he looked at.

```
branch: aakif/eng-1234-held-offers
repo: envoyai-backend

offer  (existing table, +2 columns, -1 column)
  + held_at               timestamptz      optional   when the offer was held for the team to decide
  + accepted_by_user_id   uuid → user.id   optional   who accepted it
  - legacy_note           text                        nothing has read it since ENG-1111
offer_notices  (new table)
    id                    uuid             required   primary key
    offer_id              uuid → offer.id  required   the offer the notice is about
    sent_at               timestamptz      optional   when the carrier was told
unselected_carrier_notices  (dropped table)
offer_holds  (renamed table, was held_offers)
  + reason                text             optional   why the team held it
```

Rules (the hook parses these lines, so keep the shape):

- `branch: <name>` is required: the branch the approval is recorded for, exactly as git names it (the branch `/pickup` will create, for a planned change). `repo:` defaults to envoyai-backend.
- One header line per table, starting at column 0: `<table>  (new table)`, `(existing table, ...)`, `(dropped table)` or `(renamed table, was <old name>)`. Anything after the kind inside the parentheses is free text for the reader.
- Under an existing or renamed table, each new column is a line starting with `+ <name>` and each dropped one `- <name>`, indented. Unchanged columns may be listed indented with no sign (they are context only).
- A new table's columns are listed indented, sign optional: approving the table covers every column on it, and any column the same branch later adds to it.
- After the name: type (`→ table.column` when it points at another table), required or optional, default when there is one, and what it holds in a few plain words.
- Indexes, constraints, enum values and type changes stay out: they need no approval.
