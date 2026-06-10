import re
import json
import os
import sys
import subprocess
import time
import shutil
import platform
import getpass
import atexit

# ---------------------------------------------------------------------------
# History / readline
# ---------------------------------------------------------------------------

_HISTFILE = os.path.expanduser("~/.mite/history")
_HISTFILE_MAX = 100


def _setup_readline():
    """Enable readline arrow-key history if available."""
    try:
        import readline

        histfile = _HISTFILE
        try:
            readline.read_history_file(histfile)
            readline.set_history_length(_HISTFILE_MAX)
        except FileNotFoundError:
            pass
        atexit.register(readline.write_history_file, histfile)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------

_HELP_TEXT = """
Commands:
  /exit       Exit mite
  /reset      Reset conversation
  /history    Show conversation history
  /redo       Re-run last prompt (aliases: /r)
  /agent      Show loaded AGENT.md
  /save <name>  Save conversation
  /load <name>  Load conversation
  /list       List saved conversations
  /config     Show config
  /config <key> <value>  Set config (show_sysinfo, auto_continue, model_timeout, stuck_threshold)
  /queue      Manage task queue (add <task> | list | remove <id> | clear | start | stop)
  /schedule   Manage scheduled tasks (add <interval> <task> | list | remove <id> | clear | pause | resume)
  /help       Show this help
  /version    Show version

Model commands:
  TOOL <tool_name>(<arg>=<value>, ...)
  THINK: ...
  DONE
"""


def _show_help():
    print(_HELP_TEXT)
    print("  Up-arrow recalls previous commands.")


# ---------------------------------------------------------------------------
# User data directory
# ---------------------------------------------------------------------------

_USERDATA = os.path.expanduser("~/.mite")
_CONV_DIR = os.path.join(_USERDATA, "conversations")
_CONFIG_PATH = os.path.join(_USERDATA, "config.json")
_QUEUE_PATH = os.path.join(_USERDATA, "queue.json")
_SCHEDULE_PATH = os.path.join(_USERDATA, "schedule.json")
_LAST_CONV_PATH = os.path.join(_CONV_DIR, "last.json")

DEFAULT_CONFIG = {
    "show_sysinfo": True,
    "auto_continue": True,
    "model_timeout": 300,
    "stuck_threshold": 10,
}


def _ensure_userdata_dir():
    os.makedirs(_CONV_DIR, exist_ok=True)
    if not os.path.exists(_CONFIG_PATH):
        _save_config(DEFAULT_CONFIG)


