# What running a full NixOS as a pod requires

Measured on the KEP-5474 stack (kubernetes#137568, containerd#14039),
UML backend, 2026-10.

## Result

**A full NixOS boots as a Kubernetes pod with systemd as PID 1 -- with
no capabilities beyond a default pod's.** The test passes every phase:
boot, cluster, diagnose, image, systemd, exec, report. The systemd
phase asserts PID 1 is systemd and that NixOS units reached
multi-user.target; the exec phase logs in as a user the NixOS
configuration defined, starts a unit with `systemctl` from inside the
pod, and observes it active.

The division of labour that makes it work:

- the runtime mounts `/proc`, `/dev`, `/dev/pts`, `/dev/shm`;
- the KEP field mounts cgroupfs at `/sys/fs/cgroup`;
- the pod supplies `/run` as an `emptyDir` volume (`medium: Memory`);
- NixOS mounts **nothing** (`boot.isNspawnContainer = true`).

No `privileged`, no capability overrides, seccomp at its default. And
with `hostUsers: false` the whole test also passes **under a user
namespace** -- the pod's uid 0 maps to an unprivileged host uid -- at
one measured cost (see "Under a user namespace").

Three faults had to be fixed, one at a time, to get there:

1. `/etc` ELOOP at boot (fixed in the image).
2. `/run` masked by systemd's own tmpfs (fixed by the pod's `emptyDir`
   at `/run`; see Fault 2 and Fault 3).
3. Fault 2 also *was* the "privileged required" mystery (see the last
   section).

## The recipe

The whole integration, on top of a stock `boot.isContainer` NixOS.
Nothing here needs a kernel module, a patched kubelet feature beyond
the KEP, or a capability.

NixOS configuration:

- `boot.isContainer = true` -- the stock container profile.
- `boot.isNspawnContainer = true` -- drops NixOS's mounts of the
  special file systems. The container contract says the runtime brings
  those up, and on kubelet it does. Without this, stage 2 tries to
  mount `/dev`, `/dev/pts`, `/dev/shm`, `/proc` and `/run` itself,
  which a normal pod may not do.
- `boot.specialFileSystems."/run/keys".enable = false` -- the one
  special file system `isNspawnContainer` does not drop; it is a
  ramfs, and nothing in this system holds a key.
- `boot.nixStoreMountOpts = [ ]` -- stops stage 2 rebinding
  `/nix/store` read-only. The store here is the image's own layered
  copy, not a host bind.
- `fileSystems."/".device = "/dev/null"; fsType = "ext4"` -- NixOS
  wants a root filesystem named even when there is no kernel or disk.
- `environment.systemPackages` -- the minimal toplevel ships **no
  shell at all**; bash and util-linux (for `su`) go here.
- `boot.systemdExecutable` -- a wrapper that creates `/dev/console`
  and `/dev/kmsg` (a pod has neither), writes
  `/run/systemd/container` (`docker`), then execs systemd. The image
  env also carries `container=podman`; both files and variable are
  cheap insurance for systemd's container detection.

Image, `dockerTools.buildLayeredImage`:

- `contents` = the toplevel plus bash, coreutils, findutils,
  util-linux. The closure is copied into the layers, so the image is
  self-contained -- no host store bind.
- `extraCommands`: `rm -f etc; mkdir -p etc` -- a real, empty `/etc`
  for activation to populate (Fault 1).
- `Cmd = [ "/init" ]`, the stage-2 script.

Pod:

- `cgroupOptions.mountMode: Writable` -- KEP-5474's field. Without it
  the pod has **no cgroupfs at all** (`/sys` is `ro` and bare), and a
  systemd-as-PID-1 has nothing to put itself or its units in.
- one `emptyDir` volume, `medium: Memory`, mounted at `/run` -- the
  mount point neither the runtime nor NixOS provides (Fault 2).
- `imagePullPolicy: Never`, `restartPolicy: Never`, node pinned.
- Optional, measured: `hostUsers: false` for a user namespace -- uid 0
  only inside the pod, unprivileged on the host, no resource
  controllers (see "Under a user namespace").
- Nothing else: default capabilities, default seccomp, no privileged.

## The KEP-5474 field works as claimed

| pod | `/sys` | `/sys/fs/cgroup` |
| --- | --- | --- |
| default | `ro` | **absent** |
| `cgroupOptions.mountMode: Writable` | `ro` | `rw,nosuid,nodev,noexec,relatime` |

`/proc/self/cgroup` inside a Writable pod is `0::/` -- the container is at
its cgroup namespace root, and a child cgroup can be made: `mkdir
/sys/fs/cgroup/x` succeeds.

A correction to an earlier reading: the KEP does **not** forbid arming
the namespace root. On a clean Writable pod, systemd's own sequence
works:

    mkdir /sys/fs/cgroup/init.scope
    for p in root procs: echo $p > init.scope/cgroup.procs
    echo +memory +cpu +pids > root/cgroup.subtree_control   -> armed

