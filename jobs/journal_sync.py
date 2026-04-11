"""
journal_sync.py
Wraps kairos/run_sync.sh as a subprocess so the menubar app can trigger it
without re-implementing any sync logic. Captures stdout/stderr to a log file
and returns a structured result.
"""
import subprocess
import datetime
import pathlib
import os
from typing import Optional

KAIROS_DIR = pathlib.Path(__file__).resolve().parents[2] / "kairos"
LOG_DIR    = pathlib.Path(__file__).resolve().parents[1] / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE   = LOG_DIR / "journal.log"


def run_sync(timeout: int = 300) -> dict:
    """
    Execute run_sync.sh. Returns:
      {
        "ok": bool,
        "ts": iso timestamp,
        "duration_s": float,
        "exit_code": int,
        "tail": last ~20 lines of output,
      }
    """
    started = datetime.datetime.now()
    sep = f"\n{'='*60}\n  RUN @ {started.isoformat()}\n{'='*60}\n"

    with open(LOG_FILE, "a") as f:
        f.write(sep)
        f.flush()

        try:
            proc = subprocess.run(
                ["./run_sync.sh"],
                cwd=str(KAIROS_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                env={**os.environ, "PATH": os.environ.get("PATH", "")},
            )
            output = proc.stdout or ""
            f.write(output)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            output = f"TIMEOUT after {timeout}s"
            f.write(output + "\n")
            exit_code = -1
        except Exception as e:
            output = f"EXCEPTION: {e}"
            f.write(output + "\n")
            exit_code = -2

    finished = datetime.datetime.now()
    tail_lines = output.strip().splitlines()[-20:]
    return {
        "ok": exit_code == 0,
        "ts": finished.isoformat(),
        "duration_s": round((finished - started).total_seconds(), 1),
        "exit_code": exit_code,
        "tail": "\n".join(tail_lines),
    }


def last_data_age_hours() -> Optional[float]:
    """How long since data.json was last modified (or None if missing)."""
    p = KAIROS_DIR / "data.json"
    if not p.exists():
        return None
    mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
    return round((datetime.datetime.now() - mtime).total_seconds() / 3600, 1)
