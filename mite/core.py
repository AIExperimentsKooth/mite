"""
Core interaction loop for Mite.
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


# --- Readline history (up/down arrow key support) ---
_HISTFILE = os.path.expanduser("~/.mite_history")
_HISTSIZE = 100


def _setup_readline():
    """Enable readline for arrow-key history prefilling on input()."""
    try:
        readline.set_history_length(_HISTSIZE)
        if os.path.exists(_HISTFILE):
            readline.read_history_file(_HISTFILE)
        atexit.register(readline.write_history_file, _HISTFILE)

        # Bind up arrow to previous-history (default) \u2014 no extra config needed
        # readline.enable_auto_history is on by default in CPython
    except Exception:
        pass  # Non-fatal; readline is optional


def _strip_chat_noise(text: str) -> str:
    """Strip conversation contamination from model output.

    Tiny models sometimes echo back user messages or their own previous
    responses. Strip anything that looks like chat before parsing tools.
    """
    text = str(text)

    # Strip lines that start with "USER:" or "user:" or "User:" (common contamination)
    lines = text.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that look like chat contamination
        if re.match(r'^(USER|user|User|Assistant|assistant|AI|ai|System|system)\s*:', stripped):
            continue
        # Skip lines that are full user-like questions
        if stripped.startswith("What's") or stripped.startswith("How do I") or stripped.startswith("Can you"):
            # Only skip if this looks like it's IN the middle of a tool section
            continue
        clean.append(line)

    return "\n".join(clean)


def _parse_tool_call_singleline(text: str) -> dict | None:
    """Parse a tool call from model output.

    Format: TOOL toolname arg1=value arg2="value with spaces"

    For tiny models, this format is much easier to produce reliably
    than multi-line key:value pairs.
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip()
    if not text.startswith("TOOL"):
        return None

    # Strip to only the TOOL line (ignore anything after)
    tool_line = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("TOOL"):
            tool_line = stripped
            break

    if not tool_line:
        return None

    # Parse: TOOL toolname arg1=value1 arg2=value2
    # Handle both quoted values arg="value with spaces" and unquoted arg=value
    # Regex: match tokens that are either quoted strings or non-space sequences
    tokens = []
    i = 0
    while i < len(tool_line):
        if tool_line[i] in ('"', "'"):
            # Quoted string
            quote = tool_line[i]
            j = i + 1
            while j < len(tool_line) and tool_line[j] != quote:
                j += 1
            tokens.append(tool_line[i+1:j])
            i = j + 1 if j < len(tool_line) else j
        elif tool_line[i] == '=' and tokens and (len(tokens[-1]) > 0 or not tokens[-1].startswith('--')):
            # Value might contain spaces (for command args like "ls -la")
            # Collect everything after = until next known arg or end
            val_start = i + 1
            # Look ahead for the next key= pattern that's a known arg name
            rest = tool_line[val_start:]
            # Find the next " key=" pattern
            next_key_match = re.search(r'\s+(\w[\w_]*)=', rest)
            if next_key_match:
                value = rest[:next_key_match.start()].strip()
                if value:
                    tokens[-1] += '='
                    tokens.append(value)
                # Push back the key
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
        # "TOOL" with no tool name or just bare text
        # Also handle case where TOOL is followed by toolname on same vs next line
        return None

    tool_name = tokens[1].lower().strip()

    # Validate tool name
    valid_tools = set(tools.TOOLS.keys())
    if tool_name not in valid_tools:
        # Could be contamination \u2014 "TOOL read_file" but extra text makes it "TOOL read_file:"
        # Try stripping trailing colon
        if tool_name.endswith(":") or tool_name.endswith("."):
            tool_name = tool_name[:-1].strip()
        if tool_name not in valid_tools:
            return None

    # Parse arguments: k=v pairs
    args = {}
    # Arg name aliases: some models use shorter names
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
    i = 2  # Skip "TOOL" and tool_name
    while i < len(tokens):
        part = tokens[i]
        if "=" in part:
            eq_idx = part.index("=")
            key = part[:eq_idx].strip().lower()
            value = part[eq_idx + 1:].strip().strip('"\'')

            # Apply arg name aliases
            if key in ARG_ALIASES:
                key = ARG_ALIASES[key]

            # If value is empty, the next token might be the value
            if not value and i + 1 < len(tokens):
                next_val = tokens[i + 1].strip().strip('"\'')
                if "=" not in next_val:  # Next token isn't another key=val
                    value = next_val
                    i += 1  # Skip next token

            # If value starts with -, next token might continue it (command=ls -la)
            if value and value.startswith("-") and i + 1 < len(tokens):
                next_val = tokens[i + 1].strip().strip('"\'')
                if "=" not in next_val:
                    value += " " + next_val
                    i += 1

            if key:
                # Type conversions
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
    """Parse multi-line format as backup.

    Format:
    THOUGHT: reasoning
    TOOL: tool_name
    key: value
    key2: value2
    """
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

        # Handle both "TOOL:" and "TOOL toolname"
        if stripped.upper().startswith("THOUGHT:"):
            thought = stripped[8:].strip()
            in_args = False
        elif stripped.upper().startswith("TOOL") or stripped.upper().startswith("TOOL:"):
            # Handle "TOOL: read_file" or "TOOL read_file" or "TOOL:read_file"
            tool_part = stripped[4:].strip().lstrip(":")
            tool_name = tool_part.strip().lower().split()[0] if tool_part else None
            in_args = True
        elif in_args and ":" in stripped:
            colon_idx = stripped.index(":")
            key = stripped[:colon_idx].strip().lower()
            value = stripped[colon_idx + 1:].strip()
            if key and value:
                # Don't accept random keys that look like sentences
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
    """Parse a tool call from model output using multiple strategies."""
    if not text or not isinstance(text, str):
        return None

    # Strategy 1: Single-line TOOL format
    result = _parse_tool_call_singleline(text)
    if result:
        return result

    # Strategy 2: Multi-line key:value format (backup)
    result = _parse_tool_call_multiline(text)
    if result:
        return result

    # Strategy 3: Try stripping chat noise, then re-parse
    cleaned = _strip_chat_noise(text)
    if cleaned != text:
        result = _parse_tool_call_singleline(cleaned)
        if result:
            return result
        result = _parse_tool_call_multiline(cleaned)
        if result:
            return result

    return None


