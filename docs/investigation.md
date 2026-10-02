# Investigation and validation — 2026-10-02

The investigation used public upstream `ItzLevvie/dind` commit `76c6eb0034806439b277a657bde751f84eaaf8f2`. The user's fork URL and failed Codespace were not available. The workspace's authenticated GitHub API request returned HTTP 401 (`Bad credentials`), so no fork was changed and no account Codespace was started or rebuilt.

## Confirmed upstream defects

| Finding | Evidence and consequence | Repair |
| --- | --- | --- |
| Host-wide image pruning during initialization | `initializeCommand.sh` runs `docker system prune --all --force` every initialization. The author identifies this as the cause of recovery mode on restart in [issue 9](https://github.com/ItzLevvie/dind/issues/9#issuecomment-3667570327). | Remove the initialization command. |
| Destructive checkout removal | `onCreateCommand.sh` moves `.devcontainer` out, removes `/workspaces/github` recursively, then recreates it. This removes `.git`, the README, and user files. | Install helpers without deleting the checkout or Windows disk. |
| Restart keeps stale resource/configuration settings | The original `restart` runs `compose restart`, which does not recreate the container with changed YAML. A real Docker test changed an environment value: restart retained the old value; `compose up -d` applied the new value. | Recompute resource settings after graceful shutdown, then use `compose up -d` to reconcile the container. |
| Reset does not stop the VM | Bash resolves bare `kill` to its built-in. Reproduced: `kill` without a PID returns usage status 2, while the script continues and deletes the disk. | Stop/remove the actual Compose service, convert a replacement separately, then rename it atomically. |
| Docker races and suppressed diagnostics | Post-start deletes pidfiles and launches a hidden daemon; `start` launches another daemon regardless of health. | Check the local daemon, serialize startup, wait for readiness, and retain a log. |
| Resource overcommit and disk assumptions | `nproc --all` and `free` do not enforce container quotas. Most available RAM is assigned to Windows with `RAM_CHECK=N`; a 16-GiB swap file is added. Disk sizes depend on free space and grep matches, and `/workspaces` may measure a different mount from `/workspaces/github`. | Honor affinity/cgroup CPU and memory limits, reserve host RAM, enable the RAM check, and keep the imported disk size. |
| Incomplete import can be treated as success | Original downloads and conversions have no fail-fast setting or checksums, and markers/configuration are written after errors. | Verify each archive, check extraction and conversion space, and publish a converted image only after success. |
| Empty disks can trigger a different installation | The pinned dockur runtime checks the first 100 KiB for data and treats an all-zero disk as empty. Reproduced with an empty sparse disk and a boot marker: the runtime attempted to fetch a Microsoft installer. | Refuse an existing or converted all-zero disk before launching the runtime. |
| Startup can wait indefinitely for Tailscale authentication | The original Windows start calls interactive `tailscale up` before launching the VM. | Make login a separate helper and resume stored state without blocking lifecycle commands on expired login. |
| Browser forwarding confused with native RDP | Codespaces' browser forwarding uses HTTP/HTTPS. A web URL is not an RDP TCP endpoint. | Forward only the web console in the browser; document Tailscale and a local TCP tunnel for native RDP. |
| Unpinned runtime and tool builds | Debian sid, numerous `latest` artifact binaries, and `dockurr/windows:latest` can change independently. | Use Debian stable Docker packages, stable Tailscale 1.102.4, and dockur 6.05 pinned to its tested GHCR manifest digest. |

## Validation completed

- Downloaded all five actual release 29599-1 archives, totaling **4,421,755,822 bytes**, and verified every published SHA-256 digest. The current release files are intact; no missing-release failure was found.
- Read the real archive directory: it contains `data.vhdx`, **14,701,035,520 bytes** (approximately 13.69 GiB). Archives plus extraction alone need approximately 17.81 GiB before conversion; staging checks include additional headroom.
- Read the VHDX's virtual-disk-size metadata from its header: **137,438,953,472 bytes (128 GiB)**. This was metadata inspection, not a full extraction or Windows boot. The resource generator preserves that capacity; it is not evidence of 128 GiB of physical space.
- Built the fixed devcontainer image successfully with TLS verification enabled. Checked installed Docker/Compose, QEMU tools, GitHub CLI, and Tailscale.
- Started the actual Docker daemon inside a privileged test container. Calling its startup helper twice left exactly one `dockerd` process. The tested daemon selected VFS in this cloud environment; Windows storage is a bind-mounted raw image rather than a Docker image layer.
- Ran the regression suite in the root test container: all 17 tests passed, including CPU/memory limits, disk preservation, cached-image startup, reset ordering, nonblocking Tailscale resume, and seven storage cases. Synthetic separate-filesystem measurements test maximum sizing, shared-device and tmpfs exclusions, used-image accounting, shrink prevention, and low-space failures. They do not measure the user's actual Codespace disk.
- Checked real Compose behavior with a cached image and no container: `compose images` lists only its header. This corrects an earlier simulated finding; cached images alone were not established as an original startup defect. A real container test did establish that `compose restart` ignores changed configuration.
- Imported a real synthetic VHDX with nonzero data using QEMU 10.0.13, compared the converted raw disk byte-for-byte, and verified that a failed replacement leaves the old disk unchanged. Repeated preparation kept the existing disk.
- Validated Bash syntax and ShellCheck, the generated Compose configuration, and the Windows PowerShell script's parser syntax.
- Checked the pinned dockur startup pipeline using a nonempty synthetic raw disk and the original boot marker. It invoked QEMU without fetching another Windows installer. This isolated test explicitly used TCG with networking disabled; the fixture contained no OS and reached UEFI's no-boot-device screen before the expected timeout. It is not a Windows boot or performance test.
- Repeated the isolated runtime check with `/storage2` attached: dockur created the requested **2-GiB sparse raw second disk** and launched QEMU. The synthetic boot disk again contained no OS; the test used explicit TCG with networking disabled and ended at the expected timeout. The production configuration still requires KVM. Real Compose also accepted the generated configuration and skipped fetching the locally cached, pinned VM image with `pull --policy missing`.

The first attempt to build the original Dockerfile hit Docker Hub HTTP 429 at the base image fetch. That is a test-environment registry limit, not evidence that the user's Codespace had the same failure. The fixed build used the public Debian image mirror, and the Windows runtime was fetched from its official GHCR publication.

## Requested 4-core/16-GB capacity profile

The devcontainer requests the user's maximum available 4-core/16-GB machine, with at least 32 GB of workspace storage. The VM receives all allowed vCPUs and normally up to 14 GiB RAM, retaining Linux/QEMU headroom. KVM, host CPU passthrough, and Hyper-V enlightenments remain enabled. An actual performance benchmark is still needed before claiming the fastest disk-cache mode on that machine.

`DISK_SIZE=max` keeps the oversized imported boot disk and allows expansion only when physical workspace capacity supports it. `DISK2_SIZE=auto` adds a separate disk-backed scratch filesystem's usable capacity after 4 GiB of headroom, with VM image pulls completed before the final measurement. The generator uses the actual image directory's filesystem, excludes RAM-backed or shared filesystems, accounts for allocated blocks in existing images, and never shrinks them. Current physical capacity and the last generated storage plan are available through `windows-doctor`.

GitHub documents that **`/tmp` is cleared on every Codespace stop or idle timeout**. The boot disk stays in persistent workspace storage. The added `Initialize-Scratch.ps1` requires an explicit disk number, checks its size against the host plan, and refuses boot/system disks and disks containing existing partitions. It was parser-checked, not executed in Windows. Scratch initialization must be repeated after the temporary image is cleared. The old upstream table's 118-GB temporary-disk figure has not been verified for this user's machine.

The VM's Docker restart policy is `on-failure:3` so daemon startup does not boot it ahead of the Codespace helper's KVM/resource checks. Marked Codespace resume runs that helper and computes a new scratch budget. This behavior is based on Docker's documented restart-policy semantics and still needs a real Codespace stop/resume test.

## Validation still requiring the user's Codespace

This managed test workspace has no `/dev/kvm`. The real probe reports that KVM cannot create a VM and stops before downloading or launching Windows. Full accelerated Windows boot, the fork's creation logs, native RDP authentication/UDP operation, actual Tailscale connectivity, and performance benchmarks remain unverified. The PowerShell script was parsed, not executed against a Windows guest.

To finish the investigation, provide the fork URL and restore GitHub authentication with repository write access and Codespaces access. Test an existing Codespace's machine/KVM and creation logs, apply the reviewed branch to the fork, then verify graceful stop/resume and an actual RDP login. Do not infer KVM support solely from the nominal CPU family or machine size.

## Sources checked

- [ItzLevvie recovery-mode issue and author explanation](https://github.com/ItzLevvie/dind/issues/9#issuecomment-3667570327)
- [ItzLevvie RDP drop report](https://github.com/ItzLevvie/dind/issues/10) — no confirmed cause or maintainer resolution; the repair does not claim to reproduce this separate problem.
- [Actual Windows image release 29599-1](https://github.com/ItzLevvie/artifacts/releases/tag/29599-1)
- [dockur 6.05 environment variables](https://github.com/dockur/windows/blob/v6.05/docs/environment.md) and its QEMU 7.48 initialization, CPU, disk, and network scripts.
- [dockur's recent Codespaces disk minimum fix](https://github.com/dockur/windows/pull/2214)
- [GitHub Codespaces overview and machine capacities](https://docs.github.com/en/codespaces/overview)
- [Codespaces temporary-file lifecycle](https://docs.github.com/en/codespaces/developing-in-a-codespace/persisting-environment-variables-and-temporary-files)
- [Docker restart-policy behavior](https://docs.docker.com/engine/containers/start-containers-automatically/)
- [Codespaces port forwarding](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
- [Azure Dasv5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dasv5-series) and [Dasv6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dasv6-series) feature support. Azure support does not establish what an individual Codespace exposes.
