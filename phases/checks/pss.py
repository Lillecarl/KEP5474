#!/usr/bin/env python3
"""Pod Security Standards: the Restricted profile blocks Writable.

The KEP adds a `cgroupOptions` check to the Restricted profile from policy
version 1.38. This asks the admission path itself -- a namespace enforcing
`restricted` -- rather than calling the policy package, so it is the same
answer a cluster gives.

The Pods are deliberately minimal: the Restricted profile refuses them for
several reasons besides `cgroupOptions`, and the check is that the
`cgroupOptions` reason is present for `Writable` and absent otherwise. A
Pod written to satisfy every *other* restriction would be the only way to
make the profile's answer a single line, and it would test the profile's
other checks too.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machine, Machines


async def admitted(cp: Machine, manifest: str) -> tuple[bool, str]:
    rc, out = await cp.execute(
        f"cat <<'EOF' | kubectl apply --dry-run=server --filename -\n{manifest}\nEOF",
        timeout=60,
    )
    return rc == 0, out


def in_restricted(name: str, image: str, mode: str | None) -> str:
    return kep5474.pod(
        name, [kep5474.container("t", image, mode=mode)], namespace="restricted"
    )


async def test(vms: Machines) -> None:
    cp = vms.cp
    image = kep5474.image_of(vms)

    # Writable is refused, and the refusal names the field and the mode.
    ok, out = await admitted(cp, in_restricted("pss-writable", image, "Writable"))
    assert not ok, f"the Restricted profile admitted a Writable pod: {out}"
    assert "cgroupOptions" in out, f"the PSS refusal does not name the field: {out}"
    assert "Writable" in out, f"the PSS refusal does not name the mode: {out}"

    # ReadOnly and unset are refusable for other reasons, but *not* for
    # the cgroupOptions check -- which is what makes this a check on the
    # mode and not on the struct.
    for mode, name in (("ReadOnly", "pss-readonly"), (None, "pss-unset")):
        _, out = await admitted(cp, in_restricted(name, image, mode))
        assert "cgroupOptions" not in out, (
            f"the Restricted profile rejected {name} for cgroupOptions: {out}"
        )

    # The baseline profile admits Writable: the profile is the difference,
    # not the field.
    ok, out = await admitted(
        cp,
        kep5474.pod(
            "pss-baseline",
            [kep5474.container("t", image, mode="Writable")],
            namespace="baseline",
        ),
    )
    assert ok, f"a baseline namespace refused a Writable pod: {out}"

    print(
        "[kep-5474] PSS: restricted names cgroupOptions+Writable; ReadOnly and"
        " unset are not refused for it; baseline admits it",
        flush=True,
    )
