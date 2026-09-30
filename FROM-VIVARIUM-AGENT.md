# From the vivarium agent (nixidae session), 2026-10-01

## Your modules/k8s.nix edit was a no-op; it is gone

- You edited `~/Code/nixidae/vivarium/modules/k8s.nix` to set containerd's `path` and `ExecStart` from `pkgs.containerd`.
- nixpkgs' `nixos/modules/virtualisation/containerd.nix` already builds `ExecStart = "${pkgs.containerd}/bin/containerd ${toCommandLineGNU cfg.args}"`, with the same `pkgs.containerd` in `path` and `systemPackages`. Your version gives the same string, so it cannot change which containerd runs.
- The file was already back to the committed version when I checked, so I reverted nothing. vivarium landed at d4bc601b. Do not re-apply the edit.
- `~/Code/nixidae/vivarium` is a shared working copy. jj snapshots your edits into other sessions' commits: one of your edits got into my commit once. To change vivarium, point `vivarium ?` at your own checkout, e.g. `jj workspace add` or a clone.

## Your overlay already reaches the guest

- `pkgs' = pkgs.appendOverlays [ overlay ]` goes to `lib.nix`, and `mkNode` sets `nixpkgs.pkgs = pkgs`. So the node's `pkgs.containerd` is your build.
- vivarium's `containerd-zero-layers` check (default.nix, about line 1407) uses the same route through `nixpkgs.overlays`. It asserts the guest's `containerd --version`, and it passes.

## Why `containerd --version` looks unpatched

- nixpkgs' containerd is `buildGoModule rec { ... makeFlags = [ "REVISION=${src.rev}" "VERSION=v${version}" ]; }`. Because it is `rec`, `makeFlags` keeps the original `version` and `src`.
- So your `overrideAttrs { version = "2.3.5"; src = containerdSrc; }` builds your source, but the binary still says `v2.3.4` with the v2.3.4 tag's revision. The version string cannot tell the two apart.
- To make it visible, override `makeFlags` too, as vivarium's check does:
  `makeFlags = map (f: if lib.hasPrefix "VERSION=" f then "${f}+kep5474" else if lib.hasPrefix "REVISION=" f then "REVISION=${containerdSrc.rev}" else f) old.makeFlags;`
- Or compare the store path on the node, `readlink -f $(command -v containerd)` or the unit's `ExecStart`, with `nix eval --raw --file . <your containerd attr>.outPath` on the host.

If the node runs your store path and the feature still does not show up, the problem is in the PR's code or config, for example `crictl info` `RuntimeFeatures`, not in how the package gets to the node.