Earlier "refused" readings came from a root left non-empty by a previous
experiment in the same long-lived pod, not from `nsdelegate`. A
straced boot shows systemd doing exactly this and getting **EBUSY**
while its own processes still sit in the root -- a wart it survives; the
boot reached multi-user anyway.

Why cgroupfs is a hard requirement: systemd-as-PID-1 wants a cgroup2
hierarchy from the start -- it makes `init.scope`, moves itself into
it and arms controllers there. The default pod gives it no cgroupfs
mount to do that in (`/sys` is `ro` and bare), which is why the KEP
field, not any configuration, is the load-bearing Kubernetes piece.

## Ruled out

Each of these was measured and is not part of the answer.

- **User namespaces**: measured twice, with opposite verdicts, and
  both stand. While the boot was broken by the `/etc` and `/run`
  faults, `hostUsers: false` did not help -- a namespace fixes UID
  mapping, not those. Once the faults were fixed, the same setting
  passed the full test. See the next section for what it buys and
  what it costs.
- **Container detection**: the wrapper writes
  `/run/systemd/container` and the image env carries `container=`;
  both were in place while systemd still died. Detection was never
  the fault. Kept because it is correct and costs nothing.
- **Privileged**: boots, but only by letting NixOS do the runtime's
  mounting -- the job the container contract assigns elsewhere. The
  `emptyDir` at `/run` replaces it; the pod stays a plain pod.
- **A different cgroup shape**: the KEP denies nothing that matters.
  A clean pod arms its namespace root fine (see the correction above);
  systemd's EBUSY on the same sequence during boot is transient and
  non-fatal.

## Under a user namespace

`spec.hostUsers = false` gives the pod a user namespace: uid 0 inside
maps to an unprivileged host uid. Measured on this stack:

    uid_map:   0  ->  346685440, range 65536
    cgroupfs files, as seen from inside: owner 65534:65534 (nobody)
    mkdir /sys/fs/cgroup/nsprobe            -> OK
    echo +memory > cgroup.subtree_control   -> FAILED (EPERM)

And the full test passes in this mode: systemd is PID 1, the proof
units run, `su` and `systemctl` serve from inside. So:

- **What the namespace buys**: the systemd manager holds uid 0's
  powers *inside the pod* and none on the host. Compromising the pod
  yields an unprivileged host uid, not root.
- **What it costs**: the cgroupfs is mounted by the kubelet and owned
  by host root; kubelet does not chown it into the pod's uid range, so
  from inside the namespace the files belong to nobody. Creating
  cgroups (`mkdir`) still works, arming controllers does not --
  systemd boots, notices, and runs its units **without resource
  controllers**. Per-service `MemoryMax`, `CPUWeight`, slice
  accounting: inert in this mode. Fixing it needs the kubelet (or the
  runtime) to delegate the pod's cgroup directory to the pod's uid
  range -- kernel support exists; the Kubernetes wiring does not.
- **What no namespace can change**: systemd's system-mode manager
  refuses to run as a non-root uid. Some uid 0 -- real, in a plain
  container, or mapped, in a user namespace -- is a requirement of
  systemd itself, not of Kubernetes. Services need no root at all:
  they run as the user their unit names, as always.

## Fault 1: ELOOP on /etc/systemd/user.conf

First fault, found by making `boot.systemdExecutable` a Nix-built
wrapper that prints what it sees and then execs the real binary:

    Failed to open /etc/systemd/user.conf: Too many levels of symbolic links

This is NixOS's `/etc`-as-symlink-farm meeting the layered image: the
toplevel ships `/etc` as a symlink to the store's etc, and inside the
image that resolves to a loop (`/etc/static -> /etc/static/static`).

Fix: `dockerTools.buildLayeredImage`'s `extraCommands` replaces `/etc`
with a real, empty directory. Activation then populates it the way a
real boot does: `setup-etc.pl` creates `/etc/static` and links every
managed file through it. Verified: the wrapper reads
`/etc/systemd/user.conf` and gets `[Manager]`.

Two activation warnings remain and are benign: `could not create symlink
/etc/hostname` and `/etc/hosts` -- kubelet already mounted those files
into the container, so the rename loses. Every other managed file links
through `/etc/static` fine.

## Fault 2: /run masked by systemd's tmpfs

With the ELOOP fixed, systemd still exited 255 in ~1 s with **zero
output**, even at `--log-level=debug`. Finding it took a strace, and the
strace needed a trick: a tracer cannot be PID 1 while tracing PID 1, so
the wrapper runs `strace -f unshare --pid --fork --mount-proc systemd`
-- systemd still PID 1, of a fresh PID namespace -- with the wrapper
sleeping after, so the pod stays up and the trace can be read.

