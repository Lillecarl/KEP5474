#!/usr/bin/env python3
"""KEP-5474: writable cgroups for unprivileged containers.

The feature is alpha and unmerged.  The Kubernetes half -- the Pod API, the
CRI API, validation, kubelet and scheduler -- is kubernetes/kubernetes#137568;
the runtime half is containerd/containerd#14039.  default.nix builds both, and
the node runs with the `CgroupOptions` feature gate on.

What this checks, against what the KEP promises:

  * the node declares `CgroupOptions`, which it can only do when the runtime
    advertises the CRI field, the kernel is cgroup v2, /sys/fs/cgroup carries
    `nsdelegate`, and the kubelet manages a cgroup per Pod
  * `mountMode: Writable` makes /sys/fs/cgroup writable, so the container can
    create its own child cgroups
  * the default and an explicit `ReadOnly` leave it read-only
  * the mount mode is per container, not per Pod
  * the kubelet bounds the Pod cgroup with `cgroup.max.descendants` and
    `cgroup.max.depth`, so a container cannot exhaust the node
  * `nsdelegate` stops the container changing its own resource limits
  * the API rejects a mount mode it does not know
"""

import shlex

from vivarium_runner import Machine, Machines
from vivarium_runner.cluster import (
    KUBE_PROXY,
    bring_up,
    get_json,
    wait_for_pods,
)

FEATURE = "CgroupOptions"

# mkdir in the container's own cgroup root.  It succeeds only when
# /sys/fs/cgroup is mounted read-write there.
MKDIR = "mkdir /sys/fs/cgroup/probe && rmdir /sys/fs/cgroup/probe"


def container(
    name: str,
    image: str,
    mode: str | None = None,
    memory: str | None = None,
) -> str:
    """One container, with or without a cgroup mount mode."""
    lines = [
        f"  - name: {name}",
        f"    image: {image}",
        "    imagePullPolicy: Never",
        '    command: ["/bin/sh", "-c", "sleep 3600"]',
    ]
    if mode is not None:
        lines += [
            "    securityContext:",
            "      cgroupOptions:",
            f"        mountMode: {mode}",
        ]
    if memory is not None:
        lines += [
            "    resources:",
            "      limits:",
            f"        memory: {memory}",
        ]
    return "\n".join(lines)


def pod(name: str, containers: list[str]) -> str:
    return (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        f"  name: {name}\n"
        "spec:\n"
        "  restartPolicy: Never\n"
        "  containers:\n" + "\n".join(containers) + "\n"
    )


async def apply(cp: Machine, manifest: str) -> None:
    await cp.succeed(
        f"cat <<'EOF' | kubectl apply --filename -\n{manifest}\nEOF", timeout=180
    )


async def running(cp: Machine, name: str) -> None:
    await wait_for_pods(
        cp, f"--field-selector metadata.name={name}", namespace="default"
    )


async def delete(cp: Machine, name: str) -> None:
    await cp.execute(
        f"kubectl delete pod {name} --ignore-not-found --wait=false", timeout=60
    )


async def exec_sh(cp: Machine, name: str, script: str, container: str | None = None) -> tuple[int, str]:
    target = f" -c {container}" if container is not None else ""
    return await cp.execute(
        f"kubectl exec {name}{target} -- sh -c {shlex.quote(script)}", timeout=180
    )


async def check_node_declares(cp: Machine) -> None:
    mounts = await cp.succeed("grep cgroup2 /proc/mounts")
    print(f"[kep-5474] /sys/fs/cgroup: {mounts.strip()}", flush=True)
    assert "nsdelegate" in mounts, (
        f"the host cgroup mount lacks nsdelegate, so writable cgroups cannot be"
        f" safe or declared: {mounts!r}"
    )

    node = await get_json(cp, "get node cp")
    declared = node["status"].get("declaredFeatures", [])
    assert FEATURE in declared, (
        f"node cp does not declare {FEATURE}, so neither the scheduler nor"
        f" kubelet admission will accept an explicit mount mode: {declared}"
    )
    print(f"[kep-5474] node cp declares {declared}", flush=True)


async def check_writable(cp: Machine, image: str) -> None:
    name = "cgroup-writable"
    await apply(cp, pod(name, [container("test", image, mode="Writable")]))
    await running(cp, name)
    rc, out = await exec_sh(cp, name, MKDIR)
    assert rc == 0, f"mkdir in a Writable container failed: {out}"
    print("[kep-5474] Writable: created and removed a child cgroup", flush=True)
    await delete(cp, name)


async def check_readonly(cp: Machine, image: str, mode: str | None, what: str) -> None:
    name = f"cgroup-{what}"
    await apply(cp, pod(name, [container("test", image, mode=mode)]))
    await running(cp, name)
    rc, out = await exec_sh(cp, name, MKDIR)
    assert rc != 0, f"mkdir succeeded in a {what} container: {out}"
    assert "Read-only file system" in out, (
        f"expected a read-only error in a {what} container, got: {out}"
    )
    print(f"[kep-5474] {what}: /sys/fs/cgroup is read-only", flush=True)
    await delete(cp, name)


