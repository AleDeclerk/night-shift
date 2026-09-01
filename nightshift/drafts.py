"""One draft for one item, written the same way wherever it is asked for.

The cycle and the page each held their own copy of these steps, and the two
drifted: the page walked past the ceiling, it dropped the cost of a draft
that failed, and it left the body of the item with no trace of what happened.
One function now holds the order that both of them need.
"""
import datetime as dt

from nightshift import life, mail

# What goes in front of the trace of a draft, inside the body of an item.
MARKS = ("Draft: ", "NO DRAFT. ")


def reason_of(body: str) -> str:
    """The part of a body that the triage wrote, with any older trace cut.

    A person can press `Rehacer` many times. Without this cut the traces
    pile one under the other, and the reason the triage gave sinks out of
    sight.
    """
    for mark in MARKS:
        body = body.split(f"\n\n{mark}")[0]
    return body


def trace(body: str, ok: bool, note: str) -> str:
    """The body of an item: the reason, then what the draft call answered.
    Without the trace an empty draft looks the same as a written one."""
    mark = MARKS[0] if ok else MARKS[1]
    return f"{reason_of(body)}\n\n{mark}{note}"


def for_item(conn, mail_module, runner_module, item, *, engine, cwd, save,
             model: str | None = None,
             now: dt.datetime | None = None) -> tuple[mail.Reply, int]:
    """Write one reply, keep the item, and charge every engine that spent.

    `save(body) -> item_id` belongs to the caller: the cycle inserts a new
    row, the page updates the row it already has. The order is the same for
    both, and it matters. The draft comes first, because an item whose draft
    explodes must not reach the table. The item comes next, because it gives
    the id. The events come last, because they need that id.
    """
    answer = mail.reply(mail_module, runner_module, item, engine=engine,
                        cwd=cwd, model=model)
    item_id = save(trace(item.body, answer.ok, answer.note))
    # One event for each call, so the save that Claude made is charged to
    # Claude and not to the engine that composed. A draft that failed spent
    # all the same, so it leaves its record too.
    kind = "draft_written" if answer.ok else "draft_failed"
    for charged, cost in answer.charges:
        life.record(conn, kind, item_id=item_id, engine=charged,
                    cost_usd=cost, detail=answer.note[:200], now=now)
    return answer, item_id
