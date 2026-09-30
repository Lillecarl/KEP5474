#!/usr/bin/env python3
"""The two resource-exhaustion bounds the KEP adds.

`cgroup.max.descendants` and `cgroup.max.depth` are set by the kubelet on the
Pod cgroup before any container starts, so a container that makes cgroups
in a loop cannot exhaust node memory or inotify watches. The KEP's own e2e
test stops one short of the limit because runtime-created cgroups count too.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machine, Machines


async def probe_limit(vm: Machine, name: str, script: str, limit: int, what: str) -> int:
    await kep5474.apply(vm, kep5474.pod(name, [kep5474.container("t", kep5474.image_of(_VMS), mode="Writable")]))
    await kep5474.running(vm, name)
    rc, out = await kep5474.exec_sh(vm, name, script)
    assert rc == 0, f"the {what} probe did not run: {out}"
    assert "stopped:" in out, f"the {what} limit was not enforced: {out}"
    count = int(out.split("stopped:", 1)[1].splitlines()[0])
    assert "Resource temporarily unavailable" in out, (
        f"expected EAGAIN at the {what} limit: {out}"
    )
    assert 0 < count < limit, f"created {count} {what} cgroups, limit {limit}: {out}"
    await kep5474.delete(vm, name)
    return count


_VMS: Machines


async def test(vms: Machines) -> None:
    global _VMS
    _VMS = vms
    cp = vms.cp

    descendants = await probe_limit(cp, "lim-desc", kep5474.DESCENDANTS, 250, "descendant")
    depth = await probe_limit(cp, "lim-depth", kep5474.DEPTH, 50, "depth")

    # The limits are on the Pod cgroup itself, so a second Pod gets its own
    # budget rather than sharing this one's.
    await kep5474.apply(cp, kep5474.pod("lim-second", [kep5474.container("t", kep5474.image_of(vms), mode="Writable")]))
    await kep5474.running(cp, "lim-second")
    rc, out = await kep5474.exec_sh(
        cp, "lim-second", "mkdir /sys/fs/cgroup/one && echo made"
    )
    assert rc == 0 and "made" in out, f"a second pod has no budget of its own: {out}"
    await kep5474.delete(cp, "lim-second")

    print(
        f"[kep-5474] limits: descendants stopped at {descendants}/250,"
        f" depth at {depth}/50, per pod",
        flush=True,
    )
