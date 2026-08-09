"""Compiling the filled document with Tectonic.

Compilation happens in a temporary directory that is deleted afterwards, so aux
and log files never accumulate next to your resume, whether the run succeeded or
failed.

Failures here are usually template or escaping bugs, and Tectonic's raw log is a
bad way to find them. `_summarize` pulls out the actual error lines and, where
TeX reports a line number, shows the offending line of the generated `.tex`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import CompileError

TECTONIC_ENV_VAR = "RESUME_TAILOR_TECTONIC"

_TEX_ERROR_RE = re.compile(r"^(?:error:\s*)?!\s*(.+)$")
_TECTONIC_ERROR_RE = re.compile(r"^error:\s*(.+)$")
_LINE_REF_RE = re.compile(r"^l\.(\d+)\s*(.*)$")
_MISSING_FILE_RE = re.compile(r"(?:file|package)\s+[`\"']?([\w\-.]+)[`\"']?\s+not found", re.I)


def _local_roots() -> list[Path]:
    """Where a manually downloaded binary might live.

    `.tools/` is what the README suggests on platforms with no package for
    Tectonic. Checked relative to both the working directory and the source
    tree, so running the tool from another directory still finds it.
    """

    return [Path.cwd(), Path(__file__).resolve().parents[2]]


def find_tectonic(explicit: str | None = None) -> str:
    """Locate the Tectonic binary, or explain how to get one."""

    candidates = [
        explicit,
        os.environ.get(TECTONIC_ENV_VAR),
        shutil.which("tectonic"),
        *[
            str(root / ".tools" / name)
            for root in _local_roots()
            for name in ("tectonic.exe", "tectonic")
        ],
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
        if candidate and shutil.which(candidate):
            return candidate

    raise CompileError(
        "tectonic was not found.\n"
        "Install it (see README), or point at an existing binary with "
        f"{TECTONIC_ENV_VAR}=/path/to/tectonic."
    )


def compile_pdf(
    tex: str,
    tectonic: str | None = None,
    timeout: int = 300,
) -> bytes:
    """Compile a LaTeX source string and return the PDF bytes."""

    binary = find_tectonic(tectonic)

    with tempfile.TemporaryDirectory(prefix="resume-tailor-") as workdir:
        directory = Path(workdir)
        source = directory / "resume.tex"
        source.write_text(tex, encoding="utf-8")

        command = [
            binary,
            "--outdir",
            str(directory),
            "--chatter",
            "minimal",
            "--color",
            "never",
            "--untrusted",
            str(source),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=directory,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompileError(
                f"tectonic did not finish within {timeout}s. If this is the first run, "
                "it may be downloading LaTeX packages on a slow connection; try again."
            ) from exc
        except OSError as exc:
            raise CompileError(f"could not run tectonic at {binary}: {exc}") from exc

        output = f"{result.stdout}\n{result.stderr}"
        pdf = directory / "resume.pdf"

        if result.returncode != 0 or not pdf.exists():
            raise CompileError(_summarize(output, tex))

        return pdf.read_bytes()
    # The temporary directory, and every aux/log file in it, is gone by here.


def _summarize(output: str, tex: str) -> str:
    """Turn Tectonic's output into something worth reading."""

    source_lines = tex.splitlines()
    reported: list[str] = []
    pending_line_ref: str | None = None

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue

        tex_error = _TEX_ERROR_RE.match(line)
        tectonic_error = _TECTONIC_ERROR_RE.match(line)
        line_ref = _LINE_REF_RE.match(line)

        if line_ref:
            number = int(line_ref.group(1))
            excerpt = source_lines[number - 1].strip() if 0 < number <= len(source_lines) else ""
            pending_line_ref = f"    at generated line {number}: {excerpt}" if excerpt else None
            if pending_line_ref and reported:
                reported.append(pending_line_ref)
            continue

        if tex_error:
            reported.append(f"  {tex_error.group(1)}")
        elif tectonic_error:
            message = tectonic_error.group(1)
            if _is_noise(message):
                continue
            reported.append(f"  {message}")

    if not reported:
        tail = [line for line in output.splitlines() if line.strip()][-6:]
        reported = [f"  {line.strip()}" for line in tail] or ["  (tectonic produced no output)"]

    hint = ""
    missing = _MISSING_FILE_RE.search(output)
    if missing:
        hint = (
            f"\n\nThe document asked for {missing.group(1)!r}, which Tectonic could not "
            "fetch. Check the package name in the template, and that you are online — "
            "Tectonic downloads packages on first use."
        )

    return "the resume failed to compile.\n" + "\n".join(_dedupe(reported)[:8]) + hint


def _is_noise(message: str) -> bool:
    """Tectonic's closing summary lines repeat what the real error already said."""

    lowered = message.lower()
    return lowered.startswith(
        (
            "the tex engine had an unrecoverable error",
            "the tex engine halted",
            "halted on potentially-recoverable error",
            "unable to summarize",
        )
    )


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result
