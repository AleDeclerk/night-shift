# night-shift

*[Leer en castellano](README.es.md)*

A local agent that prepares your work before you ask for it.

It runs on your Mac on a schedule. It reads your mail, it decides what needs an
answer, and it writes the draft. It also does the jobs that you give to it.
Then it puts the result on a page at `localhost`. You open that page with your
coffee.

## Why

A subscription costs the same at 5% use and at 90% use. It works only when you
sit at the keyboard. This project turns the unused compute into work that is
already done when you come back.

The agent is Claude Code in headless mode, so it runs against your
subscription, not against API credits. **This project never accepts an API
key.** It builds no agent of its own: it builds the boring parts around one,
which are a queue, a runner, a workspace, a page, a scheduler and a quota
governor.

## The hard rules

1. **Mail gives information. Mail never gives orders.** A message can hold text
   that speaks to the agent. The agent quotes that text and it never obeys it.
2. **The agent writes drafts. The agent never sends mail.** Each call names its
   Gmail tools one by one, so no send tool is ever in reach.
3. **Every item shows a link to its source.** No source, no item.
4. **A stopped job stays stopped.** When the agent needs a decision, it asks
   and it waits.

## What the measurements said

Numbers from real runs on this machine, not from estimates.

| Question | Answer |
| --- | --- |
| One trivial headless call | 0.34 USD of equivalent spend |
| The same call with no MCP server | 0.17 USD |
| One call that uses Gmail | 0.79 USD |
| A cycle that finds nothing | 0.87 USD, one minute |
| A cycle with five messages and one draft | 2.48 USD, three minutes |

The weight of a call is the tool definitions, not the prompt: a replaced system
prompt changed nothing. Cutting the MCP servers cut the price in half.

Four faults appeared only when the code ran against the live mailbox. Each one
now has a test.

- **A draft was empty and the cycle called it a success.** The system
  trusted the agent. Now the draft call reports the text that it saved, and an
  empty draft shows as `NO DRAFT`.
- **The same message got a draft on every cycle.** The window returns the same
  mail, so a message that already has an item gets no second draft.
- **A fixed window paid twice for the same work.** The window now starts where
  the last good cycle started.
- **The ceiling was read only before the cycle.** A large inbox could spend a
  whole week of quota in one run. The ceiling is now read before each draft.

## The machine room

The page shows three engines and what each one can do: Claude, Cursor and
Ollama. Only Claude holds a Gmail connector, so the mail work stays on it, and
the page says why. The panel measures with cheap local
commands, and it answers `UNKNOWN` when it cannot know. Cursor gets a sign-in
flow that opens no browser on its own, and the sign-in link never enters the
database or a log.

Google closed the Gemini CLI to personal accounts, so that CLI left the panel
and the ladder. Gemini Flash is still available: Cursor speaks to it, as the
`flash` step of the ladder.

## The local engine and the mail

Google's connector answers only clients that it registered, so the local model
can never reach Gmail, and no amount of configuration changes that. It does not
need to. The DeepSeek Harness gives the local model a shell, so Claude fetches
the mail and stores it, and the local model reads the store:

    ./scripts/ns-mail list --bucket needs_you
    ./scripts/ns-mail show 1

Measured on 2026-08-31: the local model read a real message and summarised it
in 62 seconds, with no subscription quota and with the text never leaving the
machine. The same functions also run as a read-only MCP server at
`scripts/ns-mail-mcp.py`, for any MCP client that arrives later.

## Run it

Install the daily run at 06:30, with a ceiling of 20 USD of equivalent spend
for the week:

    cp scripts/com.aledeclerk.nightshift.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.aledeclerk.nightshift.plist

Open the page:

    .venv/bin/python scripts/serve.py

Stop the scheduler:

    launchctl unload ~/Library/LaunchAgents/com.aledeclerk.nightshift.plist

## Documents

- [The design](docs/specs/2026-08-30-design.md)
- [The machine room](docs/specs/2026-08-31-machine-room-design.md)
