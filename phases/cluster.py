#!/usr/bin/env python3
"""Bring up the kubeadm node the checks run against.

The cluster is built once and every check runs against it, in order of what
breaks: the two CRIs first, then the API and admission surface, then the
runtime behaviour, then the Pod Security Standards, and finally the
privileged-interaction edges. `report.py` runs whatever happened.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machines
from vivarium_runner.cluster import KUBE_PROXY, bring_up, get_json


async def test(vms: Machines) -> None:
    print(
        f"[kep-5474] kubernetes {vms.settings['kubernetesVersion']},"
        f" cri {vms.settings['cri']}, busybox {vms.settings['workloadImage']}",
        flush=True,
    )
    cp = await bring_up(vms, addons=(KUBE_PROXY,))

    # The host prerequisites the KEP names, printed for the record. A
    # later check re-reads what it needs: `vms.shared` is one phase's, not
    # the run's.
    mounts = (await cp.succeed("grep cgroup2 /proc/mounts")).strip()
    node = await get_json(cp, "get node cp")
    declared = node["status"].get("declaredFeatures", [])
    assert "nsdelegate" in mounts, f"the host cgroup mount has no nsdelegate: {mounts}"
    print(f"[kep-5474] node declares {declared}", flush=True)
    print(f"[kep-5474] {mounts}", flush=True)

    # Namespaces the checks use, made once.
    await kep5474.ensure_namespace(cp, "restricted", level="restricted")
    await kep5474.ensure_namespace(cp, "baseline", level="baseline")
