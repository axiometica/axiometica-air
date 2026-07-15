#!/bin/bash
echo "hostname=$(hostname)"
echo "uptime=$(cut -d. -f1 /proc/uptime)"
echo "process_count=$(ps -e --no-headers | wc -l)"
echo "free_memory=$(free -m | awk "NR==2{print \$4}")"
echo "used_memory=$(free -m | awk "NR==2{print \$3}")"
