#!/usr/bin/env python3
"""Both CRIs on the train: each advertises `cgroup_mount_mode`.

The KEP's rule is that a node may only declare `CgroupOptions` when its
runtime advertises the field, so this is where "is CRI-O on the train too"
is answered.

**`crictl` cannot answer it.**  Its bundled `k8s.io/cri-api` is the released
one, and proto's JSON marshaler drops a field it does not know -- so a
runtime that advertises `cgroup_mount_mode` and one that does not look
identical through `crictl info`.  The kubelet is the client that matters,
and it is built from the PR, so the declared-feature list is the honest
answer for the runtime the node actually runs.

For the runtime the node does *not* run, there is no kubelet to ask.  Its
binary is asked instead: the string `cgroup_mount_mode` is in the compiled
proto only when the runtime was built against the CRI API that has the
field, and in no other case.
"""

from __future__ import annotations

import json

from kep5474 import FEATURE
from vivarium_runner import Machine, Machines
from vivarium_runner.cluster import get_json


async def binary_carries(cp: Machine) -> bool:
    """Whether a runtime's own binary embeds the CRI field.

    `crio` in the NixOS wrapper is a bash script that sets PATH and execs
    the real one, so the wrapper is skipped: the string lives in the
    implementation, not in the script that calls it.  Every build of that
    name in the guest's store is checked -- the store is the host's, and a
    node can see several.
    """
    binaries = (
        "/nix/store/*-cri-o-*/bin/crio",
        "/nix/store/*-containerd-*/bin/containerd",
    )
    rc, out = await cp.execute(
        f"for b in {' '.join(binaries)}; do"
        f"  [ -e $b ] || continue;"
        f"  case $(head -c2 $b) in '#!') continue ;; esac;"
        f"  if grep -qa cgroup_mount_mode $b; then echo \"$b\"; fi;"
        f"done",
        timeout=120,
    )
    return bool(out.strip())


async def test(vms: Machines) -> None:
    cp = vms.cp
    active = vms.settings["cri"]

    node = await get_json(cp, "get node cp")
    declared = node["status"].get("declaredFeatures", [])
    print(f"[kep-5474] node cp declares {declared}", flush=True)
    assert FEATURE in declared, (
        f"node cp does not declare {FEATURE}, so its runtime ({active}) does"
        f" not advertise cgroup_mount_mode or a host prerequisite is missing:"
        f" {declared}"
    )

    # The other runtime, which no node here runs. Its binary carrying the
    # string is the strongest evidence a single-node test can get.
    for name in (n for n in ("containerd", "crio") if n != active):
        carries = await binary_carries(cp)
        print(
            f"[kep-5474] {name}: cgroup_mount_mode"
            f" {'present' if carries else 'ABSENT'} in the store",
            flush=True,
        )

    # The active runtime must carry it too, on the same evidence.
    assert await binary_carries(cp), (
        "no runtime binary in the node's store embeds cgroup_mount_mode"
    )
    print(json.dumps({"active": active, "declared": declared}), flush=True)
