"""只读比较迁移前后数据库；在 Web 与 timer 启动前执行。"""
import contextlib
import json
import sqlite3
import sys
from pathlib import Path

with contextlib.ExitStack() as stack:
    before, after = [stack.enter_context(contextlib.closing(sqlite3.connect(
        f'{Path(p).resolve().as_uri()}?mode=ro', uri=True,
    ))) for p in sys.argv[1:]]
    for conn in (before, after):
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    table = 'fund_estimate_daily_accuracy'
    columns = [r[1] for r in before.execute(f'PRAGMA table_info({table})')]
    new_columns = [r[1] for r in after.execute(f'PRAGMA table_info({table})')]
    assert new_columns == columns + ['evidence_json', 'attribution_reason', 'estimate_fingerprint']
    names = ','.join(f'"{c}"' for c in columns)
    old_rows = before.execute(f'SELECT {names} FROM {table} ORDER BY id').fetchall()
    assert old_rows == after.execute(f'SELECT {names} FROM {table} ORDER BY id').fetchall()
    assert after.execute(f'''SELECT COUNT(*) FROM {table} WHERE evidence_json IS NULL
        AND attribution_reason='legacy_evidence_missing' AND estimate_fingerprint IS NULL''').fetchone()[0] == len(old_rows)
    def tables(conn):
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert tables(before) == tables(after)
    checked = []
    for name in sorted(tables(before) - {table}):
        quoted = '"' + name.replace('"', '""') + '"'
        assert before.execute(f'PRAGMA table_info({quoted})').fetchall() == after.execute(f'PRAGMA table_info({quoted})').fetchall(), name
        # 既有启动逻辑会更新档案 updated_at；业务字段仍须逐值相同。
        select = ','.join('"'+r[1]+'"' for r in before.execute(f'PRAGMA table_info({quoted})') if not (name == 'fund_valuation_profiles' and r[1] == 'updated_at'))
        assert sorted(before.execute(f'SELECT {select} FROM {quoted}').fetchall(),key=repr) == sorted(after.execute(f'SELECT {select} FROM {quoted}').fetchall(),key=repr), name
        checked.append(name)
    # 自动生成的唯一索引无 SQL，核对实际列而非索引名字。
    unique = [[c[2] for c in after.execute(f'PRAGMA index_info("{r[1]}")')]
              for r in after.execute(f'PRAGMA index_list({table})') if r[2]]
    assert unique == [['code','estimate_source','estimate_fingerprint']], unique
    assert not after.execute("SELECT 1 FROM sqlite_master WHERE name='estimate_accuracy_legacy'").fetchone()
    print(json.dumps({'original_rows_preserved':len(old_rows),'other_tables_unchanged':checked,'integrity':'ok','unique_columns':unique},ensure_ascii=False,indent=2))
