"""The pod plumbing both tests' phases share.

On the `pythonPath` of both tests -- `infra/helpers` for each -- so the
test-specific helpers (`kep5474`, `nixos_in_pod`) can re-export it and
the phases keep importing one module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vivarium_runner import Machine
from vivarium_runner.cluster import get_json, wait_for_pods

if TYPE_CHECKING:
    from vivarium_runner import Machines

NAMESPACE = "default"


async def apply(cp: Machine, manifest: str) -> None:
    await cp.succeed(
        f"cat <<'EOF' | kubectl apply --filename -\n{manifest}\nEOF", timeout=180
    )


async def running(cp: Machine, name: str, namespace: str = NAMESPACE) -> None:
    await wait_for_pods(
        cp, f"--field-selector metadata.name={name}", namespace=namespace
    )


async def delete(cp: Machine, name: str, namespace: str = NAMESPACE) -> None:
    await cp.execute(
        f"kubectl delete pod {name} --namespace {namespace}"
        " --ignore-not-found --wait=false",
        timeout=60,
    )


async def exec_sh(
    cp: Machine,
    name: str,
    script: str,
    *,
    container: str | None = None,
    namespace: str = NAMESPACE,
) -> tuple[int, str]:
    target = f" -c {container}" if container is not None else ""
    return await cp.execute(
        f"kubectl exec {name} --namespace {namespace}{target}"
        f" -- sh -c {shell_quote(script)}",
        timeout=180,
    )


def shell_quote(text: str) -> str:
    """Single-quote for the guest's sh; no shell features of ours."""
    return "'" + text.replace("'", "'\\''") + "'"


async def pod_object(cp: Machine, name: str, namespace: str = NAMESPACE) -> dict:
    return await get_json(cp, f"get pod {name} --namespace {namespace}")
