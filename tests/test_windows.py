"""Regressions for resource limits and the original destructive lifecycle paths."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("windows_config", ROOT / ".devcontainer/windows-config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ResourceTests(unittest.TestCase):
    def test_host_memory_and_cpu_count_do_not_override_container_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memory.max").write_text(str(16 * config.GIB))
            (root / "memory.current").write_text(str(config.GIB))
            (root / "memory.stat").write_text("inactive_file 0\n")
            (root / "cpu.max").write_text("400000 100000\n")
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: 134217728 kB\nMemAvailable: 125829120 kB\n")
            with patch.object(os, "sched_getaffinity", return_value=set(range(32))):
                limits = config.resources(root, meminfo)
            self.assertEqual(limits["cpus"], 4)
            self.assertEqual(limits["total"], 16 * config.GIB)
            self.assertEqual(limits["ram_mib"], 14 * 1024)

    def test_memory_limit_still_applies_without_optional_memory_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memory.max").write_text(str(8 * config.GIB))
            (root / "memory.current").write_text(str(config.GIB))
            meminfo = root / "meminfo"
            meminfo.write_text("MemTotal: 134217728 kB\nMemAvailable: 125829120 kB\n")
            self.assertEqual(config.resources(root, meminfo)["ram_mib"], 6 * 1024)

    def test_config_rejects_overcommit_and_disk_shrink(self):
        with tempfile.TemporaryDirectory(prefix="workspace with spaces ") as directory:
            root = Path(directory)
            storage = root / "windows"
            storage.mkdir()
            with (storage / "data.img").open("wb") as disk:
                disk.truncate(64 * config.GIB)
            limits = {"cpus": 4, "ram_mib": 14 * 1024}
            for setting in ({"CPU_CORES": 8}, {"RAM_SIZE": "16G"}, {"DISK_SIZE": "32G"}):
                (storage / "settings.json").write_text(json.dumps(setting))
                with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                    config.generate(root, limits)
            (storage / "settings.json").unlink()
            with patch.dict(os.environ, {}, clear=True):
                generated = config.generate(root, limits)
            env = generated["services"]["windows"]["environment"]
            self.assertEqual(env["RAM_SIZE"], "14336M")
            self.assertEqual(env["DISK_SIZE"], "65536M")
            self.assertEqual(env["KVM"], "Y")
            self.assertEqual(env["DEBUG"], "N")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dind regression ")
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / ".devcontainer", self.root / ".devcontainer")
        storage = self.root / "windows"
        storage.mkdir()
        (storage / "data.img").write_bytes(b"original Windows disk")
        (storage / "windows.yaml").write_text('{"services":{"windows":{"image":"test"}}}')
        mockbin = self.root / "bin"
        mockbin.mkdir()
        self.log = self.root / "commands"
        self.env = dict(os.environ, WINDOWS_WORKSPACE=str(self.root), CALL_LOG=str(self.log),
                        PATH=str(mockbin) + ":" + os.environ["PATH"])
        self.tool("docker", '''printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1 $2" in
  "info ") exit 0;;
  "inspect --format") exit 1;;
esac
exit 0
''')
        # Simulated hardware for lifecycle tests; production's real KVM probe
        # remains covered separately and has no fallback switch.
        (self.root / ".devcontainer/windows-config.py").write_text('''import json,os,sys
from pathlib import Path
if "--check-kvm" not in sys.argv:
    p=Path(os.environ["WINDOWS_WORKSPACE"])/"windows/windows.yaml"
    p.write_text(json.dumps({"services":{"windows":{"image":"test"}}}))
''')

    def tearDown(self):
        self.temp.cleanup()

    def tool(self, name, script):
        target = self.root / "bin" / name
        target.write_text("#!/bin/bash\n" + script)
        target.chmod(0o755)

    def run_helper(self, name, *args):
        return subprocess.run(["bash", str(self.root / ".devcontainer/helpers" / name), *args],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_cached_image_without_container_uses_up_and_preserves_disk(self):
        result = self.run_helper("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text()
        self.assertIn("up --detach --remove-orphans", commands)
        self.assertNotIn(" images", commands)
        self.assertEqual((self.root / "windows/data.img").read_bytes(), b"original Windows disk")
        self.assertTrue((self.root / "windows/.autostart").exists())

    def test_stop_removes_autostart_only_after_success(self):
        marker = self.root / "windows/.autostart"
        marker.touch()
        self.tool("docker", '''case "$*" in
  info) exit 0;;
  *"stop --timeout"*) exit 1;;
esac
''')
        self.assertNotEqual(self.run_helper("stop").returncode, 0)
        self.assertTrue(marker.exists())
        self.tool("docker", 'exit 0\n')
        self.assertEqual(self.run_helper("stop").returncode, 0)
        self.assertFalse(marker.exists())

    def test_repeated_start_does_not_reallocate_a_running_vm(self):
        self.tool("docker", '''printf '%s\\n' "$*" >> "$CALL_LOG"
case "$*" in
  *State.Running*) echo true;;
  *com.windows-dind.config*) echo 2;;
esac
''')
        result = self.run_helper("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already running", result.stdout)
        self.assertNotIn("up --detach", self.log.read_text())

    def test_missing_hardware_stops_before_docker_or_image_import(self):
        (self.root / ".devcontainer/windows-config.py").write_text('import sys; sys.exit(88)\n')
        result = self.run_helper("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertEqual((self.root / "windows/data.img").read_bytes(), b"original Windows disk")

    def test_reset_stops_container_before_import_and_failed_import_preserves_disk(self):
        (self.root / ".devcontainer/prepare-image.sh").write_text('''#!/bin/bash
printf '%s\\n' IMPORT >> "$CALL_LOG"
exit 1
''')
        result = self.run_helper("reset", "--yes")
        self.assertNotEqual(result.returncode, 0)
        commands = self.log.read_text().splitlines()
        stopped = next(i for i, command in enumerate(commands) if "down --timeout 120" in command)
        self.assertLess(stopped, commands.index("IMPORT"))
        self.assertEqual((self.root / "windows/data.img").read_bytes(), b"original Windows disk")

    def test_resume_does_not_wait_for_expired_tailscale_login(self):
        self.tool("tailscale", '''case "$1" in
  status) printf '{"BackendState":"NeedsLogin"}';;
  up) printf 'UNEXPECTED_LOGIN\\n' >> "$CALL_LOG"; exit 1;;
esac
''')
        self.tool("pgrep", 'exit 0\n')
        self.env.pop("TAILSCALE_AUTHKEY", None)
        result = self.run_helper("start-tailscale", "--resume-only")
        # The daemon's lock/log directory is writable in the devcontainer.
        if "Permission denied" in result.stderr:
            self.skipTest("Tailscale lifecycle test runs as root in the test container")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("needs authentication", result.stdout)
        self.assertFalse(self.log.exists())


@unittest.skipUnless(shutil.which("qemu-img") and shutil.which("qemu-io"), "QEMU tools are installed in the test container")
class ImageImportTests(unittest.TestCase):
    def test_conversion_and_invalid_replacement_keep_verified_bytes(self):
        with tempfile.TemporaryDirectory(prefix="image import with spaces ") as directory:
            root = Path(directory)
            shutil.copytree(ROOT / ".devcontainer", root / ".devcontainer")
            source = root / "source.vhdx"
            subprocess.run(["qemu-img", "create", "-f", "vhdx", str(source), "64M"], check=True, capture_output=True)
            subprocess.run(["qemu-io", "-f", "vhdx", "-c", "write -P 0x5a 0 1M", str(source)], check=True, capture_output=True)
            env = dict(os.environ, WINDOWS_WORKSPACE=str(root), WINDOWS_SOURCE_VHDX=str(source), WINDOWS_IMAGE_CACHE=str(root / "cache"))
            prepare = ["bash", str(root / ".devcontainer/prepare-image.sh")]
            result = subprocess.run(prepare, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            target = root / "windows/data.img"
            subprocess.run(["qemu-img", "compare", "-f", "vhdx", "-F", "raw", str(source), str(target)], check=True, capture_output=True)
            bad = root / "invalid.vhdx"
            bad.write_bytes(b"invalid VHDX data")
            env["WINDOWS_SOURCE_VHDX"] = str(bad)
            result = subprocess.run(prepare + ["--replace"], env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            subprocess.run(["qemu-img", "compare", "-f", "vhdx", "-F", "raw", str(source), str(target)], check=True, capture_output=True)
            self.assertFalse((root / "windows/data.img.partial").exists())
            empty = root / "empty.vhdx"
            subprocess.run(["qemu-img", "create", "-f", "vhdx", str(empty), "64M"], check=True, capture_output=True)
            env["WINDOWS_SOURCE_VHDX"] = str(empty)
            result = subprocess.run(prepare + ["--replace"], env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no readable boot data", result.stderr)
            subprocess.run(["qemu-img", "compare", "-f", "vhdx", "-F", "raw", str(source), str(target)], check=True, capture_output=True)
            self.assertFalse((root / "windows/data.img.partial").exists())


if __name__ == "__main__":
    unittest.main()
