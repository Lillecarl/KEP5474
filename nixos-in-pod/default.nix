# Does a full NixOS run as a Kubernetes pod?
#
# The question is whether kubelet can start a NixOS system the way
# `nixos-container` or Docker would: systemd as PID 1 of the pod, the
# system's whole closure inside the image, no host store to lean on.
#
# The guest is a `boot.isContainer` NixOS wrapped as a self-contained
# docker-archive image.  Nothing in it points into the build sandbox's
# store -- `dockerTools.buildLayeredImage` includes the closure -- so the
# image runs on any node.  That costs about 705 MiB of store and 261 MiB
# of gzipped tarball for the minimal profile, which is the price of the
# route, and it is paid once per node that imports it.
#
# The stack under test -- the KEP PRs, the overlay, the kubeadm node --
# is the parent directory's `infra/`, shared with the cgroup-delegation
# test.
#
# Run it:
#
#   nix run --file . nixos.driver -- --out ./out
{
  pkgs ? import <nixpkgs> { },
  # The vivarium checkout to build against.  `$HOME/Code/nixidae/vivarium`
  # by default, written relative to this file -- two levels up, because
  # this file sits one directory below the repository root.
  vivarium ? ../../nixidae/vivarium,
}:

let
  infra = import ../infra { inherit pkgs vivarium; };
  inherit (infra) pkgs' vivariumLib nodeWith;

  # Pure evaluation -- a flake, a `--pure-eval` run -- reads no
  # environment, so the backend knob answered its default.  The cluster
  # phase announces it.
  pureEval = !builtins.hasAttr "currentSystem" builtins;

  /*
    The system under test.

    `boot.isContainer` is the switch.  It drops the initrd, the
    bootloader and the kernel -- there is a host kernel -- and leaves a
    toplevel whose `/init` is `bootStage2`, which activates the system and
    execs systemd.  `docker-container.nix` is the profile NixOS ships for
    exactly this, but it builds a `make-system-tarball` payload; a
    layered image is the same closure with the entrypoint named in the
    image config instead of guessed by the runtime.

    `/init` and not `/sbin/init`: a container's config names the absolute
    path its runtime execs, and NixOS writes the stage-2 script there.
  */
  nixos = pkgs.nixos (
    { modulesPath, pkgs, config, ... }:
    let
      # Stage 2 `exec`s this as PID 1.  The default is
      # `/run/current-system/systemd/...`, one symlink away from the
      # store; this wrapper prints what it can see and then execs the
      # same binary by its store path, so its output -- and any failure
      # of the exec itself -- reaches the pod log rather than vanishing
      # at "starting systemd...".
      systemdRun = pkgs.writeShellScript "systemd-run-wrapper" ''
        export PATH=${pkgs.coreutils}/bin
        echo "wrapper: pid=$$ args=$*"
        echo "wrapper: /run/current-system -> $(readlink -f /run/current-system 2>&1)"
        # The boot-time /etc is what systemd will read, and it is not the
        # settled one the test sees later.  Resolve every component here,
        # at the moment systemd would, so an ELOOP names its link.
        for p in /etc /etc/systemd /etc/systemd/user.conf; do
          echo "wrapper: stat $p -> $(stat -c '%F %N' $p 2>&1)"
        done
        echo "wrapper: /etc/static -> $(stat -c '%F %N' /etc/static 2>&1)"
        echo "wrapper: open user.conf: $(head -1 /etc/systemd/user.conf 2>&1)"
        # What systemd wants before it will initialise.  A pod has no
        # /dev/console and no /dev/kmsg; systemd-as-PID-1 treats a
        # missing console as fatal before it logs, which is why it went
        # silent.  /dev/stdout is the log the pod shows, so that is what
        # the console should be.
        for d in /dev/console /dev/null /dev/kmsg /run /run/systemd; do
          echo "wrapper: $d -> $(stat -c '%F %a' $d 2>&1)"
        done
        [ -e /dev/console ] || ln -s /dev/stdout /dev/console
        [ -e /dev/kmsg ] || ln -s /dev/null /dev/kmsg
        mkdir -p /run/systemd
        # systemd decides whether it is in a container by reading this,
        # not the `container=` env var alone.  Without it systemd runs
        # full hardware initialisation it cannot finish in a pod.
        printf docker > /run/systemd/container
        echo "wrapper: after: console=$(stat -c '%N' /dev/console 2>&1)"
        echo "wrapper: container=$(cat /run/systemd/container)"
        echo "wrapper: exec systemd"
        exec ${sd} --log-target=console --log-level=debug "$@"
      '';

      # `config.systemd.package`, not the toplevel: the toplevel contains
      # `boot.systemdExecutable`, so naming it here is infinite recursion.
      sd = "${config.systemd.package}/lib/systemd/systemd";
    in
    {
      imports = [ (modulesPath + "/profiles/minimal.nix") ];

      boot.isContainer = true;
      boot.loader.grub.enable = false;
      boot.loader.systemd-boot.enable = false;

      # Point stage 2 at the wrapper.  A separate `boot.systemdExecutable`
      # is the supported knob -- see nixos/modules/system/boot/stage-2.nix.
      boot.systemdExecutable = "${systemdRun}";

      /*
         NixOS mounts nothing: boot.isNspawnContainer drops the special
         file systems -- /proc, /dev, /run and friends -- because the
         container contract is that the runtime provides them, and on
         kubelet it does: /proc, /dev, /dev/pts and /dev/shm are there
         before stage 2 runs, and the KEP's cgroupOptions puts cgroupfs
         at /sys/fs/cgroup.

         /run is the one the runtime leaves a plain directory, and it
         matters: stage 2 writes /run/current-system, and a systemd that
         finds /run not a mount point mounts a tmpfs over it, hiding
         what stage 2 wrote -- and, unprivileged, dies trying. The pod
         supplies /run itself, an emptyDir mounted at boot (see `pod`
         below), which is the same service a systemd-nspawn container
         gets from its own /run tmpfs.

         /run/keys is a ramfs and nothing here holds a key, so it goes.
      */
      boot.isNspawnContainer = true;
      boot.specialFileSystems."/run/keys".enable = false;

      # Stage 2 rebinds /nix/store onto itself to give it `ro,nodev,nosuid`.
      # A pod may not, and the store is the image's own layered copy: it is
      # already exactly what the image says. Asking for no options is what
      # stops the rebind, not asking for weaker ones.
      boot.nixStoreMountOpts = [ ];

      # No kernel and no disk.  NixOS still wants the filesystem named.
      fileSystems."/".device = "/dev/null";
      fileSystems."/".fsType = "ext4";

      system.stateVersion = "26.05";

      # There is no console to get a login on, and a getty that cannot
      # open one is a failed unit on every boot.
      services.getty.enable = false;

      # The system path needs a shell for the proof units to exec through
      # /run/current-system/sw, and su for the exec phase to become the
      # probe user.  Nothing else in this configuration adds packages, so
      # the minimal toplevel has no sh at all.
      environment.systemPackages = [
        pkgs.bashInteractive
        pkgs.util-linux
      ];

      # The proofs.  Each writes a file under /run the test reads back
      # over `kubectl exec`, so a pass is a real unit's real exit and not
      # a runtime's say-so.

      # systemd reached multi-user.target.  The strongest single fact:
      # it is PID 1, it ran a transaction, and a unit of ours completed.
      systemd.services.proof-multi-user = {
        wantedBy = [ "multi-user.target" ];
        # Remain after the oneshot exits, so a later `systemctl
        # is-active` says active rather than inactive (dead) -- the
        # exec phase starts this unit on demand and observes it.
        serviceConfig.Type = "oneshot";
        serviceConfig.RemainAfterExit = true;
        script = ''
          mkdir -p /run/proof
          systemctl is-system-running > /run/proof/system-running || true
          echo "$$" > /run/proof/systemd-pid
          echo ok > /run/proof/multi-user
        '';
      };

      # A user from the NixOS system, not the host: /etc/passwd is the
      # image's, so the uid and shell come from this configuration.
      users.users.probe = {
        isNormalUser = true;
        uid = 4242;
      };

      # The store is the image's own and writable, which is what makes it
      # self-contained rather than a symlink farm over the host's.
      systemd.services.proof-store = {
        wantedBy = [ "multi-user.target" ];
        after = [ "local-fs.target" ];
        serviceConfig.Type = "oneshot";
        script = ''
          test -x /run/current-system/sw/bin/sh
          touch /nix/store/.writable && rm /nix/store/.writable
          mkdir -p /run/proof
          echo ok > /run/proof/store
        '';
      };
    }
  );

  toplevel = nixos.config.system.build.toplevel;

  /*
    The image.

    `contents` is what NixOS itself would put on a running system, so the
    store path and its closure are the layers.  The three extra paths are
    a shell for `kubectl exec` to land in and the coreutils it calls; the
    test never assumes a command exists in the guest beyond what the
    configuration put there.
  */
  image = pkgs.dockerTools.buildLayeredImage {
    name = "nixos-test";
    tag = "latest";
    contents = [
      toplevel
      pkgs.bashInteractive
      pkgs.coreutils
      pkgs.findutils
      pkgs.util-linux
    ];
    /*
      A real /etc, not the toplevel's symlink to the store.

      On a running NixOS, /etc is a mutable directory and activation fills
      it: setup-etc.pl makes /etc/static point at the store's etc, then
      each managed file links to /etc/static/<name>.  The toplevel ships
      /etc as a symlink to the store instead, which is right for a
      *closed* system and wrong here: the indirection then resolves
      through the store copy, and the pod measured

          /etc/static -> /etc/static/static

      -- a symlink to itself.  systemd opens /etc/systemd/user.conf,
      follows it into that loop, and dies with ELOOP and exit 255 before
      it logs a line.

      An empty directory lets activation build /etc the way a real boot
      does.
    */
    extraCommands = ''
      rm -f etc
      mkdir -p etc
    '';
    config = {
      Cmd = [ "/init" ];
      Env = [
        "PATH=${pkgs.bashInteractive}/bin:${pkgs.coreutils}/bin:${pkgs.findutils}/bin"
        "container=podman"
      ];
    };
  };

  # One node is enough: the thing under test is what kubelet does with a
  # pod, and a control plane with no worker still schedules onto itself.
  # The image is 261 MiB gzipped and imports at boot; the default guest is
  # smaller and would run out of disk while unpacking it.  The image is
  # self-contained: `buildLayeredImage` copies the closure into the
  # tarball, so the node needs neither the toplevel nor the image in its
  # own store -- `extraImages` is what builds the tarball and puts it
  # where the load unit reads.
  node = nodeWith {
    cri = "containerd";
    extraImages = [ image ];
    resources = {
      memory = "3072M";
      diskSize = 4096;
    };
    network = {
      name = "nixos-in-pod";
      address = "10.108.0.1/24";
    };
  };

  /*
    The pod, as a Nix value, serialised once.

    Nix owns the shape and `builtins.toJSON` renders it, so the manifest
    cannot drift from the image tag or the mount mode the test is about
    -- both are names here, not strings repeated in Python.  A JSON
    manifest is accepted by `kubectl apply` exactly as YAML is.

    `/init` is the image's `Cmd`, so the pod does not repeat it.

    `mountMode = Writable` is KEP-5474's field and the subject: without
    it the pod gets no `/sys/fs/cgroup` and systemd exits 255.  No
    `privileged`, on purpose -- measured, a privileged container gets
    `/sys` rw and still no cgroupfs mount; the KEP field is the one that
    puts a cgroupfs there.
  */
  pod = {
    apiVersion = "v1";
    kind = "Pod";
    metadata = {
      name = "nixos";
      namespace = "default";
    };
    spec = {
      restartPolicy = "Never";
      nodeName = "cp";
      /*
        User namespace: uid 0 in the pod maps to an unprivileged host
        uid. The systemd manager keeps its uid-0 powers inside the pod
        -- mounts it may still make, the cgroupfs the KEP hands it --
        while holding no privilege on the host. The cost to check: the
        cgroupfs is mounted by the kubelet and owned by host root, and
        kubelet does not chown it into the pod's uid range, so systemd's
        cgroup setup may be denied and the boot degraded.
      */
      hostUsers = false;
      /*
        /run, provided.  It is the one file system neither the runtime
        nor this NixOS brings up (see boot.isNspawnContainer above): the
        runtime leaves a plain directory, and systemd-as-PID-1 mounts a
        tmpfs over it unless it is already a mount point. An emptyDir is
        the Kubernetes-native way to hand the container a mount point
        before it starts -- Memory because it stands in for the tmpfs a
        real boot uses.
      */
      volumes = [
        {
          name = "run";
          emptyDir.medium = "Memory";
        }
      ];
      containers = [
        {
          name = "nixos";
          image = "nixos-test:latest";
          imagePullPolicy = "Never";
          volumeMounts = [
            {
              name = "run";
              mountPath = "/run";
            }
          ];
          securityContext.cgroupOptions.mountMode = "Writable";
        }
      ];
    };
  };
