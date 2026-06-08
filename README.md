# Mite - Micro AI Terminal Engineer

**Tiny AI coding assistant for lightweight models** (Qwen2.5:0.5B, Qwen2.5:1.5B, etc.)

Mite is a Python-based AI coding assistant designed for small models that can run on modest hardware. It works like Claude Code or Codex but is optimized for models as small as **0.5B parameters**.

```
$ mite "add error handling to main.py"
┃ ⏳ thinking... done (2.3s)
┃ 🔧 read_file(path=main.py)
┃ ⏳ executing... done (0.1s)
┃ 💭 I see the file. Let me add error handling around the file read.
┃ 🔧 patch(path=main.py, old_string=open(path), new_string=try:\n    open(path)\nexcept...)
```

## Features

- 🪆 **Ultra-lightweight** — works with Qwen2.5:0.5B (500M params), runs on 4GB RAM
- 🤖 **Any Ollama model** — use whatever model you have: qwen2.5, llama3.2, phi, gemma
- 🔧 **Full tool set** — read, write, edit files, run shell commands, search code
- 🚀 **Auto-configures** — one command installs everything
- 💬 **Interactive REPL** — chat-like interface with command history (↑ arrow)
- 📁 **Userdata directory** — conversations, AGENT.md, and preferences live in `~/.mite/`
- 💾 **Save & load conversations** — save sessions, resume later
- ⚙️ **Persistent config** — preferences survive across sessions
- 📋 **AGENT.md support** — persistent instructions at project or user level
- 🧩 **Single python package** — easy to hack on
- ⏩ **Auto-continue** — agent keeps working autonomously until done or stuck
- 📋 **Task queue** — queue tasks to run in sequence
- ⏰ **Recurring schedule** — schedule tasks at intervals (e.g., every 30m)

## Quick Install

```bash
# One-liner:
git clone https://github.com/your/mite.git
cd mite
bash setup.sh

# Then:
mite "what's in this directory"
```

Or without cloning:

```bash
curl -fsSL https://raw.githubusercontent.com/your/mite/main/setup.sh | bash
```

## Usage

```bash
# Interactive mode
mite

# Run a single task
mite "fix the bug in main.py"

# Use a different model
mite --model qwen2.5:1.5b

# Use a remote Ollama instance
mite --host http://192.168.1.5:11434

# Run setup only
mite --setup

# Skip auto-setup checks
mite --no-setup

# Disable auto-continue (wait after every step)
mite --no-auto-continue
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MITE_MODEL` | `qwen2.5:0.5b` | Default Ollama model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `MITE_TOKEN` | — | GitHub token for private repo updates |
| `GITHUB_TOKEN` | — | Alternative GitHub token for updates |

### Updating Mite

Mite can update itself from GitHub:

```bash
# Via CLI (recommended):
mite --update

# Or standalone script:
bash update.sh

# For private repos, provide a GitHub token:
MITE_TOKEN=*** mite --update
```

The update script:
1. **Backs up** `~/.mite/` (conversations, config, AGENT.md)
2. **Fetches** the latest code from GitHub
3. **Restores** your userdata
4. **Re-runs** setup

On failure, it automatically restores the backup. No data loss.

### Interactive Commands

| Command | Description |
|---------|-------------|
| `/exit` | Exit Mite |
| `/reset` | Reset conversation |
| `/history` | Show recent conversation |
| `/model <name>` | Switch models mid-session |
| `/agent` | Show current AGENT.md instructions |
| `/save <name>` | Save conversation to `~/.mite/conversations/` |
| `/load <name>` | Load a saved conversation |
| `/list` | List saved conversations |
| `/config` | Show current preferences |
| `/config <k> <v>` | Set a preference (e.g., `model`, `show_sysinfo`) |
| `/redo` (`/r`) | Re-run the last prompt |
| `/help` | Show help |

### Auto-Continue

By default, Mite **automatically prompts the agent to continue** after each step until it finishes the task or asks you a question. This means you can give a multi-step task and Mite will autonomously:

1. Read files, search code, run tools
2. Interpret results and decide what to do next
3. Keep going through tool call results and natural language responses
4. **Stop** when the task is complete or when it needs your input

The `⏩ (auto-continue)` indicator shows when the agent is driving itself between steps.

Auto-continue has smart safeguards:
- **Stuck detection**: If the agent produces 3+ responses without using a tool, it stops and waits for you to guide it.
- **Step limit**: Caps at 20 continuous steps to prevent runaway loops.
- **Question detection**: If the model asks you something (contains `?` or phrases like "would you like"), it stops and waits for your answer.

```bash
# Disable auto-continue (wait after every step)
mite --no-auto-continue

# Or disable mid-session via config:
/config auto_continue false
```

### Task Queue & Schedule

Mite includes a built-in task manager for queuing and scheduling tasks:

**Sequential Queue** — add tasks to a queue and run them one after another:

```bash
# Add tasks to the queue
/queue add check if python is installed
/queue add list all files in src/
/queue add write summary.txt

# Process the queue (runs tasks sequentially)
/queue start

# Manage the queue
/queue list        # Show all tasks with status
/queue remove 2    # Remove task #2 from queue
/queue clear       # Clear entire queue
/queue stop        # Stop processing mid-queue
```

