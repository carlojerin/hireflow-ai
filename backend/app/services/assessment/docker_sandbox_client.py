import os
import subprocess
import time
import uuid
from dataclasses import dataclass


# ============================================================
# JOBPILOT AI - DIRECT DOCKER SANDBOX CLIENT
#
# Supersedes piston_client.py and judge0_client.py (both left on
# disk, unused) after Piston's post-boot package downloads proved
# unreliable on this network, and Judge0's bundled `isolate` sandbox
# turned out incompatible with Docker Desktop's cgroup v2-only
# internal environment (a structural issue, not fixable by retrying
# or waiting).
#
# This runs each submission in its OWN short-lived container,
# directly against the Docker daemon already confirmed working on
# this machine — no third-party judge service, no bundled sandbox
# tool with its own compatibility baggage. Docker's own container
# isolation (namespaces + cgroups, natively cgroup-v2-compatible)
# IS the sandbox:
#   --network none          no network access at all
#   --memory / --cpus       hard resource ceilings
#   --pids-limit            fork-bomb protection
#   --read-only root fs     only the mounted /sandbox dir is writable
#   --security-opt no-new-privileges
#   a non-root user
#
# The candidate's source file(s) are written to a host temp
# directory, bind-mounted read-write into the container at
# /sandbox (read-write only so a compile step can write its output
# there too — the directory itself is thrown away after, and is
# never anything but this one submission's own files).
# ============================================================

DEFAULT_MEMORY_MB = 256
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS_LIMIT = 128
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_COMPILE_TIMEOUT_SECONDS = 30

_SANDBOX_UID = 1000  # an ordinary, non-root, unprivileged user inside the container


class DockerUnavailableError(Exception):
    """Docker itself could not be reached/run at all (daemon down, docker.exe missing)."""


@dataclass(frozen=True)
class ContainerRunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_ms: int


def _docker_exe() -> str:
    return os.getenv("DOCKER_EXE") or "docker"


_OUTPUT_LIMIT_BYTES = 64 * 1024


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _OUTPUT_LIMIT_BYTES:
        return text
    return encoded[:_OUTPUT_LIMIT_BYTES].decode("utf-8", errors="ignore") + "\n...[output truncated]"


