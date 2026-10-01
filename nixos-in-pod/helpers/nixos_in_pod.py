"""What the nixos-in-pod phases share.

On the `pythonPath` of the test, so every phase imports it.  The pod and
exec plumbing is the shared `kube` module -- `infra/helpers`, on the
pythonPath of both tests -- and is re-exported here, so a phase imports
one name.  What is specific to this test lives here: the proof paths the
guest's units write, and the Nix-built pod manifest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

# The proofs the guest's own units write.  A pass is the unit's exit, not
# a phase's word for it.
PROOF_DIR = "/run/proof"


def pod_manifest(vms: Machines) -> str:
    """
    The pod, built in Nix and rendered there.

    Nix owns the manifest -- see `pod` in this test's default.nix -- so
    the image tag and the KEP's `cgroupOptions.mountMode` are names in
    one place, not strings repeated here.  A JSON manifest is what
    `kubectl apply` takes; Nix's `builtins.toJSON` is the writer.
    """
    return vms.settings["podManifest"]


def image_of(vms: Machines) -> str:
    return vms.settings["imageName"]
