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
from typing import Optional


_CONFIG_FILE = os.path.expanduser("~/.mite/config.json")
_HISTFILE = os.path.join(os.path.expanduser("~/.mite"), "history")


def _setup_readline():
    """Enable up/down arrow key history for input()."""
    try:
        _HISTFILE = os.path.join(os.path.expanduser("~/.mite"), "history")
        os.makedirs(os.path.dirname(_HISTFILE), exist_ok=True)
        if os.path.exists(_HISTFILE):
            readline.read_history_file(_HISTFILE)
        readline.set_history_length(100)
        atexit.register(readline.write_history_file, _HISTFILE)
    except Exception:
        pass


def _ensure_userdata_dir():
    """Create ~/.mite/ and ~/.mite/conversations/ if they don't exist."""
    base = os.path.expanduser("~/.mite")
    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(base, "conversations"), exist_ok=True)
    cfg = os.path.join(base, "config.json")
    if not os.path.exists(cfg):
        with open(cfg, "w") as f:
            json.dump({}, f)
    return base


def _save_conversation(name: str, history: list) -> str:
    """Save current conversation to ~/.mite/conversations/<name>.json."""
    path = os.path.join(os.path.expanduser("~/.mite/conversations"), f"{name}.json")
    with open(path, "w") as f:
        json.dump(history, f, indent=2)
    return f"Saved {len(history)} messages to {name}"


