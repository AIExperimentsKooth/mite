"""Core interaction loop for Mite.
Handles the model chat loop with robust parsing for small models.
"""
import re
import json
import sys
import os
import time
import atexit
import shutil
import platform
import getpass
import readline
import urllib.request
import traceback
from . import prompts
from . import tools
from .task_manager import TaskQueue, TaskSchedule, parse_interval, format_interval


# --- Readline history (up/down arrow key support) ---
_HISTFILE = os.path.expanduser("~/.mite_history")
_HISTSIZE = 100


def _setup_readline():
    try:
        readline.set_history_length(_HISTSIZE)
        if os.path.exists(_HISTFILE):
            readline.read_history_file(_HISTFILE)
        atexit.register(readline.write_history_file, _HISTFILE)
    except Exception:
        pass


_USERDATA_DIR = os.path.expanduser("~/.mite")
_CONVERSATIONS_DIR = os.path.join(_USERDATA_DIR, "conversations")
_CONFIG_FILE = os.path.join(_USERDATA_DIR, "config.json")


def _ensure_userdata_dir():
    os.makedirs(_CONVERSATIONS_DIR, exist_ok=True)


def _load_config() -> dict:
    try:
        if os.path.isfile(_CONFIG_FILE):
            with open(_CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_config(config: dict):
    try:
        _ensure_userdata_dir()
        with open(_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"  \u26a0 Failed to save config: {e}")


def _save_conversation(name: str, history: list) -> str:
    clean = [m for m in history if m["role"] != "system"]
    path = os.path.join(_CONVERSATIONS_DIR, f"{name}.json")
    try:
        _ensure_userdata_dir()
        with open(path, "w") as f:
            json.dump(clean, f, indent=2)
        return f"Saved {len(clean)} messages to '{name}'"
    except Exception as e:
        return f"Failed to save: {e}"


def _load_conversation(name: str) -> list | None:
    path = os.path.join(_CONVERSATIONS_DIR, f"{name}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"  \u26a0 Failed to load '{name}': {e}")
        return None


def _list_conversations() -> list[str]:
    try:
        files = sorted(os.listdir(_CONVERSATIONS_DIR))
        return sorted(set(f.replace(".json", "") for f in files if f.endswith(".json")))
    except FileNotFoundError:
        return []


def _strip_chat_noise(text: str) -> str:
    text = str(text)
    lines = text.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^(USER|user|User|Assistant|assistant|AI|ai|System|system)\\s*:', stripped):
            continue
        if stripped.startswith("What's") or stripped.startswith("How do I") or stripped.startswith("Can you"):
            continue
        clean.append(line)
    return "\n".join(clean)


def _parse_tool_call_singleline(text: str) -> dict | None:
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text.startswith("TOOL"):
        return None
    tool_line = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("TOOL"):
            tool_line = stripped
            break
    if not tool_line:
        return None
    tokens = []
    i = 0
    while i < len(tool_line):
        if tool_line[i] in ('"', "'"):
            quote = tool_line[i]
            j = i + 1
            while j < len(tool_line) and tool_line[j] != quote:
                j += 1
            tokens.append(tool_line[i+1:j])
            i = j + 1 if j < len(tool_line) else j
        elif tool_line[i] == '=' and tokens and (len(tokens[-1]) > 0 or not tokens[-1].startswith('--')):
            val_start = i + 1
            rest = tool_line[val_start:]
            next_key_match = re.search(r'\s+(\w[\w_]*)=', rest)
            if next_key_match:
                value = rest[:next_key_match.start()].strip()
                if value:
                    tokens[-1] += '='
                    tokens.append(value)
                i = val_start + next_key_match.start() + 1
                continue
            else:
                tokens.append(tool_line[val_start:].strip())
                i = len(tool_line)
        elif not tool_line[i].isspace():
            j = i
            while j < len(tool_line) and not tool_line[j].isspace() and tool_line[j] not in ('"', "'"):
                j += 1
            tokens.append(tool_line[i:j])
            i = j
        else:
            i += 1
    if len(tokens) < 2:
        return None
    tool_name = tokens[1].lower().strip()

    TOOL_NAME_ALIASES = {
        "write": "write_file", "read": "read_file", "edit": "patch",
        "replace": "patch", "run": "shell", "exec": "shell",
        "find": "search", "grep": "search",
    }
    tool_name = TOOL_NAME_ALIASES.get(tool_name, tool_name)

    valid_tools = set(tools.TOOLS.keys())
    if tool_name not in valid_tools:
        if tool_name.endswith(":") or tool_name.endswith("."):
            tool_name = tool_name[:-1].strip()
        if tool_name not in valid_tools:
            return None

    args = {}
    ARG_ALIASES = {
        "old": "old_string", "new": "new_string", "cmd": "command",
        "glob": "file_glob", "max": "limit", "msg": "message",
        "file": "path", "dir": "path", "folder": "path", "directory": "path",
    }
    i = 2
    while i < len(tokens):
        part = tokens[i]
        if "=" in part:
            eq_idx = part.index("=")
            key = part[:eq_idx].strip().lower()
            value = part[eq_idx + 1:].strip().strip('"\'')
            if key in ARG_ALIASES:
                key = ARG_ALIASES[key]
            if not value and i + 1 < len(tokens):
                next_val = tokens[i + 1].strip().strip('"\'')
                if "=" not in next_val:
                    value = next_val
                    i += 1
            if value and value.startswith("-") and i + 1 < len(tokens):
                next_val = tokens[i + 1].strip().strip('"\'')
                if "=" not in next_val:
                    value += " " + next_val
                    i += 1
            if key:
                if isinstance(value, str):
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    else:
                        try:
                            value = int(value)
                        except (ValueError, AttributeError):
                            pass
                args[key] = value
        i += 1
    return {"tool": tool_name, "args": args, "thought": ""}


def _parse_tool_call_multiline(text: str) -> dict | None:
    if not text or not isinstance(text, str):
        return None
    lines = text.strip().split("\n")
    tool_name = None
    args = {}
    in_args = False
    thought = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("THOUGHT:"):
            thought = stripped[8:].strip()
            in_args = False
        elif stripped.upper().startswith("TOOL") or stripped.upper().startswith("TOOL:"):
            tool_part = stripped[4:].strip().lstrip(":")
            tool_name = tool_part.strip().lower().split()[0] if tool_part else None
            in_args = True
        elif in_args and ":" in stripped:
            colon_idx = stripped.index(":")
            key = stripped[:colon_idx].strip().lower()
            value = stripped[colon_idx + 1:].strip()
            if key and value:
                if " " not in key and not key.startswith("the") and not key.startswith("to"):
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    else:
                        try:
                            value = int(value)
                        except (ValueError, AttributeError):
                            pass
                    args[key] = value
    if tool_name:
        return {"tool": tool_name, "args": args, "thought": thought}
    return None


def _parse_tool_call(text: str) -> dict | None:
    if not text or not isinstance(text, str):
        return None
    result = _parse_tool_call_singleline(text)
    if result:
        return result
    result = _parse_tool_call_multiline(text)
    if result:
        return result
    cleaned = _strip_chat_noise(text)
    if cleaned != text:
        result = _parse_tool_call_singleline(cleaned)
        if result:
            return result
        result = _parse_tool_call_multiline(cleaned)
        if result:
            return result
    text_stripped = text.strip()
    if text_stripped and "=" in text_stripped:
        first_word = text_stripped.split()[0].lower().rstrip(":").rstrip(",")
        TOOL_ALIASES = {
            "write": "write_file", "read": "read_file", "edit": "patch",
            "replace": "patch", "run": "shell", "cmd": "shell",
            "command": "shell", "exec": "shell", "find": "search",
            "grep": "search", "search": "search",
        }
        normalized = TOOL_ALIASES.get(first_word, first_word)
        valid_tools = set(tools.TOOLS.keys())
        if normalized in valid_tools or first_word in valid_tools:
            result = _parse_tool_call_singleline("TOOL " + text_stripped)
            if result:
                return result
    return None


def _call_ollama(messages: list[dict], model: str, host: str = "http://localhost:11434") -> str | None:
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
            "stop": ["\n\n\n"],
        }
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"\n  \u26a0 HTTP {e.code} from Ollama: {body[:300]}")
        return None
    except Exception as e:
        print(f"\n  \u26a0 Ollama error: {e}")
        if "Connection refused" in str(e):
            print("  Is Ollama running? Try: ollama serve")
        return None