def _call_ollama(messages: list[dict], model: str, host: str = "http://localhost:11434") -> str | None:
    """Call the Ollama API and return the response text."""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Very low temp for reliable structured output
            "num_predict": 512,   # Keep responses short
            "stop": ["\n\n\n"],   # Stop on double newlines
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
    """Gather system information string for the agent context.

    Returns a multi-line string with platform, user, hostname,
    available memory, and available disk space.
    """
    lines = []

    # Platform
    try:
        # Try os-release PRETTY_NAME first (e.g., "Debian GNU/Linux 12")
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

    # User
    try:
        user = getpass.getuser()
        lines.append(f"User: {user}")
    except Exception:
        pass

    # Hostname
    try:
        hostname = platform.node()
        if hostname:
            lines.append(f"Hostname: {hostname}")
    except Exception:
        pass

    # Memory
    try:
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1]) * 1024  # kB → bytes
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

    # Disk (current directory)
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
    """Load AGENT.md from the current working directory.

    Checks for AGENT.md (project root) or .mite/AGENT.md (scoped).
    Content is prepended to every user prompt as persistent instructions.
    """
    for rel_path in ["AGENT.md", ".mite/AGENT.md"]:
        full_path = os.path.join(os.getcwd(), rel_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path) as f:
                    content = f.read().strip()
                if content:
                    return content
            except Exception:
                pass
    return ""


