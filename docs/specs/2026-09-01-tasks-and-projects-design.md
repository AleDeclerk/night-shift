# Tasks and projects — design

> Date: 2026-09-01. Status: approved.

## 1. Purpose

A job is one line of text in a box. It belongs to nothing, it happens once, and
the system knows nothing about the work it names.

This phase gives a job a project and a schedule. The project is the key that
joins a task to the knowledge that already exists about it: several of these
projects hold a vault, and some of those vaults already hold a graph.

## 2. The form

Three fields, in this order.

**Scope, then name.** A pair of buttons picks `Personal` or `Veritas`, and the
list of projects below shows only that scope, plus `＋ Nuevo proyecto`. Two
short lists beat one long list, and the scope is the first thing the user knows
about a task.

**What to do.** The same text box as today.

**When.** Once, daily, weekly or monthly.

## 3. Where the projects come from

The system reads them from the disk instead of from a list that somebody has to
keep. A directory that holds `wiki/`, `vault/` or `graphify-out/` is a project.
On 2026-09-01 that gives nine: `aph-knowledge`, `brailleai`, `maradona`,
`remarque-devx`, `remarque-qms`, `scalence`, `thinking-nation`, `va-agents`
and `VA-patient-workflow`.

A first guess puts each one in a scope, and the user can change it. A project
made by hand needs no vault: it is a name and a scope, and it can gain a vault
later.

## 4. A recurring task is a template, not a copy

A weekly task does not become seven rows. It is a template that **makes** one
job when its turn arrives. So the history reads "sprint review, 12 runs"
instead of twelve loose lines, and the user can stop the recurrence without
erasing what it did.

**A turn whose previous job is still open is skipped, and the card says so.**
Piling identical tasks is how a queue fills with rubbish that nobody reads.

## 5. The project brings its knowledge

When a job names a project, and that project holds a graph, the system asks the
graph what it knows about the words of the task. `graphify explain` answers in
about a tenth of a second and it costs nothing: the graph is a local file.

**What it finds is shown before it is used.** The card names the concepts and
their links, and the user sends it to the prompt or ignores it. A graph that
answers with rubbish must be seen before its rubbish lands inside a mail.

This reuses what exists. The build of a graph costs tokens and hours, and it
already ran for two of the nine. Reading one costs nothing. So this phase reads
only, and the six that hold no graph simply bring no context.

## 6. Hard rules

1. **The scope is never guessed twice.** The first guess is written down, and
   from then on the stored scope wins.
2. **A recurrence that fires writes an event**, whether it made a job or it
   skipped a turn. A silent skip is a task that seems to run and does not.
3. **The graph is read, never written.** This phase runs no build.
4. **A project with no vault still works.** The dropdown is about grouping
   work, and the knowledge is a bonus that some projects have.

## 7. Out of scope

Building a graph for the six vaults that have none, editing a vault, and
choosing an engine for each project. The first one costs real tokens and needs
its own decision.

## 8. Acceptance criteria

- Given the scope `Veritas`, when the list is drawn, then it shows only the
  projects of that scope and the option to make one.
- Given a new project made by hand, when it is saved, then it appears under its
  scope and it needs no vault.
- Given a weekly task, when its turn arrives, then one job is made and the
  template stays.
- Given a weekly task whose previous job is open, when its turn arrives, then
  no job is made. An event says that the turn was skipped.
- Given a job with a project that holds a graph, when the card is drawn, then
  it shows the concepts that the graph knows and the user can add them.
- Given a job with a project that holds no graph, when the card is drawn, then
  it says so and the job runs the same.
- Given the disk holds a new vault, when the projects are read, then it appears
  with its guessed scope. A scope that the user changed stays changed.
