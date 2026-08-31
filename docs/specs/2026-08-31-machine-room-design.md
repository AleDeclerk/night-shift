# The machine room — design

> Date: 2026-08-31. Status: approved for phase 1.

## 1. Purpose

The system runs on one engine today: Claude Code against a Max subscription.
The user also holds a Cursor subscription, a Google account for the Gemini CLI,
and six local models under Ollama. This phase gives the page a machine room. The room shows the true state of
each engine, and it adds the sign-in flow for the one engine with no session.

One rule stands above the rest: **subscriptions only, never an API key.** An
API key moves the cost from a flat monthly price to a metered bill, which is
the opposite of what this project is for.

## 2. What the measurements said

Measured on this machine on 2026-08-31, before any code:

| Engine | Headless | Session | Sees the mail | Forced schema |
| --- | --- | --- | --- | --- |
| `claude` | `-p` | Max subscription | yes, connector | yes, `--json-schema` |
| `gemini` | `-p` | `oauth-personal` | **no MCP server at all** | no |
| `cursor-agent` | `-p` | **not signed in** | no server configured | no |
| `ollama` | yes | local, no account | impossible, no MCP | yes, `format` |

Two facts follow, and they shape the whole design.

**Only Claude can touch the mail.** The Gmail connector belongs to claude.ai.
So the triage and the drafts stay on Claude, and the page must say why instead
of offering a choice that would fail at 06:30.

**A real probe costs money.** One trivial headless call costs 0.17 to 0.34 USD.
A page that probes four engines on every load would burn the weekly ceiling by
itself. So the panel separates two kinds of check, and it never confuses them.

## 3. Two kinds of check, never confused

**The cheap check** runs on every page load. It spends nothing and it takes
about a second. It asks five small questions: does the binary exist, what does
`cursor-agent status` answer, does `~/.gemini/settings.json` name an auth type,
what does `ollama list` return, and does `claude mcp list` name a Gmail
server.

**The real probe** runs only when the user asks for it, with a button that
names the price first. It sends one small prompt to the engine and it records
what came back.

The page shows both, and it labels them apart. A cheap check says what the
machine claims. A probe says what the machine did. The panel shows the date of
the last real probe next to each engine. A claim that was true last week is not
evidence today.

## 4. The sign-in flow

Only Cursor needs it. Claude and Gemini hold a session, and Ollama has no
account.

1. The user pulls the lever on the Cursor engine. The page posts to
   `/machines/cursor/login`.
2. The server starts `cursor-agent login` with `NO_OPEN_BROWSER=1`, so nothing
   opens on its own.
3. The server reads the output, strips the ANSI escapes, joins the wrapped
   lines, and pulls out the link. The CLI breaks that link across three lines,
   so a plain search for a URL finds only its first half.
4. The page shows the link. The user opens it and signs in **on the Cursor
   site**. This project never sees a user name or a password, and it never asks
   for one.
5. The page asks `/machines/cursor/login/status` every three seconds. When
   `cursor-agent status` reports a session, the engine lights up.
6. After three minutes the server stops the process and says so.

## 5. Hard rules

1. **Subscriptions only.** No field of this system accepts an API key, and no
   code path reads one from the environment to send to a model.
2. **The sign-in link is a credential.** It carries a challenge and a session
   id. It lives in the memory of the process while the flow runs. It never
   enters SQLite and it never enters a log file.
3. **Sign-in starts on POST, never on GET.** A prefetch of the browser must not
   start a login.
4. **The page states what it measured and when.** A capability with no probe
   behind it says so.

## 6. Out of scope for phase 1

Choosing an engine for each kind of work, and the Ollama adapter. Ollama does
not speak the protocol of `claude -p`, so it needs a runner of its own. Both
belong to phase 2. This phase only measures, shows, and signs in.

## 7. The page

The page moves to the look that the user approved: the shift report of a
factory that worked at night. Black and white on aged paper, one stamp red as
the only accent. Oswald for factory labels, Playfair Display in italic for the
title cards of a silent film, Courier Prime for the numbers. Film grain, a
vignette, and a projector flicker, all of them off under
`prefers-reduced-motion`.

The mail items read as numbered parts on a conveyor. The weekly spend reads as
a punched time card. The machine room sits below them, one panel for each
engine, with a lamp and a lever.

## 8. Acceptance criteria

- Given the four engines, when the page loads, then each one shows a cheap
  check result and the page spends nothing.
- Given an engine with no real probe yet, when the page loads, then that engine
  says that no probe ran, instead of claiming a capability.
- Given the user asks for a real probe, when it finishes, then the page records
  the date, the answer and the cost. The weekly counter grows by that cost.
- Given Cursor has no session, when the user pulls its lever, then the page
  shows the full sign-in link, with its challenge intact.
- Given the sign-in link, when it is written anywhere, then it appears in no
  log file and in no table of the database.
- Given a sign-in that nobody completes, when three minutes pass, then the
  server stops the process and the page says that the attempt expired.
- Given the mail work, when the page shows the machine room, then Claude is the
  only engine offered for it, and the page says why.
