{
  description = ''
    Vivarium tests for KEP-5474: writable cgroups for unprivileged
    containers, and a full NixOS as a pod.

    `nix run github:Lillecarl/KEP5474#kep-5474` runs the cgroup-delegation
    suite on containerd; `nix run github:Lillecarl/KEP5474#nixos-in-pod`
    boots a full NixOS as a pod.  `#divergence` runs the pen test:
    CRI-O on a node without the nsdelegate mount option, where the
    pre-KEP annotation route is shown letting a root container remove
    its own memory limit -- the route the KEP field's gate closes.
    The knobs resolve from the environment at evaluation, so a CRI-O
    node is

        KEP5474_CRI=crio nix run --impure github:Lillecarl/KEP5474#kep-5474

    -- `--impure` because the knob reads `builtins.getEnv`, which pure
    evaluation answers with nothing.
  '';

  inputs = {
    # Tarball sources: a branch fetched as an archive.  No git graph, no
    # submodules; the cheapest fetch a flake does.
    #
    # The nixpkgs rev is the nixos-unstable snapshot the suite's own
    # <nixpkgs> tracks, and not the branch head on purpose: the suite's
    # derivations are proven against this rev, and a moving input would
    # test a different nixpkgs from one day to the next.
    nixpkgs.url = "github:NixOS/nixpkgs/aff8a0b28396750446e5537a96461bc4facdb287";
    # The test runner both tests drive.  The non-flake entry, default.nix,
    # points this at the same repository's checkout.
    vivarium.url = "github:Lillecarl/vivarium";
    /*
      The three PR sources, each overridable.  To test your own branch:

          nix flake lock --override-input kubernetes-src \
            "git+https://github.com/you/kubernetes?rev=<sha>"

      -- `nix flake lock` records the commit it fetched, so the pin stays.
      `flake = false` because these trees are not flakes, and `git+https`
      rather than `github:` because a git fetch carries the revision the
      packages stamp into their version strings.  The defaults are the PR
      commits the tests were written against; see the README's "Pointing
      the sources elsewhere".
    */
    kubernetes-src = {
      url = "git+https://github.com/kubernetes/kubernetes?rev=7811d7b62963ce2b8a8c1e03dd7fd70251107092";
      flake = false;
    };
    containerd-src = {
      url = "git+https://github.com/containerd/containerd?rev=1ad78b552ca716aa3d2dff09005fcf149f3722c9";
      flake = false;
    };
    cri-o-src = {
      url = "git+https://github.com/cri-o/cri-o?rev=c297e202b5400538dcb034fd0dcd398be90b420d";
      flake = false;
    };
  };

  outputs =
    { self, nixpkgs, vivarium, kubernetes-src, containerd-src, cri-o-src }:
    let
      # The tests drive UML guests; this is a Linux-x86_64 suite.
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      lib = pkgs.lib;

      # The non-flake entry, pointed at the inputs.  A source input
      # carries both the tree (`outPath`) and the commit it was fetched
      # at (`rev`), which is all the packages ask of a source.
      file = import ./default.nix {
        inherit pkgs;
        vivarium = vivarium.outPath;
        kubernetesSrc = kubernetes-src;
        containerdSrc = containerd-src;
        criOSrc = cri-o-src;
      };

      # The driver requires --out; hand it the run directory, let the
      # caller's arguments win.
      app =
        name: driver:
        let
          wrapper = pkgs.writeShellScriptBin name ''
            exec ${lib.getExe driver} --out "''${OUT:-./out}" "$@"
          '';
        in
        {
          type = "app";
          program = lib.getExe wrapper;
        };
    in
    {
      apps.${system} = {
        default = app "kep-5474" file.test.driver;
        kep-5474 = app "kep-5474" file.test.driver;
        nixos-in-pod = app "nixos-in-pod" file.nixos.test.driver;
        divergence = app "kep-5474-divergence" file.divergence.driver;
      };

      packages.${system} = {
        default = file.test.driver;
        kep-5474 = file.test.driver;
        nixos-in-pod = file.nixos.test.driver;
        divergence = file.divergence.driver;
      };
    };
}
