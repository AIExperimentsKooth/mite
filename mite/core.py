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
    valid_tools = set(tools.TOOLS.keys())
    if tool_name not in valid_tools:
        if tool_name.endswith(":") or tool_name.endswith("."):
            tool_name = tool_name[:-1].strip()
        if tool_name not in valid_tools:
            return None
    args = {}
    ARG_ALIASES = {
        "old": "old_string",
        "new": "new_string",
        "cmd": "command",
        "glob": "file_glob",
        "max": "limit",
        "msg": "message",
        "file": "path",
        "dir": "path",
        "folder": "path",
        "directory": "path",
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
        first_word = text_stripped.split()[0].lower().rstrip(":")
        if first_word in tools.TOOLS:
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
        r'\b(the task|the work|everything|this)\s+(is|has been)\s+(complete|completed|done|finished)',
        r'\bi(\'ve| have)\s+(finished|completed|done|accomplished)',
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
    history = []
    last_prompt = ""
    step = 0
    agent_md_active = bool(_load_agent_md())
    sysinfo = _get_sysinfo() if show_sysinfo else ""
    _sysinfo_str = f"\n[System information]\n{sysinfo}\n" if sysinfo else ""
    system_prompt = prompts.SYSTEM_PROMPT + _sysinfo_str
    convo_count = len(_list_conversations())
    print(f"\n  \U0001f916 Mite active | model: {model}")
    if agent_md_active:
        print(f"  \U0001f4cb AGENT.md loaded (re-reads on every prompt)")
    if sysinfo:
        print(f"  \U0001f5a5  System info:")
        for line in sysinfo.splitlines():
            print(f"     {line}")
    if auto_continue:
        print(f"  \u23e9 Auto-continue ON  — agent keeps working until done or stuck")
    print(f"  \U0001f4c1 ~/.mite/ ({convo_count} saved conversations)")
    print(f"  Commands: /exit  /reset  /history  /redo  /agent  /save  /load  /list  /config  /help")
    if initial_task:
        print(f"  Task: {initial_task}\n")
    else:
        print(f"  Type your task or 'help' to start.\n")
    while True:
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
            if user_input.startswith("/"):
                cmd = user_input[1:].lower()
                if cmd in ("exit", "quit", "q"):
                    print("  Goodbye!")
                    break
                elif cmd == "reset":
                    history = []
                    print("  Conversation reset.")
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
                        print(f"  \U0001f4cb AGENT.md loaded ({len(content)} chars):")
                        for line in content.splitlines():
                            print(f"    {line}")
                    else:
                        print("  No AGENT.md found.")
                        print("  Create AGENT.md in the current directory to persist instructions.")
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
                        print("  Usage: /save <name>  — save conversation to ~/.mite/conversations/<name>.json")
                        continue
                    msg = _save_conversation(name, history)
                    print(f"  \U0001f4be {msg}")
                    continue
                elif cmd == "load" or cmd.startswith("load "):
                    name = cmd[5:].strip() if len(cmd) > 5 else ""
                    if not name:
                        print("  Usage: /load <name>  — load conversation from ~/.mite/conversations/<name>.json")
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
                            print("     (defaults — no config saved yet)")
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
                else:
                    print(f"  Unknown: {user_input}")
                    continue
        last_prompt = user_input
        agent_content = _load_agent_md()
        if agent_content:
            user_input = f"[AGENT.md instructions]\n{agent_content}\n\n[Task]\n{user_input}"
        history.append({"role": "user", "content": user_input})
        max_auto_steps = 20 if auto_continue else 5
        auto_steps_remaining = max_auto_steps
        while True:
            messages = prompts.build_prompt(history, system_prompt=system_prompt)
            print(f"  \u23f3", end="", flush=True)
            start_time = time.time()
            response = _call_ollama(messages, model, host)
            if response is None:
                print("")
                history.pop()
                break
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
                    step += 1
                    return
                print(f"  \U0001f527 {tool_name}({_args_str(tool_args)})")
                result = tools.execute_tool(tool_name, tool_args)
                print(f"\n{result[:1200]}")
                history.append({"role": "assistant", "content": response.strip()})
                truncated = result[:800]
                if len(result) > 800:
                    truncated += "\n... (truncated)"
                history.append({"role": "system", "content": f"Result:\n{truncated}"})
                _trim_history(history)
                step += 1
                auto_steps_remaining -= 1
                if auto_steps_remaining <= 0:
                    if auto_continue:
                        print(f"  \u26a0 Auto-continue limit ({max_auto_steps} steps) reached. Type more to continue.")
                    break
                continue
            else:
                print(f"\n{response.strip()[:800]}")
                history.append({"role": "assistant", "content": response.strip()})
                _trim_history(history)
                step += 1
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
                            print(f"  \u26a0 Auto-continue limit ({max_auto_steps} steps) reached. Type more to continue.")
                            break
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
                            print("  \u26a0 Agent seems stuck (3+ responses without action). Type to guide it.")
                            break
                        print("  \u23e9 (auto-continue)")
                        continue_prompt = (
                            "continue. "
                            "Use TOOL read_file, write_file, patch, shell, or search to make progress. "
                            "When the task is complete, use TOOL finish."
                        )
                        history.append({"role": "user", "content": continue_prompt})
                        _trim_history(history)
                        continue
                else:
                    break
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
    /reset            - Reset conversation
    /redo (/r)        - Re-run the last prompt
    /history          - Show recent context
    /model <n>        - Switch model (for current session)
    /agent            - Show AGENT.md instructions (re-reads on every prompt)
    /save <name>      - Save conversation to ~/.mite/conversations/<name>.json
    /load <name>      - Load a saved conversation
    /list             - List saved conversations
    /config           - Show current config (~/.mite/config.json)
    /config <k> <v>   - Set a config value (model, show_sysinfo, auto_continue)
    /help             - This help

  Auto-Continue:
    By default, Mite auto-sends "continue" to the agent after each step
    until it finishes or asks a question. This lets multi-step tasks
    complete without you typing after every tool call.
    Disable with: /config auto_continue false
    Or: mite --no-auto-continue

  AGENT.md:
    Create AGENT.md in the current directory (project-level) or in
    ~/.mite/AGENT.md (user-level, persistent) to persist instructions
    the AI receives before every prompt.
    Priority: ./AGENT.md > ./.mite/AGENT.md > ~/.mite/AGENT.md

  System Info:
    By default, Mite shows your platform, user, hostname, memory,
    and disk at startup. This info is also injected into every prompt
    so the AI can use it. Disable with: mite --no-sysinfo
    Or: /config show_sysinfo false

  Conversations:
    Your conversations, AGENT.md, and preferences live under ~/.mite/.
    Use /save to save the current conversation and /load to restore it.

  Tip: Press \u2191 (up arrow) to recall previous prompts.
  Tools: read_file, write_file, patch, shell, search, finish
""")
