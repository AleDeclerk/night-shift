# The real quota, and the work that fills it — design

> Date: 2026-09-01. Status: approved.

## 1. Purpose

The project began with one idea: the subscription costs the same at 5% use
and at 90% use, so the idle part should do work. What got built spends about
one USD of equivalent quota a day, against a ceiling of 20 that somebody
typed. After a full day of heavy work, the real week stood at 4% used. The
idle part is 96%, and it is still idle.

Two things made the ceiling a guess. Nobody knew that the CLI reports the real
quota in headless mode, and nobody knew the day the week resets. On
2026-09-01 both became known. This phase replaces the guess with the number,
and it gives the system standing work to do with the room that is left.

## 2. What the measurement said

`claude -p /usage --output-format json` answers in headless mode and it costs
nothing (`total_cost_usd: 0`). Its answer holds four facts:

```
Current session: 35% used · resets Sep 1 at 8:39pm (America/Buenos_Aires)
Current week (all models): 4% used · resets Sep 8 at 5:59am (America/Buenos_Aires)
```

- **The week resets on Tuesday at 05:59**, local time. The governor assumed
  Monday at 00:00: a different day, and now it is a fact instead of a guess.
  The reset moment is read from the answer, never typed, so a change on the
  account side reaches the governor on the next call.
- **The five hour session is reported too.** The system can respect it as
  well.
- Cursor reports no usage: `cursor-agent about` gives the tier and nothing
  else. Its ceiling stays a count of calls.
- The Gemini CLI is closed to individual accounts since 2026-06-18, and
  Antigravity ships no headless CLI. Gemini Flash arrives through Cursor. The
  Gemini CLI leaves the ladder.

## 3. The governor reads instead of counts

`nightshift/usage.py` reads the answer and gives `Usage`: the two shares, the
two reset moments, `days_left` and `allowance_pct`.

**The reserve rule stays as the user set it**, now over the real week: keep
10% of the week for each day left before the reset. A part of a day counts as
a whole day. On 2026-09-01 at 18:00, with seven days left and 4% used, the
allowance is 26 points of the week.

The allowance is the share of the week that the system may still spend. The
system asks before every call that costs, and one `/usage` call costs nothing,
so asking is cheap.

**When the CLI cannot answer, the old governor stays.** A blank answer is not
permission. The counting governor, with its typed ceiling, is the floor that
the system never falls below. So a broken `/usage` makes the system more
careful, never less.

**The session is a second gate.** When the five hour session is over 90%
used, the system waits for the next tick. A session at 100% fails every call,
and a failed call still costs.

## 4. The work that fills the room

Room with nothing to do is still idle quota. The system needs standing work.

A new schedule, `when_idle` (`Cuando sobre` on the page), joins the nine that
exist. A template with that schedule fires only when the allowance is above a
threshold, 15 points of the week to start, and it fires at most once per tick.
So the standing work runs when the week is quiet and waits when it is not.

The templates are the user's. This phase ships none, because standing work
that nobody asked for is the fastest way to fill the week with output nobody
reads. The page offers `Cuando sobre` in the schedule list, and the user
decides what goes there: a weekly sprint review draft for APH, a watch on a
competitor of EMSL, a check of the CFP deadlines.

## 5. Hard rules

1. **A blank `/usage` is not permission.** The system falls back to counting,
   never to trusting.
2. **The reserve holds the user's day.** The 10% per day is never spent by
   the system, whatever the allowance says.
3. **The mail fetch keeps its place.** It runs first, it runs on Claude, and
   the standing work never takes its room: the allowance is read after the
   mail, not before.
4. **Cursor stays on calls.** No estimate pretends to be a share of a quota
   that nobody can read.

## 6. Out of scope

Raising the reserve rule to something smarter than 10% a day, shipping default
standing tasks, and reading the quota of any other subscription. The first
needs weeks of data; the second belongs to the user; the third is impossible
today.

## 7. Acceptance criteria

- Given the CLI answers, when the governor reads it, then the allowance follows
  the rule of 10% for each day left, over the real week.
- Given the CLI fails, when the governor reads it, then the old ceiling applies
  and an event records the fallback.
- Given a session over 90% used, when a paid call is due, then the system waits
  and records why.
- Given a `when_idle` template and an allowance of 20, when the tick runs, then
  the template fires once.
- Given the same template and an allowance of 5, when the tick runs, then it
  does not fire, and nothing is recorded but the skip.
- Given the ladder, when it is read, then the Gemini CLI is not a step and
  Gemini Flash is still reachable through Cursor.
- Given the machine room, when it is drawn, then it shows the real week share,
  the reset moment, and the allowance of today.