in
{
  inherit (vivariumLib) session;

  # The image, so it can be inspected without a run.
  inherit image toplevel nixos;

  # The manifest, so a reader can see it without a run.
  podManifest = builtins.toJSON pod;

  test = vivariumLib.mkTest (
    { config, ... }:
    {
      name = "nixos-in-pod";

      pythonPath = [
        ./helpers
        ../infra/helpers
      ];

      backend = config.resolved.backend.value;

      knobs = {
        backend = {
          env = "NIXOS_IN_POD_BACKEND";
          default = "uml";
          description = "uml, qemu, or container";
        };
      };

      nodes.cp = node;

      settings = {
        inherit pureEval;
        imageName = "nixos-test:latest";
        nodeName = "cp";
        uid = 4242;
        # The image's own toplevel and systemd, so a diagnose phase names
        # the binaries the init script would exec.
        toplevel = "${toplevel}";
        systemd = "${toplevel}/systemd/lib/systemd/systemd";
        podManifest = builtins.toJSON pod;
      };

      phases = {
        cluster = {
          script = ./phases/cluster.py;
          after = [ "boot" ];
        };
        image = {
          script = ./phases/image.py;
          after = [ "cluster" ];
        };
        diagnose = {
          script = ./phases/diagnose.py;
          after = [ "cluster" ];
        };
        systemd = {
          script = ./phases/systemd.py;
          after = [ "image" ];
        };
        exec = {
          script = ./phases/exec.py;
          after = [ "systemd" ];
        };
        adversarial = {
          script = ./phases/adversarial.py;
          after = [ "exec" ];
        };
        report = {
          script = ./phases/report.py;
          after = [ "adversarial" ];
          always = true;
        };
      };
    }
  );
}
