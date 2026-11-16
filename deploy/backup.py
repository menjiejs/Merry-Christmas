"""在线一致性备份，不直接复制正在使用的 WAL 数据库。"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import sqlite3
parser=argparse.ArgumentParser()
parser.add_argument('--database',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
source=Path(args.database).resolve()
if not source.is_file(): raise SystemExit('数据库不存在，未执行备份')
folder=Path(args.output).resolve(); folder.mkdir(parents=True,exist_ok=True)
name=folder/('trees-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.db')
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db:
    with sqlite3.connect(name) as backup:
        db.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise SystemExit('备份校验失败')
print('备份已完成：'+str(name))