def _load_conversation(name: str) -> list | None:
    """Load a saved conversation from ~/.mite/conversations/<name>.json."""
    path = os.path.join(os.path.expanduser("~/.mite/conversations"), f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _list_conversations() -> list[str]:
    """List saved conversations in ~/.mite/conversations/."""
    conv_dir = os.path.expanduser("~/.mite/conversations")
    if not os.path.isdir(conv_dir):
        return []
    names = []
    for fname in sorted(os.listdir(conv_dir)):
        if fname.endswith(".json"):
            names.append(fname[:-5])
    return names


def _load_config() -> dict:
    """Load user config from ~/.mite/config.json."""
    path = os.path.expanduser("~/.mite/config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return {}


def _save_config(config: dict):
    """Save user config to ~/.mite/config.json."""
    path = os.path.expanduser("~/.mite/config.json")
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def _load_agent_md() -> str:
    """Load AGENT.md from current dir, ./.mite/, or ~/.mite/.
    Returns empty string if not found.
    """
    candidates = [
        os.path.join(os.getcwd(), "AGENT.md"),
        os.path.join(os.getcwd(), ".mite", "AGENT.md"),
        os.path.expanduser("~/.mite/AGENT.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    return f.read().strip()
            except Exception:
                pass
    return ""


def _get_sysinfo() -> str:
    """Return a compact system info string."""
    lines = []
    try:
        uname = platform.uname()
        lines.append(f"OS: {uname.system} {uname.release}")
        lines.append(f"User: {getpass.getuser()}@{uname.node}")
        try:
            total, _, _, free, *_ = shutil.disk_usage(os.path.expanduser("~"))
            lines.append(f"Disk: {free // (2**30)}G free / {total // (2**30)}G")
        except Exception:
            pass
        try:
            import subprocess
            result = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5)
            mem_line = result.stdout.splitlines()[1] if result.stdout else ""
            if mem_line:
                parts = mem_line.split()
                if len(parts) >= 3:
                    lines.append(f"RAM: {parts[2]} used / {parts[1]} total")
        except Exception:
            pass
    except Exception:
        pass
    return "\n".join(lines)


def _trim_history(history: list, max_len: int = 20):
    """Keep history manageable for tiny context windows."""
    system_msgs = [m for m in history if m["role"] == "system"]
    other_msgs = [m for m in history if m["role"] != "system"]
    if len(other_msgs) > max_len:
        other_msgs = other_msgs[-max_len:]
    history.clear()
    history.extend(system_msgs)
    history.extend(other_msgs)


def _call_ollama(messages: list[dict], model: str, host: str = "http://localhost:11434",
                 timeout: int = 300) -> str | None:
    """Call the Ollama API and return the response text.
    timeout: max seconds to wait for a response (default 300).
    """
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"\n  \u26a0 HTTP {e.code} from Ollama: {body[:300]}")
        return None
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, 'reason') else str(e)
        print(f"\n  \u26a0 Connection error: {reason}")
        if "timed out" in reason.lower():
            print(f"     The model took longer than {timeout}s to respond.")
            print("     Increase timeout: /config model_timeout <seconds>")
        return None
    except Exception as e:
        print(f"\n  \u26a0 Ollama error: {e}")
        if "Connection refused" in str(e):
            print("  Is Ollama running? Try: ollama serve")
        return None


def _parse_tool_call(response: str) -> dict | None:
    """Parse a TOOL command from model response.
    Supports multiple formats for robustness with small models.
    Returns {"tool": str, "args": dict, "thought": str} or None.
    """
    if not response:
        return None

    # Strip leading/trailing whitespace
    text = response.strip()

    # Extract thought (text before the TOOL line)
    thought = ""

    strategies = []

    # Strategy 1: Standard TOOL prefix
    if "TOOL " in text:
        strategies.append(text)
    else:
        strategies.append(text)

    # Strategy 2: Allow lowercase "tool "
    lower = text.lower()
    if "tool " in lower:
        idx = lower.index("tool ")
        strategies.append(text[idx:])
        if idx > 0:
            thought = text[:idx].strip()

    for candidate in strategies:
        # Find TOOL or tool prefix
        tool_idx = -1
        lower_c = candidate.lower()
        if "tool " in lower_c:
            tool_idx = lower_c.index("tool ")
        if tool_idx >= 0:
            # Parse "TOOL name arg1=val1 arg2=val2"
            arg_text = candidate[tool_idx + 5:].strip()

            # Split on whitespace, first token is tool name
            parts = arg_text.split()
            if not parts:
                continue

            tool_name = parts[0].lower()

            # Skip if the "tool name" looks like natural language
            if tool_name in ("the", "that", "this", "it", "is", "was", "to", "i", "we", "my", "use"):
                continue

            # Normalize tool name aliases
            name_aliases = {
                "write": "write_file",
                "read": "read_file",
                "edit": "patch",
                "run": "shell",
                "execute": "shell",
                "cmd": "shell",
                "find": "search",
                "grep": "search",
                "done": "finish",
            }
            tool_name = name_aliases.get(tool_name, tool_name)

            # Parse args: key=value pairs (bare tokens with =)
            args = {}
            for p in parts[1:]:
                if "=" in p:
                    k, _, v = p.partition("=")
                    k = k.strip().lower()
                    v = v.strip()
                    # Strip surrounding quotes
                    v = v.strip('\"').strip("'")

                    # Normalize arg name aliases
                    arg_aliases = {
                        "file": "path",
                        "file_path": "path",
                        "filename": "path",
                        "name": "path",
                        "old": "old_string",
                        "new": "new_string",
                        "old_text": "old_string",
                        "new_text": "new_string",
                        "code": "content",
                        "text": "content",
                        "cmd": "command",
                        "dir": "path",
                        "folder": "path",
                    }
                    k = arg_aliases.get(k, k)
                    args[k] = v

            if tool_name in ("read_file", "write_file", "patch", "shell", "search", "finish"):
                return {"tool": tool_name, "args": args, "thought": thought}

    # Strategy 3: Bare "write_file path=x content=y" without TOOL prefix
    for candidate in strategies:
        text_lower = candidate.lower()
        for t in ("write_file", "read_file", "patch", "shell", "search"):
            if text_lower.startswith(t) or (" " + t) in text_lower or text_lower.startswith(t):
                if t in text_lower:
                    idx = text_lower.index(t)
                    arg_text = candidate[idx + len(t):].strip()
                    if idx > 0:
                        thought = candidate[:idx].strip()
                    parts = arg_text.split()
                    args = {}
                    for p in parts:
                        if "=" in p:
                            k, _, v = p.partition("=")
                            k = k.strip().lower()
                            v = v.strip("\"'").strip()
                            arg_aliases = {
                                "file": "path", "old": "old_string", "new": "new_string",
                                "code": "content", "text": "content", "cmd": "command", "dir": "path",
                            }
                            k = arg_aliases.get(k, k)
                            args[k] = v
                    return {"tool": t, "args": args, "thought": thought}

    # Strategy 4: Lenient — catch "WRITE path=x" or "read path=x"
    text_lower = text.lower()
    lenient_map = {
        "write": "write_file", "read": "read_file", "edit": "patch",
        "patch": "patch", "shell": "shell", "run": "shell",
        "search": "search", "find": "search", "finish": "finish",
    }
    for alias, actual in lenient_map.items():
        if actual == "finish":
            if text_lower.startswith("finish") or text_lower.strip() in ("done", "finish", "complete"):
                return {"tool": "finish", "args": {}, "thought": ""}
        # Check for alias followed by space or = after filtering
        patterns = [
            f"{alias} path=", f"{alias} file=", f"{alias} command=",
            f"{alias} pattern=", f"{alias} content=",
        ]
        for pat in patterns:
            if pat in text_lower:
                idx = text_lower.index(alias)
                arg_text = text[idx + len(alias):].strip()
                if idx > 0:
                    thought = text[:idx].strip()
                parts = arg_text.split()
                args = {}
                for p in parts:
                    if "=" in p:
                        k, _, v = p.partition("=")
                        k = k.strip().lower()
                        v = v.strip("\"'").strip()
                        arg_aliases = {
                            "file": "path", "old": "old_string", "new": "new_string",
                            "code": "content", "text": "content", "cmd": "command", "dir": "path",
                        }
                        k = arg_aliases.get(k, k)
                        args[k] = v
                return {"tool": actual, "args": args, "thought": thought}

    # Strategy 5: Detect "i will write_file" or "let me read_file" in NL
    nl_patterns = [
        r"(?:i['']?ll|let me|i will|i can|start by|going to|need to|trying to)\s+(write_file|read_file|patch|shell|search|write|read|edit|run|find|finish)",
        r"(?:use|using|call|calling|run|running)\s+(?:the\s+)?(write_file|read_file|patch|shell|search|write|read|edit|run|find|finish)",
    ]
    for pattern in nl_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            matched_tool = m.group(1).lower()
            matched_tool = name_aliases.get(matched_tool, matched_tool)
            thought_end = m.end()
            rest = text[thought_end:].strip()
            if matched_tool == "finish":
                return {"tool": "finish", "args": {}, "thought": ""}
            # Try to parse path/content/etc from remaining text
            args = {}
            rest_clean = rest.strip("\"'").strip()
            # If there's a path-like string (no =, looks like a filename)
            if "=" not in rest_clean and rest_clean and not rest_clean.startswith("to") and "." in rest_clean:
                args["path"] = rest_clean.split()[0]
            for p in rest.split():
                if "=" in p:
                    k, _, v = p.partition("=")
                    k = k.strip().lower()
                    v = v.strip("\"'").strip()
                    k = arg_aliases.get(k, k)
                    # Only accept valid args for the tool
                    if matched_tool in ("write_file",) and k == "content":
                        args[k] = v
                    elif k in ("path", "file", "command", "pattern", "old_string", "new_string", "content"):
                        args[k] = v
            if args or matched_tool in ("shell", "search"):
                return {"tool": matched_tool, "args": args, "thought": thought or text[:m.start()].strip()}

    return None


def _detect_finish_or_question(text: str) -> str:
    """Detect if the model signaled completion or asked a question.
    Returns 'finish', 'question', or 'continue'."""
    if not text:
        return "continue"
    lower = text.lower().strip()
    if any(finish_word in lower for finish_word in ["done", "task complete", "i'm done", "all done", "finished", "completed", "task finished"]):
        return "finish"
    if "?" in text:
        question_phrases = ["would you like", "shall i", "should i", "do you want", "can i", "may i"]
        for phrase in question_phrases:
            if phrase in lower:
                return "question"
    return "continue"


def _tool_display(tool_name: str, args: dict) -> str:
    """Display a tool call with the file path highlighted."""
    path = args.get("path", "")
    base = os.path.basename(path) if path else ""

    if tool_name == "read_file":
        return f"\U0001f4c4 {base or path}  \u2190  read_file"
    elif tool_name == "write_file":
        return f"\U0001f4dd {base or path}  \u2190  write_file"
    elif tool_name == "patch":
        return f"\U0001f527 {base or path}  \u2190  patch"
    elif tool_name == "shell":
        cmd = args.get("command", "")
        return f"  $ {cmd[:80]}" + ("..." if len(cmd) > 80 else "")
    elif tool_name == "search":
        pat = args.get("pattern", "")
        p = args.get("path", "")
        return f"\U0001f50d \"{pat}\"  in  {p or '.'}"
    elif tool_name == "finish":
        return f"\u2705 finish({_args_str(args)})"
    else:
        return f"\U0001f527 {tool_name}({_args_str(args)})"


def _args_str(args: dict) -> str:
    """Short arg display."""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 50:
            v_str = v_str[:47] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)


