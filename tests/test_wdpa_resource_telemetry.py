"""Keep the captured Cloud Run v1 readings and Docker v2 budget distinct."""

import pytest

from ingestion.wdpa_monthly import resources


def write_files(root, files):
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + "\n")


def test_captured_cloud_run_v1_limits_and_kernel_peak(tmp_path):
    write_files(
        tmp_path,
        {
            "cpu,cpuacct/cpu.cfs_period_us": 100000,
            "cpu,cpuacct/cpu.cfs_quota_us": 372000,
            "memory/memory.limit_in_bytes": 8589934592,
            "memory/memory.usage_in_bytes": 28098560,
            "memory/memory.max_usage_in_bytes": 28098560,
            "memory/memory.stat": "rss 19841024\ncache 8257536\ndirty 4096\nmapped_file 0\nswap 0\npgfault 25",
        },
    )
    assert resources.cgroup_limits(tmp_path) == (3.72, 8 * 1024**3)
    assert resources.cgroup_memory(tmp_path) == (28098560, 28098560)
    assert resources.cgroup_memory_stat(tmp_path) == {
        "rss": 19841024,
        "cache": 8257536,
        "dirty": 4096,
        "mapped_file": 0,
        "swap": 0,
    }
    assert resources.validation_limits(*resources.cgroup_limits(tmp_path))


def test_docker_v2_limits_and_peak_include_kernel_and_file_cache(tmp_path):
    write_files(
        tmp_path,
        {
            "cpu.max": "400000 100000",
            "memory.max": 8 * 1024**3,
            "memory.current": 6 * 1024**3,
            "memory.peak": 7 * 1024**3,
            "memory.stat": "anon 300\nfile 400\nfile_dirty 50\nfile_mapped 30\nkernel 100\nshmem 2\npgfault 30",
        },
    )
    assert resources.cgroup_limits(tmp_path) == (4, 8 * 1024**3)
    assert resources.cgroup_memory(tmp_path) == (6 * 1024**3, 7 * 1024**3)
    assert resources.cgroup_memory_stat(tmp_path) == {
        "anon": 300,
        "file": 400,
        "file_dirty": 50,
        "file_mapped": 30,
        "kernel": 100,
        "shmem": 2,
    }


@pytest.mark.parametrize("version", [1, 2])
def test_missing_kernel_peak_is_never_replaced_with_sampled_memory_or_rss(
    tmp_path, version
):
    name = "memory/memory.usage_in_bytes" if version == 1 else "memory.current"
    write_files(tmp_path, {name: 12345})
    assert resources.cgroup_memory(tmp_path) == (12345, None)


@pytest.mark.parametrize("version", [1, 2])
def test_unbounded_cpu_cannot_certify_a_resource_target(tmp_path, version):
    files = (
        {"cpu.max": "max 100000", "memory.current": 10, "memory.max": "max"}
        if version == 2
        else {
            "cpu,cpuacct/cpu.cfs_quota_us": -1,
            "memory/memory.usage_in_bytes": 10,
            "memory/memory.limit_in_bytes": 9223372036854771712,
        }
    )
    write_files(tmp_path, files)
    limits = resources.cgroup_limits(tmp_path)
    assert limits[0] is None
    assert not resources.validation_limits(*limits)


def test_unknown_kernel_interface_is_not_assumed_to_match_configuration(tmp_path):
    assert resources.cgroup_limits(tmp_path) == (None, None)
    assert resources.cgroup_memory(tmp_path) == (None, None)


def test_malformed_present_controller_fails_instead_of_using_another_interface(
    tmp_path,
):
    write_files(
        tmp_path, {"cpu.max": "corrupt", "cpu,cpuacct/cpu.cfs_quota_us": 372000}
    )
    with pytest.raises(ValueError):
        resources.cgroup_limits(tmp_path)
