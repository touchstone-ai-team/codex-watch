# codex-watchdog

`codex-watchdog` is a small Python CLI that makes the tmux-based Codex watchdog
easy to use. It discovers the live root Codex thread from a tmux pane, resolves
the correct root thread UUID from the rollout file actually opened by that
Codex process, and then delegates recovery to `watch-codex-session.sh`.

The common path is intentionally short:

```bash
codex-watch new touchstone_tests --workdir /path/to/repo --launcher codex-codexcn
tmux attach -t touchstone_tests
# inside Codex TUI: /goal <objective>
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
`source == "cli"`, and its native Codex Goal must be `active` or `blocked`.

The underlying watchdog will not inject input or restart work for paused,
complete, usage-limited, budget-limited, missing-Goal, or untracked threads.

## Requirements

- Linux with `/proc`
- `tmux`
- `jq`, `sqlite3`, `find`, `readlink`, `realpath`, `ps`, `awk`, `sed`, `wc`,
  and `flock` for the underlying shell watchdog
- Codex CLI running inside a dedicated one-pane tmux session
- an active or blocked Codex native Goal

## Install

Development install:

```bash
cd /root/data/disk/dev/codex-watchdog
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

## Quick start

### 1. Create a dedicated Codex tmux session

Use `new` to create a one-pane tmux session and start Codex inside it:

```bash
codex-watch new touchstone_tests --workdir /root/data/disk/dev/stock_all_0520_web
```

By default this launches:

```bash
codex
```

If you use a wrapper or alternate provider CLI, specify it here:

```bash
codex-watch new touchstone_tests \
  --workdir /root/data/disk/dev/stock_all_0520_web \
  --launcher codex-codexcn
```

`codex-watch new` saves the launcher choice under:

```text
~/.cache/codex-watchdog/sessions/<SESSION>.json
```

That means later commands can reuse it automatically.

### 2. Attach and create a native Goal

Attach to the tmux session:

```bash
tmux attach -t touchstone_tests
```

Inside the Codex TUI, create a native Goal:

```text
/goal <objective>
```

Example:

```text
/goal complete the continuous testing design and keep working through recoverable failures
```

Detach from tmux:

```text
Ctrl-b d
```

Important: a visible `Working` indicator is not enough by itself. `Working`
only means the current Codex turn is running. The watchdog requires the same
thread to have a native Goal row in `~/.codex/goals_1.sqlite` with status
`active` or `blocked`.

### 3. Start the watchdog

Start watching the session:

```bash
codex-watch on touchstone_tests
```

If the session was created with:

```bash
codex-watch new touchstone_tests --launcher codex-codexcn
```

then `on` automatically uses `codex-codexcn` for future stopped-process
recovery. You do not need to repeat `--launcher codex-codexcn`.

You can still override the launcher explicitly:

```bash
codex-watch on touchstone_tests --launcher codex
```

Launcher precedence is:

```text
explicit --launcher > launcher saved by `new` > codex
```

### 4. Check status

```bash
codex-watch status touchstone_tests
```

For a compact status line:

```bash
codex-watch status --one-line touchstone_tests
```

`RUNNING` with `Goal=active` is the positive signal that autonomous watchdog
work is live. `WAITING_RETRY`, `RECOVERING`, `VERIFYING`, and `RECOVERED` are
normal recovery states. `DISABLED Goal missing` means the thread is not
Goal-backed and the watchdog will not control it.

### 5. Stop the watchdog

```bash
codex-watch off touchstone_tests
```

Stopping kills only the watchdog tmux session. It does not kill the target
Codex session.

## Commands

### Main lifecycle

Create a dedicated tmux session and start Codex:

```bash
codex-watch new SESSION [--workdir DIR] [--launcher COMMAND]
```

Start watching a Codex session:

```bash
codex-watch on [SESSION] [--launcher COMMAND] [--base-wait SECONDS] [--max-wait SECONDS]
```

Show watchdog status:

```bash
codex-watch status [SESSION|THREAD_UUID] [--one-line]
```

Stop a watchdog without killing the target Codex session:

```bash
codex-watch off [SESSION|THREAD_UUID]
```

### Discovery and diagnostics

List running watchdog sessions:

```bash
codex-watch ls
```

Explain what would be watched and validate the underlying watchdog arguments:

```bash
codex-watch doctor [SESSION] [--launcher COMMAND]
```

Run the underlying watchdog self-test:

```bash
codex-watch self-test
```

### Aliases

```text
start/up/watch -> on
stop/down/rm   -> off
st             -> status
list           -> ls
```

### Common options

```text
--codex-home DIR       Default: $CODEX_HOME or ~/.codex
--watcher PATH         Override watch-codex-session.sh
--json                 Machine-readable output where supported
```

### Launcher option

`--launcher COMMAND` controls which executable the watchdog uses when it needs
to run:

```bash
COMMAND resume <THREAD_UUID>
```

Examples:

```bash
codex-watch new touchstone_tests --workdir /path/to/repo --launcher codex
codex-watch new touchstone_tests_cn --workdir /path/to/repo --launcher codex-codexcn
```

If you created the session with `codex-watch new`, later `on` and `doctor`
commands reuse the saved launcher automatically. If you created the tmux
session manually, no launcher metadata exists, so `on` and `doctor` default to
`codex` unless you pass `--launcher`.

## Goal commands

Native Goal commands in the Codex TUI follow:

```text
/goal [<objective>|clear|edit|pause|resume]
```

Useful examples:

```text
/goal complete the long-running implementation and verify it
/goal pause
/goal resume
/goal clear
```

## Troubleshooting

### `goal=missing`

Example:

```text
session           thread                                goal     status   cwd / reason
touchstone_tests  01a00f05-d696-73b0-bac1-7b199b7b2dd5  missing  blocked  Codex thread found, but no native Goal exists for this thread; run /goal <objective> in the TUI before enabling watchdog
```

This means discovery succeeded: `codex-watch` found the live root Codex thread
opened by the tmux pane. The watchdog still refuses to start because the thread
is not Goal-backed. Run `/goal <objective>` inside the Codex TUI, wait for the
Goal to become active or blocked, then rerun:

```bash
codex-watch on touchstone_tests
```

### `launcher is not an executable`

The underlying watchdog resolves launchers in a fresh login shell. If you use a
custom wrapper such as `codex-codexcn`, make sure it is available on `PATH`:

```bash
command -v codex-codexcn
```

If needed, pass an absolute executable path:

```bash
codex-watch new touchstone_tests --workdir /path/to/repo --launcher /usr/local/bin/codex-codexcn
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
