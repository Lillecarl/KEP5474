#!/usr/bin/env python3
"""CRI-O's annotation against the KEP's own gate: the no-nsdelegate node.

The KEP makes `nsdelegate` a hard prerequisite: both runtimes' field
implementations refuse to start a writable container on a node whose
cgroup2 hierarchy lacks it -- containerd and CRI-O's PR both check
(`checkWritableCgroupsSupported` in CRI-O, the same error string
compiled into containerd).  The pre-KEP annotation route checks
nothing: CRI-O flips the cgroup mount to rw whenever the pod carries
the annotation, whatever the mount options are.

On a node without nsdelegate the kernel assigns no ownership at the
namespace root, so the denial the battery relies on is gone: a root
container can rewrite the limits set on it.  This phase runs that
difference live and asserts it.

    1. The KEP field pod never starts; the message names nsdelegate.
    2. The annotation pod starts writable, and raising its own
       memory.max succeeds -- the resource escape the KEP field's gate
       exists to prevent.
    3. The same annotation pod under a user namespace is denied: the
       owning root is unmapped, which is what saves KEP-127 workloads
       on such nodes.

The run that executes this phase builds the node with
`KEP5474_NSDELEGATE=false`; the standard run keeps it on and never
gets here.
"""

from __future__ import annotations

import asyncio
import json

import cgroupprobes
import kep5474
from kep5474 import pod_cgroup_dir
from vivarium_runner import Machines

ANNOTATION = "cgroup2-mount-hierarchy-rw.crio.io"


async def test(vms: Machines) -> None:
    if vms.settings["nsdelegate"]:
        print("[kep-5474] nsdelegate on: nothing to demonstrate here", flush=True)
        return
    assert vms.settings["cri"] == "crio", "the annotation route is CRI-O only"
    cp = vms.cp
    image = kep5474.image_of(vms)

    # 1. The KEP field pod: created as an object, never started, and
    # the reason names the missing mount option.
    await kep5474.apply(
        cp,
        kep5474.pod(
            "div-field",
            [kep5474.container("t", image, mode="Writable", memory="256M")],
        ),
    )
    message = ""
    status: dict = {}
    for _ in range(30):
        pod = await kep5474.pod_object(cp, "div-field")
        status = pod["status"]
        waiting = (status.get("containerStatuses") or [{}])[0].get("state", {}).get("waiting", {})
        message = waiting.get("message", status.get("message", ""))
        if "nsdelegate" in message:
            break
        await asyncio.sleep(2)
    assert "nsdelegate" in message, (
        f"the field pod was not refused for nsdelegate: {json.dumps(status)[:400]}"
    )
    print(f"[kep-5474] field pod refused: {message}", flush=True)
    await kep5474.delete(cp, "div-field")

    # 2. The annotation pod, root: writable, and its own limit is
    # removable.  The pod-scope cap printed after it still holds --
    # the escape reaches the pod's budget, not the node's.
    await kep5474.apply(
        cp,
        kep5474.pod(
            "div-anno-root",
            [kep5474.container("t", image, memory="256M")],
            annotations={ANNOTATION: "true"},
        ),
    )
    await kep5474.running(cp, "div-anno-root")
    rc, out = await kep5474.exec_sh(cp, "div-anno-root", kep5474.MKDIR)
    assert rc == 0, f"the annotation pod is not writable: {out}"
    current = await cgroupprobes._read(
        cp, "div-anno-root", f"{cgroupprobes.ROOT}/memory.max"
    )
    assert current != "max", (
        f"expected the pod's memory limit at memory.max, got max: the raise "
        "would prove nothing"
    )
    rc, out = await kep5474.exec_sh(
        cp, "div-anno-root", f"echo max > {cgroupprobes.ROOT}/memory.max"
    )
    assert rc == 0, f"raising its own memory.max failed after all: {out}"
    raised = await cgroupprobes._read(
        cp, "div-anno-root", f"{cgroupprobes.ROOT}/memory.max"
    )
    assert raised == "max", f"memory.max read back {raised!r}, not max"
    print(
        "[kep-5474] HOLE: a root container removed its own memory limit"
        " without nsdelegate", flush=True,
    )
    pod_dir = await pod_cgroup_dir(cp, "div-anno-root")
    scope = await cp.succeed(f"cat {pod_dir}/memory.max")
    print(f"[kep-5474] the pod-scope cap still reads {scope.strip()}", flush=True)
    await kep5474.delete(cp, "div-anno-root")

    # 3. The same annotation under a user namespace: denied, value
    # unchanged -- the ownership mapping is the backstop.
    await kep5474.apply(
        cp,
        kep5474.pod(
            "div-anno-userns",
            [kep5474.container("t", image, memory="256M")],
            annotations={ANNOTATION: "true"},
            host_users=False,
        ),
    )
    await kep5474.running(cp, "div-anno-userns")
    userns_current = await cgroupprobes._read(
        cp, "div-anno-userns", f"{cgroupprobes.ROOT}/memory.max"
    )
    await cgroupprobes._denied(
        cp,
        "div-anno-userns",
        "memory.max under a user namespace",
        f"echo max > {cgroupprobes.ROOT}/memory.max",
        userns_current,
    )
    print("[kep-5474] user namespace denied the raise", flush=True)
    await kep5474.delete(cp, "div-anno-userns")
