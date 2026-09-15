#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

POLL_SECONDS=${WATCH_CODEX_POLL_SECONDS:-10}
CONTINUE_TEXT='继续原任务'
SUBAGENT_CONTINUE_TEXT='继续原任务；检测到子 Agent 因 429 失败，请以单并发顺序重试尚未完成的子任务，并复用已经取得的结果。'
GOAL_RESUME_TEXT='/goal resume'
ACCEPT_SECONDS=40

usage() {
    printf '%s\n' \
        'usage:' \
        '  watch-codex-session.sh self-test' \
        '  watch-codex-session.sh doctor PANE THREAD_ID [LAUNCHER] [WORKDIR]' \
        '  watch-codex-session.sh start  PANE THREAD_ID [LAUNCHER] [WORKDIR] [BASE_WAIT] [MAX_WAIT]' \
        '  watch-codex-session.sh run    PANE THREAD_ID [LAUNCHER] [WORKDIR] [BASE_WAIT] [MAX_WAIT]' \
        '  watch-codex-session.sh status THREAD_ID' \
        '  watch-codex-session.sh stop   THREAD_ID'
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    return 1
}

is_uuid() {
    [[ ${1-} =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

set_paths() {
    WATCH_SESSION="codex-watch-$THREAD_ID"
    STATE_DIR="$CODEX_HOME_DIR/watchdog/$THREAD_ID"
    LOG="$STATE_DIR/watchdog.log"
    LOCK="$STATE_DIR/watchdog.lock"
    CURSOR_FILE="$STATE_DIR/rollout.cursor"
    TARGET_FILE="$STATE_DIR/target.pane"
    mkdir -p "$STATE_DIR"
}

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"
}

publish_state() {
    local state=$1 detail=${2-} updated target_session session
    updated=$(date '+%H:%M:%S')
    target_session=${TARGET_PANE%%:*}
    for session in "$WATCH_SESSION" "$target_session"; do
        tmux has-session -t "$session" 2>/dev/null || continue
        tmux set-option -q -t "$session" @watch_state "$state"
        tmux set-option -q -t "$session" @watch_detail "$detail"
        tmux set-option -q -t "$session" @watch_updated "$updated"
    done
}

setup_status_bar() {
    local target_session original original_length
    target_session=${TARGET_PANE%%:*}
    tmux has-session -t "$target_session" 2>/dev/null || return 0
    original=$(tmux show-options -qv -t "$target_session" status-right 2>/dev/null || true)
    [[ -n "$original" ]] || original=$(tmux show-options -gv status-right 2>/dev/null || true)
    original_length=$(tmux show-options -qv -t "$target_session" status-right-length 2>/dev/null || true)
    if [[ -z "$(tmux show-options -qv -t "$target_session" @watch_original_status_right 2>/dev/null || true)" ]]; then
        tmux set-option -q -t "$target_session" @watch_original_status_right "$original"
        tmux set-option -q -t "$target_session" @watch_original_status_right_length "${original_length:-40}"
    fi
    tmux set-option -q -t "$target_session" status-right-length 120
    # Keep WATCH at the far right. tmux clips the left side of status-right on
    # narrow terminals, so placing WATCH first can make the only useful signal
    # invisible to the attached user.
    tmux set-option -q -t "$target_session" status-right \
        "$original"' | #[bold]WATCH #{@watch_state}#[default] #{@watch_detail} @#{@watch_updated}'
}

restore_status_bar() {
    local target_session original original_length
    [[ -s "$TARGET_FILE" ]] || return 0
    TARGET_PANE=$(<"$TARGET_FILE")
    target_session=${TARGET_PANE%%:*}
    tmux has-session -t "$target_session" 2>/dev/null || return 0
    original=$(tmux show-options -qv -t "$target_session" @watch_original_status_right 2>/dev/null || true)
    original_length=$(tmux show-options -qv -t "$target_session" @watch_original_status_right_length 2>/dev/null || true)
    [[ -n "$original" ]] && tmux set-option -q -t "$target_session" status-right "$original"
    [[ -n "$original_length" ]] && tmux set-option -q -t "$target_session" status-right-length "$original_length"
    tmux set-option -qu -t "$target_session" @watch_state
    tmux set-option -qu -t "$target_session" @watch_detail
    tmux set-option -qu -t "$target_session" @watch_updated
    tmux set-option -qu -t "$target_session" @watch_original_status_right
    tmux set-option -qu -t "$target_session" @watch_original_status_right_length
}

require_tools() {
    local tool
    for tool in jq sqlite3 tmux find readlink realpath ps awk sed wc flock; do
        command -v "$tool" >/dev/null 2>&1 || fail "missing required command: $tool" || return 1
    done
}

resolve_launcher() {
    local resolved
    if [[ "$LAUNCHER" == /* ]]; then
        [[ -x "$LAUNCHER" ]] || fail "launcher path is not executable: $LAUNCHER" || return 1
        LAUNCHER_PATH=$(realpath "$LAUNCHER")
        return
    fi
    [[ "$LAUNCHER" =~ ^[[:alnum:]_.-]+$ ]] || fail "unsafe launcher name: $LAUNCHER" || return 1
    resolved=$(bash -lc "command -v $LAUNCHER" 2>/dev/null || true)
    [[ -n "$resolved" && -x "$resolved" ]] || fail "launcher is not an executable in a fresh login shell: $LAUNCHER" || return 1
    LAUNCHER_PATH=$(realpath "$resolved")
}

find_rollout() {
    local -a matches=()
    mapfile -d '' -t matches < <(
        find "$CODEX_HOME_DIR/sessions" -type f -name "*-$THREAD_ID.jsonl" -print0 2>/dev/null
    )
    (( ${#matches[@]} == 1 )) || fail "thread UUID resolves to ${#matches[@]} active rollout files" || return 1
    ROLLOUT=${matches[0]}
}

validate_rollout() {
    local meta cwd source_kind
    meta=$(jq -c --arg id "$THREAD_ID" \
        'select(.type == "session_meta" and .payload.id == $id) | .payload' \
        "$ROLLOUT" | head -n 1)
    [[ -n "$meta" ]] || fail "rollout session_meta does not match thread UUID" || return 1
    source_kind=$(jq -r 'if .source == "cli" then "cli" else "other" end' <<<"$meta")
    [[ "$source_kind" == cli ]] || fail "thread is not a root interactive CLI thread" || return 1
    cwd=$(jq -r '.cwd // empty' <<<"$meta")
    [[ -n "$cwd" ]] || fail "rollout has no saved cwd" || return 1
    [[ "$(realpath -m "$cwd")" == "$(realpath -m "$WORKDIR")" ]] \
        || fail "workdir differs from rollout cwd: $cwd" || return 1
}

validate_target_pane() {
    local target_session pane_count
    target_session=${TARGET_PANE%%:*}
    [[ "$TARGET_PANE" == "$target_session:0.0" ]] \
        || fail "target must be a dedicated SESSION:0.0 pane: $TARGET_PANE" || return 1
    tmux display-message -p -t "=$TARGET_PANE" '#{pane_id}' >/dev/null 2>&1 \
        || fail "target pane does not exist: $TARGET_PANE" || return 1
    pane_count=$(tmux list-panes -s -t "=$target_session" -F '#{pane_id}' | wc -l)
    [[ "$pane_count" == 1 ]] || fail "target tmux session must contain exactly one pane" || return 1
}

descendant_pids() {
    local changed=1 pid ppid known=" $1 " snapshot
    snapshot=$(ps -eo pid=,ppid=)
    while (( changed )); do
        changed=0
        while read -r pid ppid; do
            if [[ "$known" == *" $ppid "* && "$known" != *" $pid "* ]]; then
                known+="$pid "
                changed=1
            fi
        done <<<"$snapshot"
    done
    for pid in $known; do
        printf '%s\n' "$pid"
    done
}

validate_live_rollout_owner() {
    local pane_pid pid fd opened=
    pane_pid=$(tmux display-message -p -t "=$TARGET_PANE" '#{pane_pid}')
    while IFS= read -r pid; do
        for fd in /proc/"$pid"/fd/*; do
            [[ "$(readlink "$fd" 2>/dev/null || true)" == "$ROLLOUT" ]] && opened=1
        done
    done < <(descendant_pids "$pane_pid")
    [[ -n "$opened" ]] || fail "target pane descendants do not have the root rollout open" || return 1
}

validate_no_other_watcher() {
    local session target
    while IFS= read -r session; do
        [[ -n "$session" && "$session" != "$WATCH_SESSION" ]] || continue
        target=$(tmux show-options -qv -t "$session" '@watch_target_pane' 2>/dev/null || true)
        [[ "$target" != "$TARGET_PANE" ]] || fail "another watchdog already targets $TARGET_PANE: $session" || return 1
    done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null | sed -n '/^codex-watch-/p')
}

goal_status() {
    sqlite3 -readonly "$CODEX_HOME_DIR/goals_1.sqlite" \
        "SELECT status FROM thread_goals WHERE thread_id='$THREAD_ID';" 2>/dev/null | head -n 1
}

visible_screen() {
    tmux capture-pane -p -J -t "=$TARGET_PANE" 2>/dev/null
}

is_working_screen() {
    local line trimmed
    while IFS= read -r line; do
        trimmed=${line#"${line%%[![:space:]]*}"}
        case "$trimmed" in
            [![:alnum:]]' Working ('*'esc to interrupt'*) return 0 ;;
        esac
    done <<<"$1"
    return 1
}

root_retryable_provider_state() {
    local line trimmed normalized found=
    while IFS= read -r line; do
        trimmed=${line#"${line%%[![:space:]]*}"}
        case "$trimmed" in
            '■ '*)
                normalized=${trimmed#'■ '}
                if [[ "$normalized" =~ (^|[[:space:]:])(429)([[:space:]:]|$) ]]; then
                    found=rate_limited
                elif [[ "$normalized" =~ (^|[[:space:]:])(500|502|503|504)([[:space:]:]|$) ]] \
                    || [[ "${normalized,,}" == *'system cpu overloaded'* ]] \
                    || [[ "${normalized,,}" == *'temporarily unavailable'* ]] \
                    || [[ "${normalized,,}" == *'service unavailable'* ]] \
                    || [[ "${normalized,,}" == *'bad gateway'* ]] \
                    || [[ "${normalized,,}" == *'gateway timeout'* ]]; then
                    found=provider_unavailable
                fi
                ;;
            '• '*) found= ;;
        esac
    done <<<"$1"
    [[ -n "$found" ]] && printf '%s' "$found"
}

pane_process_state() {
    local command
    command=$(tmux display-message -p -t "=$TARGET_PANE" '#{pane_current_command}' 2>/dev/null) || {
        printf 'missing'
        return
    }
    case "$command" in
        node|codex) printf 'codex' ;;
        bash|zsh|fish|sh|dash) printf 'shell' ;;
        *) printf 'unknown' ;;
    esac
}

read_cursor() {
    local current
    current=$(wc -l <"$ROLLOUT")
    if [[ -s "$CURSOR_FILE" ]]; then
        ROLLOUT_CURSOR=$(<"$CURSOR_FILE")
        [[ "$ROLLOUT_CURSOR" =~ ^[0-9]+$ ]] || ROLLOUT_CURSOR=$current
        (( ROLLOUT_CURSOR <= current )) || ROLLOUT_CURSOR=$current
    else
        ROLLOUT_CURSOR=$current
        printf '%s\n' "$ROLLOUT_CURSOR" >"$CURSOR_FILE"
    fi
}

scan_new_subagent_429s() {
    local current next count=0
    current=$(wc -l <"$ROLLOUT")
    (( current >= ROLLOUT_CURSOR )) || ROLLOUT_CURSOR=$current
    next=$((ROLLOUT_CURSOR + 1))
    if (( next <= current )); then
        count=$(sed -n "${next},${current}p" "$ROLLOUT" | jq -r '
            select(.type == "response_item" and .payload.type == "agent_message")
            | [.payload.content[]?.text?] | join("\n")
            | select(test("Agent errored: exceeded retry limit, last status: 429 Too Many Requests"))
            | 1
        ' 2>/dev/null | wc -l)
        ROLLOUT_CURSOR=$current
        printf '%s\n' "$ROLLOUT_CURSOR" >"$CURSOR_FILE"
    fi
    printf '%s' "$count"
}

rollout_has_user_message_after() {
    local after_line=$1 expected=$2 current
    current=$(wc -l <"$ROLLOUT")
    (( current > after_line )) || return 1
    sed -n "$((after_line + 1)),${current}p" "$ROLLOUT" | jq -e --arg expected "$expected" '
        select(.type == "event_msg" and .payload.type == "user_message" and .payload.message == $expected)
    ' >/dev/null 2>&1
}

classify() {
    local process=$1 goal=$2 screen=$3 pending=${4:-0} provider_state
    case "$goal" in
        paused|complete|usage_limited|budget_limited) printf 'terminal:%s' "$goal"; return ;;
        active|blocked) ;;
        *) printf 'untracked'; return ;;
    esac
    case "$process" in
        missing|shell) printf 'stopped'; return ;;
        unknown) printf 'unsafe'; return ;;
    esac
    if is_working_screen "$screen"; then
        printf 'busy'
    elif [[ "$goal" == blocked || "$screen" == *'Goal blocked (/goal resume)'* ]]; then
        printf 'blocked'
    elif provider_state=$(root_retryable_provider_state "$screen"); then
        printf '%s' "$provider_state"
    elif (( pending > 0 )); then
        printf 'subagent_failed'
    else
        printf 'idle'
    fi
}

current_state() {
    local process goal screen
    process=$(pane_process_state)
    goal=$(goal_status)
    screen=$(visible_screen || true)
    classify "$process" "$goal" "$screen" "$PENDING_SUBAGENT_FAILURES"
}

backoff_delay() {
    local attempt=$1 delay=$BASE_WAIT jitter=0 i
    for ((i = 0; i < attempt; i++)); do
        (( delay >= MAX_WAIT / 2 )) && { delay=$MAX_WAIT; break; }
        delay=$((delay * 2))
    done
    (( delay > MAX_WAIT )) && delay=$MAX_WAIT
    if [[ ${WATCH_CODEX_NO_JITTER:-0} != 1 ]]; then
        jitter=$((delay / 10))
        (( jitter > 30 )) && jitter=30
        (( jitter > 0 )) && delay=$((delay + RANDOM % (jitter + 1)))
    fi
    printf '%s' "$delay"
}

wait_for_same_state() {
    local expected=$1 seconds=$2 elapsed=0 state remaining=$2
    publish_state 'WAITING_RETRY' "$expected ${remaining}s"
    while (( elapsed < seconds )); do
        sleep "$POLL_SECONDS"
        elapsed=$((elapsed + POLL_SECONDS))
        remaining=$((seconds - elapsed))
        (( remaining < 0 )) && remaining=0
        state=$(current_state)
        [[ "$state" == "$expected" ]] || {
            log "incident changed from $expected to $state after ${elapsed}s"
            return 1
        }
        publish_state 'WAITING_RETRY' "$expected ${remaining}s"
    done
}

composer_is_empty() {
    local cursor_x
    cursor_x=$(tmux display-message -p -t "=$TARGET_PANE" '#{cursor_x}' 2>/dev/null) || return 1
    [[ "$cursor_x" == 2 ]]
}

send_line() {
    local text=$1 cursor_x delivered=0 submitted=0 i key
    composer_is_empty || {
        log 'composer is not empty; input deferred'
        return 1
    }
    tmux send-keys -t "=$TARGET_PANE" -l "$text" || return 1
    # Codex deliberately detects burst input as a paste. Give the TUI time to
    # commit the literal text before Enter; an immediate C-m can be swallowed.
    for i in {1..20}; do
        cursor_x=$(tmux display-message -p -t "=$TARGET_PANE" '#{cursor_x}' 2>/dev/null || true)
        if [[ "$cursor_x" != 2 ]]; then
            delivered=1
            break
        fi
        sleep 0.1
    done
    if (( ! delivered )); then
        log 'literal input was not visible in the composer; Enter not sent'
        return 1
    fi
    publish_state 'SUBMITTING' 'input visible; Enter pending'
    sleep 1.5
    for key in Enter C-m; do
        tmux send-keys -t "=$TARGET_PANE" "$key" || continue
        for i in {1..20}; do
            cursor_x=$(tmux display-message -p -t "=$TARGET_PANE" '#{cursor_x}' 2>/dev/null || true)
            if [[ "$cursor_x" == 2 ]]; then
                submitted=1
                break 2
            fi
            sleep 0.1
        done
        sleep 1
    done
    if (( ! submitted )); then
        log 'composer still contains input after Enter retries'
        return 1
    fi
    log 'composer submit confirmed'
    publish_state 'VERIFYING' 'submitted; awaiting acceptance'
}

wait_for_normal_message_acceptance() {
    local start_line=$1 expected=$2 elapsed=0
    while (( elapsed < ACCEPT_SECONDS )); do
        rollout_has_user_message_after "$start_line" "$expected" && return 0
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

continue_normal() {
    local text=$1 reason=$2 start_line
    start_line=$(wc -l <"$ROLLOUT")
    if ! send_line "$text"; then
        return 1
    fi
    if wait_for_normal_message_acceptance "$start_line" "$text"; then
        log "continuation accepted reason=$reason"
        publish_state 'RECOVERED' "$reason accepted"
        return 0
    fi
    log "continuation key delivery unconfirmed reason=$reason"
    return 1
}

resume_blocked_goal() {
    local elapsed=0 status state
    send_line "$GOAL_RESUME_TEXT" || return 1
    while (( elapsed < ACCEPT_SECONDS )); do
        status=$(goal_status)
        state=$(current_state)
        if [[ "$status" == active || "$state" == busy ]]; then
            log 'goal resume accepted'
            publish_state 'RECOVERED' 'Goal=active'
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    log 'goal resume key delivery unconfirmed'
    return 1
}

shell_quote_command() {
    local -a argv=("$LAUNCHER_PATH" resume "$THREAD_ID")
    local arg quoted=''
    if [[ $(goal_status) == active ]]; then
        argv+=("$CONTINUE_TEXT")
    fi
    for arg in "${argv[@]}"; do
        printf -v arg '%q' "$arg"
        quoted+=" $arg"
    done
    printf 'cd %q && env CODEX_HOME=%q%s' "$WORKDIR" "$CODEX_HOME_DIR" "$quoted"
}

restart_codex() {
    local target_session command
    target_session=${TARGET_PANE%%:*}
    if ! tmux has-session -t "=$target_session" 2>/dev/null; then
        tmux new-session -d -s "$target_session" -c "$WORKDIR" || return 1
        setup_status_bar
    fi
    [[ $(pane_process_state) == shell ]] || return 1
    command=$(shell_quote_command)
    tmux send-keys -t "=$TARGET_PANE" C-c
    tmux send-keys -t "=$TARGET_PANE" -l "$command"
    tmux send-keys -t "=$TARGET_PANE" C-m
    log 'submitted codex resume command'
}

run_watchdog() {
    exec 9>"$LOCK"
    if ! flock -n 9; then
        fail "watchdog lock is already held for $THREAD_ID"
        return 1
    fi
    trap 'log "watchdog stopped"' EXIT
    read_cursor
    PENDING_SUBAGENT_FAILURES=0
    local state previous='' attempt=0 delay new_failures
    log "watchdog started pane=$TARGET_PANE base_wait=$BASE_WAIT max_wait=$MAX_WAIT"
    publish_state 'STARTING' 'initializing'
    while :; do
        new_failures=$(scan_new_subagent_429s)
        if (( new_failures > 0 )); then
            PENDING_SUBAGENT_FAILURES=$((PENDING_SUBAGENT_FAILURES + new_failures))
            log "recorded subagent 429 count=$new_failures pending=$PENDING_SUBAGENT_FAILURES"
        fi
        state=$(current_state)
        case "$state" in
            busy|idle|terminal:*|untracked|unsafe)
                [[ "$state" != "$previous" ]] && log "state=$state"
                case "$state" in
                    busy)
                        if [[ $(goal_status) == blocked ]]; then
                            publish_state 'RUNNING_TURN' 'Goal=blocked'
                        else
                            publish_state 'RUNNING' 'Goal=active'
                        fi
                        ;;
                    idle) publish_state 'IDLE' 'Goal=active' ;;
                    terminal:*) publish_state 'STOPPED' "Goal=${state#terminal:}" ;;
                    untracked) publish_state 'DISABLED' 'Goal missing' ;;
                    unsafe) publish_state 'DISABLED' 'unknown pane command' ;;
                esac
                previous=$state
                attempt=0
                sleep "$POLL_SECONDS"
                continue
                ;;
            blocked|rate_limited|provider_unavailable|subagent_failed|stopped) ;;
            *)
                log 'state check failed; retrying'
                sleep "$POLL_SECONDS"
                continue
                ;;
        esac
        if [[ "$state" != "$previous" ]]; then
            previous=$state
            attempt=0
        fi
        delay=$(backoff_delay "$attempt")
        log "detected $state attempt=$((attempt + 1)) wait=${delay}s"
        if ! wait_for_same_state "$state" "$delay"; then
            previous=''
            attempt=0
            continue
        fi
        publish_state 'RECOVERING' "$state attempt=$((attempt + 1))"
        case "$state" in
            blocked)
                if ! resume_blocked_goal; then
                    publish_state 'RECOVERY_FAILED' 'blocked submit/accept failed'
                fi
                ;;
            rate_limited)
                if ! continue_normal "$CONTINUE_TEXT" 'root_429'; then
                    publish_state 'RECOVERY_FAILED' 'root 429 continuation failed'
                fi
                ;;
            provider_unavailable)
                if ! continue_normal "$CONTINUE_TEXT" 'root_provider_unavailable'; then
                    publish_state 'RECOVERY_FAILED' 'provider unavailable continuation failed'
                fi
                ;;
            subagent_failed)
                if continue_normal "$SUBAGENT_CONTINUE_TEXT" 'subagent_429'; then
                    PENDING_SUBAGENT_FAILURES=0
                else
                    publish_state 'RECOVERY_FAILED' 'subagent retry prompt failed'
                fi
                ;;
            stopped)
                if ! restart_codex; then
                    log 'codex restart attempt failed'
                    publish_state 'RECOVERY_FAILED' 'Codex restart failed'
                fi
                ;;
        esac
        attempt=$((attempt + 1))
        sleep "$POLL_SECONDS"
    done
}

doctor() {
    require_tools
    is_uuid "$THREAD_ID" || fail "invalid thread UUID: $THREAD_ID" || return 1
    [[ -d "$WORKDIR" ]] || fail "workdir does not exist: $WORKDIR" || return 1
    resolve_launcher
    find_rollout
    validate_rollout
    validate_target_pane
    validate_live_rollout_owner
    validate_no_other_watcher
    local status
    status=$(goal_status)
    case "$status" in
        active|blocked) ;;
        *) fail "thread Goal must be active or blocked; current status: ${status:-missing}" || return 1 ;;
    esac
    printf 'DOCTOR_OK thread=%s pane=%s launcher=%s goal=%s rollout=%s\n' \
        "$THREAD_ID" "$TARGET_PANE" "$LAUNCHER_PATH" "$status" "$ROLLOUT"
}

start_watchdog() {
    doctor
    if tmux has-session -t "=$WATCH_SESSION" 2>/dev/null; then
        fail "watchdog session already exists: $WATCH_SESSION"
        return 1
    fi
    printf '%s\n' "$(wc -l <"$ROLLOUT")" >"$CURSOR_FILE"
    printf '%s\n' "$TARGET_PANE" >"$TARGET_FILE"
    local run_command loop_command
    printf -v run_command '%q ' env "CODEX_HOME=$CODEX_HOME_DIR" \
        "WATCH_CODEX_POLL_SECONDS=$POLL_SECONDS" "$SCRIPT_PATH" run \
        "$TARGET_PANE" "$THREAD_ID" "$LAUNCHER_PATH" "$WORKDIR" "$BASE_WAIT" "$MAX_WAIT"
    printf -v loop_command 'while :; do %s; code=$?; sleep %q; done' "$run_command" "$POLL_SECONDS"
    tmux new-session -d -s "$WATCH_SESSION" -c "$WORKDIR" "$loop_command"
    tmux set-option -q -t "$WATCH_SESSION" @watch_target_pane "$TARGET_PANE"
    tmux set-option -q -t "$WATCH_SESSION" @watch_thread_id "$THREAD_ID"
    setup_status_bar
    publish_state 'STARTING' 'watchdog launched'
    printf 'STARTED %s -> %s\n' "$WATCH_SESSION" "$TARGET_PANE"
}

status_watchdog() {
    if ! tmux has-session -t "=$WATCH_SESSION" 2>/dev/null; then
        printf 'STOPPED %s\n' "$WATCH_SESSION"
        return 1
    fi
    local target state detail updated goal pane_state
    target=$(tmux show-options -qv -t "$WATCH_SESSION" @watch_target_pane 2>/dev/null || true)
    state=$(tmux show-options -qv -t "$WATCH_SESSION" @watch_state 2>/dev/null || true)
    detail=$(tmux show-options -qv -t "$WATCH_SESSION" @watch_detail 2>/dev/null || true)
    updated=$(tmux show-options -qv -t "$WATCH_SESSION" @watch_updated 2>/dev/null || true)
    goal=$(sqlite3 -readonly "$CODEX_HOME_DIR/goals_1.sqlite" \
        "SELECT status FROM thread_goals WHERE thread_id='$THREAD_ID';" 2>/dev/null | head -n 1)
    pane_state=$(tmux display-message -p -t "=$target" '#{pane_current_command}/dead=#{pane_dead}' 2>/dev/null || printf 'missing')
    printf 'WATCHDOG=RUNNING state=%s detail=%s updated=%s goal=%s target=%s pane=%s\n' \
        "${state:-unknown}" "${detail:-none}" "${updated:-unknown}" "${goal:-missing}" "${target:-unknown}" "$pane_state"
    tmux list-panes -t "=$WATCH_SESSION" \
        -F 'watcher=#{session_name} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command}'
    tail -n 20 "$LOG" 2>/dev/null || true
}

stop_watchdog() {
    if tmux has-session -t "=$WATCH_SESSION" 2>/dev/null; then
        restore_status_bar
        tmux kill-session -t "=$WATCH_SESSION"
        printf 'STOPPED %s\n' "$WATCH_SESSION"
    else
        printf 'already stopped: %s\n' "$WATCH_SESSION"
    fi
}

self_test() {
    local BASE_WAIT=120 MAX_WAIT=3600 WATCH_CODEX_NO_JITTER=1
    is_uuid '01234567-89ab-cdef-0123-456789abcdef'
    if is_uuid 'latest'; then
        return 1
    fi
    [[ $(classify codex active '• Working (12s • esc to interrupt)' 1) == busy ]]
    [[ $(classify codex blocked '◦ Working (12s • esc to interrupt)' 0) == busy ]]
    [[ $(classify codex blocked '⠹ Working (12s • esc to interrupt)' 0) == busy ]]
    [[ $(classify codex blocked 'Goal blocked (/goal resume)' 0) == blocked ]]
    [[ $(classify codex active '■ exceeded retry limit, last status: 429 Too Many Requests' 0) == rate_limited ]]
    [[ $(classify codex active '■ unexpected status 500 Internal Server Error' 0) == provider_unavailable ]]
    [[ $(classify codex active '■ unexpected status 502 Bad Gateway' 0) == provider_unavailable ]]
    [[ $(classify codex active '■ unexpected status 503 Service Unavailable: system cpu overloaded' 0) == provider_unavailable ]]
    [[ $(classify codex active '■ unexpected status 504 Gateway Timeout' 0) == provider_unavailable ]]
    [[ $(classify codex active '■ unexpected status 401 Unauthorized' 0) == idle ]]
    [[ $(classify codex active '■ unexpected status 403 Forbidden' 0) == idle ]]
    [[ $(classify codex active $'■ unexpected status 503 Service Unavailable\n• Explored current state' 0) == idle ]]
    [[ $(classify codex '' '■ unexpected status 503 Service Unavailable' 0) == untracked ]]
    [[ $(classify codex complete '■ unexpected status 503 Service Unavailable' 0) == terminal:complete ]]
    [[ $(classify codex active 'ready' 2) == subagent_failed ]]
    [[ $(classify shell active 'prompt' 0) == stopped ]]
    [[ $(classify unknown active 'prompt' 0) == unsafe ]]
    [[ $(classify codex usage_limited 'ready' 0) == terminal:usage_limited ]]
    [[ $(classify codex '' 'ready' 0) == untracked ]]
    [[ $(backoff_delay 0) == 120 ]]
    [[ $(backoff_delay 1) == 240 ]]
    [[ $(backoff_delay 8) == 3600 ]]
    local tmp rollout THREAD_ID='01234567-89ab-cdef-0123-456789abcdef'
    tmp=$(mktemp -d)
    rollout="$tmp/rollout.jsonl"
    printf '%s\n' \
        '{"type":"event_msg","payload":{"type":"user_message","message":"继续原任务"}}' \
        >"$rollout"
    ROLLOUT=$rollout
    rollout_has_user_message_after 0 "$CONTINUE_TEXT"
    rm -rf -- "$tmp"
    printf 'SELF_TEST_OK\n'
}

COMMAND=${1-}
CODEX_HOME_DIR=${CODEX_HOME:-${HOME}/.codex}
SCRIPT_PATH=$(realpath "$0")
[[ "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || { fail 'WATCH_CODEX_POLL_SECONDS must be a positive integer'; exit 2; }

case "$COMMAND" in
    self-test)
        self_test
        ;;
    doctor|start|run)
        TARGET_PANE=${2-}
        THREAD_ID=${3-}
        LAUNCHER=${4:-codex}
        WORKDIR=${5:-$PWD}
        BASE_WAIT=${6:-120}
        MAX_WAIT=${7:-3600}
        [[ -n "$TARGET_PANE" && -n "$THREAD_ID" ]] || { usage; exit 2; }
        [[ "$BASE_WAIT" =~ ^[1-9][0-9]*$ && "$MAX_WAIT" =~ ^[1-9][0-9]*$ && "$BASE_WAIT" -le "$MAX_WAIT" ]] \
            || fail 'wait values must be positive integers and BASE_WAIT <= MAX_WAIT' || exit 2
        set_paths
        if [[ "$COMMAND" == doctor ]]; then
            doctor
        elif [[ "$COMMAND" == start ]]; then
            start_watchdog
        else
            require_tools
            is_uuid "$THREAD_ID" || fail "invalid thread UUID: $THREAD_ID" || exit 1
            resolve_launcher
            find_rollout
            validate_rollout
            run_watchdog
        fi
        ;;
    status|stop)
        THREAD_ID=${2-}
        is_uuid "$THREAD_ID" || { usage; exit 2; }
        set_paths
        "${COMMAND}_watchdog"
        ;;
    *)
        usage
        exit 2
        ;;
esac
