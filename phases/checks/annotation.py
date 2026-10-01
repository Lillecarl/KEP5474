#!/usr/bin/env python3
"""The pre-KEP route: CRI-O's writable-cgroups pod annotation.

CRI-O has mounted a writable cgroup2 hierarchy for pods carrying
`cgroup2-mount-hierarchy-rw.crio.io: "true"` since long before
KEP-5474.  Reading it proves three things at once: the node's runtime
handler lists the annotation in `allowed_annotations` (CRI-O strips
others), the annotation alone flips the mount, and the negative control
-- the same pod without the annotation -- stays read-only, so the
annotation is what did it.

containerd has no such annotation; under it the check is a no-op.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machines

ANNOTATION = "cgroup2-mount-hierarchy-rw.crio.io"

# Arming the namespace root is the step the KEP-field route on CRI-O
# refuses for lack of nsdelegate on the node's cgroup2 mount.  The
# annotation route checks nothing -- what it allows is recorded, not
# asserted, because a root holding this pod's own processes turns the
# write into EBUSY for reasons that have nothing to do with the
# annotation.
ARM = 'echo "+memory" > /sys/fs/cgroup/cgroup.subtree_control 2>&1 && echo armed'


async def test(vms: Machines) -> None:
    if vms.settings["cri"] != "crio":
        return
    cp = vms.cp
    image = kep5474.image_of(vms)

    # The annotation flips the mount, and the hierarchy is the real
    # cgroup2 filesystem.
    await kep5474.apply(
        cp,
        kep5474.pod(
            "anno-rw",
            [kep5474.container("t", image)],
            annotations={ANNOTATION: "true"},
        ),
    )
    await kep5474.running(cp, "anno-rw")
    rc, out = await kep5474.exec_sh(cp, "anno-rw", kep5474.MKDIR)
    assert rc == 0, f"the annotation did not make cgroups writable: {out}"
    rc, out = await kep5474.exec_sh(cp, "anno-rw", ARM)
    print(f"[kep5474] annotation arm: {out.strip()}", flush=True)
    await kep5474.delete(cp, "anno-rw")

    # The negative control: no annotation, read-only, as on any runtime.
    await kep5474.apply(cp, kep5474.pod("anno-ro", [kep5474.container("t", image)]))
    await kep5474.running(cp, "anno-ro")
    rc, out = await kep5474.exec_sh(cp, "anno-ro", kep5474.MKDIR)
    assert rc != 0 and "Read-only file system" in out, (
        f"the pod without the annotation is not read-only: {out}"
    )
    await kep5474.delete(cp, "anno-ro")