def _get_sysinfo() -> str:
    lines = []
    try:
        _found = False
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for _line in f:
                    if _line.startswith("PRETTY_NAME="):
                        pretty = _line.split("=", 1)[1].strip().strip('"').strip("'")
                        lines.append(pretty)
                        _found = True
                        break
        if not _found:
            uname = os.uname()
            parts = [uname.sysname]
            release = uname.release.split("-")[0] if "-" in uname.release else uname.release
            parts.append(release)
            lines.append(" / ".join(parts))
    except Exception:
        try:
            lines.append(platform.platform(terse=True))
        except Exception:
            lines.append("Unknown platform")
    try:
        user = getpass.getuser()
        lines.append(f"User: {user}")
    except Exception:
        pass
    try:
        hostname = platform.node()
        if hostname:
            lines.append(f"Hostname: {hostname}")
    except Exception:
        pass
    try:
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1]) * 1024
                        if kb >= 1024 ** 3:
                            mem = f"{kb / 1024 ** 3:.1f} GB"
                        elif kb >= 1024 ** 2:
                            mem = f"{round(kb / 1024 ** 2):.0f} MB"
                        else:
                            mem = f"{round(kb / 1024):.0f} KB"
                        lines.append(f"Available memory: {mem}")
                        break
    except Exception:
        pass
    try:
        st = shutil.disk_usage(os.getcwd())
        free = st.free
        if free >= 1024 ** 3:
            disk = f"{free / 1024 ** 3:.1f} GB"
        elif free >= 1024 ** 2:
            disk = f"{round(free / 1024 ** 2):.0f} MB"
        else:
            disk = f"{round(free / 1024):.0f} KB"
        total_disk = f"{st.total / 1024 ** 3:.1f} GB"
        lines.append(f"Available storage: {disk} (of {total_disk})")
    except Exception:
        pass
    return "\n".join(lines)


