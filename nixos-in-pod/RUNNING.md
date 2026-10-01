# Running the test

The test lives at `nixos-in-pod/` in the KEP5474 repository; every
command below runs from the repository root, and the driver attribute is
`nixos` (the cgroup-delegation test's is `test`).

Never `nix build --file . nixos.test`. It hides the run in a sandbox and gives
you a log instead of a live guest. Use the driver.

## The loop

1. Start the driver in a detached systemd unit, output to a file.

       systemd-run --user --unit=nip --collect \
         --property=WorkingDirectory=$PWD \
         --property=StandardOutput=append:$PWD/run.log \
         --property=StandardError=append:$PWD/run.log \
         nix run --file . nixos.test.driver -- --out ./out

   `--break-on-failure` pauses on a failed phase, so the guest stays up.
   Add it when investigating; a plain run keeps going on an error only if
   the phase allows it.

2. Wait with the monitor, in the foreground. It exits on a pause (4) or
   the verdict (0 pass, 1 fail). This is the waiter; it wakes this loop
   when there is something to do.

       nix run --file . test.driver -- --help   # confirms the flags once
       $VIVARIUM monitor ./out --quiet --until-pause

   `$VIVARIUM` is the `vivarium` path printed in `run.log` before the
   first phase and at each pause. Read it from there.

   Do not `sleep` on the build and do not poll. The monitor returns.

3. At a pause, reach in with `ctl`, live:

       $VIVARIUM ctl --out ./out state
       $VIVARIUM ctl --out ./out exec 'await cp.succeed("systemctl --failed")'
       $VIVARIUM ctl --out ./out exec - < snippet.py   # multiline, top-level await

   `vms` holds every guest; each guest is also a bare name (`cp`). Names
   you set persist to the next `exec`.

4. Edit a phase and `inject` it without a new boot:

       $VIVARIUM ctl --out ./out inject ./phases/image.py

5. Resume and wait again:

       $VIVARIUM ctl --out ./out continue
       $VIVARIUM monitor ./out --quiet --until-pause

## Exit codes of the monitor

| 0 | passed | 1 | failed | 2 | no verdict (crashed) |
| 3 | lost the stream | 4 | paused (`--until-pause`) |

After a pause, start the monitor again; it does not re-report a pause that
already ended.

## The sandboxed build

`nix build --file . nixos.test` is the CI path only. Run it when the thing is
finished, to prove it passes with defaults and no hand-holding.
