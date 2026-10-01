#!/usr/bin/env python3
"""Bring up the kubeadm node the checks run against.

The cluster is built once and every check runs against it, in order of what
breaks: the two CRIs first, then the API and admission surface, then the
runtime behaviour, then the Pod Security Standards, and finally the
privileged-interaction edges. `report.py` runs whatever happened.
"""

from __future__ import annotations

import kep5474
from vivarium_runner import Machines
from vivarium_runner.cluster import KUBE_PROXY, bring_up, get_json


async def test(vms: Machines) -> None:
    print(
        f"[kep-5474] kubernetes {vms.settings['kubernetesVersion']},"
        f" cri {vms.settings['cri']}, busybox {vms.settings['workloadImage']}",
        flush=True,
    )
    if vms.settings.get("pureEval"):
        print(
            "[kep-5474] pure evaluation: the environment was not read;"
            " an env knob such as KEP5474_CRI needs --impure",
            flush=True,
        )
    cp = await bring_up(vms, addons=(KUBE_PROXY,))

    # The host prerequisite the KEP names, asserted to match the run's
    # setting.  `vms.shared` is one phase's, not the run's.
    #
    # nsdelegate is a superblock option: it appears in /proc/self/mountinfo's
    # super options -- the field after the `-` -- and never in /proc/mounts,
    # which lists mount options only.  The containerd KEP path passes it in
    # the mount data, so there it shows up in both; a mount made without it
    # shows it in neither.  Read the super options, and if a stray remount
    # has cleared the flag -- a remount without the option resets it -- put
    # it back before asserting: the kubelet's own cgroup setup does exactly
    # such a remount, measured.  A run with the knob off asserts the flag
    # stays off, which is what makes the divergence run's premise hold.
    expected = vms.settings["nsdelegate"]
    info = (await cp.succeed("grep cgroup2 /proc/self/mountinfo")).strip()
    if expected and "nsdelegate" not in info.split("-")[-1]:
        print("[kep-5474] nsdelegate missing from the cgroup2 superblock, remounting", flush=True)
        await cp.succeed("mount -o remount,nsdelegate /sys/fs/cgroup")
        info = (await cp.succeed("grep cgroup2 /proc/self/mountinfo")).strip()
    have = "nsdelegate" in info.split("-")[-1]
    assert have == expected, (
        f"nsdelegate is {have} on the cgroup2 superblock, expected {expected}: {info}"
    )
    node = await get_json(cp, "get node cp")
    declared = node["status"].get("declaredFeatures", [])
    print(f"[kep-5474] node declares {declared}", flush=True)
    print(f"[kep-5474] {info}", flush=True)

    # Namespaces the checks use, made once.
    await kep5474.ensure_namespace(cp, "restricted", level="restricted")
    await kep5474.ensure_namespace(cp, "baseline", level="baseline")
