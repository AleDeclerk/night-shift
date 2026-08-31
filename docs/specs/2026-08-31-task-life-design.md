# The life of a task — design

> Date: 2026-08-31. Status: approved.

## 1. Purpose

The page shows what the night shift found. It does not let the user answer it.
An item appears in `Pendiente` and stays there for ever: the system records no
verb, so nothing ever closes, and nothing can be counted.

This phase gives an item a life. Five verbs close it or send it back, and every
one of them writes an event. The weekly board of the next phase reads those
events, so it needs no new machinery.

## 2. The verbs

Four verbs close an item, and one returns it to the queue.

| Verb | What it means | What it leaves |
| --- | --- | --- |
| **Listo** | The draft is good. The user sends it from Gmail. | `done`, reason `draft_used` |
| **Lo hago yo** | The user answers by hand. | `done`, reason `by_hand` |
| **No era nada** | The triage was wrong to raise it. | `dismissed`, reason `false_alarm` |
| **Mañana** | Not today. | `snoozed`, back on the next cycle |
| **Rehacer** | The draft is no good. Write it again. | `pending`, and the compose runs again |

**`No era nada` earns its place above the others.** It is the only verb that
tells the system it was wrong. Today nothing measures whether the triage
classifies well. With this verb, two weeks of use give the real count of false
alarms, and the prompt can then change on evidence instead of on impressions.

## 3. Events, and one source of truth

A new table holds one row for each thing that happens.

```sql
CREATE TABLE IF NOT EXISTS events (
  id       INTEGER PRIMARY KEY,
  at       TEXT NOT NULL,
  kind     TEXT NOT NULL,   -- item_found | draft_written | item_closed |
                            -- job_queued | job_done | job_failed | cycle_ran
  item_id  INTEGER,
  job_id   INTEGER,
  verb     TEXT,            -- the verb, when a person closed something
  engine   TEXT,            -- which engine did the work
  cost_usd REAL NOT NULL DEFAULT 0,
  detail   TEXT
);
```

The state of an item is stored as well, in a column. A page that replays every
event to draw one list is slow, and it is hard to read. But the events are
the record: a state that disagrees with them is a bug, and a test proves they
agree.

## 4. What the user sees

The `Pendiente` panel gains a row of buttons under each item. Nothing else
moves: the board keeps its shape, and the machine room stays where it is.

A closed item leaves `Pendiente` and appears in `Ya revisado`, with the verb
that closed it and the hour. A snoozed item says when it comes back.

The jobs gain three of their own: answer the question of a stopped job, cancel
it, and run it again on another engine.

## 5. Hard rules

1. **No verb sends mail.** `Listo` marks the draft as used. The user sends it
   from Gmail. Rule 2 of the first design does not move.
2. **A verb is a fact, and it is written before the page answers.** A button
   that seems to work and stores nothing is worse than a button that fails.
3. **The events never disappear.** A closed item stays in the table. The weekly
   board reads history, so deleting a row would erase the past.
4. **`Rehacer` costs money and says so**, like the probe buttons do.

## 6. Out of scope

Sending mail from the page, editing the draft here, the weekly board, and
anything about the wikis. The board comes next and it is short, because these
events are what it reads. The wikis are a separate project with their own spec.

## 7. Acceptance criteria

- Given an item in `Pendiente`, when the user presses `Listo`, then the item
  moves to `Ya revisado`. An `item_closed` event holds the verb and the hour.
- Given an item, when the user presses `No era nada`, then the event holds the
  reason `false_alarm`, so a later count can measure the triage.
- Given a snoozed item, when the next cycle runs, then the item is in
  `Pendiente` again. The mail that raised it gets no second draft.
- Given a closed item, when a cycle runs, then it stays closed and the same
  message does not come back.
- Given `Rehacer`, when the user presses it, then a new draft is written with
  the engine that the machine room names. The item stays in `Pendiente`.
- Given any verb, when the page answers, then the event is already stored: a
  button never reports work that did not happen.
- Given the events table, when the state of an item is read from it, then it
  matches the stored state.
