"""Small, explicit macOS capabilities. Model output never becomes shell code."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlencode, urlsplit
import uuid


class ActionError(ValueError):
    """An invalid action, unavailable capability, or safely stopped operation."""


DEFAULT_CONFIG: dict[str, Any] = {
    "apps": {
        "safari": "Safari",
        "notes": "Notes",
        "calendar": "Calendar",
        "reminders": "Reminders",
        "music": "Music",
        "messages": "Messages",
        "code": "Visual Studio Code",
        "terminal": "Terminal",
    },
    "contacts": {},
    "shortcuts": {},
    "scenes": {
        "focus": {
            "title": "Focus session",
            "description": "Open a browser and make space for one thing to finish.",
            "steps": [
                {"kind": "open_app", "args": {"app": "safari"}},
                {"kind": "create_note", "args": {
                    "title": "Focus session", "content": "One thing to finish:\n",
                }},
            ],
        },
        "reset": {
            "title": "Take a breather",
            "description": "Lower the volume and take five minutes.",
            "steps": [
                {"kind": "set_volume", "args": {"level": 25}},
                {"kind": "timer", "args": {"seconds": 300, "label": "Take a breather"}},
            ],
        },
        "build": {
            "title": "Build something",
            "description": "Open Visual Studio Code and GitHub.",
            "steps": [
                {"kind": "open_app", "args": {"app": "code"}},
                {"kind": "open_url", "args": {"url": "https://github.com"}},
            ],
        },
    },
}

_MESSAGE_SCRIPT = '''on run argv
    set recipientAddress to item 1 of argv
    set messageBody to item 2 of argv
    tell application "Messages"
        set messageService to first service whose service type is iMessage
        set messageBuddy to buddy recipientAddress of messageService
        send messageBody to messageBuddy
    end tell
end run'''

_VOLUME_SCRIPT = '''on run argv
    set outputVolume to (item 1 of argv) as integer
    set volume output volume outputVolume
end run'''

_RAW_KEYS = {"kind", "args"}
_CANONICAL_KEYS = _RAW_KEYS | {"title", "detail", "risk", "reversible"}
_NOTE_NAME = re.compile(r"[0-9a-f]{32}\.md\Z")
_ALIAS = re.compile(r"[a-z0-9][a-z0-9 _-]{0,63}\Z")
_MAX_NOTE_BYTES = 150_000


def _text(value: Any, name: str, maximum: int, *, multiline: bool = False,
          empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ActionError(f"{name} must be text of at most {maximum} characters.")
    if not empty and not value.strip():
        raise ActionError(f"{name} must not be empty.")
    allowed = {"\n", "\t"} if multiline else set()
    if any((ord(char) < 32 or ord(char) == 127) and char not in allowed for char in value):
        raise ActionError(f"{name} contains unsupported control characters.")
    # JSON permits lone surrogates, but neither argv nor UTF-8 note files do.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ActionError(f"{name} must be valid Unicode text.") from None
    return value


def _fields(value: Any, allowed: set[str], required: set[str], name: str) -> dict:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ActionError(f"{name} must be an object.")
    if set(value) - allowed or required - set(value):
        raise ActionError(f"{name} has missing or unknown fields.")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ActionError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _alias(value: Any, name: str) -> str:
    value = _text(value, name, 64)
    if not _ALIAS.fullmatch(value):
        raise ActionError(f"{name} must use lowercase letters, numbers, spaces, _ or -.")
    return value


def _url(value: Any) -> str:
    value = _text(value, "URL", 4096)
    if any(char.isspace() for char in value) or "\\" in value:
        raise ActionError("URLs must not contain whitespace or backslashes.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise ActionError("Invalid web URL.") from None
    if (parsed.scheme not in {"http", "https"} or not hostname
            or parsed.username is not None or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535)):
        raise ActionError("Use an http or https URL without embedded credentials.")
    return value


def _merged_config(raw: dict) -> dict:
    _fields(raw, {"apps", "contacts", "shortcuts", "scenes"}, set(), "Configuration")
    config = copy.deepcopy(DEFAULT_CONFIG)
    for key in ("apps", "contacts", "shortcuts", "scenes"):
        if key in raw:
            if not isinstance(raw[key], dict) or len(raw[key]) > 200:
                raise ActionError(f"Configuration {key} must be an object with at most 200 entries.")
            config[key].update(copy.deepcopy(raw[key]))
    for alias, name in config["apps"].items():
        _alias(alias, "App alias")
        name = _text(name, "Application name", 100)
        if name.startswith("-") or "/" in name or "\\" in name:
            raise ActionError("Application names must be names, not paths or command options.")
    for alias, name in config["apps"].items():
        for other_name in config["apps"].values():
            if alias.casefold() == other_name.casefold() and name != other_name:
                raise ActionError("An app alias conflicts with another configured application name.")
    for alias, destination in config["contacts"].items():
        _alias(alias, "Contact alias")
        destination = _text(destination, "Contact destination", 254)
        phone = re.fullmatch(r"\+[1-9][0-9]{6,14}", destination)
        email = re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", destination)
        if not phone and not email:
            raise ActionError("Contacts need an international +phone number or an email address.")
    for alias, shortcut in config["shortcuts"].items():
        _alias(alias, "Shortcut alias")
        _fields(shortcut, {"name", "description"}, {"name", "description"}, "Shortcut")
        name = _text(shortcut["name"], "Shortcut name", 100)
        if name.startswith("-"):
            raise ActionError("Shortcut names must not begin with command options.")
        _text(shortcut["description"], "Shortcut description", 500)
    for alias, scene in config["scenes"].items():
        _alias(alias, "Scene alias")
        _fields(scene, {"title", "description", "steps"}, {"title", "description", "steps"}, "Scene")
        _text(scene["title"], "Scene title", 100)
        _text(scene["description"], "Scene description", 300, empty=True)
        _validate_actions(scene["steps"], config)
    return config


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActionError("Configuration contains a duplicate field.")
        result[key] = value
    return result


def load_config(path: Path | None = None) -> dict:
    """Merge an optional JSON file with defaults; reject malformed configuration."""
    if path is None:
        return _merged_config({})
    try:
        if path.stat().st_size > 262_144:
            raise ActionError("Configuration file is too large.")
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ActionError("Cannot read configuration. Use a readable UTF-8 JSON file.") from None
    return _merged_config(raw)


def _resolve_app(value: Any, config: dict) -> str:
    value = _text(value, "Application", 100)
    names = {name.casefold(): name for name in config["apps"].values()}
    aliases = {alias.casefold(): name for alias, name in config["apps"].items()}
    if value.casefold() in aliases:
        return aliases[value.casefold()]
    if value.casefold() in names:
        return names[value.casefold()]
    raise ActionError("That app is not configured. Add its name to the app allowlist first.")


def _validate_actions(raw_actions: list[dict], config: dict) -> list[dict]:
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 12:
        raise ActionError("A plan needs between 1 and 12 actions.")
    actions = []
    for raw in raw_actions:
        _fields(raw, _RAW_KEYS, _RAW_KEYS, "Action")
        kind = _text(raw["kind"], "Action kind", 40)
        args = raw["args"]
        risk, reversible = "local", False
        if kind == "open_app":
            _fields(args, {"app"}, {"app"}, "Open app arguments")
            app = _resolve_app(args["app"], config)
            args = {"app": app}
            title, detail = f"Open {app}", f"Launch the configured macOS app {app}."
        elif kind == "open_url":
            _fields(args, {"url"}, {"url"}, "Open URL arguments")
            url = _url(args["url"])
            args = {"url": url}
            title, detail, risk = "Open web page", url, "external"
        elif kind == "search_web":
            _fields(args, {"query"}, {"query"}, "Search arguments")
            query = _text(args["query"], "Search query", 2000)
            args = {"query": query}
            title, detail, risk = "Search the web", f"DuckDuckGo: {query}", "external"
        elif kind == "send_message":
            _fields(args, {"contact", "body"}, {"contact", "body"}, "Message arguments")
            contact = _text(args["contact"], "Contact", 64).strip().casefold()
            if contact not in config["contacts"]:
                raise ActionError("Unknown contact. Use an exact alias from your contacts configuration.")
            body = _text(args["body"], "Message", 4000, multiline=True)
            args = {"contact": contact, "body": body}
            title = f"Send iMessage to {contact}"
            detail = f"To {config['contacts'][contact]}\n\n{body}"
            risk = "external"
        elif kind == "create_note":
            _fields(args, {"title", "content"}, {"title", "content"}, "Note arguments")
            note_title = _text(args["title"], "Note title", 160)
            content = _text(args["content"], "Note content", 30_000, multiline=True, empty=True)
            args = {"title": note_title, "content": content}
            title, detail, reversible = f"Create note: {note_title}", content, True
        elif kind == "set_volume":
            _fields(args, {"level"}, {"level"}, "Volume arguments")
            level = _integer(args["level"], "Volume", 0, 100)
            args = {"level": level}
            title, detail = f"Set volume to {level}%", "Change the system output volume."
        elif kind == "timer":
            _fields(args, {"seconds", "label"}, {"seconds"}, "Timer arguments")
            seconds = _integer(args["seconds"], "Timer seconds", 1, 86_400)
            label = _text(args.get("label", "Timer"), "Timer label", 120)
            args = {"seconds": seconds, "label": label}
            title, detail, reversible = f"Start timer: {label}", f"{seconds} seconds", True
        elif kind == "say":
            _fields(args, {"text"}, {"text"}, "Speech arguments")
            spoken = _text(args["text"], "Speech text", 2000, multiline=True)
            args = {"text": spoken}
            title, detail = "Speak aloud", spoken
        elif kind == "run_shortcut":
            _fields(args, {"shortcut"}, {"shortcut"}, "Shortcut arguments")
            alias = _text(args["shortcut"], "Shortcut alias", 64).strip().casefold()
            if alias not in config["shortcuts"]:
                raise ActionError("Unknown shortcut. Configure its exact name and effects before running it.")
            shortcut = config["shortcuts"][alias]
            args = {"shortcut": alias}
            title = f"Run shortcut: {shortcut['name']}"
            detail = shortcut["description"] + "\n\nThis shortcut's own actions define its effects."
            risk = "external"
        else:
            raise ActionError("Unsupported action. Only the documented capabilities are available.")
        actions.append({"kind": kind, "args": args, "title": title, "detail": detail,
                        "risk": risk, "reversible": reversible})
    return actions


class ActionExecutor:
    """Validate whole plans first, then run approved capabilities one at a time."""

    def __init__(self, state_dir: Path, config: dict, live: bool) -> None:
        self.state_dir = Path(state_dir).expanduser().absolute()
        self._config = _merged_config(config)
        self.live = bool(live)

    def validate(self, raw_actions: list[dict]) -> list[dict]:
        """Produce canonical preview data without writes or native calls."""
        return _validate_actions(raw_actions, self._config)

    def _run(self, command: list[str], *, timeout: int = 20) -> None:
        if sys.platform != "darwin":
            raise ActionError("This native action requires macOS. Use simulation on other platforms.")
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ActionError("The macOS action timed out. Its outcome may be uncertain; inspect the app before retrying.") from None
        except (OSError, subprocess.CalledProcessError):
            raise ActionError("The macOS action failed. Check that the app is installed and its permissions are enabled.") from None

    def execute(self, action: dict) -> dict:
        """Execute one approved action; simulation is always free of side effects."""
        _fields(action, _CANONICAL_KEYS, _RAW_KEYS, "Action")
        action = self.validate([{key: action[key] for key in _RAW_KEYS}])[0]
        if not self.live:
            return {"status": "simulated", "summary": action["title"]}
        kind, args = action["kind"], action["args"]
        if kind == "open_app":
            self._run(["/usr/bin/open", "-a", args["app"]])
        elif kind == "open_url":
            self._run(["/usr/bin/open", args["url"]])
        elif kind == "search_web":
            self._run(["/usr/bin/open", "https://duckduckgo.com/?" + urlencode({"q": args["query"]})])
        elif kind == "send_message":
            self._run(["/usr/bin/osascript", "-e", _MESSAGE_SCRIPT, "--",
                       self._config["contacts"][args["contact"]], args["body"]])
        elif kind == "create_note":
            return self._create_note(args)
        elif kind == "set_volume":
            self._run(["/usr/bin/osascript", "-e", _VOLUME_SCRIPT, "--", str(args["level"])])
        elif kind == "timer":
            timer_id = uuid.uuid4().hex
            return {"status": "done", "summary": action["title"],
                    "timer": {"id": timer_id, "label": args["label"], "ends_at": time.time() + args["seconds"]},
                    "undo": {"kind": "cancel_timer", "id": timer_id}}
        elif kind == "say":
            # stdin keeps leading hyphens and speech text out of option parsing.
            self._say(args["text"])
        elif kind == "run_shortcut":
            self._run(["/usr/bin/shortcuts", "run", self._config["shortcuts"][args["shortcut"]]["name"]], timeout=60)
        return {"status": "done", "summary": action["title"]}

    def _say(self, text: str) -> None:
        if sys.platform != "darwin":
            raise ActionError("Speech requires macOS. Use simulation on other platforms.")
        try:
            subprocess.run(["/usr/bin/say"], input=text, check=True, capture_output=True,
                           text=True, timeout=20)
        except subprocess.TimeoutExpired:
            raise ActionError("Speech timed out.") from None
        except (OSError, subprocess.CalledProcessError):
            raise ActionError("macOS speech is unavailable.") from None

    def _notes_fd(self, *, create: bool) -> int:
        """Anchor operations to open directories, rejecting symlink replacements."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = None
        try:
            if create:
                self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_fd = os.open(self.state_dir, flags)
            if create:
                try:
                    os.mkdir("notes", mode=0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
            return os.open("notes", flags, dir_fd=root_fd)
        except OSError:
            raise ActionError("Notes directory is unavailable or unsafe; symlinks are not allowed.") from None
        finally:
            if root_fd is not None:
                os.close(root_fd)

    def _create_note(self, args: dict) -> dict:
        contents = f"# {args['title']}\n\n{args['content']}\n".encode("utf-8")
        filename = uuid.uuid4().hex + ".md"
        notes_fd = self._notes_fd(create=True)
        try:
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=notes_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
        except OSError:
            raise ActionError("Could not create the note safely.") from None
        finally:
            os.close(notes_fd)
        return {"status": "done", "summary": f"Created note: {args['title']}",
                "path": str(self.state_dir / "notes" / filename),
                "undo": {"kind": "delete_note", "filename": filename,
                         "sha256": hashlib.sha256(contents).hexdigest()}}

    def undo(self, undo: dict) -> dict:
        """Remove only an unchanged generated note, or return a timer cancellation."""
        if not isinstance(undo, dict):
            raise ActionError("Invalid undo record.")
        kind = undo.get("kind")
        if kind == "cancel_timer":
            _fields(undo, {"kind", "id"}, {"kind", "id"}, "Timer undo")
            if not isinstance(undo["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", undo["id"]):
                raise ActionError("Invalid timer identifier.")
            return {"status": "done" if self.live else "simulated", "summary": "Canceled timer",
                    "timer_id": undo["id"]}
        if kind != "delete_note":
            raise ActionError("This action cannot be undone.")
        _fields(undo, {"kind", "filename", "sha256"}, {"kind", "filename", "sha256"}, "Note undo")
        filename, expected_hash = undo["filename"], undo["sha256"]
        if not isinstance(filename, str) or not _NOTE_NAME.fullmatch(filename):
            raise ActionError("Invalid note filename.")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ActionError("Invalid note fingerprint.")
        if not self.live:
            return {"status": "simulated", "summary": "Would remove unchanged note"}
        notes_fd = self._notes_fd(create=False)
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=notes_fd)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _MAX_NOTE_BYTES:
                    raise ActionError("Refusing to remove an unsafe or changed note.")
                contents = stream.read(_MAX_NOTE_BYTES + 1)
                after = os.fstat(stream.fileno())
            current = os.stat(filename, dir_fd=notes_fd, follow_symlinks=False)
            def identity(item):
                return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            if (hashlib.sha256(contents).hexdigest() != expected_hash
                    or identity(before) != identity(after) or identity(after) != identity(current)):
                raise ActionError("This note changed after creation. It was preserved.")
            os.unlink(filename, dir_fd=notes_fd)
        except OSError:
            raise ActionError("The note is missing or unsafe to remove. Nothing was deleted.") from None
        finally:
            os.close(notes_fd)
        return {"status": "done", "summary": "Removed unchanged note"}