def _load_agent_md() -> str:
    """Load AGENT.md, checking project-level then user-level paths.

    Priority:
    1. ./AGENT.md (project root \u2014 highest priority)
    2. ./.mite/AGENT.md (project scoped)
    3. ~/.mite/AGENT.md (user level)
    """
    paths = [
        os.path.join(os.getcwd(), "AGENT.md"),
        os.path.join(os.getcwd(), ".mite", "AGENT.md"),
        os.path.join(_USERDATA_DIR, "AGENT.md"),
    ]
    for full_path in paths:
        if os.path.isfile(full_path):
            try:
                with open(full_path) as f:
                    content = f.read().strip()
                if content:
                    return content
            except Exception:
                pass
    return ""


def _detect_finish_or_question(text: str) -> str:
    if not text:
        return "continue"
    text_lower = text.strip().lower()
    finish_patterns = [
        r'\b(the task|the work|everything|this)\\s+(is|has been)\\s+(complete|completed|done|finished)',
        r'\bi(\'ve| have)\\s+(finished|completed|done|accomplished)',
        r'\b(task|work).*complete',
        r'\ball\s+(done|set|finished|complete)',
        r'\bdone\s*[\.!]*\s*$',
        r'\bfinished\s*[\.!]*\s*$',
        r'\bcomplete[d]?\s*[\.!]*\s*$',
    ]
    for pat in finish_patterns:
        if re.search(pat, text_lower):
            return "finish"
    if "?" in text:
        return "question"
    question_phrases = [
        "would you like", "shall i", "should i", "do you want",
        "anything else", "what next", "next step", "proceed",
        "is there anything", "let me know if", "tell me if",
        "can i help", "how can i", "what would you",
    ]
    for phrase in question_phrases:
        if phrase in text_lower:
            return "question"
    return "continue"


# --- Task processing via model loop ---

