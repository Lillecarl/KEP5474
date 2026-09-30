#!/usr/bin/env python3
"""The edges the KEP states: omitted mode, init containers, privileged.

An omitted `mountMode`, including an empty `cgroupOptions` object, uses the
runtime default and requires nothing of the node. An init container can set
it. And `ReadOnly` with `privileged` is refused -- the KEP describes this
as a container-create failure, but the API now rejects it at admission, so
the check is the earlier and stronger guarantee.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machine, Machines


async def apply(cp: Machine, manifest: str) -> tuple[int, str]:
    return await cp.execute(
        f"cat <<'EOF' | kubectl apply --dry-run=server --filename -\n{manifest}\nEOF",
        timeout=60,
    )


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = kep5474.image_of(vms)

    # An empty cgroupOptions object is a no-op, not a requirement.
    empty = kep5474.pod("edge-empty", [kep5474.container("t", image)]).replace(
        '    command: ["/bin/sh", "-c", "sleep 3600"]',
        '    command: ["/bin/sh", "-c", "sleep 3600"]\n'
        "    securityContext:\n      cgroupOptions: {}",
    )
    rc, out = await apply(cp, empty)
    assert rc == 0, f"an empty cgroupOptions object was refused: {out}"

    # An init container carries the field, and the Pod is admitted:
    # `InferForScheduling` walks initContainers too.
    rc, out = await apply(
        cp,
        kep5474.pod(
            "edge-init",
            [kep5474.container("t", image, mode="Writable")],
            init=[kep5474.container("setup", image, mode="Writable", command="true")],
        ),
    )
    assert rc == 0, f"an init container with Writable was refused: {out}"

    # ReadOnly with privileged is refused, naming both fields.
    rc, out = await apply(
        cp,
        kep5474.pod(
            "edge-priv",
            [kep5474.container("t", image, mode="ReadOnly", privileged=True)],
        ),
    )
    assert rc != 0 and "privileged" in out and "ReadOnly" in out, (
        f"the API accepted ReadOnly with privileged: {out}"
    )

    # Writable with privileged is allowed -- the restriction is the
    # read-only mode on a container that already has everything.
    rc, out = await apply(
        cp,
        kep5474.pod(
            "edge-priv-writable",
            [kep5474.container("t", image, mode="Writable", privileged=True)],
        ),
    )
    assert rc == 0, f"the API refused Writable with privileged: {out}"

    print(
        "[kep-5474] edges: empty object is a no-op, init container carries it,"
        " ReadOnly+privileged refused at admission",
        flush=True,
    )
