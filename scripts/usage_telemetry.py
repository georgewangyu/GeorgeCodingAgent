#!/usr/bin/env python3
"""Minimal Claude plugin usage with persistent opt-out. Never emit hook output."""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

PLUGIN = "george-coding-agent"
HOSTS = {"https://us.i.posthog.com", "https://eu.i.posthog.com"}
MAX_INPUT = 65536
ROOT = Path(__file__).resolve().parents[1]


def deadline(_signum, _frame):
    raise TimeoutError("telemetry deadline")


def enabled_flag(name):
    return os.environ.get(name, "").lower() not in ("", "0", "false", "no")


def consent_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        raise ValueError("configuration path must be absolute")
    directory = base.resolve() / PLUGIN
    if directory.is_symlink():
        raise ValueError("unsafe configuration directory")
    return directory / "telemetry.json"


def persistently_disabled():
    path = consent_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return True
        raw = stream.read(1025)
    value = json.loads(raw)
    if len(raw) > 1024 or not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        return True
    return not value["enabled"]


def configuration():
    # Absence follows the disclosed default. Any existing non-1 value is a refusal.
    if ("GEORGE_CODING_AGENT_TELEMETRY" in os.environ
            and os.environ["GEORGE_CODING_AGENT_TELEMETRY"] != "1"):
        return None
    if any(enabled_flag(name) for name in (
        "DO_NOT_TRACK", "DISABLE_TELEMETRY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS",
    )):
        return None
    if persistently_disabled():
        return None
    if (("GEORGE_CODING_AGENT_POSTHOG_TOKEN" in os.environ)
            != ("GEORGE_CODING_AGENT_POSTHOG_HOST" in os.environ)):
        return None
    defaults = json.loads((ROOT / "telemetry.json").read_text())
    token = os.environ.get("GEORGE_CODING_AGENT_POSTHOG_TOKEN", defaults["public_project_token"])
    host = os.environ.get("GEORGE_CODING_AGENT_POSTHOG_HOST", defaults["host"])
    if not re.fullmatch(r"phc_[A-Za-z0-9_]{1,196}", token) or host not in HOSTS:
        return None
    return token, host


def invocation(data, manifest):
    event = data.get("hook_event_name")
    if event == "SessionStart":
        return {}
    if event == "UserPromptExpansion":
        if data.get("expansion_type") != "slash_command" or data.get("command_source") != "plugin":
            return None
        name, source = data.get("command_name"), "typed"
    elif event == "PreToolUse" and data.get("tool_name") == "Skill":
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            return None
        name, source = tool_input.get("skill"), "agent"
    else:
        return None
    names = set(manifest["skills"])
    names.update("role-" + agent["name"] for agent in manifest["agents"])
    names.add(manifest.get("router", PLUGIN + "-skills"))
    if not isinstance(name, str) or not name.startswith(PLUGIN + ":"):
        return None
    skill = name[len(PLUGIN) + 1:]
    if skill not in names:
        return None
    return {"skill": skill, "invocation_source": source}


def private_file(path):
    """Reject symlinks and other users' files instead of following local state."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError("unsafe local state")
    return os.fdopen(fd, "r+")


def save(path, value):
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def send(token, host, events):
    # curl bounds DNS/connect/transfer time; body is stdin, never process arguments.
    result = subprocess.run([
        "curl", "--disable", "--silent", "--fail", "--noproxy", "*", "--proto", "=https",
        "--connect-timeout", "1", "--max-time", "2", "--retry", "0",
        "--output", os.devnull, "--write-out", "%{http_code}", "--header", "Content-Type: application/json",
        "--data-binary", "@-", host + "/batch/",
    ], input=json.dumps({"api_key": token, "batch": events}).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2.5, check=False)
    return result.returncode == 0 and result.stdout.strip() == b"200"


def run():
    config = configuration()
    if config is None:
        return
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return
    data = json.loads(raw)
    if not isinstance(data, dict):
        return
    manifest = json.loads((ROOT / "jarvis-extension.json").read_text())
    detail = invocation(data, manifest)
    if detail is None:
        return
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not base.is_absolute():
        return
    smoke = os.environ.get("GEORGE_CODING_AGENT_TELEMETRY_SMOKE") == "1"
    directory = base.resolve() / PLUGIN / ("telemetry-smoke" if smoke else "telemetry")
    # Resolve components first: no state follows a symlinked parent directory.
    if any(p.is_symlink() for p in [directory, *directory.parents]):
        return
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        return
    with private_file(directory / "lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = directory / "state.json"
        with private_file(path) as stream:
            raw_state = stream.read(4097)
        if raw_state:
            state = json.loads(raw_state)
            if (not isinstance(state, dict) or len(raw_state) > 4096
                    or str(uuid.UUID(state["installation_id"])) != state["installation_id"]
                    or not isinstance(state["first_seen_sent"], bool)):
                return
        else:
            state = {"installation_id": str(uuid.uuid4()), "first_seen_sent": False}
            save(path, state)
        common = {
            "distinct_id": state["installation_id"], "plugin": PLUGIN,
            "plugin_version": manifest["version"], "schema_version": 2, "host": "claude_code",
            "package": PLUGIN, "telemetry_test": smoke,
            "$process_person_profile": False, "$geoip_disable": True, "$ip": None,
        }
        prefix = "george_coding_agent.smoke" if smoke else "george_coding_agent"
        events = []
        if not state["first_seen_sent"]:
            events.append({"event": prefix + ".first_seen", "properties": common})
        if detail:
            events.append({"event": prefix + ".skill_invoked", "properties": {**common, **detail}})
        if events and send(*config, events):
            state["first_seen_sent"] = True
            save(path, state)


def control(command):
    # Control commands never enter the capture path, even when telemetry is on.
    if command in ("disable", "enable"):
        path = consent_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022 or path.is_symlink():
            raise ValueError("unsafe configuration path")
        save(path, {"enabled": command == "enable"})
        print("Telemetry " + ("disabled." if command == "disable" else "enabled unless an inherited refusal applies."))
    elif command == "status":
        print("Telemetry " + ("enabled." if configuration() is not None else "disabled."))
    else:
        raise ValueError("use disable, enable or status")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) == 2 else None
    exit_code = 0
    try:
        # Async hooks have no host-enforced timeout. Bound the entire process too.
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 3)
        if len(sys.argv) > 1:
            if command is None:
                raise ValueError("use disable, enable or status")
            control(command)
        else:
            run()
    except Exception:
        if len(sys.argv) > 1:
            print("Telemetry command failed; no event was sent.", file=sys.stderr)
            exit_code = 1
        # Hook failures cannot fail or inject context into coding work.
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.exit(exit_code)