def _process_user_task(user_input: str, history: list, model: str, host: str,
                       system_prompt: str, auto_continue: bool) -> bool:
    """Run a single task through the model loop.

    Appends user_input to history, runs the model loop with auto-follow-up
    and auto-continue, and returns True if TOOL finish was called
    (caller should exit), False otherwise.
    """
    history.append({"role": "user", "content": user_input})

    max_auto_steps = 20 if auto_continue else 5
    auto_steps_remaining = max_auto_steps

    while True:
        # Build prompt with system prompt (includes sysinfo + AGENT.md at session start)
        messages = prompts.build_prompt(history, system_prompt=system_prompt)

        print(f"  \u23f3", end="", flush=True)
        start_time = time.time()
        response = _call_ollama(messages, model, host)
        if response is None:
            print("")
            history.pop()
            return False

        elapsed = time.time() - start_time
        print(f" ({elapsed:.1f}s)")

        tool_call = _parse_tool_call(response)

        if tool_call:
            tool_name = tool_call["tool"]
            tool_args = tool_call["args"]
            thought = tool_call.get("thought", "")

            if thought:
                print(f"  \U0001f4ad {thought[:200]}")

            if tool_name == "finish":
                msg = tool_args.get("message", "")
                print(f"\n  \u2705 {msg}" if msg else "\n  \u2705 Done!")
                history.append({"role": "assistant", "content": response.strip()})
                return True

            print(f"  \U0001f527 {tool_name}({_args_str(tool_args)})")
            result = tools.execute_tool(tool_name, tool_args)
            print(f"\n{result[:1200]}")

            history.append({"role": "assistant", "content": response.strip()})
            truncated = result[:800]
            if len(result) > 800:
                truncated += "\n... (truncated)"
            history.append({"role": "system", "content": f"Result:\n{truncated}"})
            _trim_history(history)

            auto_steps_remaining -= 1
            if auto_steps_remaining <= 0:
                if auto_continue:
                    print(f"  \u26a0 Auto-continue limit ({max_auto_steps} steps) reached.")
                break
            continue

        else:
            print(f"\n{response.strip()[:800]}")
            history.append({"role": "assistant", "content": response.strip()})
            _trim_history(history)

            if auto_continue:
                signal = _detect_finish_or_question(response)
                if signal == "finish":
                    print("  (auto-continue: model signaled completion)")
                    break
                elif signal == "question":
                    print("  (auto-continue: waiting for your response)")
                    break
                else:
                    auto_steps_remaining -= 1
                    if auto_steps_remaining <= 0:
                        print(f"  \u26a0 Auto-continue limit ({max_auto_steps} steps) reached.")
                        break

                    # Stuck detection: 3+ consecutive NL responses = stuck
                    recent_nl = 0
                    for m in reversed(history):
                        if m["role"] == "assistant":
                            if not _parse_tool_call(m["content"]):
                                recent_nl += 1
                            else:
                                break
                            if recent_nl >= 3:
                                break
                    if recent_nl >= 3:
                        print("  \u26a0 Agent seems stuck (3+ responses without action).")
                        break

                    print("  \u23e9 (auto-continue)")
                    history.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
                    _trim_history(history)
                    continue
            else:
                break

    return False


