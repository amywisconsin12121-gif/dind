# Windows setup and performance

Use the repository's repaired branch when creating or rebuilding a Codespace. Back up an existing `windows/data.img` before applying the repair to an existing workspace. The original `onCreateCommand.sh` deletes the checkout, so inspect and back up a failed Codespace before rerunning its original lifecycle commands.

The devcontainer includes an OpenSSH server with per-container host keys so `gh codespace ssh` and `gh codespace logs` can inspect it. Connect from a computer authenticated to GitHub with `gh codespace ssh --codespace YOUR_CODESPACE_NAME`.

1. Open the Codespace terminal and run `start`.
2. Open port 8006 from the Ports panel. Keep that browser console private and finish Windows OOBE, including creating your Windows account.
3. In an elevated Windows PowerShell window, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "\\host.lan\Data\.devcontainer\windows\Configure-Windows.ps1"
   ```

   This prompts for your Windows account password, enables RDP with Network Level Authentication, enables its firewall rules, restores the Windows firewall, and selects the High performance power plan with no AC sleep. If the shared folder is unavailable, copy the script through the browser console instead.
4. Run `start-tailscale` in the Codespace terminal and finish the displayed login. Install Tailscale on your computer and sign in to the same tailnet. Connect your Remote Desktop client to the displayed IPv4 address, port 3389. Use your Windows username and password; `\.YourUserName` explicitly selects a local Windows account.

Windows startup does not wait for a Tailscale login. Persistent Tailscale state is stored in `windows/tailscale/`; existing state in `.devcontainer/tailscale/` is migrated when present. Both locations are ignored by Git. If login expires during a Codespace restart, the terminal opens and asks you to run `start-tailscale` again. You can optionally provide `TAILSCALE_AUTHKEY` as a Codespaces secret scoped to this fork. Do not commit that key.

## RDP through a local TCP tunnel

On your own computer, with GitHub CLI authenticated for Codespaces:

```sh
gh codespace ports forward 3389:13389 --codespace YOUR_CODESPACE_NAME
```

Keep that command running and point your Remote Desktop client to `127.0.0.1:13389`. This method carries RDP TCP. Tailscale also permits the published RDP UDP port, and a direct Tailscale connection can reduce latency. Use `tailscale ping YOUR_COMPUTER_NAME` to see whether the path is direct or relayed. A browser's `https://...app.github.dev` URL cannot be used as an RDP server address.

## Performance settings

The devcontainer requests a minimum of **4 cores, 16 GB RAM, and 32 GB storage**. Use the 4-core machine: it is the highest CPU/RAM configuration available to this account. The defaults use all CPUs available within affinity and cgroup limits and leave the greater of 2 GiB or 10% of effective host memory **available after Linux's existing memory use**. Guest RAM therefore depends on the actual host load; inspect the allocation printed by `start`. Giving Windows all 16 GiB leaves no room for Linux, QEMU overhead, Docker, the editor, or Tailscale. On the tested Codespace, repeated 13.6-GiB boots were externally terminated, while a 10-GiB diagnostic profile ran for over 40 minutes. CPU host passthrough and Hyper-V enlightenments come from the pinned dockur QEMU runtime. Disks use sparse raw format, VirtIO SCSI, direct I/O, and TRIM. No 16-GiB swap file or duplicate raw base disk is created.

KVM is required and its API must actually create a VM; an existing device filename alone is insufficient. `DEBUG=N` prevents dockur's debug mode from silently falling back to slow software emulation. If the host does not expose nested virtualization, scripts cannot supply it. `windows-doctor` reports the actual probe failure. Machine availability and acceleration must be checked on your own Codespace.

List the 4-core machines offered for your fork, largest persistent storage first:

```sh
gh api repos/YOUR_GITHUB_USERNAME/dind/codespaces/machines --jq '.machines | map(select(.cpus == 4)) | sort_by(.storage_in_bytes) | reverse | .[] | {name, cpus, memory_in_bytes, storage_in_bytes}'
```

Select the offered 4-core/16-GB machine with the greatest `storage_in_bytes`, and a region close to the RDP client. Scripts cannot enlarge GitHub's physical workspace allocation. Temporary disk capacity is separate and must be measured inside the Codespace; the old upstream table's 118-GB figure is not guaranteed for your machine.

To override resource settings, copy `.devcontainer/settings.example.json` to `windows/settings.json`, edit it, and run `restart`. `CPU_CORES` and `RAM_SIZE` can be `auto` or explicit values such as `4` and `12G`. The configuration rejects CPU/RAM overcommit and disk shrink. `DISK_SIZE=max` uses the primary disk's allocated bytes plus free workspace space, leaving 2 GiB for host files; it preserves any larger existing virtual disk. `keep` preserves the existing size without growth. Explicit expansion cannot exceed physical capacity after headroom.

