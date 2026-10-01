# KEP-5474, tested

Tests for [KEP-5474][kep], *Enable Writable cgroups for unprivileged
containers*, on the Kubernetes PRs that implement it, with two questions:

1. **Does the feature do what the KEP claims?** A pod asks
   `securityContext.cgroupOptions.mountMode: Writable`; the suite checks
   that its `/sys/fs/cgroup` is a writable cgroup2 hierarchy, that the
   default and `ReadOnly` stay read-only, that the mode is per container,
   and where the writable mount's power stops.
2. **Can a full NixOS boot as a pod?** (`nixos-in-pod/`) systemd as PID 1
   of the pod, the system's closure in the image, the KEP field for its
   cgroupfs. This is the workload the field exists for.

[kep]: https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5474-writable-cgroups

## The stack under test

The feature is alpha and unmerged, so nothing in nixpkgs has it. Three
PRs are pinned by commit and built from source (`infra/default.nix`):

| PR | what it adds |
| --- | --- |
| [kubernetes/kubernetes#137568][kpr] | Pod API field, CRI field, validation, kubelet, scheduler |
| [containerd/containerd#14039][cpr] | `cgroup_mount_mode` in containerd's CRI |
| [cri-o/cri-o#10384][opr] | the same for CRI-O |

[kpr]: https://github.com/kubernetes/kubernetes/pull/137568
[cpr]: https://github.com/containerd/containerd/pull/14039
[opr]: https://github.com/cri-o/cri-o/pull/10384

The nodes are UML guests driven by [vivarium][viv]: one kubeadm control
plane that runs its own pods. Both containerd and CRI-O are covered.

[viv]: https://github.com/Lillecarl/vivarium

## What the suite covers

One node, eleven phases. Each phase's assertion line is printed as it
passes and lands in the artifacts:

| phase | what it proves |
| --- | --- |
| `cluster` | the node is up, declares `CgroupOptions`, and its cgroup2 hierarchy has `nsdelegate` |
| `runtime` | Writable makes `/sys/fs/cgroup` writable in the container; default and `ReadOnly` do not; the mode is per container; the mount is real cgroup2 |
| `api` | every mode is accepted, a bad mode is refused, the field is immutable, Linux-only, not settable on ephemeral containers |
| `runtime-behaviour` | what a writable hierarchy allows: creating children, the kubelet's own limits visible |
| `annotation` | CRI-O's pre-KEP route: the `cgroup2-mount-hierarchy-rw.crio.io` pod annotation makes the hierarchy writable; the same pod without it stays read-only |
| `limits` | a container cannot exhaust the node: descendants stop at the kubelet's 250, nesting at 50, per pod |
| `security` | nsdelegate bounds the writable mount: a container cannot touch its own limits, can delegate to a child it created, and the cgroup namespace is the boundary |
| `pss` | Pod Security Standards: `restricted` still names `cgroupOptions`+`Writable`; `ReadOnly` and unset are not refused by it; `baseline` admits it |
| `edges` | the empty object is a no-op; init containers carry the field; `ReadOnly` is refused for a privileged container at admission |
| `report` | the evidence dump (below), written even when the run failed |

The second test (`nixos-in-pod/`) is seven phases: `cluster`, `diagnose`
(what the runtime hands a container), `image` (the KEP is advertised and
the image imported), `systemd` (PID 1 is systemd and a NixOS unit reached
multi-user.target), `exec` (login as a NixOS user, `systemctl` serves
from inside), `report`.

## Running it

Through the flake, from anywhere:

    nix run github:Lillecarl/KEP5474#kep-5474          # containerd
    nix run github:Lillecarl/KEP5474#nixos-in-pod      # the NixOS test

From a checkout, the same thing without a flake:

    nix run --file . test.driver -- --out ./out
    nix run --file . nixos.test.driver -- --out ./out

The knobs are read from the environment when the test is *evaluated*
-- pure evaluation sees none, so the flake path needs `--impure` to
override one:

    KEP5474_CRI=crio nix run --impure github:Lillecarl/KEP5474#kep-5474
    KEP5474_CRI=crio nix run --impure --file . test.driver -- --out ./out

`--break-on-failure` (driver flag) pauses the run with the guests up;
`nix run --file . test.driver -- --help` lists the rest.

## The evidence

A green assertion is a claim; the artifacts are the proof. Everything a
run touches lands under `--out`'s `artifacts/`:

    artifacts/
      evidence/evidence.jsonl   every manifest applied and every
                                command run in a container, each with
                                its exit code and verbatim output,
                                one JSON object per line, in order
      report/                   the cluster state at the end of the run:
                                  pods.txt, pods-full.txt   what ran, and the realised specs
                                  nodes.txt, node.txt       node and its YAML
                                  declared.txt              the runtime's advertised features
                                  cgroup-limits.txt         the pod cgroup tree the kubelet wrote
                                  host.txt                  kernel, cgroup2 superblock options, runtime version
                                  summary.txt               versions and the evidence pointer
      cp/                       the guest's own console log

`evidence.jsonl` is the audit trail: the `kind` field says `apply`
(the manifest exactly as `kubectl apply` received it), `exec` (the
script, the pod, the container), and the matching `-result` line carries
the rc and the output. Replaying any line by hand reproduces the
assertion it backs.

## Honest caveats

- The PRs are drafts against `master`-era Kubernetes (1.39.0-alpha.0);
  the suite pins them by commit, and a rebase can move semantics.
- The backend is UML: a real cloud kernel will differ in small ways
  (eBPF, SELinux, cgroup1 vestiges), though the cgroup v2 mechanics are
  kernel-standard.
- The suite does not test kubelet restarts, node reboots, or the
  `memory_recursiveprot` interaction under load; those are open.
