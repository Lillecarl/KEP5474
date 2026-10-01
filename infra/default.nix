# The KEP-5474 stack both tests run on: the PRs under test, the overlay
# that puts them on the node, and the kubeadm node itself.
#
#   kubernetes/kubernetes#137568     Pod API, CRI API, validation, kubelet,
#                                    scheduler, node e2e
#   containerd/containerd#14039      CRI `cgroup_mount_mode` in the runtime
#   cri-o/cri-o#10384                the same for CRI-O
#
# Each is pinned by the commit its PR pointed at when this was written.  A
# branch moves, and a test that silently follows it tests something else
# next week.
#
# The tests import this and add their own: the cgroup-delegation checks in
# the parent directory, the NixOS-as-a-pod test in `nixos-in-pod/`.
{
  pkgs ? import <nixpkgs> { },
  # The vivarium checkout to build against.  `$HOME/Code/nixidae/vivarium`
  # by default, written relative to this file.
  vivarium ? ../nixidae/vivarium,
  /*
    The sources under test.  The defaults are the PR commits the tests
    were written against; each can be repointed without editing anything
    else:

    - another commit: `kubernetesSrc = pkgs.fetchFromGitHub { ... rev = ...; }`
    - a local checkout: `kubernetesSrc = /home/me/kubernetes` -- the tree
      is taken as it is, dirt included
    - a flake input: pass the input itself (`kubernetes-src`), which
      carries both the tree and its locked `rev`

    A local checkout has no `.rev`; the version strings below fall back
    to `local` for it.
  */
  kubernetesSrc ? pkgs.fetchFromGitHub {
    owner = "kubernetes";
    repo = "kubernetes";
    rev = "7811d7b62963ce2b8a8c1e03dd7fd70251107092";
    hash = "sha256-wn9y3JjLJH88nCr35fMHjYNguA+XczvD93s22Dh+rpg=";
  },
  containerdSrc ? pkgs.fetchFromGitHub {
    owner = "containerd";
    repo = "containerd";
    rev = "1ad78b552ca716aa3d2dff09005fcf149f3722c9";
    hash = "sha256-us9WZSSUuxzXHjtJ13iDazRQqsLfkShuN4wpBTHw5XU=";
  },
  criOSrc ? pkgs.fetchFromGitHub {
    owner = "cri-o";
    repo = "cri-o";
    rev = "c297e202b5400538dcb034fd0dcd398be90b420d";
    hash = "sha256-pDZ+raxt4k7TrtevAC5RXt8H8k72/ABhATvFJGbMCKs=";
  },
}:

