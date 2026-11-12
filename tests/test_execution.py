"""One-shot shell commands must not grow memory unbounded or leave children."""
from __future__ import annotations
import os
from pathlib import Path
import shlex
import sys
import time

import pytest

from remote_dorado.execution import run_bounded_command


def test_bounded_stdout_and_stderr(tmp_path):
    code = "import sys;sys.stdout.write('x'*80000);sys.stderr.write('y'*90000)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    output = run_bounded_command(command, tmp_path, timeout_seconds=7, max_output=1024)
    assert output["returncode"] == 0
    assert output["stdout"] == "x" * 1024
    assert output["stderr"] == "y" * 1024
    assert output["truncated"] is True


def test_timeout_and_output(tmp_path):
    command = f"{shlex.quote(sys.executable)} -c 'import time; print(1,flush=True);time.sleep(10)'"
    start = time.monotonic()
    output = run_bounded_command(command, tmp_path, timeout_seconds=1)
    assert time.monotonic() - start < 5
    assert output["returncode"] is None
    assert output["timeout"] is True
    assert "1" in output["stdout"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX group cleanup test")
def test_timeout_terminates_process_group_children(tmp_path):
    marker = tmp_path / "orphan-created"
    code = f"import time,pathlib;time.sleep(2);pathlib.Path({str(marker)!r}).write_text('bad')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} & sleep 10"
    result = run_bounded_command(command, tmp_path, timeout_seconds=1)
    assert result["timeout"] is True
    time.sleep(2)
    assert not marker.exists()


def test_invalid_command_never_runs(tmp_path):
    with pytest.raises(ValueError):
        run_bounded_command("", tmp_path)
    with pytest.raises(ValueError):
        run_bounded_command("x" * 8001, tmp_path)
    with pytest.raises(ValueError):
        run_bounded_command("true", tmp_path, timeout_seconds=True)
