#!/usr/bin/env python3
"""Accept imported Windows disks booted through OVMF's generic fallback entry."""
from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else '/run/power.sh')
source = path.read_text()
marker = '# windows-dind: imported UEFI fallback disk'
needle = '  grep -Eq "$UEFI_WINDOWS_BOOT_PATTERN" <<< "$last" && return 3'
replacement = needle + '''

  # windows-dind: imported UEFI fallback disk
  # A VHDX does not include Hyper-V's firmware NVRAM. Fresh OVMF therefore
  # launches EFI/BOOT/BOOTX64.EFI as a generic hard disk rather than a named
  # Windows Boot Manager entry. Keep the runtime's existing failure checks
  # and six-second grace period for this valid imported-disk boot path.
  grep -Eq '^BdsDxe: starting Boot[[:xdigit:]]{4} "UEFI QEMU .*HARDDISK' <<< "$last" && return 3'''

if marker not in source:
    if source.count(needle) != 1:
        raise SystemExit('Unsupported dockur boot watchdog; the pinned image or patch must be updated together.')
    path.write_text(source.replace(needle, replacement))
