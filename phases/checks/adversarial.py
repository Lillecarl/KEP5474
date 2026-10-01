#!/usr/bin/env python3
"""The battery: the delegation model attacked from inside.

A writable container runs `cgroupprobes.battery`: every write the model
denies asserted denied with its value left alone, the v1 escape surface
asserted invisible, the namespace root asserted pinned, and the
delegation use case -- children, arm, limits, migrate -- asserted to
work.  See infra/helpers/cgroupprobes.py for the probes and their
rationale.

The battery runs against the KEP field pod and -- on CRI-O, which has
both routes -- against the pre-KEP annotation pod too: with nsdelegate
on the hierarchy both must be equally denied, because what denies is
the kernel, not the runtime.
"""

from __future__ import annotations

import cgroupprobes
import kep5474
from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = kep5474.image_of(vms)

    await kep5474.apply(
        cp,
        kep5474.pod(
            "pen-field",
            [kep5474.container("t", image, mode="Writable", memory="256M")],
        ),
    )
    await kep5474.running(cp, "pen-field")
    print("[kep-5474] battery against the KEP field pod", flush=True)
    await cgroupprobes.battery(cp, "pen-field")
    await kep5474.delete(cp, "pen-field")

    if vms.settings["cri"] == "crio":
        await kep5474.apply(
            cp,
            kep5474.pod(
                "pen-anno",
                [kep5474.container("t", image, memory="256M")],
                annotations={"cgroup2-mount-hierarchy-rw.crio.io": "true"},
            ),
        )
        await kep5474.running(cp, "pen-anno")
        print("[kep-5474] battery against the annotation pod", flush=True)
        await cgroupprobes.battery(cp, "pen-anno")
        await kep5474.delete(cp, "pen-anno")

    print("[kep-5474] adversarial: the battery held", flush=True)
