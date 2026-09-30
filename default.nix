# A vivarium test for KEP-5474, "Enable Writable cgroups for unprivileged
# containers".
#
# The feature is alpha and unmerged upstream, so nothing nixpkgs carries has
# it.  It lives in open PRs, and this builds them from source:
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
# The phases build one kubeadm node and then ask it a lot of questions; see
# phases/.  Run it:
#
#   nix build --file . test                      # sandboxed, CI path
#   nix run --file . test.driver -- --out ./out  # by hand
#   nix run --file . test.driverDebug -- --out ./out
#
# A node on CRI-O instead, to see whether the other runtime is on the train:
#
#   KEP5474_CRI=crio nix build --file . test
{
  pkgs ? import <nixpkgs> { },
  # The vivarium checkout to build against.  `$HOME/Code/nixidae/vivarium`
  # by default, written relative to this file.
  vivarium ? ../nixidae/vivarium,
}:

let
  lib = pkgs.lib;

  # ── the PRs ────────────────────────────────────────────────────────

  kubernetesSrc = pkgs.fetchFromGitHub {
    owner = "kubernetes";
    repo = "kubernetes";
    rev = "7811d7b62963ce2b8a8c1e03dd7fd70251107092";
    hash = "sha256-wn9y3JjLJH88nCr35fMHjYNguA+XczvD93s22Dh+rpg=";
  };

  containerdSrc = pkgs.fetchFromGitHub {
    owner = "containerd";
    repo = "containerd";
    rev = "1ad78b552ca716aa3d2dff09005fcf149f3722c9";
    hash = "sha256-us9WZSSUuxzXHjtJ13iDazRQqsLfkShuN4wpBTHw5XU=";
  };

  criOSrc = pkgs.fetchFromGitHub {
    owner = "cri-o";
    repo = "cri-o";
    rev = "c297e202b5400538dcb034fd0dcd398be90b420d";
    hash = "sha256-pDZ+raxt4k7TrtevAC5RXt8H8k72/ABhATvFJGbMCKs=";
  };

  # nixpkgs' Kubernetes is 1.37.  The PR is against master, which needs Go
  # 1.27 -- nixpkgs' `go` is 1.26, and `buildGoLatestModule` is 1.27.
  #
  # KUBE_GIT_VERSION is set because a fetched archive has no `.git`, and
  # kubeadm reads its own version to choose the images it asks for.  It has
  # to agree with `imageTags` below and with the tag k8s-images.nix builds.
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

  # containerd 2.3.4 has the node-level `cgroup_writable` toggle but not the
  # per-container CRI field.  The PR adds the field, advertises it in
  # `RuntimeFeatures`, and compiles in the runtime check.
  #
  # nixpkgs' package is `buildGoModule rec`, so `makeFlags` is evaluated
  # against the *original* `version` and `src` -- an `overrideAttrs` of
  # those two builds the new source under the old version string.  The
  # flags are overridden here too, or `containerd --version` says v2.3.4
  # for a v2.3.5 build and nothing can tell them apart.
  containerd = pkgs.containerd.overrideAttrs (old: {
    version = "2.3.5";
    src = containerdSrc;
    makeFlags = map (
      flag:
      if lib.hasPrefix "VERSION=" flag then
        "VERSION=v2.3.5"
      else if lib.hasPrefix "REVISION=" flag then
        "REVISION=${containerdSrc.rev}"
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

  # The kubelet and the control-plane images both come from `pkgs.kubernetes`
  # and `pkgs.containerd`, so an overlay reaches every one of them.  The
  # guest's package set is `pkgs` too: `lib.nix` hands it to the module
  # system as `nixpkgs.pkgs`, so a source override on the attribute alone
  # reaches the *host* and not the node that actually runs the daemon.
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
    One node: it runs the control plane and the pods, so the test needs no
    worker and no scheduler subtlety.  The feature is a property of the node
    the container runs on, whichever that is.

    The CRI is fixed here and not read from the run's resolved knob.  A
    guest is evaluated once and shared by the test and both of its backend
    variants, so it cannot read a value that differs per run -- the same
    reason k8s-images.nix writes its tags down.  `mkTest` calls this with
    the resolved CRI.
  */
  nodeWith =
    cri:
    { ... }:
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
      };
      vivarium = {
        memory = "2560M";
        diskSize = 2048;
        lan = {
          network = "kep5474";
          address = "10.107.0.1/24";
        };
      };
    };

  nodeConfigOf =
    { cri, ... }:
    (vivariumLib.mkNode { imports = [ (nodeWith cri) ]; }).config;
in
{
  # The `vivarium` CLI, for `vivarium ctl` against a paused run.
  inherit (vivariumLib) session;

  test = vivariumLib.mkTest (
    { config, ... }:
    let
      cri = config.resolved.cri.value;
      nodeConfig = nodeConfigOf { inherit cri; };
    in
    {
      name = "kep-5474-${cri}";

      pythonPath = [ ./helpers ];

      backend = config.resolved.backend.value;

      knobs = {
        cri = {
          env = "KEP5474_CRI";
          default = "containerd";
          description = "containerd or crio";
        };
        backend = {
          env = "KEP5474_BACKEND";
          default = "uml";
          description = "uml, qemu, or container";
        };
      };

      nodes.cp = nodeWith cri;

      settings = {
        inherit cri;
        inherit (nodeConfig.services.vivarium-k8s) workloadImage;
        kubernetesVersion = pkgs'.kubernetes.version;
        featureName = "CgroupOptions";
      };

      phases = {
        cluster = {
          script = ./phases/cluster.py;
          after = [ "boot" ];
        };
        runtime = {
          script = ./phases/checks/runtime.py;
          after = [ "cluster" ];
        };
        api = {
          script = ./phases/checks/api.py;
          after = [ "cluster" ];
        };
        runtime-behaviour = {
          script = ./phases/checks/runtime_behaviour.py;
          after = [ "api" ];
        };
        limits = {
          script = ./phases/checks/limits.py;
          after = [ "runtime-behaviour" ];
        };
        security = {
          script = ./phases/checks/security.py;
          after = [ "limits" ];
        };
        pss = {
          script = ./phases/checks/pss.py;
          after = [ "security" ];
        };
        edges = {
          script = ./phases/checks/edges.py;
          after = [ "pss" ];
        };
        report = {
          script = ./phases/report.py;
          after = [ "edges" ];
          always = true;
        };
      };
    }
  );
}
