# Mite

Micro AI Terminal Engineer — a lightweight AI coding assistant designed for small local models (0.5B–3B parameters).

## Design Philosophy

Most AI coding assistants (Claude Code, Codex, Hermes) require powerful models with JSON function calling. Mite is built for the opposite end of the spectrum — tiny models that fit on a Raspberry Pi, run on a CPU, and respond in seconds.

### Key Design Choices for Tiny Models

- **Single-line TOOL format**: `TOOL read_file path=main.py` instead of JSON — tiny models produce structured text much more reliably than JSON
- **Ultra-concise system prompt**: Under 200 tokens — every word matters for a 0.5B model
- **Aggressive context trimming**: Keeps only 4 recent messages — tiny context windows can't handle long histories
- **Low temperature (0.1)**: Minimizes creative drift in structured output
- **Short response limit**: 512 tokens max — small models degenerate with long generations
- **Structured noise filtering**: Strips chat contamination common in tiny model outputs

## Quick Start

```bash
# Run the auto-setup (installs Ollama + pulls model)
./setup.sh

# Or with Python directly
python -m mite

# Run a single task
python -m mite "list all Python files in this project"
```

## Requirements

- Python 3.10+
- Ollama (auto-installed by setup.sh)
- Internet connection (first run only — pulls the model)

## Commands

| Command | Description |
|---------|-------------|
| `/exit` | Exit Mite |
| `/reset` | Reset conversation |
| `/redo` (`/r`) | Re-run the last prompt |
| `/history` | Show recent context |
| `/model <name>` | Switch model (e.g., `/model qwen2.5:3b`) |
| `/agent` | View AGENT.md instructions |
| `/help` | Help text |

## Features

- **Up arrow history**: Press ↑ to recall previous prompts
- **`/redo`**: Retry the last prompt instantly
- **AGENT.md**: Create an `AGENT.md` file in your project root for persistent instructions the AI receives before every prompt
- **System info**: Automatically reports platform, memory, and disk at startup (disable with `--no-sysinfo`)
- **Auto-setup**: Installs Ollama and pulls the model on first run
- **Zero external dependencies**: Pure Python stdlib — no pip install needed

## Tools Available to the AI

- `read_file` — Read a file
- `write_file` — Write/create a file
- `patch` — Edit a file (find and replace)
- `shell` — Run a shell command
- `search` — Search file contents or find files
- `finish` — Mark task complete

## Example

```
$ python -m mite
  🤖 Mite active | model: qwen2.5:0.5b
  🖥  System info:
     Debian GNU/Linux 12 (bookworm)
     User: user
     Hostname: my-machine
     Available memory: 4.7 GB
     Available storage: 45.2 GB (of 120.5 GB)
  Commands: /exit  /reset  /history  /redo  /agent  /help
  Type your task or 'help' to start.

┃ show me all files in this project
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `MITE_MODEL` | `qwen2.5:0.5b` | Ollama model to use |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |

## License
MIT
