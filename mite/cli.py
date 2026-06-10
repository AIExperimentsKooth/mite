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
  mite --update                 Update Mite to the latest version from GitHub
  mite --update --dev            Update from the dev branch
  mite --branch dev              (same, for scripting)
  mite --no-sysinfo             Skip system information report
  mite --host http://192.168.1.5:11434  Connect to remote Ollama
  mite --no-auto-continue      Disable auto-continue (wait after every step)
  mite --stuck-threshold 15   No-tool replies before stuck detection (default: 10)
  mite --backend llamacpp     Use llama.cpp backend instead of Ollama (default: ollama)
  mite --setup --backend llamacpp  Setup llama.cpp backend on i686/ARM
        """
    )
    parser.add_argument("task", nargs="?", help="Task to execute (omit for interactive mode)")
    parser.add_argument("--model", "-m", default=os.environ.get("MITE_MODEL", "qwen2.5:0.5b"),
                        help="Ollama model to use (default: qwen2.5:0.5b, env: MITE_MODEL)")
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                        help="API host (default: http://localhost:11434, env: OLLAMA_HOST). For llamacpp: http://localhost:8080")
    parser.add_argument("--setup", action="store_true",
                        help="Run setup (install Ollama, pull model) then exit")
    parser.add_argument("--update", action="store_true",
                        help="Update Mite to the latest version from GitHub")
    parser.add_argument("--branch", "-b", default="main",
                        help="Git branch to update from (default: main). Use --dev for dev branch")
    parser.add_argument("--dev", action="store_true",
                        help="Shorthand for --branch dev")
    parser.add_argument("--version", "-v", action="store_true",
                        help="Show version and exit")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Auto-confirm setup prompts")
    parser.add_argument("--no-setup", action="store_true",
                        help="Skip auto-setup check and run directly")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug mode (show full model output, raw tool calls, thinking)")
    parser.add_argument("--no-sysinfo", action=argparse.BooleanOptionalAction, default=None,
                        help="Skip system information report at startup (also: --sysinfo)")
    parser.add_argument("--no-auto-continue", action=argparse.BooleanOptionalAction, default=None,
                        help="Disable auto-continue (wait for input after every step; also: --auto-continue)")
    parser.add_argument("--stuck-threshold", type=int, default=None,
                        help="No-tool replies before stuck detection (default: 10)")
    parser.add_argument("--backend", default=None, choices=["ollama", "llamacpp"],
                        help="LLM backend to use (ollama or llamacpp, default: ollama)")
    args = parser.parse_args()
    if args.version:
        print(f"Mite v{__version__}")
        print(f"Model: {args.model}")
        return
    if args.debug:
        print(f"Mite v{__version__} — debug mode enabled")
        print(f"Model: {args.model}")
        print(f"Host: {args.host}")
        print(f"Backend: {args.backend}")
    if args.update:
        branch = "dev" if args.dev else args.branch
        _run_update(args.yes, branch)
        return
    if not args.no_setup:
        _auto_setup(args.model, args.host, args.yes, backend=args.backend)
    if args.setup:
        return
    try:
        core.run_loop(
            model=args.model,
            host=args.host,
            initial_task=args.task,
            show_sysinfo=None if args.no_sysinfo is None else (not args.no_sysinfo),
            auto_continue=None if args.no_auto_continue is None else (not args.no_auto_continue),
            stuck_threshold=args.stuck_threshold,
            backend=args.backend,
            debug=True if args.debug else None
        )
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        print(f"\n  \u26a0 Fatal error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def _auto_setup(model: str, host: str, auto_confirm: bool = False, backend: str = "auto"):
    import subprocess
    import shutil
    from . import setup

    if backend == "auto":
        backend = setup.suggest_backend()

    if backend == "llamacpp":
        print(f"  \u2699 Setting up llama.cpp backend for {setup.detect_arch()}...")
        if not auto_confirm:
            response = input("  Build llama.cpp from source + download model? [Y/n]: ").strip().lower()
            if response in ("n", "no"):
                print("  Setup skipped. Run: python -m mite --setup")
                return
        setup.run(model, backend="llamacpp")
        return

    # Ollama path
    ollama_ok = shutil.which("ollama") is not None
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
        setup.run(model, backend="ollama")
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
        setup.test_ollama(model)
    else:
        setup.start_ollama()
        setup.wait_for_ollama()


def _run_update(auto_confirm: bool = False, branch: str = "main"):
    import subprocess
    import os
    search_paths = [
        os.path.join(os.path.dirname(__file__), "..", "update.sh"),
        os.path.join(os.getcwd(), "update.sh"),
    ]
    update_script = None
    for p in search_paths:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            update_script = p
            break
    if not update_script:
        print("  \u26a0 Cannot find update.sh.")
        print("     Download it from: https://github.com/AIExperimentsKooth/mite")
        print("     Or run: bash <(curl -fsSL https://raw.githubusercontent.com/AIExperimentsKooth/mite/main/update.sh)")
        return
    print(f"  Running: {update_script}\n")
    cmd = ["bash", update_script, "--branch", branch]
    if auto_confirm:
        cmd.append("--yes")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n  \u26a0 Update failed (exit code {e.returncode}).")
        print("  Your ~/.mite/ userdata was backed up and restored automatically.")
        if e.returncode == 128:
            print()
            print("  This is usually a GitHub authentication issue for private repos.")
            print("  To fix, run with a token:")
            print("    MITE_TOKEN=ghp_xxx mite --update")
            print("  Or authenticate gh CLI:")
            print("    gh auth login")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Update cancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
