# codex-watchdog

`codex-watchdog` provides a one-command wrapper around the tmux-based Codex
watchdog. It discovers the active root Codex thread from the target tmux pane,
derives the root thread UUID from the rollout file actually opened by that
Codex process, and then delegates recovery to `watch-codex-session.sh`.

The goal is to make the common path short:

```bash
codex-watch on
codex-watch status
codex-watch off
```

When multiple watchable Codex sessions exist, specify only the tmux session
name:

```bash
codex-watch on touchstone_tests
codex-watch status touchstone_tests
codex-watch off touchstone_tests
```

You should not need to copy a root thread UUID by hand in the normal case.

## Safety model

This package does not guess by "latest session".

Discovery starts from a live tmux pane, walks the pane process tree, inspects
`/proc/<pid>/fd/*`, and accepts only a rollout file that is actually open by a
Codex descendant process. The rollout must contain root `session_meta` with
`source == "cli"`, and its Goal must be `active` or `blocked`.

If discovery is ambiguous, the command fails and prints the candidate sessions
so you can select one explicitly.

## Requirements

- Linux with `/proc`
- `tmux`
- `jq`, `sqlite3`, `find`, `readlink`, `realpath`, `ps`, `awk`, `sed`, `wc`,
  `flock` for the underlying shell watchdog
- Codex CLI running inside a dedicated one-pane tmux session
- an active or blocked Codex native Goal

## Install

Development install:

```bash
pip install -e .
```

Offline or no-PyPI server install from a source tree, when `setuptools` is
already available in the target environment:

```bash
pip install --no-build-isolation -e .
```

For fully offline environments, build a wheel once in a prepared environment
and install that wheel on the server:

```bash
pip wheel . --no-build-isolation -w dist
pip install dist/codex_watchdog-*.whl
```

Recommended isolated install:

```bash
pipx install .
```

## Typical workflow

Start Codex inside a dedicated tmux session:

```bash
tmux new -s touchstone_tests
cd /path/to/your/repo
codex
```

Create or resume a Codex Goal in that session. To create one, type a slash
command in the Codex TUI:

```text
/goal <objective>
```

For example:

```text
/goal keep working through the continuous testing plan until the design is complete
```

Then from another terminal:

```bash
codex-watch on touchstone_tests
codex-watch status touchstone_tests
```

If there is only one watchable Codex session, this is enough:

```bash
codex-watch on
codex-watch status
codex-watch off
```

Create a fresh tmux session:

```bash
codex-watch new touchstone_tests --workdir /path/to/your/repo
tmux attach -t touchstone_tests
```

Inside Codex, submit a real task and create an active Goal with
`/goal <objective>`. Wait until Codex is `Working` or the Goal is `blocked`;
then detach and run:

```bash
codex-watch on touchstone_tests
```

Important: a visible `Working` indicator is not enough by itself. `Working`
only means the current Codex turn is running. The watchdog requires the same
thread to have a native Goal row in `~/.codex/goals_1.sqlite` with status
`active` or `blocked`. If `codex-watch on` reports `goal=missing`, the tmux
session is a normal Codex thread/turn rather than Goal-backed autonomous work;
run `/goal <objective>` inside the TUI, then rerun `codex-watch on`.

## Troubleshooting

### `goal=missing`

Example:

```text
session           thread                                goal     status   cwd / reason
touchstone_tests  01a00f05-d696-73b0-bac1-7b199b7b2dd5  missing  blocked  Codex thread found, but no native Goal exists for this thread; run /goal <objective> in the TUI before enabling watchdog
```

This means discovery succeeded: `codex-watch` found the live root Codex thread
opened by the tmux pane. The watchdog still refuses to start because the thread
is not Goal-backed. This is intentional; the underlying watchdog will only
inject recovery input, restart, or resume work for native Goals with status
`active` or `blocked`.

Native Goal commands in the Codex TUI follow:

```text
/goal [<objective>|clear|edit|pause|resume]
```

## Commands

```text
codex-watch on [SESSION]
codex-watch off [SESSION|THREAD_UUID]
codex-watch status [SESSION|THREAD_UUID]
codex-watch ls
codex-watch doctor [SESSION]
codex-watch self-test
codex-watch new SESSION [--workdir DIR]
```

Aliases:

```text
start/up/watch -> on
stop/down/rm   -> off
st             -> status
list           -> ls
```

Useful options:

```text
--codex-home DIR       Default: $CODEX_HOME or ~/.codex
--launcher COMMAND     Default: codex
--base-wait SECONDS    Default: 120
--max-wait SECONDS     Default: 3600
--watcher PATH         Override underlying watch-codex-session.sh
--json                 Machine-readable output where supported
```

## Underlying watcher

By default `codex-watch` prefers:

```text
$CODEX_HOME/skills/watch-codex-session/scripts/watch-codex-session.sh
```

If that skill is not installed, it falls back to the bundled copy included in
this package and materializes it under:

```text
~/.cache/codex-watchdog/watch-codex-session.sh
```

The Python CLI only discovers and orchestrates. The long-running recovery loop,
tmux status bar, 429 handling, blocked Goal resume, and stopped-process
recovery remain in `watch-codex-session.sh`.
