#!/usr/bin/env bash
# benchmark-aurora.sh - sample steady-state resource use of the aurora daemon.
#   ./benchmark-aurora.sh [seconds] [interval] [warmup]
# warmup: seconds to wait before sampling (default 5) so startup ramp is excluded.
set -u
export LC_ALL=C LC_NUMERIC=C   # force '.' decimals so awk math is correct

DURATION="${1:-30}"
INTERVAL="${2:-2}"
WARMUP="${3:-5}"

# Match the python daemon specifically - NOT editors/greps that merely have
# "aurora.py" in their command line (e.g. `nvim aurora.py`).
PID="$(pgrep -f 'python3?.*aurora\.py' | head -1)"
if [ -z "$PID" ]; then
  echo "aurora not running (no python process matching aurora.py). Start it first." >&2
  exit 1
fi
echo "Matched processes:"; pgrep -af 'python3?.*aurora\.py' | sed 's/^/  /'

echo "Found aurora pid=$PID. Warming up ${WARMUP}s (excluding startup ramp)..."
sleep "$WARMUP"
echo "Sampling ${DURATION}s every ${INTERVAL}s"
echo

GPU=""
command -v nvidia-smi >/dev/null 2>&1 && GPU="nvidia"
[ -z "$GPU" ] && [ -r /sys/class/drm/card0/device/gpu_busy_percent ] && GPU="amdsys"

n=0; cpu_sum=0; rss_sum=0; rss_max=0; gpu_sum=0; gpu_n=0; dec_sum=0; dec_n=0
end=$(( $(date +%s) + DURATION ))

while [ "$(date +%s)" -lt "$end" ]; do
  pids="$PID$(pgrep -P "$PID" 2>/dev/null | sed 's/^/,/' | tr -d '\n')"
  vals="$(ps -o %cpu=,rss= --pid "$pids" 2>/dev/null | awk '{c+=$1; r+=$2} END{printf "%.1f %d", c, r}')"
  cpu="${vals%% *}"; rss="${vals##* }"
  cpu="${cpu:-0}"; rss="${rss:-0}"
  rss_mb=$(( rss / 1024 ))

  gpu=""; dec=""
  if [ "$GPU" = "nvidia" ]; then
    gpu="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')"
    # NVDEC decoder-engine utilization: the real 'is decode on the GPU' signal
    dec="$(nvidia-smi --query-gpu=utilization.decoder --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')"
  elif [ "$GPU" = "amdsys" ]; then
    gpu="$(cat /sys/class/drm/card0/device/gpu_busy_percent 2>/dev/null)"
  fi

  printf "  cpu=%5s%%  rss=%5sMB" "$cpu" "$rss_mb"
  [ -n "$gpu" ] && printf "  gpu=%3s%%" "$gpu"
  [ -n "$dec" ] && printf "  nvdec=%3s%%" "$dec"
  printf "\n"

  cpu_sum="$(awk "BEGIN{print $cpu_sum + $cpu}")"
  rss_sum=$(( rss_sum + rss_mb )); [ "$rss_mb" -gt "$rss_max" ] && rss_max="$rss_mb"
  [ -n "$gpu" ] && { gpu_sum=$(( gpu_sum + gpu )); gpu_n=$(( gpu_n + 1 )); }
  [ -n "$dec" ] && { dec_sum=$(( dec_sum + dec )); dec_n=$(( dec_n + 1 )); }
  n=$(( n + 1 )); sleep "$INTERVAL"
done

echo
echo "==== averages over $n samples ===="
awk "BEGIN{printf \"  avg CPU  : %.1f%%\n\", $cpu_sum/$n}"
echo "  avg RSS  : $(( rss_sum / n )) MB   (peak ${rss_max} MB)"
[ "$gpu_n" -gt 0 ] && echo "  avg GPU  : $(( gpu_sum / gpu_n ))%"
if [ "$dec_n" -gt 0 ]; then
  echo "  avg NVDEC: $(( dec_sum / dec_n ))%   <- >0 means H.264 decode is on the GPU"
fi
