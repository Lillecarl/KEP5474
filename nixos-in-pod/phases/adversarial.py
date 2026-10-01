#!/usr/bin/env python3
"""The battery, run inside the NixOS pod.

The pod is a user-namespace workload (`hostUsers: false`) with systemd
as PID 1 and a writable cgroupfs from the KEP field -- the delegated
regime end to end.  `cgroupprobes.battery` asserts the same model here
it asserts in the kep test's pods: with nsdelegate on the hierarchy,
the denials hold at the namespace root and the delegation flow works
under it.  The pod has no memory limit, so the memory probe records the
denial and skips its unchanged-value half.
"""

from __future__ import annotations

import cgroupprobes
from vivarium_runner import Machines

POD_NAME = "nixos"


async def test(vms: Machines) -> None:
    print("[nixos-in-pod] running the battery inside the pod", flush=True)
    await cgroupprobes.battery(vms.cp, POD_NAME)
    print("[nixos-in-pod] adversarial: the battery held", flush=True)