def _save_config(cfg):
    with open(_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def _load_config():
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def _save_conversation(name, messages):
    path = os.path.join(_CONV_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(messages, f, indent=2)
    print(f"  Saved to {name}")


def _load_conversation(name):
    path = os.path.join(_CONV_DIR, f"{name}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"  No saved conversation '{name}'")
        return None


def _list_conversations():
    if not os.path.isdir(_CONV_DIR):
        return []
    return sorted(f.replace(".json", "") for f in os.listdir(_CONV_DIR) if f.endswith(".json"))


def _auto_save_conversation(messages):
    """Auto-save formatted conversation to last.json for crash recovery."""
    conv = _format_conversation(messages)
    try:
        os.makedirs(_CONV_DIR, exist_ok=True)
        with open(_LAST_CONV_PATH, "w") as f:
            json.dump(conv, f)
    except Exception:
        pass  # silent — don't interrupt the user for a save failure


def _auto_load_conversation():
    """Load last.json if it exists, return list of messages or None."""
    try:
        with open(_LAST_CONV_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# AGENT.md
# ---------------------------------------------------------------------------

def _load_agent_md():
    """Load AGENT.md from workspace directory or ~/.mite/."""
    candidates = [
        os.path.join(os.getcwd(), "AGENT.md"),
        os.path.join(os.path.dirname(os.getcwd()), "AGENT.md"),
        os.path.join(_USERDATA, "AGENT.md"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
    return None


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------

def _get_sysinfo():
    mem = "?"
    disk = "?"
    try:
        if shutil.which("free"):
            out = subprocess.check_output(["free", "-h"], text=True).split("\n")[1]
            mem = out.split()[1]
    except Exception:
        pass
    try:
        if shutil.which("df"):
            out = subprocess.check_output(["df", "-h", "/"], text=True).split("\n")[1]
            disk = out.split()[3]
    except Exception:
        pass
    return (
        f"Platform: {platform.system()} {platform.release()}\n"
        f"Host: {platform.node()}\n"
        f"User: {getpass.getuser()}\n"
        f"CWD: {os.getcwd()}\n"
        f"Memory: {mem}\n"
        f"Disk: {disk}"
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _read_file(path):
    try:
        with open(path) as f:
            content = f.read()
        lines = content.count("\n") + 1
        return f"[OK] {path} ({lines} lines):\n" + content
    except Exception as e:
        return f"[ERROR] {e}"


def _write_file(path, content):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"[OK] Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"[ERROR] {e}"


def _patch(path, old_string, new_string, replace_all=False):
    try:
        with open(path) as f:
            content = f.read()
        if replace_all:
            if content.count(old_string) == 0:
                return f"[ERROR] '{old_string}' not found"
            new_content = content.replace(old_string, new_string)
        else:
            if content.count(old_string) == 0:
                return f"[ERROR] '{old_string}' not found"
            if content.count(old_string) > 1:
                return f"[ERROR] '{old_string}' found {content.count(old_string)} times; use replace_all"
            new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        return f"[OK] Patched {path}"
    except Exception as e:
        return f"[ERROR] {e}"


def _shell(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        out = result.stdout
        if result.stderr:
            out += "\n[STDERR]\n" + result.stderr
        if result.returncode != 0:
            out += f"\n[EXIT CODE] {result.returncode}"
        if not out.strip():
            out = "[OK] Command completed (no output)"
        return out
    except subprocess.TimeoutExpired:
        return "[ERROR] Command timed out (30s)"
    except Exception as e:
        return f"[ERROR] {e}"


def _search_files(pattern, path="."):
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*", pattern, path],
            capture_output=True, text=True, timeout=15,
        )
        out = result.stdout
        if not out.strip():
            return "[OK] No matches"
        lines = out.rstrip("\n").split("\n")
        if len(lines) > 50:
            out = "\n".join(lines[:50]) + f"\n... and {len(lines)-50} more"
        return out
    except Exception as e:
        return f"[ERROR] {e}"


_TOOLS = {
    "read_file": _read_file,
    "write_file": _write_file,
    "patch": _patch,
    "shell": _shell,
    "search_files": _search_files,
    "finish": lambda: "[FINISH]",
}


# ---------------------------------------------------------------------------
# Tool display helpers
# ---------------------------------------------------------------------------

_TOOL_DISPLAY = {
    "read_file": "\U0001f4c4",
    "write_file": "\U0001f4dd",
    "patch": "\U0001f527",
    "shell": "$",
    "search_files": "\U0001f50d",
    "finish": "\u2705",
}


def _tool_display(tool_name, args):
    """Show tool call with file path first."""
    icon = _TOOL_DISPLAY.get(tool_name, "\u2699")
    if tool_name == "read_file" and "path" in args:
        return f"{icon} {args['path']}  \u2190  read_file"
    if tool_name == "write_file" and "path" in args:
        return f"{icon} {args['path']}  \u2190  write_file"
    if tool_name == "patch" and "path" in args:
        return f"{icon} {args['path']}  \u2190  patch"
    if tool_name == "shell" and "command" in args:
        return f"{icon} {args['command']}"
    if tool_name == "search_files" and "pattern" in args:
        path = args.get("path", ".")
        return f"{icon} \"{args['pattern']}\"  in  {path}"
    if tool_name == "finish":
        return f"{icon} finish"
    return f"{icon} {tool_name}({', '.join(f'{k}={v}' for k, v in args.items())})"


# ---------------------------------------------------------------------------
# Task manager (queue + schedule)
# ---------------------------------------------------------------------------

class TaskQueue:
    """Simple FIFO task queue persisted to JSON."""
    def __init__(self, path):
        self.path = path
        self.tasks = []
        self._running = False
        self._idx = 0
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
                self.tasks = data.get("tasks", [])
                self._running = data.get("running", False)
                self._idx = data.get("idx", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            self.tasks = []
            self._running = False
            self._idx = 0

    def save(self):
        with open(self.path, "w") as f:
            json.dump({"tasks": self.tasks, "running": self._running, "idx": self._idx}, f, indent=2)

    def add(self, content):
        tid = str(int(time.time() * 1000))[-8:]
        self.tasks.append({"id": tid, "content": content, "status": "pending"})
        self.save()
        return tid

    def list_tasks(self):
        return self.tasks

    def remove(self, tid):
        self.tasks = [t for t in self.tasks if t["id"] != tid]
        self.save()

    def clear(self):
        self.tasks = []
        self._idx = 0
        self._running = False
        self.save()

    def next_pending(self):
        for t in self.tasks:
            if t["status"] == "pending":
                return t
        return None

    def start(self):
        self._running = True
        self._idx = 0
        self.save()

    def stop(self):
        self._running = False
        self.save()

    @property
    def is_running(self):
        return self._running


class TaskSchedule:
    """Scheduled tasks persisted to JSON."""
    def __init__(self, path):
        self.path = path
        self.tasks = []
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                self.tasks = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.tasks = []

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.tasks, f, indent=2)

    def add(self, content, interval_seconds):
        sid = str(int(time.time() * 1000))[-8:]
        self.tasks.append({
            "id": sid,
            "content": content,
            "interval_seconds": interval_seconds,
            "next_run": time.time() + interval_seconds,
            "status": "active",
        })
        self.save()
        return sid

    def list_tasks(self):
        return self.tasks

    def remove(self, sid):
        self.tasks = [t for t in self.tasks if t["id"] != sid]
        self.save()

    def clear(self):
        self.tasks = []
        self.save()

    def due_tasks(self):
        now = time.time()
        due = []
        for t in self.tasks:
            if t["status"] == "active" and now >= t["next_run"]:
                due.append(t)
                t["next_run"] = now + t["interval_seconds"]
        if due:
            self.save()
        return due

    def pause(self, sid):
        for t in self.tasks:
            if t["id"] == sid:
                t["status"] = "paused"
        self.save()

    def resume(self, sid):
        for t in self.tasks:
            if t["id"] == sid:
                t["status"] = "active"
                t["next_run"] = time.time() + t["interval_seconds"]
        self.save()


def parse_interval(s):
    """Parse human interval like '30s', '5m', '2h', '1d' into seconds."""
    s = s.strip().lower()
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    return int(s)


def format_interval(seconds):
    if seconds >= 86400:
        return f"{seconds//86400}d"
    if seconds >= 3600:
        return f"{seconds//3600}h"
    if seconds >= 60:
        return f"{seconds//60}m"
    return f"{seconds}s"


# ---------------------------------------------------------------------------
# Ollama interaction
# ---------------------------------------------------------------------------

def _call_ollama(model, messages, host="http://localhost:11434", timeout=300):
    """Call Ollama chat API with the given messages."""
    import urllib.request
    import urllib.error

    url = f"{host}/api/chat"
    data = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "num_predict": -2,
            "temperature": 0.2,
            "top_p": 0.9,
        }
    }).encode()

    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        result = json.loads(resp.read())
        return result.get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return f"[OLLAMA ERROR] HTTP {e.code}: {body}"
    except urllib.error.URLError as e:
        return f"[OLLAMA ERROR] {e.reason} - Is Ollama running?"
    except Exception as e:
        return f"[OLLAMA ERROR] {e}"


# ---------------------------------------------------------------------------
# Tool parsing (lenient -- handles many formats)
# ---------------------------------------------------------------------------

_TOOL_ALIASES = {
    "write": "write_file",
    "read": "read_file",
    "edit": "patch",
    "search": "search_files",
    "execute": "shell",
    "run": "shell",
    "cmd": "shell",
    "grep": "search_files",
}


def _parse_tool_call(text):
    """Parse model output into (tool_name, args_dict) or None.

    Accepts formats (tried in order):

    BLOCK FORMAT (multi-line, best for write_file/patch with large content):
      [TOOL write_file]
      path: hello.py
      content:
        #!/usr/bin/env python
        print("hello")
      [/TOOL]

      [TOOL patch]
      path: main.py
      old_string:
        def old():
          pass
      new_string:
        def new():
          return 42
      [/TOOL]

      [TOOL shell]
      command: ls -la
      [/TOOL]

    SINGLE-LINE FORMATS (backward compat, unchanged):
      TOOL read_file(path="foo.txt")
      TOOL: write_file(path="bar", content="hi")
      read_file(path="foo.txt")
      WRITE_FILE path=test.txt content="hi"
      shell(command="ls -la")
      finish
      DONE
    """
    text = text.strip()

    # Strategy 0: explicit finish / done
    if text == "finish" or text == "DONE":
        return ("finish", {})

    # Strategy 1 (NEW): multi-line block format [TOOL name] ... [/TOOL]
    result = _parse_block_format(text)
    if result:
        return result

    # Strategy 2: TOOL name(args) or TOOL: name(args)
    m = re.match(r"^TOOL\s*:?\s*(\w+)\((.+)\)", text)
    if m:
        name = m.group(1).lower()
        if name in _TOOL_ALIASES:
            name = _TOOL_ALIASES[name]
        args_raw = m.group(2)
        args = _parse_args_singleline(args_raw)
        return (name, args)

    # Strategy 3: just name(args) -- no TOOL prefix
    m = re.match(r"^(\w+)\((.+)\)", text)
    if m:
        name = m.group(1).lower()
        if name in _TOOL_ALIASES:
            name = _TOOL_ALIASES[name]
        args_raw = m.group(2)
        args = _parse_args_singleline(args_raw)
        if name in _TOOLS:
            return (name, args)

    # Strategy 4: TOOL name key=val key=val (no parens)
    m = re.match(r"^TOOL\s*:?\s*(\w+)\s+(.+)", text)
    if m:
        name = m.group(1).lower()
        if name in _TOOL_ALIASES:
            name = _TOOL_ALIASES[name]
        args_raw = m.group(2)
        args = _parse_args_singleline(args_raw)
        if name in _TOOLS:
            return (name, args)

    # Strategy 5: bare name key=val (no TOOL, no parens) -- lenient
    m = re.match(r"^(\w+)\s+(.+)", text)
    if m:
        name = m.group(1).lower()
        if name in _TOOL_ALIASES:
            name = _TOOL_ALIASES[name]
        args_raw = m.group(2)
        args = _parse_args_singleline(args_raw)
        if name in _TOOLS and len(args) >= 1:
            return (name, args)

    return None


def _parse_block_format(text):
    """Try to parse as [TOOL name] ... [/TOOL] multi-line block.

    Returns (tool_name, args_dict) or None.
    """
    # Match [TOOL name] or [TOOL: name] at start, with [/TOOL] closing
    # Also accept [name] ... [/name] for aliases like [write_file]
    m = re.search(
        r"\[TOOL\s*:?\s*(\w+)\](.*?)\[/TOOL\]",
        text, re.DOTALL
    )
    if not m:
        # Also try bare [name] ... [/name] (without TOOL prefix)
        m = re.search(
            r"\[(\w+)\](.*?)\[/\1\]",
            text, re.DOTALL
        )
    if not m:
        return None

    name = m.group(1).lower()
    if name in _TOOL_ALIASES:
        name = _TOOL_ALIASES[name]
    if name not in _TOOLS:
        return None

    body = m.group(2).strip()
    args = _parse_block_args(body)
    return (name, args)


def _parse_block_args(body):
    """Parse key: value pairs from a block body.

    Handles:
      key: simple_value
      key:
        multi-line
        indented content
      key:
        until next key: or end

    Returns dict.
    """
    args = {}
    lines = body.split("\n")
    current_key = None
    current_value_lines = []

    def _flush():
        """Save current multi-line value if any."""
        nonlocal current_key, current_value_lines
        if current_key is not None and current_value_lines:
            # Detect common indent and strip it
            val = _dedent(current_value_lines)
            args[current_key] = val.strip()
            current_key = None
            current_value_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check if this line starts a new key: value pair
        kv_match = re.match(r"^(\w[\w_]*)\s*:\s*(.*)", line)
        if kv_match:
            _flush()
            key = kv_match.group(1)
            val = kv_match.group(2).strip()
            if val:
                # Inline value on same line
                args[key] = val
            else:
                # Multi-line value starts on next lines
                current_key = key
                current_value_lines = []
            i += 1
            continue

        # If we're collecting a multi-line value, add this line
        if current_key is not None:
            current_value_lines.append(line)
        i += 1

    # Flush any remaining multi-line value
    _flush()

    return args


def _dedent(lines):
    """Remove common leading whitespace from a list of lines."""
    if not lines:
        return ""
    # Find minimum indent among non-empty lines
    indents = []
    for line in lines:
        if line.strip():  # non-empty
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            indents.append(indent)
    common = min(indents) if indents else 0
    return "\n".join(line[common:] if common > 0 else line for line in lines)


def _parse_args_singleline(raw):
    """Parse key=value pairs from a single-line string."""
    args = {}
    for m in re.finditer(r"(\w+)=\"(.*?)\"|(\w+)='(.*?)'|(\w+)=(\S+?)(?:\s|$)", raw + " "):
        key = m.group(1) or m.group(3) or m.group(5)
        val = m.group(2) or m.group(4) or m.group(6)
        val = val.strip().strip("\"'").strip()
        args[key] = val
    return args


# ---------------------------------------------------------------------------
# Finish detection
# ---------------------------------------------------------------------------

def _detect_finish_or_question(text):
    """Detect if text signals finish or a user question.

    Returns 'finish', 'question', or None.

    Only explicit 'TOOL finish' or bare word 'finish' triggers finish.
    Get the model to ask the user when it needs guidance.
    """
    t = text.strip()

    if t == "finish" or t == "TOOL finish" or t == "TOOL: finish":
        return "finish"
    if re.match(r"^TOOL\s*:?\s*finish\s*$", t):
        return "finish"

    if t.endswith("?"):
        return "question"

    return None


# ---------------------------------------------------------------------------
# Conversation handling
# ---------------------------------------------------------------------------

def _format_conversation(messages):
    return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop(model="qwen2.5:0.5b", host="http://localhost:11434", show_sysinfo=True,
             auto_continue=True, model_timeout=300, stuck_threshold=10, initial_task=None):
    """Run the interactive mite loop."""
    _setup_readline()
    _ensure_userdata_dir()

    cfg = _load_config()
    if "show_sysinfo" in cfg and not isinstance(cfg["show_sysinfo"], bool):
        cfg["show_sysinfo"] = str(cfg["show_sysinfo"]).lower() == "true"
    show_sysinfo = cfg.get("show_sysinfo", show_sysinfo)
    auto_continue = cfg.get("auto_continue", auto_continue)
    model_timeout = cfg.get("model_timeout", model_timeout)
    stuck_threshold = cfg.get("stuck_threshold", stuck_threshold)

    workspace = os.path.join(_USERDATA, "project-x")
    os.makedirs(workspace, exist_ok=True)
    os.chdir(workspace)

    agent_md = _load_agent_md()

    from . import prompts
    sysinfo_text = _get_sysinfo() if show_sysinfo else ""
    system_prompt = prompts.SYSTEM_PROMPT
    if agent_md:
        system_prompt += f"\n[AGENT.md instructions]\n{agent_md}\n[/AGENT.md]"
    if show_sysinfo:
        system_prompt += f"\n[System information]\n{sysinfo_text}\n[/System information]"

    messages = [{"role": "system", "content": system_prompt}]
    history = []
    last_prompt = ""

    # Auto-load last conversation if available
    last_conv = _auto_load_conversation()
    if last_conv:
        # Strip saved system message (if present) — we just built a fresh one
        last_conv = [m for m in last_conv if m.get("role") != "system"]
        if last_conv:
            messages.extend(last_conv)
            print(f"  \U0001f4c2 Restored conversation ({len(last_conv)} messages from last session)")
        else:
            print("  \U0001f4c2 Found saved conversation but it was only system messages")

    task_queue = TaskQueue(_QUEUE_PATH)
    task_schedule = TaskSchedule(_SCHEDULE_PATH)

    print(f"\n  \U0001f916 Mite v0.1.0  \u2014  Model: {model}")
    print(f"  \U0001f4c2 Workspace: {workspace}")
    print(f"  \U0001f504 Auto-continue: {'on' if auto_continue else 'off'}  (stuck threshold: {stuck_threshold})")
    if agent_md:
        print(f"  \U0001f4cb AGENT.md loaded at startup (re-reads on /reset)")
    print(f"  Commands: /exit  /reset  /history  /redo  /agent  /save  /load  /list  /config  /queue  /schedule  /help")
    print()

    # Process initial single-task mode, then fall through to interactive loop
    if initial_task:
        print(f"  \U0001f3af Task: {initial_task[:100]}")
        _process_user_task(initial_task, system_prompt, messages,
                           model, host, history, auto_continue, model_timeout,
                           stuck_threshold, task_queue, task_schedule)
        _auto_save_conversation(messages)
        print()

    while True:
        due = task_schedule.due_tasks()
        for sched_task in due:
            print(f"  \u23f0 Scheduled task [{sched_task['id']}]: {sched_task['content'][:60]}...")
            _process_user_task(sched_task["content"], system_prompt, messages,
                               model, host, history, auto_continue, model_timeout,
                               stuck_threshold, task_queue, task_schedule, sched_task_mode=True)
            _auto_save_conversation(messages)

        if task_queue.is_running:
            t = task_queue.next_pending()
            if t:
                print(f"  \u25b6 Queue task [{t['id']}]: {t['content'][:80]}...")
                t["status"] = "in_progress"
                task_queue.save()
                _process_user_task(t["content"], system_prompt, messages,
                                   model, host, history, auto_continue, model_timeout,
                                   stuck_threshold, task_queue, task_schedule)
                _auto_save_conversation(messages)
                t["status"] = "completed"
                task_queue.save()
            else:
                print("  \u2705 Queue empty -- stopping")
                task_queue.stop()

        try:
            user_input = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        raw_input = user_input

        if not user_input.strip():
            continue

        if user_input.startswith("/"):
            cmd = user_input[1:].strip().split()
            command = cmd[0].lower() if cmd else ""

            if command == "exit":
                print("  Goodbye!")
                break

            elif command == "reset":
                messages = [{"role": "system", "content": system_prompt}]
                history = []
                print("  \U0001f504 Conversation reset")
                continue

            elif command in ("redo", "r"):
                if last_prompt:
                    user_input = last_prompt
                    print(f"  \u21a9 Re-running: {last_prompt[:80]}")
                else:
                    print("  No previous prompt to redo")
                    continue

            elif command == "history":
                if not history:
                    print("  (empty)")
                else:
                    for i, (role, text) in enumerate(history):
                        prefix = "You" if role == "user" else "Mite"
                        print(f"  [{i}] {prefix}: {text[:120]}")
                continue

            elif command == "help":
                _show_help()
                continue

            elif command == "agent":
                if agent_md:
                    print(f"  Agent instructions file:")
                    print(f"  {agent_md[:60]}..." if len(agent_md) > 60 else f"  {agent_md}")
                    print(f"  (injected into system prompt, re-reads on /reset)")
                else:
                    print("  No AGENT.md found")
                continue

            elif command == "version":
                print("Mite v0.1.0")
                continue

            elif command == "save":
                if len(cmd) < 2:
                    print("  Usage: /save <name>")
                else:
                    _save_conversation(cmd[1], _format_conversation(messages))
                continue

            elif command == "load":
                if len(cmd) < 2:
                    print("  Usage: /load <name>")
                else:
                    conv = _load_conversation(cmd[1])
                    if conv is not None:
                        messages = [{"role": "system", "content": system_prompt}] + conv
                        print(f"  \U0001f4c2 Loaded {len(conv)} messages")
                continue

            elif command == "list":
                convs = _list_conversations()
                if not convs:
                    print("  (no saved conversations)")
                else:
                    print("  Saved conversations:")
                    for c in convs:
                        print(f"    - {c}")
                continue

            elif command == "config":
                if len(cmd) == 1:
                    print(f"  show_sysinfo: {show_sysinfo}")
                    print(f"  auto_continue: {auto_continue}")
                    print(f"  model_timeout: {model_timeout}")
                    print(f"  stuck_threshold: {stuck_threshold}")
                elif len(cmd) >= 3:
                    key = cmd[1]
                    value = cmd[2]
                    if key == "show_sysinfo":
                        show_sysinfo = value.lower() == "true"
                        cfg["show_sysinfo"] = show_sysinfo
                        _save_config(cfg)
                        print(f"  show_sysinfo = {show_sysinfo}")
                    elif key == "auto_continue":
                        auto_continue = value.lower() == "true"
                        cfg["auto_continue"] = auto_continue
                        _save_config(cfg)
                        print(f"  auto_continue = {auto_continue}")
                    elif key == "model_timeout":
                        try:
                            model_timeout = int(value)
                            cfg["model_timeout"] = model_timeout
                            _save_config(cfg)
                            print(f"  model_timeout = {model_timeout}s")
                        except ValueError:
                            print("  Invalid timeout value")
                    elif key == "stuck_threshold":
                        try:
                            stuck_threshold = int(value)
                            cfg["stuck_threshold"] = stuck_threshold
                            _save_config(cfg)
                            print(f"  stuck_threshold = {stuck_threshold}")
                        except ValueError:
                            print("  Invalid threshold value")
                    else:
                        print(f"  Unknown config key: {key}")
                else:
                    print("  Usage: /config [key value]")
                continue

            elif command == "queue":
                if len(cmd) < 2:
                    print("  Usage: /queue add <task> | list | remove <id> | clear | start | stop")
                elif cmd[1] == "add" and len(cmd) >= 3:
                    task_text = " ".join(cmd[2:])
                    tid = task_queue.add(task_text)
                    print(f"  \u2795 Added [{tid}]: {task_text[:60]}")
                elif cmd[1] == "list":
                    items = task_queue.list_tasks()
                    if not items:
                        print("  (queue empty)")
                    else:
                        for t in items:
                            icon = "\u2705" if t["status"] == "completed" else ("\u25b6" if t["status"] == "in_progress" else "\u23f3")
                            print(f"  {icon} [{t['id']}] {t['content'][:70]} ({t['status']})")
                elif cmd[1] == "remove" and len(cmd) >= 3:
                    task_queue.remove(cmd[2])
                    print(f"  Removed [{cmd[2]}]")
                elif cmd[1] == "clear":
                    task_queue.clear()
                    print("  Queue cleared")
                elif cmd[1] == "start":
                    task_queue.start()
                    print("  Queue started")
                elif cmd[1] == "stop":
                    task_queue.stop()
                    print("  Queue stopped")
                continue

            elif command == "schedule":
                if len(cmd) < 2:
                    print("  Usage: /schedule add <interval> <task> | list | remove <id> | clear | pause <id> | resume <id>")
                elif cmd[1] == "add" and len(cmd) >= 4:
                    interval = parse_interval(cmd[2])
                    task_text = " ".join(cmd[3:])
                    sid = task_schedule.add(task_text, interval)
                    print(f"  \u23f0 Added [{sid}]: every {format_interval(interval)} - {task_text[:60]}")
                elif cmd[1] == "list":
                    items = task_schedule.list_tasks()
                    if not items:
                        print("  (no scheduled tasks)")
                    else:
                        for t in items:
                            icon = "\u25b6" if t["status"] == "active" else "\u23f8"
                            next_s = max(0, int(t["next_run"] - time.time()))
                            print(f"  {icon} [{t['id']}] every {format_interval(t['interval_seconds'])} - {t['content'][:50]} (next in {next_s}s)")
                elif cmd[1] == "remove" and len(cmd) >= 3:
                    task_schedule.remove(cmd[2])
                    print(f"  Removed [{cmd[2]}]")
                elif cmd[1] == "clear":
                    task_schedule.clear()
                    print("  All scheduled tasks cleared")
                elif cmd[1] == "pause" and len(cmd) >= 3:
                    task_schedule.pause(cmd[2])
                    print(f"  Paused [{cmd[2]}]")
                elif cmd[1] == "resume" and len(cmd) >= 3:
                    task_schedule.resume(cmd[2])
                    print(f"  Resumed [{cmd[2]}]")
                continue

            else:
                print(f"  Unknown command: {user_input}")
                continue

        last_prompt = raw_input
        history.append(("user", user_input))

        _process_user_task(user_input, system_prompt, messages,
                           model, host, history, auto_continue, model_timeout,
                           stuck_threshold, task_queue, task_schedule)

        _auto_save_conversation(messages)

    try:
        import readline
        readline.write_history_file(_HISTFILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Task processing
# ---------------------------------------------------------------------------

def _process_user_task(user_input, system_prompt, messages, model, host, history,
                       auto_continue, model_timeout, stuck_threshold, task_queue, task_schedule,
                       sched_task_mode=False):
    """Process a single user task through the model loop.

    When sched_task_mode=True, never prints user-facing output or waits for
    confirmation -- scheduled tasks run silently and abort quietly if stuck.
    Returns False when finish is called (task done, continue main loop).
    """
    messages.append({"role": "user", "content": user_input})

    auto_steps = 0
    max_auto_steps = 20 if not sched_task_mode else 30
    no_tool_count = 0
    max_no_tool = stuck_threshold
    force_prompt_sent = False
    from . import prompts  # import once

    while True:
        model_reply = _call_ollama(model, messages, host=host, timeout=model_timeout)
        if not model_reply:
            if not sched_task_mode:
                print("  \u26a0 No response from model")
            break
        if model_reply.startswith("[OLLAMA ERROR]"):
            if not sched_task_mode:
                print(f"  \u26a0 {model_reply}")
            break

        messages.append({"role": "assistant", "content": model_reply})
        history.append(("mite", model_reply[:60] + ("..." if len(model_reply) > 60 else "")))
        _auto_save_conversation(messages)

        finish_state = _detect_finish_or_question(model_reply)
        if finish_state == "finish":
            if sched_task_mode:
                print(f"  \u2705 Scheduled task done\n")
            else:
                print(f"\n  \u2705 Done!  (use /exit to quit)\n")
            return False

        tool_result = _parse_tool_call(model_reply)
        if tool_result:
            name, args = tool_result
            no_tool_count = 0
            force_prompt_sent = False

            if name == "finish":
                if sched_task_mode:
                    print(f"  \u2705 Scheduled task done\n")
                else:
                    print(f"\n  \u2705 Done!  (use /exit to quit)\n")
                return False

            display = _tool_display(name, args)
            print(f"  {display}")

            if name in _TOOLS:
                try:
                    result_text = _TOOLS[name](**args)
                except TypeError as e:
                    result_text = f"[ERROR] Invalid args for {name}: {e}"
            else:
                result_text = f"[ERROR] Unknown tool: {name}"

            if result_text.startswith("[ERROR]"):
                print(f"    \u274c {result_text}")
            elif name == "write_file" and not result_text.startswith("[ERROR]"):
                print(f"    \u2705 Done")
            elif name == "finish" or result_text == "[FINISH]":
                if sched_task_mode:
                    print(f"  \u2705 Scheduled task done\n")
                else:
                    print(f"\n  \u2705 Done!  (use /exit to quit)\n")
                return False
            else:
                short = result_text[:200]
                print(f"    {short}")

            messages.append({"role": "user", "content": f"[Tool result: {name}]\n{result_text[:500]}"})
            auto_steps += 1

            if auto_steps >= max_auto_steps:
                how = "scheduled task" if sched_task_mode else "auto-steps"
                print(f"  \u26a0 Hit max {how} ({max_auto_steps}). Stopping.")
                break

            continue

        no_tool_count += 1

        if sched_task_mode:
            if finish_state == "question" or no_tool_count < max_no_tool:
                messages.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
                continue
            if not force_prompt_sent:
                messages.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
                force_prompt_sent = True
                continue
            print(f"  \u26a0 Scheduled task aborted (stuck)\n")
            break

        # --- Normal (non-scheduled) paths below ---

        if finish_state == "question":
            if auto_continue and no_tool_count <= max_no_tool:
                messages.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
                continue
            else:
                print(f"\n  \U0001f916 {model_reply}")
                break

        if no_tool_count >= max_no_tool:
            if not force_prompt_sent:
                messages.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
                force_prompt_sent = True
                continue
            else:
                print(f"\n  \U0001f916 {model_reply[:200]}")
                print(f"  \u26a0 Model seems stuck ({no_tool_count} no-tool replies). Returning to prompt.\n")
                break

        if auto_continue:
            messages.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
            continue
        else:
            print(f"\n  \U0001f916 {model_reply[:200]}")
            break

    return False