def _process_user_task(user_input: str, history: list, model: str, host: str,
                       system_prompt: str, auto_continue: bool,
                       model_timeout: int = 300) -> bool:
    """Run a single task through the model loop.

    Appends user_input to history, runs the model loop with auto-follow-up
    and auto-continue. Returns True on fatal error (caller should exit),
    False otherwise.
    model_timeout: max seconds to wait for each model response (default 300).
    """
    history.append({"role": "user", "content": user_input})

    max_auto_steps = 20 if auto_continue else 5
    auto_steps_remaining = max_auto_steps
    stuck_countdown = 0

    while True:
        messages = prompts.build_prompt(history, system_prompt=system_prompt)

        print(f"  \u23f3", end="", flush=True)
        start_time = time.time()
        response = _call_ollama(messages, model, host, timeout=model_timeout)
        stuck_countdown = max(0, stuck_countdown - 1)

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

            # Handle finish \u2014 end this task, go back to user prompt
            if tool_name == "finish":
                msg = tool_args.get("message", "")
                print(f"\n  \u2705 {msg}" if msg else "\n  \u2705 Done!")
                print("  (use /exit to quit)")
                history.append({"role": "assistant", "content": response.strip()})
                break

            print(f"  {_tool_display(tool_name, tool_args)}")

            # Execute
            result = tools.execute_tool(tool_name, tool_args)
            print(f"\n{result[:1200]}")

            # Add response + result to history
            history.append({"role": "assistant", "content": response.strip()})
            truncated = result[:800]
            if len(result) > 800:
                truncated += "\n... (truncated)"
            history.append({"role": "system", "content": f"Result:\n{truncated}"})
            _trim_history(history)

            # Auto-follow-up
            auto_steps_remaining -= 1
            if auto_steps_remaining <= 0:
                if auto_continue:
                    print(f"  \u26a0 Auto-continue limit ({max_auto_steps} steps) reached.")
                break
            continue

        else:
            # Natural language response
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

                    # Stuck detection: 3+ consecutive NL responses
                    recent_nl = 0
                    for m in reversed(history):
                        if m["role"] == "assistant":
                            if not _parse_tool_call(m["content"]):
                                recent_nl += 1
                            else:
                                break
                            if recent_nl >= 3:
                                break
                    if recent_nl >= 3 and stuck_countdown <= 0:
                        print("  \u26a0 Agent stuck (3+ descriptions without action).")
                        print("  Sending force prompt...")
                        stuck_countdown = 2
                        FORCE_PROMPT = ("Respond with exactly ONE TOOL command now. "
                                        "Do not describe. If the task is done, say: TOOL finish")
                        history.append({"role": "user", "content": FORCE_PROMPT})
                        _trim_history(history)
                        continue
                    elif recent_nl >= 3 and stuck_countdown <= 0:
                        print("  \u26a0 Agent did not respond with a tool. Stopping.")
                        break

                    print("  \u23e9 (auto-continue)")
                    history.append({"role": "user", "content": prompts.CONTINUE_PROMPT})
                    _trim_history(history)
                    continue
            else:
                break

    return False


