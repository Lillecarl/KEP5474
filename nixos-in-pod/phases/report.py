#!/usr/bin/env python3
"""State for a failed run, written whenever this phase runs.

Even a run that got nowhere leaves the pod's events and logs somewhere a
person can read them, and this is that place.  It asserts nothing.
"""

from __future__ import annotations

import nixos_in_pod as h
from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    cp = vms.cp
    out = vms.artifacts / "report"
    out.mkdir(parents=True, exist_ok=True)

    for name, command in (
        ("pods.txt", "kubectl get pods --all-namespaces --output wide"),
        ("pods-full.txt", "kubectl get pods --all-namespaces --output yaml"),
        ("nixos.yaml", "kubectl get pod nixos --output yaml"),
        (
            "nixos-events.txt",
            "kubectl get events --field-selector involvedObject.name=nixos"
            " --sort-by .lastTimestamp",
        ),
        (
            "nixos-logs.txt",
            "kubectl logs nixos --tail=-1 --all-containers 2>&1",
        ),
        (
            "previous-logs.txt",
            "kubectl logs nixos --tail=-1 --all-containers --previous 2>&1",
        ),
        ("images.txt", "ctr --namespace k8s.io images ls"),
        ("load-images.txt", "journalctl -u k8s-load-images.service --no-pager"),
        ("describe-node.txt", "kubectl describe node"),
        (
            "host.txt",
            "uname -a; grep cgroup2 /proc/self/mountinfo;"
            " cat /sys/fs/cgroup/cgroup.controllers; containerd --version",
        ),
    ):
        code, text = await cp.execute(command)
        (out / name).write_text(text)
        print(f"[nixos-in-pod] {name}: {len(text)} bytes, rc={code}", flush=True)

    # The proofs, if systemd got far enough to write any -- the useful
    # half of a failure in the phase after this one.
    code, listing = await h.exec_sh(cp, "nixos", f"ls -la {h.PROOF_DIR} 2>&1")
    (out / "proofs.txt").write_text(listing)
    print(f"[nixos-in-pod] proofs: {listing.strip() or '(none)'}", flush=True)
