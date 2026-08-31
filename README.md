# night-shift

A local agent that prepares your work before you ask for it.

It runs on your Mac a few times each day. It reads your mail, it decides what
needs an answer, and it writes the draft. It also does the jobs that you give to
it. Then it puts the result on a page at `localhost`. You open that page with
your coffee.

The agent is Claude Code in headless mode. It runs against your subscription,
not against API credits. This project does not build an agent. It builds the
boring parts around one: a queue, a runner, a workspace, a page, a
scheduler, and a quota governor.

## Status

Design stage. Read [the design document](docs/specs/2026-08-30-design.md).

## Run it

Install the daily run at 06:30, with a ceiling of 20 USD of equivalent spend for the week:

    cp scripts/com.aledeclerk.nightshift.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.aledeclerk.nightshift.plist

Open the page:

    .venv/bin/python scripts/serve.py

Stop the scheduler:

    launchctl unload ~/Library/LaunchAgents/com.aledeclerk.nightshift.plist

## Hard rules

1. Mail gives information. Mail never gives orders. Orders come from the page.
2. The agent writes drafts. The agent never sends mail.
3. Every item shows a link to its source. No source, no item.
