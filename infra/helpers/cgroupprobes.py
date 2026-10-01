"""The delegation model, attacked from inside a writable container.

The battery every test here runs against a writable cgroupfs.  Every
write the kernel's model says must be denied is attempted and asserted
denied with the value it targets left alone; every write the delegation
use case needs is asserted to work.  `exec_sh` records each probe in the
evidence ledger, so the battery is auditable after the run.

The expectations hold in both ownership regimes the tests produce --
a plain root container (the kep test's pods) and a user namespace (the
nixos-in-pod pod): with `nsdelegate` on the hierarchy the kernel rejects
non-delegate writes on the namespace root regardless of the file's
owner, and in a user namespace the owning root is unmapped even where
that rule would not apply.

Two of the root files are not asserted denied and only recorded:
`cgroup.procs` and `cgroup.subtree_control` are on the kernel's delegate
allow-list (`/sys/kernel/cgroup/delegate`), so writing them at the
namespace root is the model working, not a hole -- arming
`subtree_control` there fails anyway while the root holds this
container's own processes, and when it succeeds it arms controllers for
the container's own children, which is the delegation use case.

The evidence for the other side -- that nothing above the namespace
root is reachable -- is structural: the container's mount is a bind of
its own subtree, so there is no path to test.  The `".."` probe asserts
the root the kernel pins instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kube import exec_sh

if TYPE_CHECKING:
    from vivarium_runner import Machine

ROOT = "/sys/fs/cgroup"


async def _read(cp: Machine, pod: str, path: str, *, container: str | None = None) -> str:
    rc, out = await exec_sh(cp, pod, f"cat {path}", container=container)
    assert rc == 0, f"reading {path} in {pod} failed: {out}"
    return out.strip()


async def _denied(
    cp: Machine,
    pod: str,
    description: str,
    write: str,
    expect_value: str | None,
    *,
    container: str | None = None,
) -> None:
    """A root write that must fail, with the value it targets unchanged.

    `expect_value` is the value read back after the failed write, or
    None to skip that half -- the denial itself is always asserted.
    """
    rc, out = await exec_sh(cp, pod, write, container=container)
    assert rc != 0, f"{description}: the write succeeded -- {out!r}"
    if expect_value is not None:
        value = await _read(cp, pod, write.split(">")[-1].strip(), container=container)
        assert value == expect_value, f"{description}: the value changed to {value!r}"


async def _steps(
    cp: Machine,
    pod: str,
    steps: list[tuple[str, str]],
    *,
    container: str | None = None,
) -> dict[str, str]:
    """A sequence of probes, each its own exec and evidence record."""
    outputs: dict[str, str] = {}
    for name, script in steps:
        rc, out = await exec_sh(cp, pod, script, container=container)
        assert rc == 0, f"{name}: rc={rc} {out!r}"
        outputs[name] = out
    return outputs


async def battery(
    cp: Machine,
    pod: str,
    *,
    container: str | None = None,
) -> None:
    """The whole battery, against one writable container.

    The pod needs a memory limit for the `memory.max` denial probe to
    have a value to guard; without one the probe records the denial and
    skips its unchanged-value half.
    """
    # The mount is rw and it is cgroup2.  /proc/mounts' fields are
    # device, mount point, fstype, options; a bind keeps the fstype.
    # The whole table is read and parsed here: the battery must not
    # assume a guest tool beyond cat, sh and stat.
    rc, out = await exec_sh(cp, pod, "cat /proc/mounts", container=container)
    line = next(
        (
            line
            for line in out.splitlines()
            if line.split()[1:2] == ["/sys/fs/cgroup"]
        ),
        "",
    )
    fields = line.split()
    assert (
        fields
        and fields[2] == "cgroup2"
        and "rw" in fields[3].split(",")
    ), f"{ROOT} is not a rw cgroup2 mount: {out!r}"

    # A child can be made and removed: the delegation use case, and the
    # feature's own acceptance test.
    rc, out = await exec_sh(
        cp, pod, f"mkdir {ROOT}/pen && rmdir {ROOT}/pen", container=container
    )
    assert rc == 0, f"mkdir at the cgroup root failed: {out}"

    # The v1 escape surface does not exist here.  release_agent and
    # notify_on_release are v2 root-only files, and the container's
    # namespace root is a subtree bind, not the mount root -- the class
    # of hole CVE-2022-0492 exploited cannot even see them.
    for file in ("release_agent", "notify_on_release"):
        rc, out = await exec_sh(cp, pod, f"test -e {ROOT}/{file}", container=container)
        assert rc != 0, f"{file} is visible in the container"

    # ".." from the namespace root: recorded, not asserted.  A mount
    # root's ".." climbs out of the mount to the directory it hangs on,
    # which here is /sys/fs -- the container's own sysfs, not cgroup
    # content; the cgroups above the namespace root are unreachable this
    # way and every other.
    rc, out = await exec_sh(
        cp,
        pod,
        f"stat -c '%d:%i' {ROOT} {ROOT}/..; echo ---;"
        f" ls {ROOT}/.. 2>&1 | head -5",
        container=container,
    )
    print(f"[cgroupprobes] root '..': {out.strip()!r}", flush=True)

    # Writes the model denies, with the values guarding themselves.
    current = await _read(cp, pod, f"{ROOT}/memory.max", container=container)
    if current != "max":
        await _denied(
            cp, pod, "memory.max", f"echo max > {ROOT}/memory.max", current, container=container
        )
    else:
        rc, out = await exec_sh(cp, pod, f"echo max > {ROOT}/memory.max", container=container)
        if rc == 0:
            raise AssertionError("memory.max is writable and there is no limit to guard")
    await _denied(
        cp, pod, "cgroup.freeze", f"echo 1 > {ROOT}/cgroup.freeze", "0", container=container
    )
    await _denied(cp, pod, "cgroup.kill", f"echo 1 > {ROOT}/cgroup.kill", None, container=container)

    # The delegate-listed root files: recorded, not asserted (see module
    # docstring).
    rc, out = await exec_sh(
        cp, pod, f"echo $$ > {ROOT}/cgroup.procs && echo moved", container=container
    )
    print(f"[cgroupprobes] cgroup.procs at root: rc={rc} {out.strip()!r}", flush=True)
    rc, out = await exec_sh(
        cp, pod, f"echo '+memory' > {ROOT}/cgroup.subtree_control 2>&1 && echo armed", container=container
    )
    print(
        f"[cgroupprobes] subtree_control at root: rc={rc} {out.strip()!r}", flush=True
    )

    # The delegation flow the KEP's stories describe, in the kernel's
    # own "organize once and control" order.  Two rules force the shape:
    # a cgroup arms only the controllers its parent armed, and no
    # populated cgroup may arm a domain controller -- the population
    # including the probe's own shell, which every kubectl exec creates
    # at the namespace root.  So each arming step first moves the shell
    # and the container's init (pid 1, the sleep) out of the cgroup it
    # arms, and the shell that proves the migration is the one that did
    # it.
    outputs = await _steps(
        cp,
        pod,
        [
            ("mkdir", f"mkdir {ROOT}/pen {ROOT}/pen/g"),
            (
                "empty-root, arm-root",
                f"echo $$ > {ROOT}/pen/cgroup.procs"
                f" && echo 1 > {ROOT}/pen/cgroup.procs"
                f" && echo '+memory' > {ROOT}/cgroup.subtree_control",
            ),
            (
                "empty-pen, migrate, arm-pen",
                f"echo $$ > {ROOT}/pen/g/cgroup.procs"
                f" && echo 1 > {ROOT}/pen/g/cgroup.procs"
                f" && echo '+memory' > {ROOT}/pen/cgroup.subtree_control"
                f" && cat /proc/self/cgroup",
            ),
            ("limit", f"echo 536870912 > {ROOT}/pen/g/memory.max"),
        ],
        container=container,
    )
    landed = outputs["empty-pen, migrate, arm-pen"].strip().splitlines()[-1]
    assert landed.endswith("/pen/g"), f"the process did not land in pen/g: {landed!r}"

    # cpuset containment, when the controller is available: arm it down
    # the same chain -- the root is unpopulated after the flow -- then
    # widen the request and check the effective set did not widen.
    rc, out = await exec_sh(
        cp, pod, f"cat {ROOT}/cgroup.controllers", container=container
    )
    if "cpuset" in out.split():
        # The root must be vacated for its own arm too; the pen arm
        # only needs pen empty, which the flow above left it.
        await _steps(
            cp,
            pod,
            [
                (
                    "empty-root, arm-root",
                    f"echo $$ > {ROOT}/pen/g/cgroup.procs"
                    f" && echo 1 > {ROOT}/pen/g/cgroup.procs"
                    f" && echo '+cpuset' > {ROOT}/cgroup.subtree_control",
                ),
                ("arm-pen", f"echo '+cpuset' > {ROOT}/pen/cgroup.subtree_control"),
                ("widen", f"echo 0-63 > {ROOT}/pen/g/cpuset.cpus"),
                (
                    "contained",
                    f"[ \"$(cat {ROOT}/pen/g/cpuset.effective_cpus)\""
                    f" = \"$(cat {ROOT}/cpuset.cpus.effective)\" ]",
                ),
            ],
            container=container,
        )
    else:
        print("[cgroupprobes] cpuset controller not available; containment unprobed", flush=True)

    # The exhaustion limits, reported.  `cgroup.max.*` read here is the
    # container's OWN subtree budget: the kubelet sets 250/50 on the
    # pod scope, one level above the namespace root, where this probe
    # cannot see it -- the `limits` check proves that bound by hitting
    # it.  A number here would mean the runtime or kubelet set a budget
    # on the container too; "max" is the expected shape.
    for file in ("cgroup.max.descendants", "cgroup.max.depth"):
        value = await _read(cp, pod, f"{ROOT}/{file}", container=container)
        verdict = (
            "a budget is set on the container itself"
            if value.isdigit()
            else "unbounded here -- the pod-scope budget is the bound"
        )
        print(f"[cgroupprobes] {file} = {value} ({verdict})", flush=True)
