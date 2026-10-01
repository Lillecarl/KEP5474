#!/usr/bin/env python3
"""Capture what systemd as PID 1 meets, from clean pods.

Ad hoc probing in a long-lived pod is worthless: the cgroup root keeps a
trickle of processes and every later reading lies about it.  Each test
here gets its own pod and reads its result once.

Two questions, and they are separable:

1. Does an `exec` of the image's systemd work at all?  `--help` exits
   without initialising anything, so its output proves the binary runs.
2. If it runs, where does a real init stop?

The shell is only used to reach `exec`.  Pids come from `/proc/self` in
the same process that writes them, never from a `$()` subshell.
"""

from __future__ import annotations

import base64
import json

import nixos_in_pod as h
from vivarium_runner import Machine, Machines


async def pod_or_failed(
    cp: Machine,
    name: str,
    image: str,
    script: str,
    *,
    extra_spec: dict | None = None,
) -> str:
    """One pod, its command a base64-decoded script; returns phase and log.

    A pod that dies is the result here, not a phase failure.
    """
    encoded = base64.b64encode(script.encode()).decode()
    spec = {
        "restartPolicy": "Never",
        "nodeName": "cp",
        "containers": [
            {
                "name": name,
                "image": image,
                "imagePullPolicy": "Never",
                "command": [
                    "/bin/sh",
                    "-c",
                    f"echo {encoded} | base64 -d | sh",
                ],
                "securityContext": {"cgroupOptions": {"mountMode": "Writable"}},
            }
        ],
    }
    if extra_spec:
        spec.update(extra_spec)
    document = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": "default"},
        "spec": spec,
    }
    await cp.succeed(f"kubectl delete pod {name} --ignore-not-found --wait=false")
    await cp.succeed(
        "cat <<'EOF' | kubectl apply -f -\n" + json.dumps(document) + "\nEOF"
    )
    import asyncio

    phase = ""
    for _ in range(40):
        phase = (
            await cp.succeed(
                f"kubectl get pod {name} -o jsonpath={{.status.phase}} 2>/dev/null"
            )
        ).strip()
        if phase in ("Succeeded", "Failed"):
            break
        await asyncio.sleep(2)
    state = (
        await cp.succeed(
            f"kubectl get pod {name} -o jsonpath={{.status.phase}}"
            ":{.status.containerStatuses[0].state.terminated.exitCode} 2>/dev/null"
        )
    ).strip()
    logs = (await cp.execute(f"kubectl logs {name}"))[1]
    return f"phase:exit={state}\n{logs}"


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = h.image_of(vms)
    systemd = vms.settings["systemd"]

    # 1. Does the exec work?  --help is the shortest path that exits.
    print("=== exec systemd --help ===")
    print(await pod_or_failed(cp, "sdhelp", image, f"exec {systemd} --help"))

    # 2. A real init, with the console at debug.  The env is read before
    #    any unit loads, so it reaches systemd's earliest output.
    print("=== exec systemd --system, debug to console ===")
    print(
        await pod_or_failed(
            cp,
            "sdrun",
            image,
            f"SYSTEMD_LOG_LEVEL=debug SYSTEMD_LOG_TARGET=console"
            f" exec {systemd} --system",
        )
    )

    # 3. What a user namespace is worth.  The pod's uid 0 maps to an
    #    unprivileged host uid; inside, systemd-as-PID-1 keeps uid 0.
    #    The open question is the cgroupfs: mounted by the kubelet and
    #    owned by host root, with no chown into the pod's uid range --
    #    so which cgroup operations survive the mapping is a
    #    measurement, not a given.  Every read names its numbers.
    nsprobe = r"""
echo "=== identity ==="
id
echo "--- uid_map ---"
cat /proc/self/uid_map
echo "=== cgroupfs, as the namespace sees it ==="
cat /proc/self/cgroup
ls -ln /sys/fs/cgroup/ 2>&1 | head -5
echo "--- mkdir probe ---"
mkdir /sys/fs/cgroup/nsprobe 2>&1 && echo "mkdir: OK" && rmdir /sys/fs/cgroup/nsprobe || echo "mkdir: FAILED"
echo "--- arm probe ---"
echo "+memory" > /sys/fs/cgroup/cgroup.subtree_control 2>&1 && echo "arm: OK" || echo "arm: FAILED"
"""
    print("=== user namespace: identity and cgroupfs ===")
    print(
        await pod_or_failed(
            cp,
            "nsprobe",
            image,
            nsprobe,
            extra_spec={"hostUsers": False},
        )
    )