def run_loop(model: str, host: str, initial_task: str = None, show_sysinfo: bool = True, auto_continue: bool = True):
    _setup_readline()
    _ensure_userdata_dir()

    config = _load_config()
    if config.get("model"):
        model = config["model"]
    if "show_sysinfo" in config:
        raw = config["show_sysinfo"]
        show_sysinfo = raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")
    if "auto_continue" in config:
        raw = config["auto_continue"]
        auto_continue = raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")

    # Initialize task queue and schedule
    task_queue = TaskQueue()
    task_schedule = TaskSchedule()
    pending_count = task_queue.count_pending()
    schedule_count = len(task_schedule.list())

    history = []
    last_prompt = ""
    step = 0
    agent_md_active = bool(_load_agent_md())

    # Gather system info
    sysinfo = _get_sysinfo() if show_sysinfo else ""
    _sysinfo_str = f"\n[System info]\n{sysinfo}\n" if sysinfo else ""

    # Load AGENT.md once at session start (not per-prompt)
    agent_content = _load_agent_md()
    _agent_md_str = f"\n[Project context]\n{agent_content}\n" if agent_content else ""

    # Build augmented system prompt with sysinfo and AGENT.md
    system_prompt = prompts.SYSTEM_PROMPT + _sysinfo_str + _agent_md_str

    # Count saved conversations
    convo_count = len(_list_conversations())

    print(f"\n  \U0001f916 Mite active | model: {model}")
    if agent_md_active:
        print(f"  \U0001f4cb AGENT.md loaded at startup (re-reads on /reset)")
    if sysinfo:
        print(f"  \U0001f5a5  System info:")
        for line in sysinfo.splitlines():
            print(f"     {line}")
    if auto_continue:
        print(f"  \u23e9 Auto-continue ON  \u2014 agent keeps working until done or stuck")
    if pending_count:
        print(f"  \U0001f4cb {pending_count} task{'s' if pending_count != 1 else ''} queued  (\u2014 /queue list)")
    if schedule_count:
        print(f"  \U0001f4c5 {schedule_count} scheduled task{'s' if schedule_count != 1 else ''} (\u2014 /schedule list)")
    print(f"  \U0001f4c1 ~/.mite/ ({convo_count} saved conversations)")
    print(f"  Commands: /exit  /reset  /history  /redo  /agent  /save  /load  /list  /config  /queue  /schedule  /help")
    if initial_task:
        print(f"  Task: {initial_task}\n")
    else:
        print(f"  Type your task or 'help' to start.\n")

    while True:
        # --- Check for due scheduled tasks ---
        due = task_schedule.check_due()
        if due:
            for entry in due:
                print(f"\n  \u23f0 Scheduled task due: \"{entry['task']}\" (every {entry['interval_label']})")
                print(f"     Run now? [Y/n] ", end="")
                try:
                    resp = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if resp in ("", "y", "yes"):
                    task_schedule.mark_run(entry["id"])
                    print(f"  Running scheduled: {entry['task']}")
                    should_exit = _process_user_task(
                        entry["task"], history, model, host,
                        system_prompt, auto_continue
                    )
                    if should_exit:
                        return
                    print()
                else:
                    task_schedule.mark_run(entry["id"])
                    print(f"  Skipped. Will run again in {entry['interval_label']}.\n")

        # --- Get user input ---
        if initial_task:
            user_input = initial_task
            initial_task = None
        else:
            try:
                user_input = input("\U00010203 ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Goodbye!")
                break

            if not user_input:
                continue

            # --- Command handling ---
            if user_input.startswith("/"):
                cmd = user_input[1:].lower()
                if cmd in ("exit", "quit", "q"):
                    print("  Goodbye!")
                    break
                elif cmd == "reset":
                    history = []
                    # Re-read AGENT.md and rebuild system prompt
                    agent_content = _load_agent_md()
                    _agent_md_str = f"\n[Project context]\n{agent_content}\n" if agent_content else ""
                    system_prompt = prompts.SYSTEM_PROMPT + _sysinfo_str + _agent_md_str
                    agent_md_active = bool(agent_content)
                    print(f"  Conversation reset. AGENT.md {'loaded' if agent_md_active else 'not found'}.")
                    continue
                elif cmd == "history":
                    for h in history[-6:]:
                        role = h["role"].upper()
                        content = h["content"][:200]
                        print(f"  [{role}] {content}")
                    continue
                elif cmd == "help":
                    _show_help()
                    continue
                elif cmd.startswith("model "):
                    model = cmd[6:].strip()
                    history = []
                    print(f"  Switched to: {model}")
                    continue
                elif cmd == "agent":
                    content = _load_agent_md()
                    if content:
                        print(f"  \U0001f4cb AGENT.md loaded ({len(content)} chars, injected into system prompt):")
                        print(f"     (re-reads on /reset)")
                        for line in content.splitlines():
                            print(f"    {line}")
                    else:
                        print("  No AGENT.md found.")
                        print("  Create AGENT.md in the current directory, then run /reset.")
                    continue
                elif cmd in ("redo", "r"):
                    if last_prompt:
                        user_input = last_prompt
                        print(f"  Redo: {user_input}")
                    else:
                        print("  No previous prompt to redo.")
                        continue
                elif cmd == "save" or cmd.startswith("save "):
                    name = cmd[5:].strip() if len(cmd) > 5 else ""
                    if not name:
                        print("  Usage: /save <name>  \u2014 save conversation to ~/.mite/conversations/<name>.json")
                        continue
                    msg = _save_conversation(name, history)
                    print(f"  \U0001f4be {msg}")
                    continue
                elif cmd == "load" or cmd.startswith("load "):
                    name = cmd[5:].strip() if len(cmd) > 5 else ""
                    if not name:
                        print("  Usage: /load <name>  \u2014 load conversation from ~/.mite/conversations/<name>.json")
                        continue
                    loaded = _load_conversation(name)
                    if loaded is None:
                        print(f"  \u26a0 No saved conversation '{name}'.")
                        print(f"     See /list for available conversations.")
                        continue
                    history = loaded
                    print(f"  \U0001f4c2 Loaded {len(history)} messages from '{name}'")
                    continue
                elif cmd == "list":
                    names = _list_conversations()
                    if names:
                        print(f"  \U0001f4c1 Saved conversations in ~/.mite/conversations/:")
                        for n in names:
                            print(f"     \u2022 {n}")
                    else:
                        print("  No saved conversations yet.")
                        print("  Use /save <name> to save the current conversation.")
                    continue
                elif cmd == "config" or cmd.startswith("config "):
                    rest = cmd[7:].strip() if len(cmd) > 7 else ""
                    if not rest:
                        print(f"  \u2699\ufe0f  Config  ({_CONFIG_FILE}):")
                        if config:
                            for k, v in config.items():
                                print(f"     {k}: {v}")
                        else:
                            print("     (defaults \u2014 no config saved yet)")
                        print(f"     Use /config <key> <value> to set")
                        print(f"     Current model: {model}")
                        print(f"     Current sysinfo: {show_sysinfo}")
                        print(f"     Current auto_continue: {auto_continue}")
                        continue
                    if "=" in rest:
                        key, _, value = rest.partition("=")
                    else:
                        parts = rest.split(" ", 1)
                        key = parts[0]
                        value = parts[1] if len(parts) > 1 else ""
                    key = key.strip().lower()
                    value = value.strip()
                    if not key or not value:
                        print("  Usage: /config <key> <value>")
                        print("  Keys: model, show_sysinfo (true/false), auto_continue (true/false)")
                        print(f"  Example: /config model qwen2.5:3b")
                        continue
                    config[key] = value
                    _save_config(config)
                    print(f"  \u2705 Config updated: {key} = {value}")
                    if key == "model":
                        model = value
                        print(f"  Switched to: {model}")
                    elif key == "show_sysinfo":
                        show_sysinfo = value.lower() in ("true", "1", "yes")
                        print(f"  sysinfo {'enabled' if show_sysinfo else 'disabled'} (will apply on next launch)")
                    elif key == "auto_continue":
                        auto_continue = value.lower() in ("true", "1", "yes")
                        print(f"  auto_continue {'enabled' if auto_continue else 'disabled'}")
                    continue

                elif cmd == "queue" or cmd.startswith("queue "):
                    qargs = cmd[6:].strip() if len(cmd) > 6 else ""
                    if qargs.startswith("add "):
                        task_text = qargs[4:].strip()
                        if task_text:
                            tid = task_queue.add(task_text)
                            qcount = task_queue.count_pending()
                            print(f"  \U0001f4cb Task #{tid} queued ({qcount} pending)")
                        else:
                            print("  Usage: /queue add <task description>")
                    elif qargs == "list":
                        tasks = task_queue.list()
                        if tasks:
                            for t in tasks:
                                icon = {"pending": "\u23f3", "running": "\U0001f527", "completed": "\u2705", "failed": "\u274c"}.get(t["status"], "?")
                                print(f"  {icon} #{t['id']:2d} [{t['status']:9s}] {t['task'][:80]}")
                        else:
                            print("  Queue is empty.")
                    elif qargs.startswith("remove "):
                        try:
                            rid = int(qargs.split()[1])
                            if task_queue.remove(rid):
                                print(f"  Removed task #{rid}")
                            else:
                                print(f"  No task #{rid}")
                        except (ValueError, IndexError):
                            print("  Usage: /queue remove <task_id>")
                    elif qargs == "clear":
                        task_queue.clear()
                        print("  Queue cleared.")
                    elif qargs == "start":
                        pending = task_queue.pending()
                        if not pending:
                            print("  Queue is empty. Use /queue add <task> first.")
                            continue
                        task_queue.processing = True
                        print(f"  Processing {len(pending)} queued task{'s' if len(pending) != 1 else ''}...\n")
                        while task_queue.processing:
                            task = task_queue.next_pending()
                            if not task:
                                print("  \u2705 Queue complete!")
                                break
                            print(f"  \U0001f4cb Task #{task['id']}: {task['task']}")
                            print(f"  {'=' * 60}")
                            should_exit = _process_user_task(
                                task["task"], history, model, host,
                                system_prompt, auto_continue
                            )
                            if should_exit:
                                task_queue.mark_done(task["id"])
                                task_queue.processing = False
                                return
                            task_queue.mark_done(task["id"])
                            print(f"  \u2705 Task #{task['id']} completed\n")
                            remaining = task_queue.count_pending()
                            if remaining:
                                print(f"  \U0001f4cb {remaining} task{'s' if remaining != 1 else ''} remaining in queue.\n")
                            else:
                                print("  \u2705 All tasks completed!")
                        task_queue.processing = False
                    elif qargs == "stop":
                        if task_queue.processing:
                            task_queue.processing = False
                            print("  Queue processing stopped.")
                        else:
                            print("  Queue is not currently processing.")
                    else:
                        print("  Subcommands: add, list, remove <id>, clear, start, stop")
                    continue

                elif cmd == "schedule" or cmd.startswith("schedule "):
                    sargs = cmd[9:].strip() if len(cmd) > 9 else ""
                    if sargs.startswith("add "):
                        rest = sargs[4:].strip()
                        if not rest:
                            print("  Usage: /schedule add <interval> <task>")
                            print("  Intervals: 30m, 1h, 2h30m, 3600, 'every 30 minutes'")
                            continue
                        sep_idx = -1
                        for i, ch in enumerate(rest):
                            if ch == ' ' and i > 0:
                                left = rest[:i]
                                parsed = parse_interval(left)
                                if parsed is not None:
                                    sep_idx = i
                                    break
                        if sep_idx == -1:
                            for i, ch in enumerate(rest):
                                if i > 0 and ch.isalpha() and rest[i-1].isdigit():
                                    if i+1 < len(rest) and rest[i+1] in (' ', '\t'):
                                        left = rest[:i+1]
                                        parsed = parse_interval(left)
                                        if parsed is not None:
                                            sep_idx = i+2
                                            break
                        if sep_idx == -1:
                            print(f"  Could not parse interval from '{rest}'.")
                            print("  Examples: /schedule add 30m check disk space")
                            print("            /schedule add 1h backup database")
                            continue
                        interval_str = rest[:sep_idx]
                        task_str = rest[sep_idx:].strip()
                        interval_sec = parse_interval(interval_str)
                        if interval_sec is None:
                            print(f"  Could not parse interval '{interval_str}'.")
                            continue
                        if not task_str:
                            print("  No task specified.")
                            continue
                        sid = task_schedule.add(interval_sec, task_str)
                        print(f"  \U0001f4c5 Scheduled #{sid}: \"{task_str}\" every {format_interval(interval_sec)}")
                        print(f"     (will prompt on next interaction)")
                    elif sargs == "list":
                        entries = task_schedule.list()
                        if entries:
                            for e in entries:
                                icon = "\u2705" if e.get("enabled", True) else "\u23f8"
                                print(f"  {icon} #{e['id']:2d} [{e['interval_label']:>6s}] {e['task'][:70]}{' [paused]' if not e.get('enabled', True) else ''}")
                        else:
                            print("  No scheduled tasks.")
                    elif sargs.startswith("remove "):
                        try:
                            rid = int(sargs.split()[1])
                            if task_schedule.remove(rid):
                                print(f"  Removed schedule #{rid}")
                            else:
                                print(f"  No schedule #{rid}")
                        except (ValueError, IndexError):
                            print("  Usage: /schedule remove <id>")
                    elif sargs == "clear":
                        task_schedule.clear()
                        print("  All scheduled tasks cleared.")
                    elif sargs.startswith("pause "):
                        try:
                            pid = int(sargs.split()[1])
                            task_schedule.disable(pid)
                            print(f"  Schedule #{pid} paused.")
                        except (ValueError, IndexError):
                            print("  Usage: /schedule pause <id>")
                    elif sargs.startswith("resume "):
                        try:
                            pid = int(sargs.split()[1])
                            task_schedule.enable(pid)
                            print(f"  Schedule #{pid} resumed (next run: now).")
                        except (ValueError, IndexError):
                            print("  Usage: /schedule resume <id>")
                    else:
                        print("  Subcommands: add <interval> <task>, list, remove <id>, clear, pause <id>, resume <id>")
                    continue

                else:
                    print(f"  Unknown: {user_input}")
                    continue

        # --- Track last prompt for /redo ---
        last_prompt = user_input

        # Process through the model loop (system prompt already includes AGENT.md + sysinfo)
        should_exit = _process_user_task(user_input, history, model, host, system_prompt, auto_continue)
        if should_exit:
            return

        if step > 50:
            print("\n  \u26a0 Many steps. Use /reset for a new task.")


