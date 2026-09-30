#!/usr/bin/env python3
"""The API surface: what the field accepts, what it refuses, and the gates.

Each assertion is named, because a run's `junit.xml` should say which part of
the KEP failed and not only that the phase did.
"""

from __future__ import annotations

import json

import kep5474
from vivarium_runner import Machine, Machines

from vivarium_runner.cluster import get_json


async def dry_run(cp: Machine, manifest: str) -> tuple[int, str]:
    return await cp.execute(
        f"cat <<'EOF' | kubectl apply --dry-run=server --filename -\n{manifest}\nEOF",
        timeout=60,
    )


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = kep5474.image_of(vms)

    # 1. The node declares it. It can only do so with a runtime that
    #    advertises the field, cgroup v2 with nsdelegate, and a cgroup per
    #    Pod -- the create check below is the one that fails.
    node = await get_json(cp, "get node cp")
    declared = node["status"].get("declaredFeatures", [])
    assert kep5474.FEATURE in declared, (
        f"node cp does not declare {kep5474.FEATURE}: {declared}"
    )

    # 2. Both valid modes are accepted, and ReadOnly is a value rather than
    #    an absence.
    for mode in ("ReadOnly", "Writable"):
        rc, out = await dry_run(
            cp, kep5474.pod(f"api-{mode.lower()}", [kep5474.container("t", image, mode=mode)])
        )
        assert rc == 0, f"the API refused mountMode {mode}: {out}"

    # 3. An unknown mode is refused, and the error names the allowed set.
    rc, out = await dry_run(cp, kep5474.pod("api-bogus", [kep5474.container("t", image, mode="Bogus")]))
    assert rc != 0 and "Unsupported value" in out, (
        f"the API accepted an unknown mount mode: {out}"
    )

    # 4. `mountMode` is immutable after creation, per the KEP's Update Flow.
    await kep5474.apply(cp, kep5474.pod("api-immutable", [kep5474.container("t", image, mode="ReadOnly")]))
    rc, out = await cp.execute(
        "kubectl patch pod api-immutable --type merge"
        " --patch '{\"spec\":{\"containers\":[{\"name\":\"t\","
        "\"securityContext\":{\"cgroupOptions\":{\"mountMode\":\"Writable\"}}}]}}'",
        timeout=60,
    )
    assert rc != 0, f"the API allowed changing an existing pod's mount mode: {out}"
    await kep5474.delete(cp, "api-immutable")

    # 5. A Linux-only field: a Windows pod is refused, as the other
    #    securityContext fields are.
    rc, out = await dry_run(
        cp,
        kep5474.pod(
            "api-windows",
            [kep5474.container("t", image, mode="Writable")],
            os_name="windows",
        ),
    )
    assert rc != 0 and "cgroupOptions" in out, (
        f"the API accepted cgroupOptions on a windows pod: {out}"
    )

    # 6. Ephemeral containers cannot set it -- the KEP excludes them because
    #    it would need the descendant limits applied to a running Pod.
    rc, out = await dry_run(
        cp,
        kep5474.pod("api-ephemeral", [kep5474.container("t", image)])
        + "  ephemeralContainers:\n"
        + "\n".join(kep5474.container("debug", image, mode="Writable").splitlines()[1:])
        + "\n",
    )
    assert rc != 0 and ("cgroupOptions" in out or "ephemeral" in out), (
        f"the API accepted cgroupOptions on an ephemeral container: {out}"
    )

    print("[kep-5474] API: modes accepted, bad mode refused, immutable,"
          " linux-only, no ephemeral", flush=True)