def run_loop(model: str, host: str, initial_task: str = None, show_sysinfo: bool = True):
    """Run the interactive Mite loop with auto-follow-up after tool calls."""
    # Enable up/down arrow key history for input()
    _setup_readline()

    history = []
    last_prompt = ""
    step = 0
    agent_md_active = bool(_load_agent_md())

    # Gather system info
    sysinfo = _get_sysinfo() if show_sysinfo else ""
    _sysinfo_str = f"\n[System information]\n{sysinfo}\n" if sysinfo else ""

    # Build augmented system prompt with sysinfo context for the model
    system_prompt = prompts.SYSTEM_PROMPT + _sysinfo_str

    print(f"\n  \U0001f916 Mite active | model: {model}")
    if agent_md_active:
        print(f"  \U0001f4cb AGENT.md loaded (re-reads on every prompt)")
    if sysinfo:
        print(f"  \U0001f5a5  System info:")
        for line in sysinfo.splitlines():
            print(f"     {line}")
    print(f"  Commands: /exit  /reset  /history  /redo  /agent  /help")
    if initial_task:
        print(f"  Task: {initial_task}\n")
    else:
        print(f"  Type your task or 'help' to start.\n")

    while True:
        # --- Get user input ---
        if initial_task:
            user_input = initial_task
            initial_task = None
        else:
            try:
                user_input = input("\u2503 ").strip()
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
                else:
                    print(f"  Unknown: {user_input}")
                    continue

        # Track last prompt for /redo (raw input, before AGENT.md augmentation)
        last_prompt = user_input

        # Prepend AGENT.md instructions if available (re-reads on every prompt)
        agent_content = _load_agent_md()
        if agent_content:
            user_input = f"[AGENT.md instructions]\n{agent_content}\n\n[Task]\n{user_input}"

        history.append({"role": "user", "content": user_input})

        # --- Run model loop (auto-follow-up after tool calls) ---
        auto_steps_remaining = 5  # Max auto-follow-up steps per user input
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

            # Try to parse as tool call
            tool_call = _parse_tool_call(response)

            if tool_call:
                tool_name = tool_call["tool"]
                tool_args = tool_call["args"]
                thought = tool_call.get("thought", "")

                if thought:
                    print(f"  \U0001f4ad {thought[:200]}")

                # Handle finish
                if tool_name == "finish":
                    msg = tool_args.get("message", "")
                    print(f"\n  \u2705 {msg}" if msg else "\n  \u2705 Done!")
                    history.append({"role": "assistant", "content": response.strip()})
                    step += 1
                    return  # Exit loop entirely on finish

                print(f"  \U0001f527 {tool_name}({_args_str(tool_args)})")

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
                step += 1

                # Auto-follow-up: let model process the result
                auto_steps_remaining -= 1
                if auto_steps_remaining <= 0:
                    break  # Back to user input
                continue  # Auto: call model again with result

            else:
                # Natural language response \u2014 done with this turn
                print(f"\n{response.strip()[:800]}")
                history.append({"role": "assistant", "content": response.strip()})
                _trim_history(history)
                step += 1
                break  # Back to user input

        if step > 50:
            print("\n  \u26a0 Many steps. Use /reset for a new task.")


def _args_str(args: dict) -> str:
    """Short arg display."""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 50:
            v_str = v_str[:47] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)


def _trim_history(history: list[dict], max_msgs: int = 6):
    """Ultra-aggressive trim for tiny context windows."""
    if len(history) <= max_msgs:
        return
    # Keep system + last max_msgs-1 entries
    new_hist = []
    if history and history[0]["role"] == "system":
        new_hist.append(history[0])
    new_hist.extend(history[-(max_msgs - len(new_hist)):])
    history.clear()
    history.extend(new_hist)


def _show_help():
    print("""
  Mite Commands:
    /exit       - Exit
    /reset      - Reset conversation
    /redo (/r)  - Re-run the last prompt
    /history    - Show recent context
    /model <n>  - Switch model
    /agent      - Show AGENT.md instructions (re-reads on every prompt)
    /help       - This help

  AGENT.md:
    Create AGENT.md in the current directory (or .mite/AGENT.md)
    to persist instructions the AI receives before every prompt.
    Great for project conventions, style guides, or role definitions.

  System Info:
    By default, Mite shows your platform, user, hostname, memory,
    and disk at startup. This info is also injected into every prompt
    so the AI can use it. Disable with: mite --no-sysinfo

  Tip: Press \u2191 (up arrow) to recall previous prompts.
  Tools: read_file, write_file, patch, shell, search, finish
""")
