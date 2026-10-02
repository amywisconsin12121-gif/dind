# Windows setup and performance

Use the repository's repaired branch when creating or rebuilding a Codespace. Back up an existing `windows/data.img` before applying the repair to an existing workspace. The original `onCreateCommand.sh` deletes the checkout, so inspect and back up a failed Codespace before rerunning its original lifecycle commands.

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

The defaults use all CPUs available within affinity and cgroup limits, reserve the greater of 2 GiB or 10% of host memory for Linux, and leave additional room if current available RAM is lower. A lightly loaded 4-core/16-GiB machine normally receives 4 vCPUs and 14 GiB of guest RAM. CPU host passthrough and Hyper-V enlightenments come from the pinned dockur QEMU runtime. The disk uses sparse raw format, VirtIO SCSI, direct I/O, and TRIM. No 16-GiB swap file, duplicate raw base disk, or automatically sized second disk is created.

KVM is required and its API must actually create a VM; an existing device filename alone is insufficient. `DEBUG=N` prevents dockur's debug mode from silently falling back to slow software emulation. If the host does not expose nested virtualization, scripts cannot supply it. `windows-doctor` reports the actual probe failure. Machine availability and acceleration must be checked on your own Codespace.

List the machines offered for your fork before choosing a larger one:

```sh
gh api repos/YOUR_GITHUB_USERNAME/dind/codespaces/machines --jq '.machines[] | {name, cpus, memory_in_bytes, storage_in_bytes}'
```

GitHub documents machine sizes up to 32 cores and 128 GiB RAM; your repository/account may offer a smaller set. Select a region close to the RDP client. Larger machines increase both compute capacity and usage charges. Codespaces supplies no GPU through this configuration.

To override resource settings, copy `.devcontainer/settings.example.json` to `windows/settings.json`, edit it, and run `restart`. `CPU_CORES` and `RAM_SIZE` can be `auto` or explicit values such as `4` and `12G`. The configuration rejects CPU/RAM overcommit and disk shrink. `DISK_SIZE=keep` preserves the image's virtual size. Sparse virtual disk capacity does not increase the workspace's physical storage; check `windows-doctor` for free disk space.

`DISK_CACHE=none` with `DISK_IO=native` is the default. `writeback` with `threads` is available for workload-specific testing; it uses host cache and can lose buffered writes after an abrupt host stop. There is no measured fastest I/O profile for your Codespace yet. Keep the default until a benchmark on your actual machine supports a change.

## Startup, storage, and recovery

Use `stop` for graceful shutdown, `restart` to apply settings and boot again, and `remove` to remove the VM container while retaining Windows files. Bash reserves the name `kill`; to force-stop the VM, invoke `/usr/local/bin/kill` explicitly. `reset --yes` replaces Windows changes with the original image and may download it again. It preserves the old disk if replacement conversion fails, but enough free space is required to stage the replacement before the atomic rename.

Image downloads resume and are checked against five pinned SHA-256 digests. Archives and the extracted VHDX use `/tmp/windows-dind-image` by default. Their peak staging requirement is approximately 19 GiB for release 29599-1. If `/tmp` is small or a tmpfs, select an appropriate disk-backed filesystem with `WINDOWS_IMAGE_CACHE=/path/to/cache start`. A custom existing VHDX can be imported using `WINDOWS_SOURCE_VHDX=/path/to/data.vhdx start` before the first boot.

Run `windows-doctor` after startup. It checks guest TCP port 3389 directly rather than mistaking Docker's host listener for a ready Windows RDP service. A successful port probe still requires an actual RDP client login to verify credentials. If KVM works but RDP is closed, complete OOBE and run the Windows configuration script. If RDP disconnects after inactivity, check the Codespace's state and configured idle timeout. This repair does not bypass Codespaces' idle timeout.
