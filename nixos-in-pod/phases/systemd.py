#!/usr/bin/env python3
"""systemd came up as PID 1 and reached multi-user.

The whole point.  A NixOS container's `/init` is the stage-2 script: it
activates the system, then execs systemd.  If the pod is running at all
the exec happened; this reads what the configuration's own units wrote,
which proves the transaction completed rather than just the process
existing.

`systemctl is-system-running` is reported, not asserted: a NixOS
container has failed units a full system would not (kmod, udev) and
multi-user.target is the honest bar.  A value of `running` or `degraded`
is a systemd that got there; the proof unit is what must be present.
"""

from __future__ import annotations

import nixos_in_pod as h
from vivarium_runner import Machine, Machines


async def _read(cp: Machine, path: str) -> str:
    code, out = await h.exec_sh(cp, "nixos", f"cat {path}")
    return out.strip() if code == 0 else ""


async def test(vms: Machines) -> None:
    cp = vms.cp

    # PID 1 is systemd, not a shell and not the stage-2 script.  The
    # script execs systemd, so by the time a unit of ours has run, the
    # process table has one process whose comm is systemd.
    comm = await h.exec_sh(cp, "nixos", "cat /proc/1/comm")
    print(f"[nixos-in-pod] pid 1 comm: {comm[1].strip()!r}", flush=True)
    assert comm[0] == 0, f"could not read /proc/1/comm: {comm[1]}"
    assert "systemd" in comm[1], f"pid 1 is {comm[1].strip()!r}, not systemd"

    # A NixOS unit of our own reached multi-user.target.  This is the
    # assertion the phase exists for.
    marker = await _read(cp, f"{h.PROOF_DIR}/multi-user")
    assert marker == "ok", (
        "the proof-multi-user unit did not run;"
        f" systemd said: {await _read(cp, f'{h.PROOF_DIR}/system-running')!r}"
    )
    running = await _read(cp, f"{h.PROOF_DIR}/system-running")
    print(f"[nixos-in-pod] systemd: {running}", flush=True)

    # The environment the image config set, so the pod's process is the
    # image's and not a shell the kubelet invented.
    store = await _read(cp, f"{h.PROOF_DIR}/store")
    assert store == "ok", "the store is not writable from the pod"
    print("[nixos-in-pod] store: writable, /run/current-system resolvable", flush=True)

    # The hostname is NixOS's, which the pod's UTS namespace carries.
    hostname = await h.exec_sh(cp, "nixos", "cat /proc/sys/kernel/hostname")
    print(f"[nixos-in-pod] hostname: {hostname[1].strip()}", flush=True)
