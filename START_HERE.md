# Run D2 on the D1 Ubuntu VM

1. In VirtualBox, add a shared folder named **D2Transfer** pointing to the
   Windows **D2 Transfer** folder (the one containing Team2-D2.zip). Leave
   **Read-only** unchecked if you want to copy evidence back to Windows.
   Use the Ubuntu VM where D1 already ran.
2. In an Ubuntu terminal, mount the share and extract the project into Linux:

   ```bash
   mkdir -p ~/d2-transfer ~/cn-team2-project
   mountpoint -q ~/d2-transfer || sudo mount -t vboxsf -o uid="$(id -u)",gid="$(id -g)" D2Transfer ~/d2-transfer
   python3 -m zipfile -e ~/d2-transfer/Team2-D2.zip ~/cn-team2-project
   cd ~/cn-team2-project/Team2-D2
   ```

3. Run the paired experiment. This is the command that generates actual D2
   performance results. Three pairs take roughly 9–10 minutes.

   ```bash
   sudo python3 benchmark.py --pairs 3
   ```

   Each mode runs the same five phases. A trial with an unobserved alert is
   retained. Inspect any launch error using the printed terminal-log path.
   For a standalone live demo (about 90 seconds), use:

   ```bash
   sudo python3 run_demo.py
   ```

4. Read the `comparison.md` at the path printed by the benchmark. To copy the
   latest comparison and its raw evidence back to the mounted Windows share:

   ```bash
   if mountpoint -q ~/d2-transfer; then
     latest=$(ls -dt runs/comparison-* 2>/dev/null | head -n 1)
     if [ -n "$latest" ]; then
       sudo tar -czf "$HOME/d2-transfer/Team2-D2-evidence.tar.gz" -C "$(dirname "$latest")" "$(basename "$latest")"
     fi
   fi
   ```

5. Prepare the final GitHub submission with the project files, measured
   comparison, selected evidence and reproduction instructions. See
   [README.md](README.md) for the demo sequence and rubric mapping.

Suggested opening for the demo: “D1 monitored the link and raised alerts. D2
also changes how often it asks the switches for statistics: slowly while the
network is quiet and faster when it sees heavier traffic or an agent stops
reporting. We compare that against a fixed interval on the same network.”
