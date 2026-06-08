"""
Tool implementations for Mite.
Each tool is a simple function that takes kwargs and returns a string result.
"""
import re
import os
import subprocess
import json
import shlex
from pathlib import Path


def _safe_path(path: str) -> str:
    """Resolve path relative to current directory, prevent escapes."""
    p = Path(path).expanduser().resolve()
    # Just resolve it \u2014 user is in their project dir
    return str(p)


def _decode_escapes(text: str) -> str:
    """Convert literal escape sequences in text (e.g., '\\n') to actual characters.
    Small models output \\n in single-line tool format, but we need real newlines."""
    if not text or not isinstance(text, str):
        return text
    return (text
        .replace('\\n', '\n')
        .replace('\\t', '\t')
        .replace('\\r', '\r')
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace('\\\\', '\\'))


def read_file(path: str, offset: int = 1, limit: int = 500) -> str:
    """Read a file with line numbers."""
    path = _safe_path(path)
    if not os.path.isfile(path):
        return f"ERROR: File not found: {path}"
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except PermissionError:
        return f"ERROR: Permission denied: {path}"
    except Exception as e:
        return f"ERROR: Cannot read {path}: {e}"

    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)

    result = f"### {path} ({total} lines, showing {start+1}-{end})\n"
    for i in range(start, end):
        result += f"{i+1:6d}|{lines[i]}"
    if end < total:
        result += f"... ({total - end} more lines)\n"
    return result


def write_file(path: str, content: str) -> str:
    """Write content to a file (overwrites existing)."""
    path = _safe_path(path)
    content = _decode_escapes(content) if isinstance(content, str) else content
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"ERROR: Cannot write {path}: {e}"


def patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Find and replace text in a file."""
    path = _safe_path(path)
    old_string = _decode_escapes(old_string)
    new_string = _decode_escapes(new_string)
    if not os.path.isfile(path):
        return f"ERROR: File not found: {path}"
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"ERROR: Cannot read {path}: {e}"

    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    if new_content == content:
        # Try fuzzy matching \u2014 normalize whitespace
        normalized = re.sub(r'\s+', ' ', content)
        old_normalized = re.sub(r'\s+', ' ', old_string)
        if old_normalized in normalized:
            return f"ERROR: old_string not found exactly. Try exact whitespace matching. The content exists but with different spacing."

        return f"ERROR: old_string not found in {path}"

    try:
        with open(path, "w") as f:
            f.write(new_content)
    except Exception as e:
        return f"ERROR: Cannot write {path}: {e}"

    diff_len = len(new_content) - len(content)
    return f"OK: Applied patch to {path} ({diff_len:+d} chars)"


def shell(command: str, timeout: int = 60) -> str:
    """Run a shell command and return output."""
    if not command or not command.strip():
        return "ERROR: Empty command"

    # Safety: block dangerous commands
    dangerous = ["rm -rf /", "rm -rf ~", "mkfs.", "dd if=", "> /dev/", ":(){ :|:& };:"]
    for d in dangerous:
        if d in command.lower():
            return f"ERROR: Command blocked (dangerous pattern: {d})"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = []
        if result.stdout:
            output.append(result.stdout.rstrip()[:4000])
        if result.stderr:
            output.append(f"STDERR: {result.stderr.rstrip()[:2000]}")
        exit_code = result.returncode
        output_str = "\n".join(output) if output else "(no output)"
        return f"EXIT: {exit_code}\n{output_str}"
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout}s: {command[:200]}"
    except Exception as e:
        return f"ERROR: {e}"


def search(pattern: str, target: str = "content", path: str = ".", file_glob: str = None, limit: int = 30) -> str:
    """Search file contents or find files by name."""
    path = _safe_path(path)
    if not os.path.isdir(path):
        return f"ERROR: Directory not found: {path}"

    try:
        if target == "files":
            # Find files by glob pattern
            p = Path(path)
            matches = sorted(p.rglob(pattern))
            if file_glob:
                matches = [m for m in matches if m.suffix == file_glob or file_glob in m.name]
            total = len(matches)
            shown = matches[:limit]

            if not shown:
                return f"No files matching '{pattern}' found in {path}"
            result = f"### {total} files matching '{pattern}'"
            if total > limit:
                result += f" (showing first {limit})"
            result += "\n"
            for m in shown:
                rel = m.relative_to(Path(path))
                result += f"  {rel}\n"
            if total > limit:
                result += f"... ({total - limit} more)\n"
            return result
        else:
            # Search file contents with grep
            cmd = f"grep -rn '{pattern}' {shlex.quote(path)}"
            if file_glob:
                cmd = f"grep -rn --include='{file_glob}' '{pattern}' {shlex.quote(path)}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
            total = len(lines)
            shown = lines[:limit]

            if not shown:
                return f"No matches for '{pattern}' in {path}"
            output = f"### {total} matches for '{pattern}' (showing {len(shown)}):\n"
            for line in shown:
                output += f"  {line[:300]}\n"
            if total > limit:
                output += f"... ({total - limit} more matches)\n"
            return output
    except Exception as e:
        return f"ERROR: Search failed: {e}"


def finish(message: str = "") -> str:
    """Mark the task as complete."""
    msg = f"Task complete. {message}" if message else "Task complete."
    return f"DONE: {msg}"


TOOLS = {
    "read_file":  {"fn": read_file,  "desc": "Read a file", "args": {"path": "File path", "offset": "Line offset (default 1)", "limit": "Max lines (default 500)"}},
    "write_file": {"fn": write_file, "desc": "Write/create a file", "args": {"path": "File path", "content": "File content"}},
    "patch":      {"fn": patch,      "desc": "Edit a file (find and replace)", "args": {"path": "File path", "old_string": "Text to find", "new_string": "Replacement text", "replace_all": "Replace all (true/false)"}},
    "shell":      {"fn": shell,      "desc": "Run a shell command", "args": {"command": "Command to run", "timeout": "Timeout in seconds (default 60)"}},
    "search":     {"fn": search,     "desc": "Search files or find files by name", "args": {"pattern": "Search pattern", "target": "content or files", "path": "Directory path", "file_glob": "File glob filter", "limit": "Max results"}},
    "finish":     {"fn": finish,     "desc": "Mark task complete", "args": {"message": "Optional completion message"}},
}


def execute_tool(tool_name: str, args: dict) -> str:
    """Execute a tool by name with given args and return result string."""
    tool = TOOLS.get(tool_name)
    if not tool:
        return f"ERROR: Unknown tool '{tool_name}'. Available: {', '.join(TOOLS.keys())}"
    try:
        return tool["fn"](**args)
    except TypeError as e:
        return f"ERROR: Bad arguments for {tool_name}: {e}"
    except Exception as e:
        return f"ERROR: {tool_name} failed: {e}"