let
  lib = pkgs.lib;

  # ── the PRs ────────────────────────────────────────────────────────

  # nixpkgs' Kubernetes is 1.37.  The PR is against master, which needs Go
  # 1.27 -- nixpkgs' `go` is 1.26, and `buildGoLatestModule` is 1.27.
  #
  # KUBE_GIT_VERSION is set because a fetched archive has no `.git`, and
  # kubeadm reads its own version to choose the images it asks for.  It
  # has to agree with `imageTags` in a node and with the tag
  # k8s-images.nix builds.
  kubernetes = (pkgs.kubernetes.override {
    buildGoModule = pkgs.buildGoLatestModule;
  }).overrideAttrs (old: {
    version = "1.39.0-alpha.0";
    src = kubernetesSrc;
    env = (old.env or { }) // {
      KUBE_GIT_VERSION = "v1.39.0-alpha.0";
      KUBE_GIT_TREE_STATE = "clean";
    };
  });

  # nixpkgs' package is `buildGoModule rec`, so `makeFlags` is evaluated
  # against the *original* `version` and `src`; overriding only those two
  # builds the new source under the old version string.  The flags are
  # mapped too, or the binary reports v2.3.4 and nothing can tell the two
  # apart.
  containerd = pkgs.containerd.overrideAttrs (old: {
    version = "2.3.5";
    src = containerdSrc;
    makeFlags = map (
      flag:
      if lib.hasPrefix "VERSION=" flag then
        "VERSION=v2.3.5"
      else if lib.hasPrefix "REVISION=" flag then
        "REVISION=${containerdSrc.rev or "local"}"
      else
        flag
    ) old.makeFlags;
  });

  # The wrapper puts runc, conmon and iptables on CRI-O's PATH; it is what
  # the NixOS module installs, so the wrapper is what has to change.  The
  # module's own default would rebuild the wrapper around the *unwrapped*
  # attribute, so the overlay has to name the wrapped one under the key the
  # module reads.
  crio = pkgs."cri-o".override {
    cri-o-unwrapped = pkgs."cri-o-unwrapped".overrideAttrs (old: {
      version = "1.38.0";
      src = criOSrc;
    });
  };

  # The overlay reaches every copy the node uses -- the daemon, the
  # runtimes the CRI plugin hands to kubelet, and the kubelet itself all
  # come from `pkgs`.  `appendOverlays` is how vivarium sets
  # `nixpkgs.pkgs` for the guest, so an override of the attribute alone
  # would reach the host and not the node.
  overlay =
    _final: _prev:
    {
      inherit
        kubernetes
        containerd
        ;
      # The wrapper puts runc, conmon and iptables on CRI-O's PATH, and it
      # is what the NixOS module installs.  The module's own default would
      # rebuild the wrapper around `pkgs.cri-o-unwrapped`, so the overlay
      # names the wrapped one under the key the module reads.
      "cri-o" = pkgs."cri-o".override {
        cri-o-unwrapped = pkgs."cri-o-unwrapped".overrideAttrs (old: {
          version = "1.38.0";
          src = criOSrc;
        });
      };
    };

  pkgs' = pkgs.appendOverlays [ overlay ];

  vivariumLib = import (vivarium + "/lib.nix") { pkgs = pkgs'; };

  /*
    The kubeadm node both tests build their pods on.

    One node: it runs the control plane and the pods, so no worker and no
    scheduler subtlety.  The feature is a property of the node the
    container runs on, whichever that is.

    The CRI is fixed here and not read from a run's resolved knob.  A
    guest is evaluated once and shared by the test and both of its
    backend variants, so it cannot read a value that differs per run --
    the same reason k8s-images.nix writes its tags down.  `mkTest` calls
    this with the resolved CRI.

    *extraImages* are the images a node imports at boot; *resources* and
    *network* are the guest's vivarium settings, which differ per test --
    the NixOS image is 261 MiB gzipped and imports at boot, so its node
    has more disk and memory and its own segment.
  */
  nodeWith =
    {
      cri ? "containerd",
      extraImages ? [ ],
      resources ? {
        memory = "2560M";
        diskSize = 2048;
      },
      network ? {
        name = "kep5474";
        address = "10.107.0.1/24";
      },
    }:
    { lib, pkgs, ... }:
    {
      imports = [ (vivarium + "/modules/k8s.nix") ];
      services.vivarium-k8s = {
        enable = true;
        role = "control-plane";
        inherit cri;
        # The gate on kube-apiserver and kubelet.  NodeDeclaredFeatures,
        # which the feature depends on, is GA and on by default.
        featureGates.CgroupOptions = true;
        # The PR's kubeadm names newer etcd and CoreDNS tags than 1.37's.
        # The images are looked up by tag, so they have to match.
        imageTags = {
          etcd = "3.7.1-0";
          coredns = "v1.14.7";
        };
        # Nothing here resolves a name, and a sandboxed CoreDNS spends the
        # run timing out against an upstream it cannot reach.
        skipAddons = [ "coredns" ];
        inherit extraImages;
      };
      # CRI-O's pre-KEP route to writable cgroups: a pod annotation.
      # Annotations outside the runtime handler's allowed_annotations
      # list are stripped before CRI-O reads them, and this one is not
      # on the default list -- so without this line the annotation is a
      # no-op. The runc handler is the default_runtime.
      virtualisation.cri-o.settings.crio.runtime.runtimes.runc.allowed_annotations =
        lib.mkIf (cri == "crio") [ "cgroup2-mount-hierarchy-rw.crio.io" ];
      # The KEP field's CRI-O path refuses to create a writable-cgroup
      # container unless the node's cgroup2 hierarchy is mounted with
      # nsdelegate (the PR checks and errors).  systemd mounts cgroup2
      # without it, and a remount cannot add it: the kernel parses
      # nsdelegate only at the superblock's first mount and ignores the
      # option on remount -- a remount unit runs, exits 0, and the
      # option is still absent.  So stage 2 mounts the hierarchy
      # itself, before systemd has a chance: activation runs after
      # stage 2 mounts /sys and before it execs systemd, and systemd
      # finds the hierarchy already mounted and keeps it.
      system.activationScripts."cgroup2-nsdelegate" = lib.mkIf (cri == "crio") {
        text = ''
          if ! ${pkgs.util-linux}/bin/mountpoint -q /sys/fs/cgroup; then
            ${pkgs.util-linux}/bin/mount -t cgroup2 -o nsdelegate none /sys/fs/cgroup
          fi
        '';
      };
      vivarium = {
        memory = resources.memory;
        diskSize = resources.diskSize;
        lan = {
          network = network.name;
          address = network.address;
        };
      };
    };
in
{
  inherit
    kubernetesSrc
    containerdSrc
    criOSrc
    kubernetes
    containerd
    overlay
    pkgs'
    vivariumLib
    nodeWith
    ;
}
