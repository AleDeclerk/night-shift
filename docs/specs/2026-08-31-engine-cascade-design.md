# The engine cascade — design

> Date: 2026-08-31. Status: approved.

## 1. Purpose

The system runs on one engine at a time, chosen by hand in the machine room.
When that engine runs out of quota, the work stops.

This phase makes the choice automatic. The work walks down a ladder of engines,
and it takes the first one with room left. The user can still force any step by
hand.

## 2. The ladder

| Step | Command | Model |
| --- | --- | --- |
| 1 | `claude` | Opus 5 |
| 2 | `cursor-agent --model` | `cursor-grok-4.6-high-fast` |
| 3 | `cursor-agent --model` | `gemini-3.7-flash-high` |
| 4 | `dsh --profile headless` | qwen3.8 27B, local |

Steps 2 and 3 share one subscription, so they fall together. Step 4 has no
limit and no cost: it runs on this machine.

**Gemini through a Google account is closed, not missing.** On 2026-06-18
Google stopped serving Gemini CLI to individual accounts, paid ones included,
and it points them at Antigravity, which ships no headless CLI. Only a paid API
key still works there, and rule 1 of this project forbids one. The same model
arrives through Cursor instead.

## 3. What the system can measure, and what it cannot

No command reports how much quota a subscription has left. Not for Claude, not
for Cursor. So the ladder counts **what this system spent**, against a ceiling
that the user sets, and never claims to know the real balance.

- **Claude** reports `total_cost_usd` on every call, so its ceiling is in
  equivalent USD, the unit the governor already uses.
- **Cursor** reports tokens and no cost, so its ceiling counts **calls**. Sixty
  a week to start. A number that needs no translation is worth more here than a
  precise one that does.
- **The local engine** has no ceiling.

## 4. The reserve, and why it changes each day

Cursor spends to its ceiling with no reserve.

Claude keeps **one tenth of its weekly ceiling for each day left before the
reset**. With a ceiling of 20 and a reset on Monday:

| Day | Days left | Reserve | The system may use |
| --- | --- | --- | --- |
| Monday | 6 | 12 | 8 |
| Tuesday | 5 | 10 | 10 |
| Wednesday | 4 | 8 | 12 |
| Thursday | 3 | 6 | 14 |
| Friday | 2 | 4 | 16 |
| Saturday | 1 | 2 | 18 |
| Sunday | 0 | 0 | 20 |

The allowance opens as the week goes on. Early on the system holds back,
because it does not know what is coming. On the last day there is nothing left
to save it for.

This runs over the budget of the system, not over the real subscription. No
command reports what the user spent working, so the margin for their own day is
kept by the size of the ceiling itself.

**The reset is Monday at 00:00.** That is an assumption: Anthropic fixes a day
for each account and reports it nowhere. One function holds it.

## 5. How a step is chosen

For each call, the system walks the ladder from the top and takes the first
step that answers yes to all of these:

1. its engine is installed and holds a session;
2. its last real probe did not fail, when one exists;
3. its spend this week is under its ceiling, minus the reserve of today.

When no step qualifies, the local engine takes the work, because it has no
limit. The choice is written into the event, so the weekly board can show which
engine did what.

**A step that the user forces stays forced.** The machine room keeps its
selectors, and a fixed engine skips the ladder. That is how the user overrides
a bad automatic choice without editing anything.

## 6. Hard rules

1. **Subscriptions only.** No step reads an API key. The local placeholder buys
   nothing and meters nothing.
2. **The mail fetch never moves.** Only Claude holds the Gmail connector, so
   step 1 is the only one that can fetch and save. When Claude has no room, the
   cycle does not fetch: it says so, and it waits for the next day.
3. **A fall is visible.** When the ladder moves down a step, the page says
   which engine did the work and why the one above was skipped.

## 7. Out of scope

Changing the ceiling from the page, a ladder for each kind of work, and any
attempt to read the real balance of a subscription. The first two are easy
later; the third is impossible today.

## 8. Acceptance criteria

- Given Claude under its allowance, when a call runs, then it runs on Claude.
- Given Claude over its allowance for today, when a call runs, then it runs on
  Grok through Cursor.
- Given Cursor over its ceiling of calls, when a call runs, then it runs on the
  local engine.
- Given Monday and a ceiling of 20, when the reserve is read, then the system
  may use 8.
- Given Sunday and a ceiling of 20, when the reserve is read, then the system
  may use 20.
- Given an engine whose last probe failed, when the ladder is walked, then that
  step is skipped.
- Given an engine fixed by hand, when a call runs, then the ladder is not
  walked and the fixed engine takes the work.
- Given a call that fell to a lower step, when the page shows it, then it names
  the engine that worked and the step that was skipped.
- Given the mail fetch and no room on Claude, when the cycle runs, then it
  fetches nothing and it records why.
