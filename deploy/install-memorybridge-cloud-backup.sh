#!/bin/sh
set -eu

repo_dir=${1:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}

if [ "$(id -u)" -ne 0 ]; then
    echo "run this installer as root" >&2
    exit 1
fi

install -d -m 0755 /usr/local/sbin
install -m 0755 "$repo_dir/scripts/memorybridge_cloud_backup.py" /usr/local/sbin/memorybridge-cloud-backup.py
install -m 0644 "$repo_dir/deploy/systemd/memorybridge-cloud-backup.service" \
    /etc/systemd/system/memorybridge-cloud-backup.service
install -m 0644 "$repo_dir/deploy/systemd/memorybridge-cloud-backup.timer" \
    /etc/systemd/system/memorybridge-cloud-backup.timer

if [ ! -e /etc/memorybridge-cloud-backup.env ]; then
    install -m 0600 "$repo_dir/deploy/memorybridge-cloud-backup.env.example" \
        /etc/memorybridge-cloud-backup.env
fi

systemctl daemon-reload
systemctl enable --now memorybridge-cloud-backup.timer
echo "installed memorybridge-cloud-backup.timer; use systemctl start memorybridge-cloud-backup.service for an immediate run"
