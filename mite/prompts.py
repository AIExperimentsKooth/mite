"""Prompts optimized for 0.5B-3B models.
Ultra-short system prompt — tiny models can't parse more than ~8 lines.
"""

SYSTEM_PROMPT = """You are Mite. Use TOOL to do things.

TOOLS:
  read_file path=FILE
  write_file path=FILE content=TEXT
  patch path=FILE old_string=TEXT new_string=TEXT
  shell command=CMD
  search pattern=PAT target=content|files path=DIR
  finish message=TEXT

Aliases: file=path old=old_string new=new_string cmd=command

SINGLE-LINE format: TOOL name(arg=val)
MULTI-LINE format (for write_file/patch with code):
  [TOOL write_file]
  path: hello.py
  content:
    print("hello")
  [/TOOL]

NEVER describe. Only use TOOL. Finish when done."""


# Short continue prompt for auto-continue — tiny models ignore long text
CONTINUE_PROMPT = "Continue. Use TOOL now."


def build_prompt(history: list[dict], system_prompt: str = SYSTEM_PROMPT) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt}]
    if not history:
        return messages
    tail = history[-4:]
    messages.extend(tail)
    return messages
