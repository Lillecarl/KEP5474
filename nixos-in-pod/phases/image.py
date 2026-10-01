#!/usr/bin/env python3
"""The node has the KEP, and the image starts as a Writable pod.

Two failures worth separating.  A node whose runtime does not advertise
`cgroup_mount_mode` cannot give the pod a cgroupfs at all, and the pod
would fail for a reason that has nothing to do with this test.  A tag
nobody imported means the tarball never reached containerd.
"""

from __future__ import annotations

import json

import nixos_in_pod as h
from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = h.image_of(vms)

    # The runtime advertises the field KEP-5474 adds.  crictl cannot be
    # asked -- its bundled cri-api predates the field and drops it -- so
    # the node's own declared features are the honest source.  A node
    # without `CgroupOptions` would reject the pod's securityContext.
    node = json.loads(await cp.succeed("kubectl get node cp -o json"))
    declared = node["status"].get("declaredFeatures", [])
    assert "CgroupOptions" in declared, (
        f"the node does not declare CgroupOptions, so no pod can ask for a"
        f" Writable cgroup; it declares: {declared}"
    )
    print(f"[nixos-in-pod] node declares {declared}", flush=True)

    # The tag in containerd, so a pod asking for it cannot be the first
    # thing to find out it is missing.
    listed = await cp.succeed(
        "ctr --namespace k8s.io images ls --quiet | grep -F "
        + h.shell_quote(image)
        + " || true"
    )
    assert listed.strip(), (
        f"the node never imported {image}; containerd has:"
        f" {await cp.succeed('ctr --namespace k8s.io images ls --quiet')}"
    )
    print(f"[nixos-in-pod] imported: {listed.strip()}", flush=True)

    # Start the pod Writable and let the runtime have its say.  `running`
    # waits for the container to be up, which asks whether the process
    # started at all.
    await h.apply(cp, h.pod_manifest(vms))
    try:
        await h.running(cp, "nixos")
    except AssertionError:
        pod = await h.pod_object(cp, "nixos")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        logs = await cp.execute("kubectl logs nixos --all-containers 2>&1 | tail -40")
        raise AssertionError(
            f"the pod never ran; container status: {statuses}\n{logs[1]}"
        )

    pod = await h.pod_object(cp, "nixos")
    print(
        f"[nixos-in-pod] pod {pod['status']['phase']},"
        f" container {pod['status']['containerStatuses'][0]['state']}",
        flush=True,
    )
