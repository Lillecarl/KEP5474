#!/usr/bin/env python3
"""The runtime behaviour: what the container can and cannot do.

Writable makes /sys/fs/cgroup read-write in the container's cgroup namespace;
the default and ReadOnly leave it read-only; the mode is per container, not
per Pod.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = kep5474.image_of(vms)

    # Writable: a child cgroup can be made and removed.
    await kep5474.apply(cp, kep5474.pod("rt-writable", [kep5474.container("t", image, mode="Writable")]))
    await kep5474.running(cp, "rt-writable")
    rc, out = await kep5474.exec_sh(cp, "rt-writable", kep5474.MKDIR)
    assert rc == 0, f"mkdir in a Writable container failed: {out}"
    await kep5474.delete(cp, "rt-writable")

    # The default and an explicit ReadOnly are both read-only, and the
    # error is the kernel's, not a missing file.
    for mode, name in ((None, "rt-default"), ("ReadOnly", "rt-readonly")):
        await kep5474.apply(cp, kep5474.pod(name, [kep5474.container("t", image, mode=mode)]))
        await kep5474.running(cp, name)
        rc, out = await kep5474.exec_sh(cp, name, kep5474.MKDIR)
        assert rc != 0 and "Read-only file system" in out, (
            f"{name} is not read-only: {out}"
        )
        await kep5474.delete(cp, name)

    # Per container: two containers in one Pod, one of each.
    await kep5474.apply(
        cp,
        kep5474.pod(
            "rt-mixed",
            [
                kep5474.container("writable", image, mode="Writable"),
                kep5474.container("readonly", image, mode="ReadOnly"),
            ],
        ),
    )
    await kep5474.running(cp, "rt-mixed")
    rc, out = await kep5474.exec_sh(cp, "rt-mixed", kep5474.MKDIR, container="writable")
    assert rc == 0, f"mkdir failed in the writable container: {out}"
    rc, out = await kep5474.exec_sh(cp, "rt-mixed", kep5474.MKDIR, container="readonly")
    assert rc != 0 and "Read-only file system" in out, (
        f"the readonly container is not read-only: {out}"
    )
    await kep5474.delete(cp, "rt-mixed")

    # The mount is a cgroup2 filesystem, not a bind of something else.
    # `stat -fc %T` says UNKNOWN in the guest's busybox, so read the mount
    # table, which is the same evidence by another route.
    await kep5474.apply(cp, kep5474.pod("rt-mount", [kep5474.container("t", image, mode="Writable")]))
    await kep5474.running(cp, "rt-mount")
    _, out = await kep5474.exec_sh(cp, "rt-mount", "grep cgroup /proc/mounts")
    assert "cgroup2" in out, f"/sys/fs/cgroup is not cgroup2 in the container: {out!r}"
    await kep5474.delete(cp, "rt-mount")

    print("[kep-5474] runtime: writable works, default/ReadOnly read-only,"
          " per container, cgroup2", flush=True)
