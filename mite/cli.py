"""
CLI entry point for Mite.
Parses arguments, runs setup if needed, then starts the interactive loop.
"""
import argparse
import sys
import os
from . import core, setup, __version__


def main():
    parser = argparse.ArgumentParser(
        prog="mite",
        description="Mite - Micro AI Terminal Engineer (lightweight AI coding assistant)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  mite                          Interactive mode with default model
  mite "fix the bug in main.py" Run a single task
  mite --model qwen2.5:3b       Use a larger model
  mite --setup                  Run setup only (install Ollama, pull model)
  mite --no-sysinfo             Skip system information report
  mite --host http://192.168.1.5:11434  Connect to remote Ollama
        """
    )
    parser.add_argument("task", nargs="?", help="Task to execute (omit for interactive mode)")
    parser.add_argument("--model", "-m", default=os.environ.get("MITE_MODEL", "qwen2.5:0.5b"),
                        help="Ollama model to use (default: qwen2.5:0.5b, env: MITE_MODEL)")
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                        help="Ollama API host (default: http://localhost:11434, env: OLLAMA_HOST)")
    parser.add_argument("--setup", action="store_true",
                        help="Run setup (install Ollama, pull model) then exit")
    parser.add_argument("--version", "-v", action="store_true",
                        help="Show version and exit")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Auto-confirm setup prompts")
    parser.add_argument("--no-setup", action="store_true",
                        help="Skip auto-setup check and run directly")
    parser.add_argument("--debug", action="store_true",
                        help="Show debug information")
    parser.add_argument("--no-sysinfo", action="store_true",
                        help="Skip system information report at startup")

    args = parser.parse_args()

    if args.version:
        print(f"Mite v{__version__}")
        print(f"Model: {args.model}")
        return

    if args.debug:
        print(f"Mite v{__version__}")
        print(f"Model: {args.model}")
        print(f"Host: {args.host}")
        print(f"Task: {args.task}")
        print(f"Python: {sys.version}")
        return

    # Auto-setup: run setup if needed, unless --no-setup
    if not args.no_setup:
        _auto_setup(args.model, args.host, args.yes)

    # If --setup only, we're done
    if args.setup:
        return

    # Start the interactive loop
    try:
        core.run_loop(
            model=args.model,
            host=args.host,
            initial_task=args.task,
            show_sysinfo=not args.no_sysinfo
        )
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        print(f"\n  \u26a0 Fatal error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def _auto_setup(model: str, host: str, auto_confirm: bool = False):
    """Check if setup is needed, and run it if so."""
    import subprocess
    import shutil

    # Check if Ollama is available
    ollama_ok = shutil.which("ollama") is not None

    # Check if model is already pulled
    model_pulled = False
    if ollama_ok:
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10
            )
            if model in result.stdout:
                model_pulled = True
        except Exception:
            pass

    if not ollama_ok:
        print("  \u26a0 Ollama not found. Setup required.")
        if not auto_confirm:
            response = input("  Run setup (install Ollama + pull model)? [Y/n]: ").strip().lower()
            if response in ("n", "no"):
                print("  Setup skipped. You'll need to install Ollama manually.")
                print("  See: https://ollama.com/download")
                return
        setup.run(model)
    elif not model_pulled:
        print(f"  \u26a0 Model '{model}' not found locally.")
        if not auto_confirm:
            response = input(f"  Pull model '{model}'? [Y/n]: ").strip().lower()
            if response in ("n", "no"):
                print(f"  Skipping model pull. Run: ollama pull {model}")
                return
        setup.start_ollama()
        setup.wait_for_ollama()
        setup.pull_model(model)
        setup.test_model(model)
    else:
        # Ensure Ollama is running
        setup.start_ollama()
        setup.wait_for_ollama()


if __name__ == "__main__":
    main()