def run_loop(model: str, host: str, initial_task: str = None,
             show_sysinfo: bool = True, auto_continue: bool = True,
             model_timeout: int = 300):
    """Run the interactive Mite loop with auto-follow-up after tool calls."""
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
    if "model_timeout" in config:
        raw = config["model_timeout"]
        try:
            model_timeout = int(raw)
        except (ValueError, TypeError):
            pass

    task_queue = TaskQueue()
    task_schedule = TaskSchedule()
    pending_count = task_queue.count_pending()
    schedule_count = len(task_schedule.list())

    history = []
    last_prompt = ""
    step = 0
    agent_md_active = bool(_load_agent_md())

    sysinfo = _get_sysinfo() if show_sysinfo else ""
    _sysinfo_str = f"\n[System info]\n{sysinfo}\n" if sysinfo else ""

    agent_content = _load_agent_md()
    _agent_md_str = f"\n[Project context]\n{agent_content}\n" if agent_content else ""

    system_prompt = prompts.SYSTEM_PROMPT + _sysinfo_str + _agent_md_str

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
                    _process_user_task(
                        entry["task"], history, model, host,
                        system_prompt, auto_continue,
                        model_timeout=model_timeout
                    )
                    print()
                else:
                    task_schedule.mark_run(entry["id"])
                    print(f"  Skipped. Will run again in {entry['interval_label']}.\n")

        try:
            raw = input(f"\U0001f4a0 ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        user_input = raw.strip()
        step += 1

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input[1:].strip()

            # --- Built-in commands ---
            if cmd == "exit":
                print("  Goodbye!")
                break
            elif cmd == "help" or cmd == "h":
                _show_help()
                continue
            elif cmd == "redo" or cmd == "r":
                if last_prompt:
                    user_input = last_prompt
                    print(f"  Redoing: {user_input}")
                else:
                    print("  Nothing to redo.")
                    continue
            elif cmd.startswith("history"):
                for i, msg in enumerate(history):
                    role = msg["role"][:4]
                    content = msg["content"][:80].replace("\n", " ")
                    print(f"  {i:3d} [{role}] {content}")
                continue
            elif cmd == "reset":
                history = []
                agent_content = _load_agent_md()
                _agent_md_str = f"\n[Project context]\n{agent_content}\n" if agent_content else ""
                system_prompt = prompts.SYSTEM_PROMPT + _sysinfo_str + _agent_md_str
                agent_md_active = bool(agent_content)
                print("  Conversation reset.")
                if agent_md_active:
                    print(f"  AGENT.md re-read and injected into system prompt.")
                continue
            elif cmd.startswith("save") or cmd == "save":
                name = cmd[5:].strip() if len(cmd) > 5 else ""
                if not name:
                    print("  Usage: /save <name>")
                    continue
                msg = _save_conversation(name, history)
                print(f"  \U0001f4be {msg}")
                continue
            elif cmd.startswith("load") or cmd == "load":
                name = cmd[5:].strip() if len(cmd) > 5 else ""
                if not name:
                    print("  Usage: /load <name>")
                    continue
                loaded = _load_conversation(name)
                if loaded is None:
                    print(f"  \u26a0 No saved conversation '{name}'.")
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
            elif cmd == "agent":
                content = _load_agent_md()
                if content:
                    print(f"  \U0001f4cb AGENT.md loaded ({len(content)} chars, injected into system prompt):")
                    for line in content.splitlines():
                        print(f"     | {line}")
                    print("  (re-reads on /reset)")
                else:
                    print("  No AGENT.md found.")
                    print("  Create one of: ./AGENT.md, ./.mite/AGENT.md, ~/.mite/AGENT.md")
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
                    print(f"     Current model_timeout: {model_timeout}s")
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
                    print("  Keys: model, show_sysinfo (true/false), auto_continue (true/false), model_timeout (seconds)")
                    print(f"  Example: /config model qwen2.5:3b")
                    print(f"  Example: /config model_timeout 600")
                    continue
                config[key] = value
                _save_config(config)
                print(f"  \u2705 Config updated: {key} = {value}")
                if key == "model":
                    model = value
                    print(f"  Switched to: {model}")
                elif key == "show_sysinfo":
                    show_sysinfo = value.lower() in ("true", "1", "yes")
                    print(f"  sysinfo {'enabled' if show_sysinfo else 'disabled'}")
                elif key == "auto_continue":
                    auto_continue = value.lower() in ("true", "1", "yes")
                    print(f"  auto_continue {'enabled' if auto_continue else 'disabled'}")
                elif key == "model_timeout":
                    try:
                        model_timeout = int(value)
                        print(f"  model_timeout set to {model_timeout}s")
                    except ValueError:
                        print(f"  \u26a0 model_timeout must be a number (seconds), got '{value}'")
                continue

            # --- Task queue commands ---
            elif cmd == "queue" or cmd.startswith("queue "):
                qargs = cmd[6:].strip() if len(cmd) > 6 else ""
                if qargs.startswith("add "):
                    task_text = qargs[4:].strip()
                    if task_text:
                        task_queue.add(task_text)
                        print(f"  \U0001f4cb Added task #{task_queue.counter}: {task_text}")
                    else:
                        print("  Usage: /queue add <task description>")
                elif qargs == "list":
                    items = task_queue.list()
                    if items:
                        print(f"  \U0001f4cb Task queue ({len(items)} tasks):")
                        for t in items:
                            pfx = "\u25b6" if t["status"] == "running" else "\u2022"
                            print(f"     #{t['id']} {pfx} [{t['status']}] {t['task'][:80]}")
                    else:
                        print("  Queue is empty.")
                elif qargs.startswith("remove "):
                    try:
                        tid = int(qargs[7:].strip())
                        if task_queue.remove(tid):
                            print(f"  Removed task #{tid}")
                        else:
                            print(f"  Task #{tid} not found.")
                    except ValueError:
                        print(f"  Usage: /queue remove <id>")
                elif qargs == "clear":
                    task_queue.clear()
                    print("  Queue cleared.")
                elif qargs == "start":
                    pending = task_queue.list(status="pending")
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
                        _process_user_task(
                            task["task"], history, model, host,
                            system_prompt, auto_continue,
                            model_timeout=model_timeout
                        )
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

            # --- Schedule commands ---
            elif cmd == "schedule" or cmd.startswith("schedule "):
                sargs = cmd[9:].strip() if len(cmd) > 9 else ""
                if sargs.startswith("add "):
                    schedule_text = sargs[4:].strip()
                    if schedule_text:
                        task_schedule.add(schedule_text)
                        print(f"  \U0001f4c5 Added scheduled task #{task_schedule.counter}: {schedule_text}")
                    else:
                        print("  Usage: /schedule add <interval> <task>")
                        print("  Example: /schedule add every 30m check disk space")
                elif qargs == "list":
                    items = task_schedule.list()
                    if items:
                        print(f"  \U0001f4c5 Scheduled tasks ({len(items)}):")
                        for s in items:
                            status_icon = "\u25b6" if s["enabled"] else "\u23f8"
                            nxt = s.get("next_run", "?")
                            print(f"     #{s['id']} {status_icon} [{s['interval_label']}] {s['task'][:60]} (next: {nxt})")
                    else:
                        print("  No scheduled tasks.")
                elif sargs.startswith("remove "):
                    try:
                        sid = int(sargs[7:].strip())
                        if task_schedule.remove(sid):
                            print(f"  Removed scheduled task #{sid}")
                        else:
                            print(f"  Scheduled task #{sid} not found.")
                    except ValueError:
                        print("  Usage: /schedule remove <id>")
                elif sargs == "clear":
                    task_schedule.clear()
                    print("  Schedule cleared.")
                elif sargs.startswith("pause "):
                    try:
                        sid = int(sargs[6:].strip())
                        if task_schedule.disable(sid):
                            print(f"  Paused scheduled task #{sid}")
                        else:
                            print(f"  Scheduled task #{sid} not found.")
                    except ValueError:
                        print("  Usage: /schedule pause <id>")
                elif sargs.startswith("resume "):
                    try:
                        sid = int(sargs[7:].strip())
                        if task_schedule.enable(sid):
                            print(f"  Resumed scheduled task #{sid}")
                        else:
                            print(f"  Scheduled task #{sid} not found.")
                    except ValueError:
                        print("  Usage: /schedule resume <id>")
                else:
                    print("  Subcommands: add <interval> <task>, list, remove <id>, clear, pause <id>, resume <id>")
                continue

            else:
                print(f"  Unknown: {user_input}")
                continue

        last_prompt = user_input

        _process_user_task(user_input, history, model, host,
                           system_prompt, auto_continue,
                           model_timeout=model_timeout)

        if step > 50:
            print("\n  \u26a0 Many steps. Use /reset for a new task.")


def _show_help():
    """Display help information."""
    print()
    print("  \U0001f916 Mite - Micro AI Terminal Engineer")
    print("  " + "=" * 40)
    print()
    print("  Commands:")
    print("    /exit             - Exit Mite")
    print("    /reset            - Clear conversation history (re-reads AGENT.md)")
    print("    /history          - Show conversation history")
    print("    /redo (or /r)     - Re-submit last prompt")
    print("    /agent            - Show AGENT.md content (injected at startup)")
    print("    /save <name>      - Save conversation to ~/.mite/conversations/<name>.json")
    print("    /load <name>      - Load a saved conversation")
    print("    /list             - List saved conversations")
    print("    /config           - Show current config (~/.mite/config.json)")
    print("    /config <k> <v>   - Set config (model, show_sysinfo, auto_continue, model_timeout)")
    print("    /queue add <t>    - Add a task to the sequential queue")
    print("    /queue list       - List queued tasks")
    print("    /queue start      - Start processing the queue (runs tasks one at a time)")
    print("    /queue stop       - Stop processing mid-queue")
    print("    /schedule add <interval> <t>  - Schedule recurring task")
    print("    /schedule list    - List scheduled tasks")
    print()
    print("  Tip: Press \u2191 / \u2193 for command history.")
    print()
    print("  How it works:")
    print("    You describe what you want, Mite decides which tools to use.")
    print("    The AI can read files, write files, patch code, run shell commands,")
    print("    search files, and signal completion ('Done').")
    print()
    print("  Tool format (what the AI outputs):")
    print("    TOOL read_file path=FILE")
    print("    TOOL write_file path=FILE content=TEXT")
    print("    TOOL patch path=FILE old_string=OLD new_string=NEW")
    print("    TOOL shell command=CMD")
    print("    TOOL search pattern=PAT [target=content|files] [path=DIR]")
    print("    TOOL finish [message=TEXT]")
    print()
    print("  Auto-continue:")
    print("    Mite automatically continues after tool calls to complete")
    print("    multi-step tasks. Disable with: --no-auto-continue")
    print("    Or: /config auto_continue false")
    print()
    print("  AGENT.md:")
    print("    Create AGENT.md in the current directory (project-level) or in")
    print("    ~/.mite/AGENT.md (user-level) to inject persistent instructions")
    print("    into the system prompt at session start.")
    print("    Inject once at startup, re-read on /reset.")
    print("    Priority: ./AGENT.md > ./.mite/AGENT.md > ~/.mite/AGENT.md")
    print()
    print("  System Info & Timeout:")
    print("    By default, Mite shows your platform, user, hostname, memory,")
    print("    and disk at startup. This info is also injected into every prompt")
    print("    so the AI can use it. Disable with: mite --no-sysinfo")
    print("    Or: /config show_sysinfo false")
    print()
    print("    Model response timeout: default 300s (5 min). For slow/big models:")
    print("      mite --timeout 600         # CLI flag")
    print("      /config model_timeout 600  # persistent setting")
    print()
    print("  Conversations:")
    print("    Your conversations, AGENT.md, and preferences live under ~/.mite/.")
    print("    Use /save to save the current conversation and /load to restore it.")
    print()
    print("  Task Queue:")
    print("    Queue tasks to run in sequence, one after another.")
    print("    /queue add 'fix the bug'")
    print("    /queue start")
    print("    Tasks run sequentially. The queue persists in ~/.mite/queue.json.")
    print()
    print("  Recurring Schedule:")
    print("    Schedule tasks to run at intervals.")
    print("    /schedule add every 30m check disk space")
    print("    Supports: 30m, 1h, 2h30m, 1d, or seconds.")
    print("    Persists in ~/.mite/schedule.json.")
    print()