`DISK_CACHE=none` with `DISK_IO=native` is the default. `writeback` with `threads` is available for workload-specific testing; it uses host cache and can lose buffered writes after an abrupt host stop. There is no measured fastest I/O profile for your Codespace yet. Keep the default until a benchmark on your actual machine supports a change.

## Maximum usable disk capacity

The Windows boot disk remains in persistent `windows/data.img`. ItzLevvie's image has a **128-GiB virtual disk**, but a sparse image does not supply 128 GiB of physical storage. On a 32-GB workspace, C: can report more free space than the host can hold. `start` prints actual host growth space, and `windows-doctor` reports current filesystem capacity. Leave the reserved host space free; filling an oversized C: image can exhaust the workspace.

`DISK2_SIZE=auto` exposes an additional scratch drive when `/tmp` is a separate, disk-backed filesystem. It sizes this image from allocated image bytes plus actual free space, leaving 4 GiB for system files. VM image pulls and initial Windows import finish before the final measurement. No second disk is created if `/tmp` shares the workspace's filesystem or is a tmpfs consuming RAM. For example, 117 GiB free on a separate temporary disk gives a 113-GiB scratch image; this is a sizing example, not a measurement of your Codespace.

**Codespaces deletes `/tmp` whenever the Codespace stops, including an idle timeout. Everything on this scratch drive is then lost.** Keep installed Windows and important files on persistent workspace storage or back them up outside the Codespace. A devcontainer rebuild alone may retain `/tmp`; stopping the Codespace does not. See [GitHub's temporary-file lifecycle documentation](https://docs.github.com/en/codespaces/developing-in-a-codespace/persisting-environment-variables-and-temporary-files).

After Windows starts, view the disks in an elevated PowerShell window:

```powershell
Get-Disk | Format-Table Number, FriendlyName, PartitionStyle, Size, IsBoot, IsSystem
```

Initialize only the blank secondary disk whose size matches the scratch size printed by `start`. It is normally disk 1:

```powershell
powershell -ExecutionPolicy Bypass -File "\\host.lan\Data\.devcontainer\windows\Initialize-Scratch.ps1" -DiskNumber 1
```

The script checks the selected disk against the host's storage plan, refuses boot/system disks and disks with existing partitions, and creates an NTFS `S:` volume labeled `TEMPORARY SCRATCH`. Use `-DriveLetter T` if S: is already occupied. If the host share is unavailable, copy both this script and the generated `windows/storage.json` into Windows or an RDP redirected drive, then pass `-PlanPath` with that JSON file's Windows path. Repeat initialization when `/tmp` has been cleared. If an existing scratch disk grows, extend its partition through Windows Disk Management; the script deliberately preserves existing partitions.

`DISK2_SIZE=off` disables attachment without deleting an existing image. An explicit size such as `100G` must fit the measured capacity. `SCRATCH_DIR` can select another absolute disk-backed path outside the workspace. Paths outside `/workspaces` do not carry the workspace's persistence guarantee; check that filesystem's lifecycle before placing important files there. Existing images are never shrunk or removed automatically when host space decreases. The generated `windows/storage.json` records both virtual sizes and physical growth budgets.

## Startup, storage, and recovery

Use `stop` for graceful shutdown, `restart` to apply settings and boot again, and `remove` to remove the VM container while retaining Windows files. Bash reserves the name `kill`; to force-stop the VM, invoke `/usr/local/bin/kill` explicitly. `reset --yes` replaces Windows changes with the original image and may download it again. It preserves the old disk if replacement conversion fails, but enough free space is required to stage the replacement before the atomic rename.

Codespace resume uses the persistent `.autostart` marker and the `start` helper, which checks KVM and measures CPU, RAM, and storage before boot. Docker's automatic restart policy is disabled: during actual testing, it restored an interrupted container with its old RAM settings before the helper could recompute them. VM startup and recovery go through the helper. A guest shutdown or runtime failure leaves the container stopped until `start` or the next marked Codespace resume.

Image downloads resume and are checked against five pinned SHA-256 digests. Archives and the extracted VHDX use `/tmp/windows-dind-image` by default. Their peak staging requirement is approximately 19 GiB for release 29599-1, checked before downloading. Staging files are removed after a successful import, before allocating the scratch disk's size. If `/tmp` is small or a tmpfs, select an appropriate disk-backed filesystem with `WINDOWS_IMAGE_CACHE=/path/to/cache start`. A custom existing VHDX can be imported using `WINDOWS_SOURCE_VHDX=/path/to/data.vhdx start` before the first boot. A later `reset --yes` may require freeing scratch space to stage the image again; it fails without deleting the old boot disk if the staging space is unavailable.

Run `windows-doctor` after startup. It checks guest TCP port 3389 directly rather than mistaking Docker's host listener for a ready Windows RDP service. A successful port probe still requires an actual RDP client login to verify credentials. If KVM works but RDP is closed, complete OOBE and run the Windows configuration script. If RDP disconnects after inactivity, check the Codespace's state and configured idle timeout. This repair does not bypass Codespaces' idle timeout.
