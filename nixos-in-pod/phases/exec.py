#!/usr/bin/env python3
"""The system is usable, not just up.

A systemd that reaches multi-user and serves nothing is a container that
hosted a correction.  This drives the system the way it is meant to be
used: a login as a user the image defined, its own store, and a service
of ours that starts and is observed running.
"""

from __future__ import annotations

import nixos_in_pod as h
from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    cp = vms.cp
    uid = vms.settings["uid"]

    # A user from the image, by uid.  NixOS's /etc/passwd is the pod's,
    # so the name resolves and the shell is the store's.
    images_uid = await h.exec_sh(cp, "nixos", f"id -u probe")
    assert images_uid[1].strip() == str(uid), (
        f"expected uid {uid}, got {images_uid[1].strip()!r};"
        " the pod is not using the image's /etc/passwd"
    )
    print(f"[nixos-in-pod] probe uid {uid}: present in the image", flush=True)

    # A command from one of the closure's own store paths, run as that
    # user.  `su` is NixOS's, and it works because PAM and the store are
    # both the image's.  By absolute path: `kubectl exec` sessions run
    # under the image Env's PATH, which is not the system path, and the
    # point is to use NixOS's su, not whatever the runtime offers.
    code, out = await h.exec_sh(
        cp, "nixos",
        f"/run/current-system/sw/bin/su probe -s /run/current-system/sw/bin/sh -c 'whoami'",
    )
    # su may also print a PAM warning on stderr -- the container has no
    # shadow database for it to consult -- which `kubectl exec` folds
    # into the same stream. The whoami line is what the assertion is
    # about; it is the first line of output.
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0 and lines and lines[0] == "probe", (
        f"could not become probe: rc={code} {out!r}"
    )
    print("[nixos-in-pod] login probe: works", flush=True)

    # Switch the running system's store for its own path.  `nix-store` is
    # in the closure; a self-contained image resolves its own references
    # rather than the build sandbox's.
    code, out = await h.exec_sh(
        cp, "nixos", "readlink -f /run/current-system"
    )
    assert code == 0 and out.startswith("/nix/store/"), (
        f"/run/current-system is not a store path: {out!r}"
    )
    print(f"[nixos-in-pod] current-system: {out.strip()}", flush=True)

    # A unit of the image, started on demand.  `systemctl start` from
    # inside the pod, observed by `is-active`: the init system answers,
    # which is the property a supervisor has and a dead process does not.
    # By absolute path, like su above -- the image Env's PATH is not the
    # system path.
    code, out = await h.exec_sh(
        cp,
        "nixos",
        "/run/current-system/sw/bin/systemctl start proof-multi-user.service"
        " && /run/current-system/sw/bin/systemctl is-active"
        " proof-multi-user.service",
    )
    assert code == 0 and out.strip() == "active", (
        f"systemctl could not start a unit: rc={code} {out!r}"
    )
    print("[nixos-in-pod] systemctl start/is-active: works", flush=True)