**Recurring Schedule** — schedule tasks to run at intervals:

```bash
# Schedule a task every 30 minutes
/schedule add 30m check disk usage

# Schedule a task every hour
/schedule add 1h backup database

# Schedule a task every 2 hours 30 minutes
/schedule add 2h30m run health check

# Manage schedule
/schedule list         # Show all scheduled tasks
/schedule pause 1      # Pause schedule #1 (skip runs)
/schedule resume 1     # Resume schedule #1
/schedule remove 1     # Remove schedule #1
/schedule clear        # Clear all scheduled tasks
```

**Supported interval formats**: `30m`, `1h`, `2h30m`, `1d`, `3600` (seconds), `every 30 minutes`

When a scheduled task is due, Mite prompts you before running it:

```
  ⏰ Scheduled task due: "check disk usage" (every 30m)
     Run now? [Y/n]
```

Queued and scheduled tasks persist in `~/.mite/queue.json` and `~/.mite/schedule.json` — they survive restarts and power loss.

### Userdata Directory (`~/.mite/`)

Mite stores your data in `~/.mite/`:

| Path | Description |
|------|-------------|
| `~/.mite/config.json` | Preferences (model, show_sysinfo, auto_continue) — set via `/config` |
| `~/.mite/queue.json` | Task queue — managed via `/queue` |
| `~/.mite/schedule.json` | Scheduled tasks — managed via `/schedule` |
| `~/.mite/conversations/` | Saved conversations — use `/save` and `/load` |
| `~/.mite/AGENT.md` | User-level persistent instructions — loaded on every prompt |
| `~/.mite_history` | Arrow-key command history |

**AGENT.md priority**: `./AGENT.md` > `./.mite/AGENT.md` > `~/.mite/AGENT.md`

Example:
```
# Save a conversation
┃ /save my-project-setup
  💾 Saved 12 messages to 'my-project-setup'

# List saved conversations
┃ /list
  📁 Saved conversations in ~/.mite/conversations/:
     • my-project-setup

# Load later
┃ /load my-project-setup
  📂 Loaded 12 messages from 'my-project-setup'
```

## How It Works

Mite uses a **structured output format** optimized for small models:

1. You type a task or question
2. Mite sends it to the local model
3. The model responds with either:
   - A natural language answer, OR
   - A tool call in structured format:
     ```
     THOUGHT: I should read the file first
     TOOL: read_file
     path: main.py
     ```
4. Mite parses the tool call, executes it, shows you the result
5. The result is fed back to the model for the next step

This `THOUGHT/TOOL/ARGS` format is much easier for small models to produce reliably than JSON function calling.

## Recommended Models

| Model | Params | RAM | Speed | Best for |
|-------|--------|-----|-------|----------|
| `qwen2.5:0.5b` | 0.5B | ~1GB | ⚡⚡⚡ | Basic file ops, simple tasks |
| `qwen2.5:1.5b` | 1.5B | ~2GB | ⚡⚡ | Good balance for most tasks |
| `qwen2.5:3b` | 3B | ~3GB | ⚡ | Complex reasoning, larger files |
| `llama3.2:1b` | 1B | ~2GB | ⚡⚡ | Multi-turn conversations |
| `phi3:mini` | 3.8B | ~4GB | ⚡ | Full-featured coding |

## Architecture

```
mite/
├── bin/mite           # Shell entry point
├── mite/
│   ├── __init__.py    # Package metadata
│   ├── __main__.py    # python -m mite
│   ├── cli.py         # CLI argument parsing + auto-setup
│   ├── core.py        # Main interaction loop + command handlers
│   ├── tools.py       # File/shell/search tool implementations
│   ├── prompts.py     # System prompts optimized for small models
│   ├── setup.py       # Ollama install + model pull
│   └── task_manager.py # Task queue + schedule management
├── update.sh           # Self-update script (backup → clone → restore)
├── setup.sh            # One-click setup script
└── README.md
```

## Why Small Models?

Not everyone has a GPU with 24GB of VRAM. Mite is for:

- **Laptops without dedicated GPUs** — Qwen2.5:0.5B runs on CPU with 4GB RAM
- **Raspberry Pi / edge devices** — the 0.5B model fits on an RPi 5
- **Privacy-first setups** — everything runs locally, no data leaves your machine
- **Quick tasks** — for simple code edits, a 0.5B model responds in 1-3 seconds

## Development

```bash
# Setup
cd mite
python3 -m venv .venv
source .venv/bin/activate

# Run directly
python -m mite

# Test specific models
MITE_MODEL=qwen2.5:1.5b python -m mite "list all python files"
```

## How It Differs From Claude Code / Codex

| Feature | Claude Code | Codex | **Mite** |
|---------|-------------|-------|----------|
| Model size | ~100B+ | ~100B+ | **0.5B-3B** |
| Local-only | ❌ | ❌ | **✅** |
| Auto-setup | Manual | Manual | **✅ One command** |
| RAM needed | 8GB+ | 8GB+ | **~1GB** |
| GPU needed | Yes | Yes | **Optional** (CPU ok) |
| Tool format | JSON | JSON | **Simple text** (small models) |
| Speed | Fast (API) | Fast (API) | **Fast (local)** |

## License

MIT
