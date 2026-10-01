"""What this process can actually use — cores, memory, and whether it may fork.

Every function here answers the same question in a different unit: *how much
of this machine is really mine?* The answer is never `os.cpu_count()` and
never `MemFree`, because a container is limited by two independent mechanisms
and neither of those two numbers can see either one.

These lived in `application/services/ingest/PdfLayoutService`, which is a module
about PDF word boxes. Nothing about parsing a cgroup file is PDF-specific, and
`ProcessService` was already importing them across from there to size its
OCR pool — a dependency on a PDF module for a fact about the host. They are
read-only probes of the machine, with no settings and no state, so they belong
beside the other pure readers in `utils`.

The path constants are module-level rather than inlined so a test can point
them at a fixture file instead of the real (and, on a dev box, absent)
`/sys/fs/cgroup`.
"""

import os
from pathlib import Path

# cgroup v2's unified hierarchy, and v1's fallback — checked in that order.
_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")

_MEMINFO = Path("/proc/meminfo")
_CGROUP_V2_MEM_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEM_MAX = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")


def cgroup_quota_cores() -> float | None:
    """Cores this process is limited to by a cgroup CPU *quota*, or None.

    A quota (Docker's ``--cpus``, compose's ``deploy.resources.limits.cpus``)
    caps total CPU *time* across however many cores the kernel schedules
    onto — it is a different mechanism from ``--cpuset-cpus``, which pins a
    process to specific cores. Nothing about a time quota changes which cores
    are visible, so `sched_getaffinity` cannot see it and would report the
    full host regardless of it — this is the other half of the ceiling,
    checked separately, because either limit can apply without the other.
    """
    try:
        if _CGROUP_V2_CPU_MAX.is_file():
            quota, period = _CGROUP_V2_CPU_MAX.read_text().split()
            return int(quota) / int(period) if quota != "max" else None

        if _CGROUP_V1_QUOTA.is_file() and _CGROUP_V1_PERIOD.is_file():
            quota = int(_CGROUP_V1_QUOTA.read_text())
            return quota / int(_CGROUP_V1_PERIOD.read_text()) if quota > 0 else None
    except (OSError, ValueError):
        # Malformed or unreadable — same as "no quota found", not a reason to
        # fail the work it is only trying to size a worker pool for.
        pass

    return None


def available_memory_mb() -> float | None:
    """Memory this process can actually take, in MB, or None if unknowable.

    A container limit when there is one, the host's own `MemAvailable`
    otherwise — an unlimited container on a busy host is not unlimited, it is
    limited by whatever else is running, and that is the number that decides
    whether the kernel reaches for the OOM killer.

    `MemAvailable` rather than `MemFree` deliberately: the kernel will evict
    page cache under pressure, so free memory alone understates what is
    obtainable by several gigabytes.
    """
    for path in (_CGROUP_V2_MEM_MAX, _CGROUP_V1_MEM_MAX):
        try:
            if path.is_file():
                raw = path.read_text().strip()
                # v2 writes "max" for no limit; v1 writes a number near 2**63.
                if raw != "max" and int(raw) < (1 << 62):
                    return int(raw) / (1024 * 1024)
        except (OSError, ValueError):
            pass

    try:
        for line in _MEMINFO.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass

    return None


def is_daemonic() -> bool:
    """Whether this process may not fork children.

    True inside a Celery prefork worker. Checked rather than assumed, so the
    same caller stays parallel when run from the API process, a script or a
    test — the constraint belongs to the calling context, not to any one
    module.
    """
    import multiprocessing

    try:
        return bool(multiprocessing.current_process().daemon)
    except Exception:  # noqa: BLE001 - an unknown context is not a daemon
        return False


def cpu_count() -> int:
    """The CPUs this process can actually use in parallel — not the host's
    total, and not only what a cpuset affinity mask reports.

    Two different things restrict a container, and neither implies the
    other:

      * ``--cpuset-cpus`` pins the process to specific cores. Linux's own
        affinity mask sees this; plain ``os.cpu_count()`` does not, which is
        why that alone is not used here.
      * ``--cpus`` / ``deploy.resources.limits.cpus`` caps total CPU *time*
        across however many cores the kernel happens to schedule onto. The
        affinity mask cannot see this either — under a `--cpus=2` limit on a
        24-core host, it still reports 24, and sizing the pool from that
        would spawn 24 workers to fight over a 2-core budget, each starved to
        a fraction of what a correctly-sized pool would have given it.

    The real ceiling is the smaller of whichever of the two actually apply.
    """
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:
        # sched_getaffinity doesn't exist off Linux (Windows, macOS); a dev
        # machine there has no cgroup to check either.
        return os.cpu_count() or 1

    quota = cgroup_quota_cores()
    if quota is not None:
        # Floored, not rounded: a 2.5-core quota can usefully run 2 workers
        # at full tilt, not 3 fighting over the last half-core between them.
        cores = min(cores, max(1, int(quota)))

    return cores
