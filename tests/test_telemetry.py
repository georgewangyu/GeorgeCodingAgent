"""Exercise the shipped hook with a fake network executable, never real telemetry."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "usage_telemetry.py"


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = self.root / "requests.jsonl"
        fake = self.root / "curl"
        fake.write_text(
            f"#!{sys.executable}\nimport os,sys,json\n"
            "body=sys.stdin.read()\n"
            "with open(os.environ['TEST_REQUESTS'],'a') as f: "
            "f.write(json.dumps({'args':sys.argv[1:],'body':json.loads(body)})+'\\n')\n"
            "if os.environ.get('TEST_CURL_HANG')=='1':\n import signal; signal.pause()\n"
            "print(os.environ.get('TEST_HTTP_STATUS','200'))\n"
            "sys.exit(int(os.environ.get('TEST_CURL_EXIT','0')))\n"
        )
        fake.chmod(0o700)
        self.env = {
            "PATH": str(self.root) + os.pathsep + os.defpath,
            "HOME": str(self.root),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "GEORGE_CODING_AGENT_TELEMETRY": "1",
            "GEORGE_CODING_AGENT_POSTHOG_TOKEN": "phc_synthetic_project_token",
            "GEORGE_CODING_AGENT_POSTHOG_HOST": "https://us.i.posthog.com",
            "TEST_REQUESTS": str(self.requests),
        }

    def run_hook(self, value, **env):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=value if isinstance(value, str) else json.dumps(value),
            text=True, capture_output=True, env={**self.env, **env}, timeout=5,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        return [json.loads(line) for line in self.requests.read_text().splitlines()] if self.requests.exists() else []

    def test_explicit_refusals_and_incomplete_config_never_write_or_send(self):
        for overrides in (
            {"GEORGE_CODING_AGENT_TELEMETRY": ""},
            {"GEORGE_CODING_AGENT_TELEMETRY": "0"},
            {"GEORGE_CODING_AGENT_TELEMETRY": "true"},
            {"GEORGE_CODING_AGENT_TELEMETRY": "no"},
            {"GEORGE_CODING_AGENT_POSTHOG_TOKEN": ""},
            {"GEORGE_CODING_AGENT_POSTHOG_TOKEN": "phx_personal_key"},
            {"GEORGE_CODING_AGENT_POSTHOG_HOST": "https://unrelated.example"},
            {"DO_NOT_TRACK": "1"},
            {"DISABLE_TELEMETRY": "1"},
            {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
            {"DO_NOT_TRACK": "true"},
            {"CI": "true"},
            {"CONTINUOUS_INTEGRATION": "1"},
            {"GITHUB_ACTIONS": "true"},
        ):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}, **overrides), [])
                self.assertFalse((self.root / "state").exists())

    def test_first_seen_is_stable_across_sessions(self):
        self.run_hook({"hook_event_name": "SessionStart", "session_id": "secret-session"})
        calls = self.run_hook({"hook_event_name": "SessionStart", "session_id": "other-session"})
        self.assertEqual(len(calls), 1)
        event = calls[0]["body"]["batch"][0]
        self.assertEqual(event["event"], "george_coding_agent.first_seen")
        self.assertEqual(set(event["properties"]), {
            "distinct_id", "plugin", "plugin_version", "schema_version", "host",
            "$process_person_profile", "$geoip_disable", "$ip", "package", "telemetry_test",
        })
        self.assertNotIn("secret-session", json.dumps(calls))

    def test_typed_and_agent_skills_emit_only_known_names_without_arguments(self):
        self.run_hook({"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command",
                       "command_source": "plugin", "command_name": "george-coding-agent:code-review-and-quality",
                       "command_args": "password=secret", "prompt": "PRIVATE PROMPT", "cwd": "/private/project"})
        calls = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_use_id": "secret-tool",
                               "tool_input": {"skill": "george-coding-agent:test-driven-development", "args": "SECRET"}})
        events = [e for call in calls for e in call["body"]["batch"]]
        self.assertEqual([e["event"] for e in events], ["george_coding_agent.first_seen", "george_coding_agent.skill_invoked", "george_coding_agent.skill_invoked"])
        self.assertEqual([e["properties"]["invocation_source"] for e in events[1:]], ["typed", "agent"])
        self.assertEqual(len({e["properties"]["distinct_id"] for e in events}), 1)
        wire = json.dumps(calls)
        for secret in ("password", "PRIVATE PROMPT", "/private/project", "SECRET", "secret-tool"):
            self.assertNotIn(secret, wire)
        for event in events:
            self.assertFalse(event["properties"]["$process_person_profile"])
            self.assertTrue(event["properties"]["$geoip_disable"])
            self.assertIsNone(event["properties"]["$ip"])

    def test_other_plugins_unknown_skills_and_malformed_input_never_send(self):
        for value in (
            "not json", "[]", "{}", "x" * 65537,
            {"hook_event_name": "UserPromptSubmit", "prompt": "/george-coding-agent:test-driven-development"},
            {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command", "command_source": "plugin", "command_name": "other:test-driven-development"},
            {"hook_event_name": "UserPromptExpansion", "expansion_type": "mcp_prompt", "command_source": "plugin", "command_name": "george-coding-agent:test-driven-development"},
            {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "test-driven-development"}},
            {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "george-coding-agent:private-unknown-skill"}},
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"skill": "george-coding-agent:test-driven-development"}},
            {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": []},
        ):
            with self.subTest(value=str(value)[:90]):
                self.assertEqual(self.run_hook(value), [])
        self.assertFalse((self.root / "state").exists())

    def test_network_failure_is_silent_and_retries_first_seen_on_next_event(self):
        self.run_hook({"hook_event_name": "SessionStart"}, TEST_CURL_EXIT="28")
        calls = self.run_hook({"hook_event_name": "SessionStart"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["body"]["batch"][0]["properties"]["distinct_id"], calls[1]["body"]["batch"][0]["properties"]["distinct_id"])
        self.assertIn("--max-time", calls[0]["args"])
        self.assertIn("--retry", calls[0]["args"])
        self.assertNotIn("phc_synthetic_project_token", json.dumps(calls[0]["args"]))

    def test_corrupt_or_symlinked_state_fails_closed(self):
        state = self.root / "state" / "george-coding-agent" / "telemetry"
        state.mkdir(parents=True, mode=0o700)
        (state / "state.json").write_text("broken")
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}), [])
        (state / "state.json").unlink()
        target = self.root / "unrelated"
        target.write_text("do not touch")
        (state / "state.json").symlink_to(target)
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}), [])
        self.assertEqual(target.read_text(), "do not touch")

    def test_default_on_uses_bundled_project_without_setting_any_opt_in(self):
        self.env.pop("GEORGE_CODING_AGENT_POSTHOG_TOKEN")
        self.env.pop("GEORGE_CODING_AGENT_POSTHOG_HOST")
        self.env.pop("GEORGE_CODING_AGENT_TELEMETRY")
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"},
                                       GEORGE_CODING_AGENT_POSTHOG_HOST="https://eu.i.posthog.com"), [])
        calls = self.run_hook({"hook_event_name": "SessionStart"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["body"]["api_key"], json.loads((ROOT / "telemetry.json").read_text())["public_project_token"])

    def run_control(self, command, **env):
        result = subprocess.run([sys.executable, str(SCRIPT), command],
                                text=True, capture_output=True, env={**self.env, **env}, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.requests.exists(), "control command captured an event")
        self.assertFalse((self.root / "state").exists(), "control command created adoption state")
        return result.stdout

    def test_persistent_disable_precedes_first_capture_and_survives_new_process(self):
        self.env.pop("GEORGE_CODING_AGENT_TELEMETRY")
        self.assertEqual(self.run_control("disable"), "Telemetry disabled.\n")
        path = self.root / "config" / "george-coding-agent" / "telemetry.json"
        self.assertEqual(json.loads(path.read_text()), {"enabled": False})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}), [])
        self.assertEqual(self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Skill",
                                       "tool_input": {"skill": "george-coding-agent:test-driven-development"}},
                                       GEORGE_CODING_AGENT_TELEMETRY="1"), [])
        self.assertEqual(self.run_control("status"), "Telemetry disabled.\n")

    def test_enable_command_never_captures_and_cannot_override_inherited_refusal(self):
        self.run_control("disable")
        self.run_control("enable", GEORGE_CODING_AGENT_TELEMETRY="0")
        self.assertEqual(self.run_control("status", GEORGE_CODING_AGENT_TELEMETRY="0"), "Telemetry disabled.\n")
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}, CI="true"), [])
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}, DO_NOT_TRACK="1"), [])

    def test_invalid_unreadable_or_symlinked_consent_fails_closed(self):
        directory = self.root / "config" / "george-coding-agent"
        directory.mkdir(parents=True)
        path = directory / "telemetry.json"
        for value in ("broken", "{}", '{"enabled":"false"}', "x" * 1025):
            path.write_text(value)
            self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}), [])
        path.unlink()
        target = self.root / "other-config.json"
        target.write_text('{"enabled":true}')
        path.symlink_to(target)
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}), [])
        self.assertFalse((self.root / "state").exists())

    def test_smoke_is_separate_from_production_adoption_and_honors_opt_out(self):
        self.env.pop("GEORGE_CODING_AGENT_TELEMETRY")
        self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"},
                                       GEORGE_CODING_AGENT_TELEMETRY_SMOKE="1", DO_NOT_TRACK="1"), [])
        calls = self.run_hook({"hook_event_name": "SessionStart"},
                              GEORGE_CODING_AGENT_TELEMETRY_SMOKE="1")
        first = calls[0]["body"]["batch"][0]
        self.assertEqual(first["event"], "george_coding_agent.smoke.first_seen")
        self.assertTrue(first["properties"]["telemetry_test"])
        self.assertEqual(first["properties"]["package"], "george-coding-agent")
        self.assertFalse((self.root / "state/george-coding-agent/telemetry").exists())
        calls = self.run_hook({"hook_event_name": "SessionStart"})
        production = calls[1]["body"]["batch"][0]
        self.assertEqual(production["event"], "george_coding_agent.first_seen")
        self.assertFalse(production["properties"]["telemetry_test"])
        self.assertNotEqual(first["properties"]["distinct_id"], production["properties"]["distinct_id"])

    def test_unsuccessful_http_response_does_not_mark_first_seen_delivered(self):
        self.run_hook({"hook_event_name": "SessionStart"}, TEST_HTTP_STATUS="503")
        calls = self.run_hook({"hook_event_name": "SessionStart"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["body"]["batch"][0]["event"], "george_coding_agent.first_seen")

    def test_busy_lock_drops_event_instead_of_waiting(self):
        state = self.root / "state" / "george-coding-agent" / "telemetry"
        state.mkdir(parents=True, mode=0o700)
        lock = state / "lock"
        lock.touch(mode=0o600)
        with lock.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_hook({"hook_event_name": "SessionStart"}), [])
        self.assertFalse((state / "state.json").exists())

    def test_hung_transport_and_unclosed_input_cannot_leave_hook_running(self):
        self.run_hook({"hook_event_name": "SessionStart"}, TEST_CURL_HANG="1")
        process = subprocess.Popen([sys.executable, str(SCRIPT)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        try:
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(process.stdout.read(), b"")
            self.assertEqual(process.stderr.read(), b"")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_hook_manifest_is_async_and_uses_real_plugin_root_paths(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptExpansion", "PreToolUse"})
        for groups in hooks.values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertIs(hook["async"], True)
                    self.assertIn('${CLAUDE_PLUGIN_ROOT}', hook["command"])
                    self.assertNotIn("/Users/", hook["command"])


if __name__ == "__main__":
    unittest.main()
