"""The pod plumbing both tests' phases share.

On the `pythonPath` of both tests -- `infra/helpers` for each -- so the
test-specific helpers (`kep5474`, `nixos_in_pod`) can re-export it and
the phases keep importing one module.

Everything that touches the cluster is recorded: every manifest applied
and every command's script, exit code and verbatim output land in the
run's artifacts as JSONL (`evidence/evidence.jsonl`), so a reader who
was not here can audit exactly what was done and what the cluster
answered.  `Machine.artifacts` is a host directory the phase process can
write directly; each phase is its own process, so append mode is the
shared ledger.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

from vivarium_runner import Machine
from vivarium_runner.cluster import get_json, wait_for_pods

if TYPE_CHECKING:
    from pathlib import Path

    from vivarium_runner import Machines

NAMESPACE = "default"


def _record(cp: Machine, kind: str, **fields: object) -> None:
    """One evidence line, into the run's artifacts if there are any."""
    if cp.artifacts is None:
        return
    directory: Path = cp.artifacts / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "time": time.strftime("%H:%M:%S"),
        # pytest names the running test after the phase script; on a
        # `ctl exec` probe there is none.
        "phase": os.environ.get("PYTEST_CURRENT_TEST", "").replace(" (call)", ""),
        "kind": kind,
        **fields,
    }
    with (directory / "evidence.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")


async def apply(cp: Machine, manifest: str) -> None:
    _record(cp, "apply", manifest=manifest)
    rc, out = await cp.execute(
        f"cat <<'EOF' | kubectl apply --filename -\n{manifest}\nEOF", timeout=180
    )
    _record(cp, "apply-result", rc=rc, output=out)
    if rc != 0:
        raise AssertionError(f"kubectl apply failed (rc={rc}):\n{out}")


async def running(cp: Machine, name: str, namespace: str = NAMESPACE) -> None:
    await wait_for_pods(
        cp, f"--field-selector metadata.name={name}", namespace=namespace
    )


async def delete(cp: Machine, name: str, namespace: str = NAMESPACE) -> None:
    _record(cp, "delete", name=name)
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
    _record(cp, "exec", pod=name, container=container, script=script)
    rc, out = await cp.execute(
        f"kubectl exec {name} --namespace {namespace}{target}"
        f" -- sh -c {shell_quote(script)}",
        timeout=180,
    )
    _record(cp, "exec-result", rc=rc, output=out)
    return rc, out


def shell_quote(text: str) -> str:
    """Single-quote for the guest's sh; no shell features of ours."""
    return "'" + text.replace("'", "'\\''") + "'"


async def pod_object(cp: Machine, name: str, namespace: str = NAMESPACE) -> dict:
    return await get_json(cp, f"get pod {name} --namespace {namespace}")