def _decode(value: bytes | str | None) -> str:
    """proc.stdout/stderr are bytes now that input is piped in binary
    mode (see run_in_container()); TimeoutExpired's own .stdout/.stderr
    can be None if nothing was captured before the kill."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


_last_health_check_at: float = 0.0
_last_health_check_ok: bool = False
_HEALTH_CHECK_CACHE_SECONDS = 20


def is_available(force: bool = False) -> bool:
    """
    Cheap reachability check, cached for a short TTL: can the Docker
    CLI actually reach the daemon right now. Real submissions still
    run through run_in_sandbox() below regardless — this only gates
    whether a runner reports itself usable at all.
    """

    global _last_health_check_at, _last_health_check_ok

    now = time.monotonic()

    if not force and (now - _last_health_check_at) < _HEALTH_CHECK_CACHE_SECONDS:
        return _last_health_check_ok

    try:
        proc = subprocess.run(
            [_docker_exe(), "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10,
        )
        _last_health_check_ok = proc.returncode == 0

    except Exception:
        _last_health_check_ok = False

    _last_health_check_at = now

    return _last_health_check_ok


_SEED_IMAGE = "alpine:latest"


def create_workdir(files: dict[str, str]) -> str:
    """
    Creates a fresh DOCKER NAMED VOLUME (not a host directory) and
    seeds it with `files` ({relative_path: content}), returning the
    volume's name. A multi-step submission (compile THEN run) must
    reuse this SAME name across both run_in_container() calls — each
    call mounts whatever volume it's given, but never creates or
    deletes one itself, so a compiled binary written by a compile
    step is still there for the run step that follows it. The caller
    is responsible for calling cleanup_workdir() when done, in a
    `finally` block.

    A HOST temp directory bind-mounted in (this function's first
    version) measured 25-30x slower for I/O-heavy compilers on this
    Docker Desktop/WSL2 setup — a kotlinc compile that takes ~3s
    against a volume took ~96s against a bind-mounted host directory,
    because every one of its many small classfile reads/writes
    crosses the Windows-host <-> WSL2 translation layer. A named
    volume lives entirely inside Docker's own Linux environment, so
    it never crosses that boundary.
    """

    volume_name = f"jobpilot_exec_{uuid.uuid4().hex[:16]}"

    subprocess.run(
        [_docker_exe(), "volume", "create", volume_name],
        capture_output=True, text=True, timeout=15, check=True,
    )

    for relative_path, content in files.items():

        directory = os.path.dirname(relative_path)

        shell_script = (
            (f"mkdir -p '{directory}' && " if directory else "")
            + f"cat > '{relative_path}'"
        )

        subprocess.run(
            [
                _docker_exe(), "run", "--rm", "-i",
                "--network", "none",
                "-v", f"{volume_name}:/sandbox",
                "-w", "/sandbox",
                _SEED_IMAGE, "sh", "-c", shell_script,
            ],
            # Raw bytes, no text=True: on Windows, subprocess's text
            # mode silently translates every "\n" the candidate's own
            # source/input content wrote into "\r\n" before piping it
            # into the (Linux) container — corrupting file content.
            input=content.encode("utf-8"), capture_output=True, timeout=15,
        )

    # The seed container above runs as root (Docker's default), so
    # every file it just wrote — and the volume's own root directory
    # — is root-owned. run_in_container() later runs as a non-root
    # sandbox uid, which a compile step's OWN output file (a .class/
    # .jar/binary it needs to CREATE, not just read) would then be
    # denied permission to write — confirmed directly: kotlinc's
    # output jar failed with FileNotFoundException/Permission denied
    # until this chmod was added. World-writable is fine: this
    # volume holds exactly one submission's own files for its own
    # short lifetime, nothing sensitive.
    subprocess.run(
        [
            _docker_exe(), "run", "--rm",
            "--network", "none",
            "-v", f"{volume_name}:/sandbox",
            _SEED_IMAGE, "chmod", "-R", "777", "/sandbox",
        ],
        capture_output=True, text=True, timeout=15,
    )

    return volume_name


def cleanup_workdir(work_dir: str) -> None:

    subprocess.run(
        [_docker_exe(), "volume", "rm", "-f", work_dir],
        capture_output=True, timeout=15,
    )


def run_in_container(
    image: str,
    command: list[str],
    work_dir: str,
    stdin: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: str = DEFAULT_CPUS,
) -> ContainerRunResult:
    """
    Runs `command` inside a throwaway container of `image`, with
    `work_dir` (from create_workdir(), still on disk) mounted at
    /sandbox (cwd) — a compile step and the run step that follows it
    pass the SAME work_dir, so a compiled binary the first step
    wrote is still there for the second. Pipes `stdin` in and
    returns the captured result. Never raises for a candidate-caused
    failure (non-zero exit, timeout) — only a genuine Docker/
    infrastructure failure raises DockerUnavailableError.
    """

    container_name = f"jobpilot_exec_{uuid.uuid4().hex[:16]}"

    args = [
        _docker_exe(), "run", "--rm", "-i",
        "--name", container_name,
        "--network", "none",
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",
        "--cpus", cpus,
        "--pids-limit", str(DEFAULT_PIDS_LIMIT),
        "--security-opt", "no-new-privileges",
        "--user", str(_SANDBOX_UID),
        # The sandbox uid isn't a real user in most images' /etc/passwd,
        # so it has no HOME by default — some toolchains (Go's build
        # cache is the concrete case that surfaced this) then try to
        # write to a literal "/.cache" and fail with permission denied,
        # since only root can create new top-level directories. Pointing
        # HOME at the one directory this uid can always write to fixes
        # that for every language uniformly, not just Go.
        "-e", "HOME=/sandbox",
        "-v", f"{work_dir}:/sandbox",
        "-w", "/sandbox",
        image,
        *command,
    ]

    started = time.monotonic()

    try:
        # Raw bytes, no text=True: on Windows, subprocess's text mode
        # silently translates every "\n" in `stdin` into "\r\n" before
        # piping it into the (Linux) container, so a candidate's
        # correct solution reading multi-line stdin (e.g. via
        # "\n".join(lines) or an exact string comparison) would see
        # corrupted input and fail test cases it should pass. Decoded
        # back to str manually below instead.
        proc = subprocess.run(
            args, input=stdin.encode("utf-8"), capture_output=True,
            timeout=timeout_seconds,
        )

    except subprocess.TimeoutExpired as exc:

        # subprocess's own timeout only kills the local `docker
        # run` CLI process, not necessarily the container itself
        # (docker run without --rm-on-kill semantics can leave
        # the container running) — kill it explicitly by name.
        subprocess.run(
            [_docker_exe(), "kill", container_name],
            capture_output=True, timeout=10,
        )

        elapsed_ms = int((time.monotonic() - started) * 1000)

        return ContainerRunResult(
            exit_code=None,
            stdout=_truncate(_decode(exc.stdout)),
            stderr=_truncate(_decode(exc.stderr)),
            timed_out=True,
            elapsed_ms=elapsed_ms,
        )

    except FileNotFoundError as exc:
        raise DockerUnavailableError(f"docker executable not found: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)

    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)

    # A non-zero exit specifically from `docker run` ITSELF
    # (image pull failure, daemon unreachable mid-call) looks
    # like a normal non-zero exit too — but Docker prefixes its
    # own errors with "docker:" on stderr, never the sandboxed
    # program's own stderr, which this project's runners never
    # otherwise produce with that literal prefix.
    if proc.returncode == 125 and stderr.strip().startswith("docker:"):
        raise DockerUnavailableError(stderr.strip()[:300])

    return ContainerRunResult(
        exit_code=proc.returncode,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        timed_out=False,
        elapsed_ms=elapsed_ms,
    )
