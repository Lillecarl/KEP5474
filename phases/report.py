#!/usr/bin/env python3
"""Whatever happened, write the namespace's state where a reader can find it.

`always`, so it runs after a check failed -- which is when it is read. The
cluster test's own `until` already attaches the event log and the pod
describes to the error it raises; this is the KEP-specific picture, written
to `/artifacts` so it survives the run.
"""

from __future__ import annotations

from vivarium_runner import Machines
from vivarium_runner.cluster import get_json, kubectl


async def test(vms: Machines) -> None:
    cp = vms.cp
    report = vms.artifacts / "report"
    report.mkdir(parents=True, exist_ok=True)

    for name, command in (
        ("events", "get events --all-namespaces --sort-by=.lastTimestamp"),
        ("pods", "get pods --all-namespaces --output wide"),
        ("nodes", "get nodes --output wide"),
        ("declared", "get node cp --output jsonpath={.status.declaredFeatures}"),
    ):
        rc, out = await cp.execute(f"kubectl {command}", timeout=120)
        (report / f"{name}.txt").write_text(out)

    # The cgroup tree, for a reader asking why a limit did or did not apply.
    tree = await cp.execute(
        "find /sys/fs/cgroup/kubepods.slice -maxdepth 4 -name 'cgroup.max.*'"
        " -exec sh -c 'echo {}: $(cat {})' \\; 2>/dev/null | head -40",
        timeout=120,
    )
    (report / "cgroup-limits.txt").write_text(tree[1])

    # The two nodes' declared features, as a small table for the record.
    node = await get_json(cp, "get node cp")
    lines = [
        f"kubernetes   {vms.settings['kubernetesVersion']}",
        f"cri          {vms.settings['cri']}",
        f"declared     {', '.join(node['status'].get('declaredFeatures', []))}",
    ]
    (report / "summary.txt").write_text("\n".join(lines) + "\n")
    print("[kep-5474] report: " + str(report), flush=True)
