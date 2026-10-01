# A vivarium test for KEP-5474, "Enable Writable cgroups for unprivileged
# containers".
#
# The feature is alpha and unmerged upstream, so nothing nixpkgs carries has
# it.  It lives in open PRs, built in `infra/` -- the stack both tests here
# share.  This one builds one kubeadm node and asks it a lot of questions
# (see phases/); `nixos-in-pod/` asks the neighbouring question, whether a
# full NixOS boots as a pod on this stack.
#
# Run it:
#
#   nix build --file . test                      # sandboxed, CI path
#   nix run --file . test.driver -- --out ./out  # by hand
#
# A node on CRI-O instead, to see whether the other runtime is on the train:
#
#   KEP5474_CRI=crio nix build --file . test
#
# The other test:
#
#   nix run --file . nixos.driver -- --out ./out
{
  pkgs ? import <nixpkgs> { },
  # The vivarium checkout to build against.  `$HOME/Code/nixidae/vivarium`
  # by default, written relative to this file.
  vivarium ? ../nixidae/vivarium,
}:

let
  infra = import ./infra { inherit pkgs vivarium; };
  inherit (infra) pkgs' vivariumLib nodeWith;

  nodeConfigOf =
    { cri, ... }:
    (vivariumLib.mkNode { imports = [ (nodeWith { inherit cri; }) ]; }).config;
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

      pythonPath = [
        ./helpers
        ./infra/helpers
      ];

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

      nodes.cp = nodeWith { inherit cri; };

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
        annotation = {
          script = ./phases/checks/annotation.py;
          after = [ "runtime-behaviour" ];
        };
        limits = {
          script = ./phases/checks/limits.py;
          after = [ "annotation" ];
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

  /*
    The second test: a full NixOS boots as a pod on this stack.  Its own
    module -- image, configuration, phases, findings -- lives in
    `nixos-in-pod/`; the driver is `nixos.driver`.
  */
  nixos = import ./nixos-in-pod {
    inherit pkgs vivarium;
  };
}
