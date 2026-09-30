#!/usr/bin/env python3
"""The security model: nsdelegate bounds what a writable container can reach.

The KEP's whole claim is that `nsdelegate` makes writable cgroups safe. The
container may create and manage child cgroups; it may not change its own
resource limits, and it may not see or touch a sibling's. Each assertion
carries the errno the kernel gives, so a container that succeeds for the
wrong reason ("no such file") and one that fails for the right one
("Operation not permitted") do not look the same.

**Making a child usable takes three steps, and that is the point.**  A
container that only `mkdir`s a child finds its `cgroup.controllers` empty
and `memory.max` unopenable.  cgroup v2 will not let a cgroup both hold
processes and delegate controllers downward ("no internal processes"), and
the container's root holds its own PID 1.  A runtime manager -- Ray's
worker isolation, KubeVirt's vCPU cgroups -- moves its processes into a
leaf, enables the controllers on the root, and *then* limits the leaves.
The KEP's `Writable` is what makes all three possible; this phase proves
the whole sequence, not just the `mkdir`.
"""

from __future__ import annotations

import base64

import kep5474
from vivarium_runner import Machine, Machines

POD = "sec-isolation"

# Everything after the container's root holds a process: move the root's
# processes into one leaf, delegate cpu and memory on the root, and limit a
# second leaf.  Written as one script because the moves must not race the
# shell that runs them.
DELEGATE = """
set -e
mkdir -p /sys/fs/cgroup/manager /sys/fs/cgroup/worker
for p in $(cat /sys/fs/cgroup/cgroup.procs); do
  echo "$p" > /sys/fs/cgroup/manager/cgroup.procs 2>/dev/null || true
done
echo "+memory +cpu" > /sys/fs/cgroup/cgroup.subtree_control
echo 52428800 > /sys/fs/cgroup/worker/memory.max
echo "leaf=$(cat /sys/fs/cgroup/worker/memory.max)"
echo "controllers=$(cat /sys/fs/cgroup/worker/cgroup.controllers)"
"""


async def sh(vm: Machine, script: str) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode()).decode()
    return await kep5474.exec_sh(vm, POD, f"echo {encoded} | base64 -d | sh")


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = kep5474.image_of(vms)

    await kep5474.apply(
        cp,
        kep5474.pod(POD, [kep5474.container("t", image, mode="Writable", memory="128Mi")]),
    )
    await kep5474.running(cp, POD)

    # The container sees its own limit as the delegated root's.
    rc, out = await kep5474.exec_sh(cp, POD, "cat /sys/fs/cgroup/memory.max")
    assert rc == 0 and out.strip() == str(128 * 1024 * 1024), (
        f"the container's memory.max is not the 128Mi limit: {out!r}"
    )

    # It may not raise it. This is the cpuset-isolation claim applied to
    # memory: nsdelegate denies writes to the delegated root's controllers.
    rc, out = await kep5474.exec_sh(cp, POD, "echo 1000000 > /sys/fs/cgroup/memory.max")
    assert rc != 0 and "Operation not permitted" in out, (
        f"the container changed its own memory.max: {out}"
    )

    # Nor its CPU limit.
    rc, out = await kep5474.exec_sh(cp, POD, "echo 'max 100000' > /sys/fs/cgroup/cpu.max")
    assert rc != 0 and "Operation not permitted" in out, (
        f"the container changed its own cpu.max: {out}"
    )

    # The container's cgroup namespace root is its own cgroup: `0::/` is
    # the delegated root, not the node's tree.  Read before delegating,
    # which moves the container's own process into a child.
    rc, out = await kep5474.exec_sh(cp, POD, "cat /proc/self/cgroup")
    assert out.strip() == "0::/", (
        f"the container's cgroup namespace root is not its own cgroup: {out!r}"
    )

    # Delegating controllers and limiting a child is the feature's purpose.
    rc, out = await sh(cp, DELEGATE)
    assert rc == 0, f"the container could not delegate and limit a child cgroup: {out}"
    assert "leaf=52428800" in out, f"the child's memory.max did not take: {out}"
    assert "controllers=cpu memory" in out, (
        f"the child was not given cpu and memory controllers: {out}"
    )

    # A sibling Pod's cgroup is not in this container's namespace at all,
    # so it is not reachable by name -- not merely read-only.  `/..` leaves
    # the cgroup mount for the parent filesystem, so it is not an escape
    # either; both are checked for the node's cgroup tree.
    await kep5474.apply(cp, kep5474.pod("sec-sibling", [kep5474.container("t", image, mode="ReadOnly")]))
    await kep5474.running(cp, "sec-sibling")
    for where in ("/sys/fs/cgroup", "/sys/fs/cgroup/..", "/sys/fs/cgroup/../.."):
        rc, out = await kep5474.exec_sh(cp, POD, f"find {where} -maxdepth 2 -name 'kubepods*' 2>/dev/null")
        assert "kubepods" not in out, (
            f"the container can see the node's cgroup tree at {where}: {out!r}"
        )

    await kep5474.delete(cp, "sec-sibling")
    await kep5474.delete(cp, POD)
    print(
        "[kep-5474] security: own limits denied, child delegated and limited,"
        " namespace is a boundary",
        flush=True,
    )
