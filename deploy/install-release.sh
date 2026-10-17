#!/bin/bash
# 在服务器上执行；archive 为本地上传的代码包，不包含数据库。
set -euo pipefail
archive=$(realpath "$1")
release_id=${2:?release ID required}
[[ "$release_id" =~ ^[a-zA-Z0-9._-]+$ ]]
base=/opt/fund-analysis
release="$base/releases/$release_id"
if [[ -e "$release" ]]; then
    echo "Release already exists: $release" >&2
    exit 1
fi
sudo -n useradd --system --home-dir /var/lib/fund-analysis --shell /usr/sbin/nologin fund-analysis 2>/dev/null || id fund-analysis >/dev/null
sudo -n install -d -o ubuntu -g ubuntu -m 755 "$base" "$base/releases"
sudo -n install -d -o fund-analysis -g fund-analysis -m 700 /var/lib/fund-analysis /var/lib/fund-analysis/backups
mkdir "$release"
tar -xzf "$archive" -C "$release"
python3 -m venv "$release/.venv"
if [[ -f "$base/current/requirements-installed.txt" ]]; then
    sed '/^#/d; /^-e /d' "$base/current/requirements-installed.txt" > "$release/runtime-constraints.txt"
    "$release/.venv/bin/python" -m pip install -c "$release/runtime-constraints.txt" -e "$release"
else
    "$release/.venv/bin/python" -m pip install -e "$release"
fi
"$release/.venv/bin/python" -m pip check
"$release/.venv/bin/python" -m pip freeze --exclude fund-analysis > "$release/requirements-installed.txt"
TZ=Asia/Shanghai "$release/.venv/bin/python" -c 'from fund_analysis.collection import collection_slot; from fund_analysis.web.app import DIST_DIR; assert (DIST_DIR / "index.html").is_file(), DIST_DIR'
verify_dir=$(mktemp -d)
for unit_file in "$release"/deploy/systemd/*; do
    sed "s|$base/current|$release|g" "$unit_file" > "$verify_dir/$(basename "$unit_file")"
done
sudo -n systemd-analyze verify "$verify_dir"/*
rm "$verify_dir"/*
rmdir "$verify_dir"
# 当前版本不健康时拒绝再次切换，避免失败版本覆盖健康的回滚目标。
if [[ -L "$base/current" ]] && ! curl --fail --silent --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/funds >/dev/null; then
    echo 'Current release is unhealthy. Roll back before deploying again; previous is unchanged.' >&2
    exit 1
fi
# 先备份，再停止 timer 与本项目服务；不操作其他服务。
if sudo -n test -f /var/lib/fund-analysis/fund_analysis.db; then
    sudo -n -u fund-analysis python3 "$release/deploy/backup.py" /var/lib/fund-analysis/fund_analysis.db "/var/lib/fund-analysis/backups/pre-$release_id.db"
fi
for unit in fund-analysis-collect.timer fund-analysis-collect.service fund-analysis-simulation.timer fund-analysis-simulation.service fund-analysis.service; do
    if [[ $(systemctl show "$unit" -p LoadState --value) != not-found ]]; then
        sudo -n systemctl stop "$unit"
    fi
done
if [[ -L "$base/current" ]]; then
    ln -sfn "$(readlink "$base/current")" "$base/previous"
fi
ln -s "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
sudo -n install -m 644 "$release"/deploy/systemd/* /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now fund-analysis.service
for attempt in {1..30}; do
    if curl --fail --silent --connect-timeout 2 --max-time 5 http://127.0.0.1:8765/api/funds >/dev/null; then
        sudo -n systemctl enable --now fund-analysis-collect.timer
        # 模拟调度 timer 不随发布首次启用：阶段 7.5 的真实自动运行门槛（规则包、
        # P0-B 采集与备份证据、年份日历）需人工确认。已由管理员启用过时恢复运行，
        # 避免发布在上面第 44 行停掉之后静默不再启动。
        if [[ $(systemctl show fund-analysis-simulation.timer -p UnitFileState --value) == enabled ]]; then
            sudo -n systemctl start fund-analysis-simulation.timer
        fi
        echo "Active release: $release"
        exit 0
    fi
    sleep 1
done
echo 'Startup check failed. Timer remains stopped; see journal and README rollback commands.' >&2
exit 1
