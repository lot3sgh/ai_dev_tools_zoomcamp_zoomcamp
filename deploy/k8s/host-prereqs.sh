#!/usr/bin/env bash
# Host prerequisites for running k3s + this stack behind firewalld.
#
# Symptom without this: the chat/grafana pages return 502 from Traefik. Traefik
# balances directly to pod IPs, and firewalld's FORWARD filter rejects cni0
# traffic (ClusterIP still works via conntrack dnat, so the bootstrap Job
# succeeds and the failure looks like an ingress-only problem).
#
# The fix: trust the CNI interfaces. Run once, then restart k3s so it and
# klipper-lb re-apply their node iptables rules (firewalld's reload flushes
# them, which breaks ClusterIP/NodePort until k3s is restarted).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)" >&2; exit 1; }
command -v firewall-cmd >/dev/null || { echo "firewalld not installed; nothing to do"; exit 0; }

changed=0
for iface in cni0 flannel.1; do
  if firewall-cmd --zone=trusted --query-interface="$iface" >/dev/null 2>&1; then
    echo "trusted zone already contains $iface"
  else
    firewall-cmd --zone=trusted --add-interface="$iface" --permanent
    changed=1
  fi
done

if [ "$changed" -eq 1 ]; then
  firewall-cmd --reload
  echo "firewalld reloaded; restarting k3s to re-apply its node iptables rules"
  systemctl restart k3s
  echo "done — wait for 'kubectl get node' to report Ready"
else
  echo "no change needed"
fi
