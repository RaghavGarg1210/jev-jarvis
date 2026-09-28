"""Capability boundary tests. Native apps are never driven by this suite."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jarvis.actions import ActionError, ActionExecutor, DEFAULT_CONFIG, load_config


def action(kind, **args):
    return {"kind": kind, "args": args}


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config["contacts"] = {"alice": "+15555550123"}
        self.config["shortcuts"] = {"meeting": {
            "name": "Meeting prep", "description": "Open my meeting notes and calendar.",
        }}
        self.executor = ActionExecutor(self.state, self.config, live=True)

    def test_entire_plan_validation_is_side_effect_free(self):
        with patch("jarvis.actions.subprocess.run") as native:
            with self.assertRaises(ActionError):
                self.executor.validate([action("open_app", app="safari"), action("shell", command="id")])
            native.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_all_supported_actions_are_inert_in_simulation(self):
        executor = ActionExecutor(self.state, self.config, live=False)
        raw = [action("open_app", app="safari"), action("open_url", url="https://example.com"),
               action("search_web", query="fun macOS tools"),
               action("send_message", contact="alice", body="hello"),
               action("create_note", title="Ideas", content="One idea"),
               action("set_volume", level=42), action("timer", seconds=1), action("say", text="hello"),
               action("run_shortcut", shortcut="meeting")]
        with patch("jarvis.actions.subprocess.run") as native:
            for item in executor.validate(raw):
                self.assertEqual(executor.execute(item)["status"], "simulated")
            native.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_open_app_uses_only_configured_name_and_fixed_arguments(self):
        canonical = self.executor.validate([action("open_app", app="safari")])[0]
        self.assertEqual(canonical["args"], {"app": "Safari"})
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            self.executor.execute(canonical)
        self.assertEqual(native.call_args.args[0], ["/usr/bin/open", "-a", "Safari"])
        self.assertNotIn("shell", native.call_args.kwargs)
        self.assertEqual(native.call_args.kwargs["timeout"], 20)
        for app in ("Unknown app", "/bin/sh", "Safari; touch /tmp/evil", "-a Calculator"):
            with self.subTest(app=app), self.assertRaises(ActionError):
                self.executor.validate([action("open_app", app=app)])

    def test_message_injection_is_passed_as_literal_argv(self):
        body = 'hello\"\nend tell\ndo shell script "touch /tmp/unsafe"\n$(id) `whoami`'
        preview = self.executor.validate([action("send_message", contact="alice", body=body)])[0]
        self.assertIn("+15555550123", preview["detail"])
        self.assertIn(body, preview["detail"])
        self.assertEqual(preview["risk"], "external")
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            self.executor.execute(preview)
        command = native.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertNotIn(body, command[2])
        self.assertEqual(command[3:], ["--", "+15555550123", body])

    def test_contact_resolution_is_exact_and_copied_at_creation(self):
        for contact in ("ali", "+15555550123", "someone@example.com"):
            with self.subTest(contact=contact), self.assertRaises(ActionError):
                self.executor.validate([action("send_message", contact=contact, body="hi")])
        self.config["contacts"]["alice"] = "+15555550999"
        preview = self.executor.validate([action("send_message", contact="alice", body="hi")])[0]
        self.assertIn("+15555550123", preview["detail"])
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            self.executor.execute(preview)
        self.assertEqual(native.call_args.args[0][-2], "+15555550123")

    def test_contact_alias_case_and_surrounding_spaces_are_normalized(self):
        preview = self.executor.validate([action("send_message", contact=" Alice ", body="hi")])[0]
        self.assertEqual(preview["args"]["contact"], "alice")
        self.assertIn("+15555550123", preview["detail"])

    def test_rejects_unknown_fields_and_unbounded_arguments(self):
        invalid = [
            {"kind": "open_app", "args": {"app": "safari"}, "risk": "local"},
            action("open_app", app="safari", command="id"),
            action("send_message", contact="alice", body="x" * 4001),
            action("send_message", contact="alice", body="hi\x00bad"),
            action("say", text="bad\ud800"), action("timer", seconds=True),
            action("timer", seconds=0), action("timer", seconds=86401),
            action("set_volume", level=-1), action("set_volume", level=101),
            action("set_volume", level="50"), action("set_volume", level=True),
            action("create_note", title="", content="yes"),
            action("search_web", query="\n"),
        ]
        for raw in invalid:
            with self.subTest(raw=repr(raw)[:120]), self.assertRaises(ActionError):
                self.executor.validate([raw])
        for invalid_plan in ([], {}, None, [action("timer", seconds=1)] * 13):
            with self.subTest(plan=invalid_plan), self.assertRaises(ActionError):
                self.executor.validate(invalid_plan)

    def test_url_policy_and_query_encoding(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "mailto:hi@example.com",
                    "https://user:pass@example.com", "https://example.com\n", "https:///missing",
                    "https://example.com:99999", "https://example.com\\@other.com", "https://example .com"):
            with self.subTest(url=url), self.assertRaises(ActionError):
                self.executor.validate([action("open_url", url=url)])
        self.executor.validate([action("open_url", url="http://localhost:8000/path?q=x")])
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            self.executor.execute(action("search_web", query="cats & dogs #1"))
        self.assertEqual(native.call_args.args[0][-1], "https://duckduckgo.com/?q=cats+%26+dogs+%231")

    def test_volume_bounds_and_fixed_script(self):
        for level in (0, 100):
            with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
                self.executor.execute(action("set_volume", level=level))
            self.assertEqual(native.call_args.args[0][-2:], ["--", str(level)])

    def test_speech_uses_stdin_even_when_text_starts_with_options(self):
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            self.executor.execute(action("say", text="-o /tmp/do-not-create"))
        self.assertEqual(native.call_args.args[0], ["/usr/bin/say"])
        self.assertEqual(native.call_args.kwargs["input"], "-o /tmp/do-not-create")

    def test_shortcut_alias_resolves_to_snapshotted_name_and_effects(self):
        preview = self.executor.validate([action("run_shortcut", shortcut=" Meeting ")])[0]
        self.assertEqual(preview["args"], {"shortcut": "meeting"})
        self.assertEqual(preview["title"], "Run shortcut: Meeting prep")
        self.assertIn("Open my meeting notes and calendar.", preview["detail"])
        self.assertIn("own actions define its effects", preview["detail"])
        self.assertEqual(preview["risk"], "external")
        self.assertFalse(preview["reversible"])
        self.config["shortcuts"]["meeting"]["name"] = "Different shortcut"
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            self.executor.execute(preview)
        self.assertEqual(native.call_args.args[0], ["/usr/bin/shortcuts", "run", "Meeting prep"])
        self.assertEqual(native.call_args.kwargs["timeout"], 60)

    def test_shortcuts_reject_unconfigured_names_and_extra_arguments(self):
        invalid = [action("run_shortcut", shortcut="Meeting prep"),
                   action("run_shortcut", shortcut="meet"),
                   action("run_shortcut", shortcut="--help"),
                   action("run_shortcut", shortcut="meeting", input="arbitrary input")]
        with patch("jarvis.actions.subprocess.run") as native:
            for raw in invalid:
                with self.subTest(raw=raw), self.assertRaises(ActionError):
                    self.executor.execute(raw)
            native.assert_not_called()

    def test_shortcut_name_is_one_literal_argument(self):
        config = copy.deepcopy(self.config)
        name = "Meeting; $(id) ' quoted"
        config["shortcuts"]["meeting"]["name"] = name
        executor = ActionExecutor(self.state, config, live=True)
        with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run") as native:
            executor.execute(action("run_shortcut", shortcut="meeting"))
        self.assertEqual(native.call_args.args[0], ["/usr/bin/shortcuts", "run", name])
        self.assertNotIn("shell", native.call_args.kwargs)

    def test_subprocess_errors_do_not_echo_private_arguments(self):
        private = "secret message"
        failures = [subprocess.CalledProcessError(1, [private], stderr=private),
                    subprocess.TimeoutExpired([private], 20, stderr=private), OSError(private)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("jarvis.actions.sys.platform", "darwin"), patch("jarvis.actions.subprocess.run", side_effect=failure):
                    with self.assertRaises(ActionError) as caught:
                        self.executor.execute(action("send_message", contact="alice", body=private))
                self.assertNotIn(private, str(caught.exception))

    def test_non_macos_fails_before_native_calls(self):
        with patch("jarvis.actions.sys.platform", "linux"), patch("jarvis.actions.subprocess.run") as native:
            with self.assertRaisesRegex(ActionError, "requires macOS"):
                self.executor.execute(action("open_app", app="safari"))
            native.assert_not_called()

    def test_note_content_and_undo(self):
        result = self.executor.execute(action("create_note", title="../ideas", content="Hello ☕\nsecond line"))
        path = Path(result["path"])
        self.assertEqual(path.parent, self.state / "notes")
        self.assertEqual(path.read_text(), "# ../ideas\n\nHello ☕\nsecond line\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.executor.undo(result["undo"])["status"], "done")
        self.assertFalse(path.exists())

    def test_user_modified_note_is_preserved(self):
        result = self.executor.execute(action("create_note", title="Ideas", content="original"))
        path = Path(result["path"])
        path.write_text("user wrote something new")
        with self.assertRaisesRegex(ActionError, "changed"):
            self.executor.undo(result["undo"])
        self.assertEqual(path.read_text(), "user wrote something new")

    def test_undo_does_not_follow_file_symlink(self):
        result = self.executor.execute(action("create_note", title="Ideas", content="original"))
        path = Path(result["path"])
        outside = self.root / "important.txt"
        outside.write_text(path.read_text())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ActionError):
            self.executor.undo(result["undo"])
        self.assertTrue(path.is_symlink())
        self.assertTrue(outside.exists())

    def test_undo_does_not_remove_hardlinked_note(self):
        result = self.executor.execute(action("create_note", title="Ideas", content="original"))
        path = Path(result["path"])
        (self.root / "linked-note.md").hardlink_to(path)
        with self.assertRaises(ActionError):
            self.executor.undo(result["undo"])
        self.assertTrue(path.exists())

    def test_rejects_symlink_notes_directory_on_create_and_undo(self):
        result = self.executor.execute(action("create_note", title="Ideas", content="original"))
        notes = self.state / "notes"
        moved = self.root / "moved"
        notes.rename(moved)
        notes.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ActionError):
            self.executor.execute(action("create_note", title="Bad", content="no"))
        with self.assertRaises(ActionError):
            self.executor.undo(result["undo"])
        self.assertEqual(len(list(moved.iterdir())), 1)

    def test_rejects_symlink_state_directory(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.state.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ActionError):
            self.executor.execute(action("create_note", title="Bad", content="no"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_undo_rejects_path_traversal_before_filesystem_access(self):
        for filename in ("../important.txt", "/etc/passwd", "hello.md", "a" * 32 + "/../b.md"):
            with self.subTest(filename=filename), self.assertRaises(ActionError):
                self.executor.undo({"kind": "delete_note", "filename": filename, "sha256": "0" * 64})
        self.assertFalse(self.state.exists())

    def test_note_creation_is_exclusive(self):
        with patch("jarvis.actions.uuid.uuid4") as generated:
            generated.return_value.hex = "a" * 32
            first = self.executor.execute(action("create_note", title="First", content="keep"))
            with self.assertRaises(ActionError):
                self.executor.execute(action("create_note", title="Second", content="replace"))
        self.assertIn("keep", Path(first["path"]).read_text())

    def test_timer_and_undo_are_structured(self):
        with patch("jarvis.actions.time.time", return_value=100):
            result = self.executor.execute(action("timer", seconds=90, label="Tea"))
        self.assertEqual(result["timer"]["ends_at"], 190)
        self.assertEqual(result["timer"]["label"], "Tea")
        self.assertEqual(self.executor.undo(result["undo"])["timer_id"], result["timer"]["id"])
        self.assertFalse(self.state.exists())

    def test_simulated_undo_has_no_effect(self):
        result = self.executor.execute(action("create_note", title="Ideas", content="keep"))
        simulator = ActionExecutor(self.state, self.config, live=False)
        self.assertEqual(simulator.undo(result["undo"])["status"], "simulated")
        self.assertTrue(Path(result["path"]).exists())


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_defaults_and_file_overrides_do_not_mutate_global_defaults(self):
        default = load_config()
        self.assertIn("focus", default["scenes"])
        self.write({"contacts": {"friend": "friend@example.com"}, "apps": {"browser": "Firefox"}})
        config = load_config(self.path)
        self.assertEqual(config["apps"]["browser"], "Firefox")
        self.assertEqual(config["apps"]["safari"], "Safari")
        self.assertEqual(config["contacts"]["friend"], "friend@example.com")
        self.assertEqual(DEFAULT_CONFIG["contacts"], {})

    def test_rejects_malformed_and_unknown_config(self):
        cases = [[], {"unknown": True}, {"contacts": {"friend": "friend"}},
                 {"contacts": {"friend": "+12"}}, {"apps": {"browser": "/bin/bash"}},
                 {"apps": {"browser": "-a"}}, {"apps": {"Bad Alias": "Safari"}},
                 {"apps": {"code": "Safari", "safari": "Terminal"}},
                 {"scenes": {"bad": {"title": "Bad", "description": "", "steps": [action("shell")]}}}]
        for value in cases:
            with self.subTest(value=value):
                self.write(value)
                with self.assertRaises(ActionError):
                    load_config(self.path)
        for text in ("not JSON", '{"contacts": {}, "contacts": {}}'):
            self.path.write_text(text)
            with self.assertRaises(ActionError):
                load_config(self.path)

    def test_example_config_is_valid(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "config.json"
        self.assertIn("reading", load_config(path)["scenes"])

    def test_shortcut_config_requires_name_and_explicit_description(self):
        invalid = [{"name": "Meeting prep"}, {"name": "Meeting prep", "description": ""},
                   {"name": "--help", "description": "Bad name"},
                   {"name": "line\nbreak", "description": "Bad name"},
                   {"name": "x" * 101, "description": "Too long"},
                   {"name": "Valid", "description": "x" * 501},
                   {"name": "Valid", "description": "Description", "args": []}]
        for shortcut in invalid:
            with self.subTest(shortcut=shortcut):
                self.write({"shortcuts": {"meeting": shortcut}})
                with self.assertRaises(ActionError):
                    load_config(self.path)
        self.write({"shortcuts": {"meeting": {"name": "Meeting prep", "description": "Open my calendar."}}})
        self.assertEqual(load_config(self.path)["shortcuts"]["meeting"]["name"], "Meeting prep")
        self.assertEqual(DEFAULT_CONFIG["shortcuts"], {})


if __name__ == "__main__":
    unittest.main()
