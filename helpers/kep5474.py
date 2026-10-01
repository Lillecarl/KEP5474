"""What the KEP-5474 phases and checks share.

On the `pythonPath` of the test, so `phases/cluster.py`, `phases/report.py`
and everything under `phases/checks/` import it.  The pod, exec and cgroup
plumbing the checks all want lives in `infra/helpers/kube.py`, shared with
the `nixos-in-pod` test; this module keeps what is specific to this test
-- the cgroup probes, the manifest builder, the PSS namespaces -- and
re-exports the shared plumbing so a phase imports one name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vivarium_runner import Machine
from vivarium_runner.cluster import get_json
from vivarium_runner.cluster import wait_for_pods  # re-exported

from kube import (
    NAMESPACE,  # noqa: F401 -- re-exported
    apply,  # noqa: F401 -- re-exported
    delete,  # noqa: F401 -- re-exported
    exec_sh,  # noqa: F401 -- re-exported
    pod_object,  # noqa: F401 -- re-exported
    running,  # noqa: F401 -- re-exported
    shell_quote,  # noqa: F401 -- re-exported
)

if TYPE_CHECKING:
    from vivarium_runner import Machines

FEATURE = "CgroupOptions"

# mkdir in the container's own cgroup root.  It succeeds only when
# /sys/fs/cgroup is mounted read-write there.
MKDIR = "mkdir /sys/fs/cgroup/probe && rmdir /sys/fs/cgroup/probe"

# Descendants: every mkdir adds one to the Pod subtree, so the loop must stop
# before the kubelet's 250.  The bound is well past it, so a missing limit
# fails the check rather than hanging.
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


def container(
    name: str,
    image: str,
    *,
    mode: str | None = None,
    memory: str | None = None,
    privileged: bool = False,
    command: str = "sleep 3600",
) -> str:
    """One container, with or without a cgroup mount mode."""
    lines = [
        f"  - name: {name}",
        f"    image: {image}",
        "    imagePullPolicy: Never",
        f'    command: ["/bin/sh", "-c", "{command}"]',
    ]
    security: list[str] = []
    if mode is not None:
        security += ["      cgroupOptions:", f"        mountMode: {mode}"]
    if privileged:
        security += ["      privileged: true"]
    if security:
        lines += ["    securityContext:"] + security
    if memory is not None:
        lines += ["    resources:", "      limits:", f"        memory: {memory}"]
    return "\n".join(lines)


def pod(
    name: str,
    containers: list[str],
    *,
    init: list[str] | None = None,
    namespace: str = "default",
    node: str | None = None,
    os_name: str | None = None,
    extra_spec: str = "",
    annotations: dict[str, str] | None = None,
) -> str:
    head = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        f"  name: {name}\n"
        f"  namespace: {namespace}\n"
    )
    if annotations:
        head += "  annotations:\n" + "".join(
            f'    {key}: "{value}"\n' for key, value in annotations.items()
        )
    spec = "spec:\n  restartPolicy: Never\n"
    if node is not None:
        spec += f"  nodeName: {node}\n"
    if os_name is not None:
        spec += f"  os:\n    name: {os_name}\n"
    if init is not None:
        spec += "  initContainers:\n" + "\n".join(init) + "\n"
    spec += "  containers:\n" + "\n".join(containers) + "\n"
    return head + spec + extra_spec


async def pod_cgroup_dir(cp: Machine, name: str) -> str:
    """The pod's cgroup v2 directory on the node, by its UID.

    The systemd driver names the slice after the pod UID with dashes for
    underscores; the QoS class in the middle is burstable or besteffort,
    so the UID is what identifies it.
    """
    pod = await get_json(cp, f"get pod {name}")
    key = pod["metadata"]["uid"].replace("-", "_")
    out = await cp.succeed(
        f"ls -d /sys/fs/cgroup/kubepods.slice/*pod{key}* 2>/dev/null | head -1"
    )
    path = out.strip()
    if not path:
        raise AssertionError(f"no cgroup directory for pod {name}")
    return path


async def ensure_namespace(cp: Machine, name: str, level: str | None = None) -> None:
    """A namespace, optionally with a Pod Security Standards enforce level."""
    await cp.succeed(
        f"kubectl create namespace {name} --dry-run=client --output yaml"
        " | kubectl apply --filename -"
    )
    if level is not None:
        await cp.succeed(
            f"kubectl label namespace {name} --overwrite"
            f" pod-security.kubernetes.io/enforce={level}"
            " pod-security.kubernetes.io/enforce-version=latest"
        )


def image_of(vms: Machines) -> str:
    return vms.settings["workloadImage"]


def runtime_of(vms: Machines) -> str:
    return vms.settings["cri"]