async def check_mixed(cp: Machine, image: str) -> None:
    name = "cgroup-mixed"
    await apply(
        cp,
        pod(
            name,
            [
                container("writable", image, mode="Writable"),
                container("readonly", image, mode="ReadOnly"),
            ],
        ),
    )
    await running(cp, name)

    rc, out = await exec_sh(cp, name, MKDIR, container="writable")
    assert rc == 0, f"mkdir failed in the writable container: {out}"
    rc, out = await exec_sh(cp, name, MKDIR, container="readonly")
    assert rc != 0 and "Read-only file system" in out, (
        f"the readonly container is not read-only: {out}"
    )
    print("[kep-5474] mixed: the mode is per container", flush=True)
    await delete(cp, name)


async def check_limit(
    cp: Machine, image: str, name: str, script: str, limit: int, what: str
) -> None:
    await apply(cp, pod(name, [container("test", image, mode="Writable")]))
    await running(cp, name)

    rc, out = await exec_sh(cp, name, script)
    assert rc == 0, f"the {what} probe did not run: {out}"
    assert "stopped:" in out, f"the {what} limit was not enforced: {out}"
    count = int(out.split("stopped:", 1)[1].splitlines()[0])
    assert 0 < count < limit, f"created {count} {what} cgroups, limit {limit}: {out}"
    assert "Resource temporarily unavailable" in out, (
        f"expected EAGAIN at the {what} limit: {out}"
    )
    print(f"[kep-5474] {what} limit: stopped after {count} cgroups (limit {limit})", flush=True)
    await delete(cp, name)


# Descendants: every mkdir adds one to the Pod subtree, so the loop must stop
# before the kubelet's 250.  The bound is well past it, so a missing limit
# fails the test rather than hanging.
DESCENDANTS = (
    "i=0; while [ $i -lt 400 ]; do "
    "if ! mkdir /sys/fs/cgroup/d$i 2>/tmp/e; then echo stopped:$i; cat /tmp/e; exit 0; fi; "
    "i=$((i+1)); done; echo nolimit:$i"
)

# Depth: a chain of nested cgroups, which must stop before the kubelet's 50.
DEPTH = (
    "d=/sys/fs/cgroup; i=0; while [ $i -lt 100 ]; do "
    "d=$d/d; if ! mkdir $d 2>/tmp/e; then echo stopped:$i; cat /tmp/e; exit 0; fi; "
    "i=$((i+1)); done; echo nolimit:$i"
)


async def check_isolation(cp: Machine, image: str) -> None:
    """nsdelegate: the container cannot change its own limits."""
    name = "cgroup-isolation"
    await apply(cp, pod(name, [container("test", image, mode="Writable", memory="128Mi")]))
    await running(cp, name)

    rc, out = await exec_sh(cp, name, "cat /sys/fs/cgroup/memory.max")
    assert rc == 0 and out.strip() == str(128 * 1024 * 1024), (
        f"the container's own memory.max is not the 128Mi limit: {out!r}"
    )
    rc, out = await exec_sh(cp, name, "echo 1000000 > /sys/fs/cgroup/memory.max")
    assert rc != 0, "the container changed its own memory.max"
    print(
        f"[kep-5474] nsdelegate: memory.max is not writable from inside ({out.strip()})",
        flush=True,
    )
    await delete(cp, name)


async def check_invalid_mode(cp: Machine, image: str) -> None:
    manifest = pod("cgroup-invalid", [container("test", image, mode="Bogus")])
    rc, out = await cp.execute(
        f"cat <<'EOF' | kubectl apply --filename -\n{manifest}\nEOF", timeout=60
    )
    assert rc != 0 and "Unsupported value" in out, (
        f"the API accepted an unknown mount mode: {out}"
    )
    print("[kep-5474] the API rejected an unknown mount mode", flush=True)


async def test(vms: Machines) -> None:
    image = vms.settings["workloadImage"]
    print(
        f"[kep-5474] kubernetes {vms.settings['kubernetesVersion']},"
        f" busybox {image}",
        flush=True,
    )

    cp = await bring_up(vms, addons=(KUBE_PROXY,))

    await check_node_declares(cp)
    await check_writable(cp, image)
    await check_readonly(cp, image, mode=None, what="default")
    await check_readonly(cp, image, mode="ReadOnly", what="readonly")
    await check_mixed(cp, image)
    await check_limit(cp, image, "cgroup-descendants", DESCENDANTS, 250, "descendant")
    await check_limit(cp, image, "cgroup-depth", DEPTH, 50, "depth")
    await check_isolation(cp, image)
    await check_invalid_mode(cp, image)

    print("[kep-5474] all checks passed", flush=True)
