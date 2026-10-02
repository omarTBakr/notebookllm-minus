"""How many workers this process may actually run, and how much memory it has.

The point of this whole file: a container capped with --cpus (a CFS bandwidth
quota) is invisible to sched_getaffinity, which only sees --cpuset-cpus (a pin
to specific cores). Sizing a pool from affinity alone would spawn a worker per
host core regardless of the quota, and starve every one of them to a fraction
of what a correctly-sized pool would have given it.

These moved here with the functions themselves, out of test_pdf_layout.py —
nothing about parsing a cgroup file is specific to PDFs, and both the layout
extractor and ProcessService's OCR pool size themselves from these.
"""

import shared.utils.resources as resources
from shared.utils import cgroup_quota_cores, cpu_count


def _no_cgroup_files(monkeypatch, tmp_path):
    """Neither v2 nor v1 quota files exist — the common case off a real
    cgroup (this dev sandbox included)."""
    monkeypatch.setattr(resources, "_CGROUP_V2_CPU_MAX", tmp_path / "absent-v2")
    monkeypatch.setattr(resources, "_CGROUP_V1_QUOTA", tmp_path / "absent-v1-quota")
    monkeypatch.setattr(resources, "_CGROUP_V1_PERIOD", tmp_path / "absent-v1-period")


def test_no_cgroup_files_means_no_quota(monkeypatch, tmp_path):
    _no_cgroup_files(monkeypatch, tmp_path)

    assert cgroup_quota_cores() is None


def test_cgroup_v2_max_means_unlimited(monkeypatch, tmp_path):
    v2 = tmp_path / "cpu.max"
    v2.write_text("max 100000\n")
    monkeypatch.setattr(resources, "_CGROUP_V2_CPU_MAX", v2)

    assert cgroup_quota_cores() is None


def test_cgroup_v2_quota_is_quota_over_period(monkeypatch, tmp_path):
    """--cpus=2, in v2's own units: 200000 quota / 100000 period = 2 cores."""
    v2 = tmp_path / "cpu.max"
    v2.write_text("200000 100000\n")
    monkeypatch.setattr(resources, "_CGROUP_V2_CPU_MAX", v2)

    assert cgroup_quota_cores() == 2.0


def test_cgroup_v2_fractional_quota(monkeypatch, tmp_path):
    """--cpus=2.5."""
    v2 = tmp_path / "cpu.max"
    v2.write_text("250000 100000\n")
    monkeypatch.setattr(resources, "_CGROUP_V2_CPU_MAX", v2)

    assert cgroup_quota_cores() == 2.5


def test_cgroup_v1_negative_quota_means_unlimited(monkeypatch, tmp_path):
    """-1 is v1's spelling of "no limit"."""
    _no_cgroup_files(monkeypatch, tmp_path)  # v2 absent, so v1 is checked
    quota, period = tmp_path / "cfs_quota_us", tmp_path / "cfs_period_us"
    quota.write_text("-1\n")
    period.write_text("100000\n")
    monkeypatch.setattr(resources, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(resources, "_CGROUP_V1_PERIOD", period)

    assert cgroup_quota_cores() is None


def test_cgroup_v1_quota_is_quota_over_period(monkeypatch, tmp_path):
    _no_cgroup_files(monkeypatch, tmp_path)
    quota, period = tmp_path / "cfs_quota_us", tmp_path / "cfs_period_us"
    quota.write_text("150000\n")
    period.write_text("100000\n")
    monkeypatch.setattr(resources, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(resources, "_CGROUP_V1_PERIOD", period)

    assert cgroup_quota_cores() == 1.5


def test_v2_is_checked_before_v1():
    """Both directories can exist on a v1 host running a v2-aware kernel;
    v2 is the one actually enforced when both are mounted."""
    # Not monkeypatched — this just documents the order cgroup_quota_cores
    # itself checks in, via its source: v2's is_file() check comes first and
    # returns before v1 is ever consulted. Covered functionally by the tests
    # above, each of which patches only the layer they mean to exercise.
    import inspect

    source = inspect.getsource(cgroup_quota_cores)
    assert source.index("_CGROUP_V2_CPU_MAX") < source.index("_CGROUP_V1_QUOTA")


def test_a_malformed_quota_file_is_treated_as_no_quota(monkeypatch, tmp_path):
    """Sizing a worker pool is not worth failing the whole extraction over."""
    v2 = tmp_path / "cpu.max"
    v2.write_text("garbage\n")
    monkeypatch.setattr(resources, "_CGROUP_V2_CPU_MAX", v2)

    assert cgroup_quota_cores() is None


def testcpu_count_is_capped_by_a_quota_smaller_than_affinity(monkeypatch):
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(24)))
    monkeypatch.setattr(resources, "cgroup_quota_cores", lambda: 2.0)

    assert cpu_count() == 2


