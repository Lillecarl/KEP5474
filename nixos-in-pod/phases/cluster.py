#!/usr/bin/env python3
"""Bring up the node and record what the host offers.

Nothing here asserts the feature; it is the ground the pod needs.  The
numbers printed are the ones a failure will be explained by later.
"""

from __future__ import annotations

import nixos_in_pod
from vivarium_runner import Machines
from vivarium_runner.cluster import KUBE_PROXY, bring_up


async def test(vms: Machines) -> None:
    print(
        f"[nixos-in-pod] image {vms.settings['imageName']},"
        f" uid {vms.settings['uid']}",
        flush=True,
    )
    cp = await bring_up(vms, addons=(KUBE_PROXY,))

    # What a container can and cannot do here, for the record.  A
    # systemd-in-a-pod needs clone, mount and cgroup namespaces, and the
    # kernel to allow them unprivileged.
    for label, command in (
        ("cgroup2", "stat -fc %T /sys/fs/cgroup"),
        ("controllers", "cat /sys/fs/cgroup/cgroup.controllers"),
        ("mount", "grep ' / ' /proc/mounts"),
        ("unprivileged_userns", "cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo absent"),
    ):
        code, out = await cp.execute(command)
        print(f"[nixos-in-pod] {label}: {out.strip() or '(empty)'}", flush=True)

    # The image is imported by the node's own unit; the pod would sit in
    # ErrImagePull if it were not.  Waiting here means the image phase
    # reports an import failure, not a pod that never starts.
    await cp.succeed(
        "systemctl is-active k8s-load-images.service"
        " || systemctl start k8s-load-images.service",
        timeout=600,
    )
    images = await cp.succeed(
        "ctr --namespace k8s.io images ls --quiet | grep -c nixos || true"
    )
    print(f"[nixos-in-pod] node has {images.strip()} nixos image(s)", flush=True)