The trace showed something unexpected: **systemd was fine**. journald,
udevd, dbus-broker and logind all ran. `nsenter` into systemd's mount
namespace showed `/run/proof/multi-user: ok` -- the NixOS boot had
reached multi-user.target.

The 255 was the *unshare crutch*, not the disease: the phase asserts
`/proc/1/comm` is systemd, and with the wrapper as pod PID 1 the comm is
the wrapper's store-hash basename. But the run also exposed the real
fault:

- stage 2 wrote `/run/current-system` onto the rootfs;
- systemd, finding `/run` not a mount point, mounted a fresh tmpfs over
  it -- the way it does on every boot;
- the symlink vanished under it. Units resolving
  `/run/current-system/sw/bin/sh` failed, the store proof failed, and
  the pod was broken in ways that looked like a systemd problem.

`boot.isNspawnContainer = true` had been set to make stage 2 skip
re-mounting what the runtime mounted -- correct in spirit, wrong in
effect: kubelet does not mount `/run`, so with the skip in place
systemd did the mounting itself, and that is the fault above.

The diagnostic technique, for reuse:

- `strace -f unshare --pid --fork --mount-proc systemd` keeps systemd
  as PID 1 of a fresh PID namespace while a tracer watches. The
  `unshare` itself needs CAP_SYS_ADMIN, so this only works in a pod
  that has it; without, `unshare` fails EPERM first.
- The wrapper sleeps after systemd exits, keeping the pod alive so the
  trace and log survive the container.
- `-e status=failed` writes only failed syscalls to the trace -- the
  denial stands out from the boot noise.
- journald writes `/var/log/journal` on the rootfs, so the boot's own
  log stays readable over `kubectl exec` even when `/run` is masked
  and the manager's sockets are not.
- `nsenter -t <pid> -m` reads another process's mount namespace --
  how the masked `/run/proof` was reached.

## Fault 3: the "privileged required" mystery was Fault 2

With `boot.isNspawnContainer` removed instead, stage 2 mounted the
special file systems itself -- which needs CAP_SYS_ADMIN, so the pod
went privileged, and booting worked. That fixed the symptom while
misreading the mechanism: the whole story was `/run`.

The correct shape is the one a container contract implies: **Kubernetes
provides every mount; NixOS provides none.** The pod spec gains

    volumes:       [ { name: run, emptyDir: { medium: Memory } } ]
    volumeMounts:  [ { name: run, mountPath: /run } ]

so `/run` is a tmpfs mount point before the container starts, exactly
as systemd-nspawn provides one. systemd then finds `/run` already
mounted and skips its own mount; stage 2's `/run/current-system` lands
on it and survives. With `boot.isNspawnContainer = true` restored and
**no capabilities at all** added, the boot reaches multi-user and the
whole test passes.

The historical silent-255 unprivileged death is explained by the same
mechanism: systemd-as-PID-1, finding `/run` a plain directory, mounts a
tmpfs there early -- EPERM without CAP_SYS_ADMIN -- and aborts before
any log target is configured. Give it the mount point and the death
goes away; no cap bisect needed.

## Test-infrastructure notes (all bit once)

- `kubectl exec` sessions run under the image `Env`'s PATH, not the
  NixOS system path. `su` and `systemctl` must be called by absolute
  path (`/run/current-system/sw/bin/...`).
- The minimal toplevel has **no shell at all**:
  `environment.systemPackages = [ pkgs.bashInteractive pkgs.util-linux ]`.
- Proof units that both write `/run/proof` must each `mkdir -p` it:
  they are wanted by multi-user.target in parallel, and the race is
  real (one run lost it).
- A oneshot proof unit needs `RemainAfterExit = true` for a later
  `systemctl is-active` to say `active`.
- `nixos-containers.nix` and nspawn both create a real `/etc` -- the
  image fix above agrees with upstream practice.
- `systemd.extraConfig` no longer exists; `systemd.settings.Manager` is
  the knob.
- `/proc/1/comm` for a shell script is the script's basename, which for
  a `writeShellScript` is its store-hash prefix -- random-looking.
- `su` without a shadow database prints "Authentication service cannot
  retrieve authentication info (Ignored)" and still succeeds; the exec
  phase asserts on the `whoami` line, not the whole stream.

## What is already true and worth keeping

- The image builds and runs: `boot.isContainer` wrapped by
  `dockerTools.buildLayeredImage`, 261 MiB gzipped, self-contained.
- `/run/keys` disabled (unconditional ramfs, nothing holds a key).
- The node accepts the pod's `cgroupOptions` once kubelet is the PR's.
- `boot.nixStoreMountOpts = [ ]`: stage 2 does not rebind the store
  read-only, so the image's own layered store stays writable -- the
  store proof asserts this.
- The pod is a plain pod: default capabilities, default seccomp, one
  `emptyDir` at `/run`, and the KEP's `cgroupOptions`. The container
  contract plus one volume is the whole integration.
