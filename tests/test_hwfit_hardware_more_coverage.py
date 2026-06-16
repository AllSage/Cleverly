import json

from services.hwfit import hardware


def _reset_hardware_state():
    hardware._remote_host = None
    hardware._remote_port = None
    hardware._remote_platform = None
    hardware._last_gpu_error = None
    hardware._cache_by_host.clear()


def test_nvidia_detection_groups_driver_errors_and_remote_fallback(monkeypatch):
    _reset_hardware_state()
    monkeypatch.setattr(hardware, "_run", lambda cmd: "24576, NVIDIA RTX 4090\n12288, NVIDIA RTX 3060\nbad, ignored")
    info = hardware._detect_nvidia()
    assert info["backend"] == "cuda"
    assert info["gpu_count"] == 2
    assert info["gpu_vram_gb"] == 36.0
    assert info["homogeneous"] is False
    assert info["gpu_groups"][0]["vram_total"] == 24.0

    monkeypatch.setattr(hardware, "_run", lambda cmd: "Failed to initialize NVML: Driver/library version mismatch")
    assert hardware._detect_nvidia() is None
    assert "NVML" in hardware._last_gpu_error

    calls = []

    def remote_run(cmd):
        calls.append(cmd)
        if isinstance(cmd, str) and "/usr/bin/nvidia-smi" in cmd:
            return "8192, NVIDIA A10"
        return None

    hardware._remote_host = "gpu-box"
    monkeypatch.setattr(hardware, "_run", remote_run)
    remote = hardware._detect_nvidia()
    assert remote["gpu_name"] == "NVIDIA A10"
    assert len(calls) >= 3
    _reset_hardware_state()


def test_amd_meminfo_cpu_and_windows_detection(monkeypatch):
    _reset_hardware_state()
    hardware._remote_host = "amd-box"

    def run(cmd):
        if cmd == ["ls", "/sys/class/drm"]:
            return "card0 card0-DP-1 card1"
        path = cmd[1] if isinstance(cmd, list) and cmd[:1] == ["cat"] else ""
        values = {
            "/sys/class/drm/card0/device/vendor": "0x1002",
            "/sys/class/drm/card0/device/mem_info_vram_total": "4294967296",
            "/sys/class/drm/card0/device/mem_info_vis_vram_total": "8589934592",
            "/sys/class/drm/card0/device/mem_info_gtt_total": "0",
            "/sys/class/drm/card0/device/product_name": "AMD APU",
            "/sys/class/drm/card1/device/vendor": "0x10de",
        }
        return values.get(path)

    monkeypatch.setattr(hardware, "_run", run)
    amd = hardware._detect_amd()
    assert amd["backend"] == "rocm"
    assert amd["gpu_vram_gb"] == 8.0
    assert amd["unified_memory"] is True

    monkeypatch.setattr(hardware, "_read_file", lambda path: "MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\nBad: no\n" if path == "/proc/meminfo" else "model name: Test CPU\nprocessor: 0\nprocessor: 1\n")
    assert round(hardware._get_ram_gb(), 1) == 16.0
    assert round(hardware._get_available_ram_gb(), 1) == 8.0
    assert hardware._get_cpu_name() == "Test CPU"

    payload = {
        "ram_gb": 64,
        "avail_gb": 48,
        "cpu_name": "Ryzen",
        "cpu_cores": 16,
        "gpu_name": "NVIDIA RTX",
        "gpu_vram_gb": 24,
        "gpu_count": 2,
        "gpu_backend": "cuda",
    }
    monkeypatch.setattr(hardware, "_run", lambda cmd: json.dumps(payload))
    win = hardware._detect_windows()
    assert win["backend"] == "cuda"
    assert win["gpu_groups"][0]["vram_each"] == 12
    assert win["gpus"][1]["index"] == 1

    monkeypatch.setattr(hardware, "_run", lambda cmd: "{broken")
    assert hardware._detect_windows() is None
    _reset_hardware_state()


def test_detect_system_cache_gpu_cpu_and_remote_windows(monkeypatch):
    _reset_hardware_state()
    calls = {"gpu": 0}
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    monkeypatch.setattr(hardware.os, "name", "posix")
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 32.0)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 20.0)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "CPU")

    def fake_nvidia():
        calls["gpu"] += 1
        return {
            "gpu_name": "GPU",
            "gpu_vram_gb": 24,
            "gpu_count": 1,
            "gpus": [{"index": 0, "name": "GPU", "vram_gb": 24}],
            "gpu_groups": [{"name": "GPU", "vram_each": 24, "count": 1, "indices": [0], "vram_total": 24}],
            "homogeneous": True,
            "backend": "cuda",
        }

    monkeypatch.setattr(hardware, "_detect_nvidia", fake_nvidia)
    monkeypatch.setattr(hardware, "_detect_amd", lambda: None)
    first = hardware.detect_system(fresh=True)
    second = hardware.detect_system()
    assert first == second
    assert first["has_gpu"] is True
    assert calls["gpu"] == 1

    _reset_hardware_state()
    monkeypatch.setattr(hardware.os, "name", "posix")
    monkeypatch.setattr(hardware, "_detect_windows", lambda: None)
    failed = hardware.detect_system(host="box", platform="windows", fresh=True)
    assert failed == {"error": "Cannot connect to box", "host": "box"}

    _reset_hardware_state()
    monkeypatch.setattr(hardware.os, "name", "posix")
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    monkeypatch.setattr(hardware, "_detect_nvidia", lambda: None)
    monkeypatch.setattr(hardware, "_detect_amd", lambda: None)
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 16.0)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 11.2)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 4)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "ARM CPU")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "aarch64")
    cpu = hardware.detect_system(fresh=True)
    assert cpu["has_gpu"] is False
    assert cpu["backend"] == "cpu_arm"
