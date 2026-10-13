"""Select a TTS environment, then run tools.personal (standard library only).

    uv run --no-project --python 3.12 tools/personal_setup.py --name Arlen --realm Example --dry-run

Use --backend cuda|rocm|cpu to override detection. This never installs drivers.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def detect_backend() -> str | None:
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            result = subprocess.run(
                [nvidia, "-L"], capture_output=True, text=True, timeout=10, check=False
            )
            if result.returncode == 0 and "GPU" in result.stdout:
                return "cuda"
        except (OSError, subprocess.TimeoutExpired):
            pass
    if platform.system() == "Linux" and Path("/dev/kfd").exists():
        return "rocm"
    return None


def command(uv: str, backend: str, arguments: list[str]) -> list[str]:
    group = "tts-rocm" if backend == "rocm" else "tts"
    # --no-default-groups ensures ROCm does not also select the CUDA group.
    return [
        uv,
        "run",
        "--project",
        str(ROOT),
        "--no-default-groups",
        "--group",
        group,
        "--python",
        "3.12",
        "-m",
        "tools.personal",
        *arguments,
        "--device",
        "cpu" if backend == "cpu" else "auto",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument(
        "--backend", choices=("auto", "cuda", "rocm", "cpu"), default="auto"
    )
    args, remaining = parser.parse_known_args(argv)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv first, then rerun this command.")
    # Planning and help require neither a GPU nor the multi-GB TTS dependencies.
    if "--dry-run" in remaining or "--help" in remaining or "-h" in remaining:
        run = [
            uv,
            "run",
            "--project",
            str(ROOT),
            "--no-default-groups",
            "--python",
            "3.12",
            "-m",
            "tools.personal",
            *remaining,
        ]
        environment = "plan"
    else:
        backend = detect_backend() if args.backend == "auto" else args.backend
        if backend is None and "--cpu" in remaining:
            backend = "cpu"
        if backend is None:
            raise SystemExit(
                "No NVIDIA driver or Linux ROCm device detected. Use --backend cuda/rocm "
                "if detection missed your GPU, or --backend cpu to accept slow CPU generation. "
                "Apple MPS is not yet validated."
            )
        if backend == "rocm" and (
            platform.system() != "Linux"
            or platform.machine().lower() not in ("x86_64", "amd64")
        ):
            raise SystemExit(
                "The pinned ROCm wheels require Linux x86_64. Use a supported Linux setup or --backend cpu."
            )
        if backend == "cuda" and platform.system() == "Darwin":
            raise SystemExit(
                "CUDA is not available on macOS. Use --backend cpu or a supported GPU machine."
            )
        run = command(uv, backend, remaining)
        environment = backend
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(ROOT / f".venv-personal-{environment}")
    print(
        f"Personal voices: {platform.system()} {platform.machine()}, environment {environment}",
        flush=True,
    )
    print(
        "uv will install the selected dependencies if missing; TTS/model downloads can take several GB.",
        flush=True,
    )
    return subprocess.run(run, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