def _args_str(args: dict) -> str:
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 50:
            v_str = v_str[:47] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)


def _trim_history(history: list[dict], max_msgs: int = 6):
    if len(history) <= max_msgs:
        return
    new_hist = []
    if history and history[0]["role"] == "system":
        new_hist.append(history[0])
    new_hist.extend(history[-(max_msgs - len(new_hist)):])
    history.clear()
    history.extend(new_hist)


def _show_help():
    print("""
  Mite Commands:
    /exit             - Exit
    /reset            - Reset conversation (re-reads AGENT.md)
    /redo (/r)        - Re-run the last prompt
    /history          - Show recent context
    /model <n>        - Switch model (for current session)
    /agent            - Show AGENT.md instructions (injected into system prompt)
    /save <name>      - Save conversation to ~/.mite/conversations/<name>.json
    /load <name>      - Load a saved conversation
    /list             - List saved conversations
    /config           - Show current config (~/.mite/config.json)
    /config <k> <v>   - Set a config value (model, show_sysinfo, auto_continue)
    /queue add <t>    - Add a task to the sequential queue
    /queue list       - List queued tasks
    /queue start      - Start processing the queue (runs tasks one at a time)
    /queue stop       - Stop queue processing
    /queue clear      - Clear all queued tasks
    /queue remove <n> - Remove a specific task from the queue
    /schedule add <interval> <task> - Schedule a recurring task
    /schedule list    - List scheduled tasks
    /schedule remove  - Remove a scheduled task
    /schedule pause   - Pause a scheduled task
    /schedule resume  - Resume a paused scheduled task
    /schedule clear   - Clear all scheduled tasks
    /help             - This help

  Auto-Continue:
    By default, Mite auto-sends "continue" to the agent after each step
    until it finishes or asks a question. This lets multi-step tasks
    complete without you typing after every tool call.
    Disable with: /config auto_continue false
    Or: mite --no-auto-continue

  AGENT.md:
    Create AGENT.md in the current directory (project-level) or in
    ~/.mite/AGENT.md (user-level) to inject persistent instructions
    into the system prompt at session start.
    Inject once at startup, re-read on /reset.
    Priority: ./AGENT.md > ./.mite/AGENT.md > ~/.mite/AGENT.md

  System Info:
    By default, Mite shows your platform, user, hostname, memory,
    and disk at startup. This info is also injected into every prompt
    so the AI can use it. Disable with: mite --no-sysinfo
    Or: /config show_sysinfo false

  Task Manager:
    Queue tasks sequentially with /queue, schedule recurring tasks
    with /schedule. Tasks persist in ~/.mite/queue.json and
    ~/.mite/schedule.json. See /queue help and /schedule help.

  Conversations:
    Your conversations, AGENT.md, and preferences live under ~/.mite/.
    Use /save to save the current conversation and /load to restore it.

  Tip: Press \u2191 (up arrow) to recall previous prompts.
  Tools: read_file, write_file, patch, shell, search, finish
""")
