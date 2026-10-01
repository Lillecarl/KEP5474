{
  description = ''
    Vivarium tests for KEP-5474: writable cgroups for unprivileged
    containers, and a full NixOS as a pod.

    `nix run github:Lillecarl/KEP5474#kep-5474` runs the cgroup-delegation
    suite on containerd; `nix run github:Lillecarl/KEP5474#nixos-in-pod`
    boots a full NixOS as a pod.  The knobs resolve from the environment
    at evaluation, so a CRI-O node is

        KEP5474_CRI=crio nix run --impure github:Lillecarl/KEP5474#kep-5474

    -- `--impure` because the knob reads `builtins.getEnv`, which pure
    evaluation answers with nothing.
  '';

  inputs = {
    # Tarball sources: a branch fetched as an archive.  No git graph, no
    # submodules; the cheapest fetch a flake does.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # The test runner both tests drive.  The non-flake entry, default.nix,
    # points this at the same repository's checkout.
    vivarium.url = "github:Lillecarl/vivarium";
  };

  outputs =
    { self, nixpkgs, vivarium }:
    let
      # The tests drive UML guests; this is a Linux-x86_64 suite.
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      lib = pkgs.lib;

      # The non-flake entry, pointed at the inputs.  It evaluates nothing
      # until a driver is built.
      file = import ./default.nix {
        inherit pkgs;
        vivarium = vivarium.outPath;
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
      };

      packages.${system} = {
        default = file.test.driver;
        kep-5474 = file.test.driver;
        nixos-in-pod = file.nixos.test.driver;
      };
    };
}
