# A vivarium test for KEP-5474, "Enable Writable cgroups for unprivileged
# containers".
#
# The feature is alpha and unmerged upstream, so nothing nixpkgs carries has
# it.  It lives in two open PRs, and this builds both from source:
#
#   kubernetes/kubernetes#137568   Pod API, CRI API, validation, kubelet,
#                                  scheduler, node e2e
#   containerd/containerd#14039    CRI `cgroup_mount_mode` in the runtime
#
# Both are pinned by the commit each PR pointed at when this was written.
# The test is in tests/cgroup-options.py.
{
  pkgs ? import <nixpkgs> { },
  # The vivarium checkout to build against.  `$HOME/Code/nixidae/vivarium`
  # by default, written relative to this file.
  vivarium ? ../nixidae/vivarium,
}:

let
  lib = pkgs.lib;

  # ── the two PRs ────────────────────────────────────────────────────
  #
  # Fetched by commit rather than by branch: a branch moves, and a test
  # that silently follows it tests something else next week.

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
  # per-container CRI field.  The PR adds the field and advertises it in
  # `RuntimeFeatures`, which is what makes the node declare `CgroupOptions`.
  containerd = pkgs.containerd.overrideAttrs (old: {
    version = "2.3.5";
    src = containerdSrc;
  });

  # The kubelet and the control-plane images both come from `pkgs.kubernetes`
  # and `pkgs.containerd`, so an overlay reaches every one of them.
  pkgs' = pkgs.extend (_final: _prev: { inherit kubernetes containerd; });

  vivariumLib = import (vivarium + "/lib.nix") { pkgs = pkgs'; };

  # One node: it runs the control plane and the pods, so the test needs no
  # worker and no scheduler subtlety.  The feature is a property of the node
  # the container runs on, whichever that is.
  node = {
    imports = [ (vivarium + "/modules/k8s.nix") ];
    services.vivarium-k8s = {
      enable = true;
      role = "control-plane";
      # The gate on kube-apiserver and kubelet.  NodeDeclaredFeatures, which
      # the feature depends on, is GA and on by default.
      featureGates.CgroupOptions = true;
      # The PR's kubeadm names newer etcd and CoreDNS tags than 1.37's.  The
      # images are looked up by tag, so they have to match.
      imageTags = {
        etcd = "3.7.1-0";
        coredns = "v1.14.7";
      };
      # Nothing here resolves a name, and a sandboxed CoreDNS spends the run
      # timing out against an upstream it cannot reach.
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

  nodeConfig = (vivariumLib.mkNode node).config;
in
{
  # The `vivarium` CLI, for `vivarium ctl` against a paused run.
  inherit (vivariumLib) session;

  test = vivariumLib.mkTest {
    name = "kep-5474";

    phases.check = {
      script = ./tests/cgroup-options.py;
      after = [ "boot" ];
    };

    nodes.cp = node;

    settings = {
      # The busybox the test schedules, and the Kubernetes it runs against.
      inherit (nodeConfig.services.vivarium-k8s) workloadImage;
      kubernetesVersion = pkgs'.kubernetes.version;
    };
  };
}