def testcpu_count_floors_a_fractional_quota(monkeypatch):
    """2.9 cores of quota can run 2 workers at full tilt, not 3 splitting a
    ninth of a core three ways for no benefit."""
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(24)))
    monkeypatch.setattr(resources, "cgroup_quota_cores", lambda: 2.9)

    assert cpu_count() == 2


def testcpu_count_ignores_a_quota_larger_than_affinity(monkeypatch):
    """A cpuset pin to 4 cores plus a --cpus=16 quota: the pin is the real
    ceiling, not the quota."""
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(4)))
    monkeypatch.setattr(resources, "cgroup_quota_cores", lambda: 16.0)

    assert cpu_count() == 4


def testcpu_count_with_no_quota_at_all_is_just_affinity(monkeypatch):
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(6)))
    monkeypatch.setattr(resources, "cgroup_quota_cores", lambda: None)

    assert cpu_count() == 6


def test_a_sub_one_quota_still_runs_at_least_one_worker(monkeypatch):
    """--cpus=0.5 is real and legal; the pool must not shrink to zero workers."""
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(24)))
    monkeypatch.setattr(resources, "cgroup_quota_cores", lambda: 0.5)

    assert cpu_count() == 1


# --- what the worker is actually started with ----------------------------------


def _settings(**overrides):
    from shared.utils.config import Settings

    return Settings(**overrides)


def test_available_cpus_is_computed_not_a_constant(monkeypatch):
    """The whole point of the property: the answer differs between a laptop, a
    --cpus=2 container on a 24-core host, and a cpuset-pinned pod, and it has
    to be read at runtime rather than written into the settings."""
    import shared.utils.config as config

    monkeypatch.setattr(config, "cpu_count", lambda: 7)
    assert _settings().available_cpus == 7

    monkeypatch.setattr(config, "cpu_count", lambda: 2)
    assert _settings().available_cpus == 2


def test_available_cpus_is_never_zero(monkeypatch):
    """Anything sizing a pool from this divides by it or spawns it."""
    import shared.utils.config as config

    monkeypatch.setattr(config, "cpu_count", lambda: 0)

    assert _settings().available_cpus == 1


def test_the_default_concurrency_is_two(monkeypatch):
    """Two, not one per core: every slot is a prefork process holding the
    asset's bytes and a batch of parsed pages, so one per core on a 24-core box
    is 24 copies of a large PDF resident at once -- the shape of the OOM
    OCR_WORKERS already carries a warning about."""
    import shared.utils.config as config

    monkeypatch.setattr(config, "cpu_count", lambda: 24)

    assert _settings().celery_worker_concurrency == 2


def test_zero_concurrency_resolves_to_the_available_cpus(monkeypatch):
    """0 is how an operator opts into one worker per available CPU, resolved
    through the property rather than through the host's raw core count."""
    import shared.utils.config as config

    monkeypatch.setattr(config, "cpu_count", lambda: 7)

    assert _settings(CELERY_WORKER_CONCURRENCY=0).celery_worker_concurrency == 7


def test_zero_never_resolves_to_zero(monkeypatch):
    """The failure this rules out is silent and total: Celery reads a
    concurrency of 0 as "spawn no worker processes", so the worker starts,
    reports ready, consumes nothing, and every ingestion sits QUEUED."""
    import shared.utils.config as config

    monkeypatch.setattr(config, "cpu_count", lambda: 0)

    assert _settings(CELERY_WORKER_CONCURRENCY=0).celery_worker_concurrency == 1


def test_an_explicit_concurrency_is_honoured_exactly(monkeypatch):
    """Including above what the box has. An operator who sets this can see the
    machine and may know something this cannot -- that swap exists, that the
    monitoring stack is about to move off. Same policy as OCR_WORKERS."""
    import shared.utils.config as config

    monkeypatch.setattr(config, "cpu_count", lambda: 4)

    assert _settings(CELERY_WORKER_CONCURRENCY=1).celery_worker_concurrency == 1
    assert _settings(CELERY_WORKER_CONCURRENCY=32).celery_worker_concurrency == 32


def test_the_celery_app_is_configured_from_the_resolved_value():
    """celery_app sets worker_concurrency from the property, and the process
    worker passes no --concurrency flag -- so this conf value is what that
    worker actually runs with. A CLI flag anywhere would override it."""
    import celery_app
    from shared.utils import get_settings

    assert celery_app.celery_app.conf.worker_concurrency == get_settings().celery_worker_concurrency
    assert celery_app.celery_app.conf.worker_concurrency >= 1
