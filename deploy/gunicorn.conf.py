"""从项目目录运行：.venv/bin/gunicorn -c deploy/gunicorn.conf.py server:application"""
import os
bind = '127.0.0.1:8000'
workers = 2
worker_class = 'gthread'
threads = 4
worker_connections = 64
backlog = 64
timeout = 30
graceful_timeout = 30
keepalive = 2
max_requests = 2000
max_requests_jitter = 200
accesslog = '-'
errorlog = '-'
loglevel = 'warning'
forwarded_allow_ips = '127.0.0.1,::1'
# 覆盖代理传来的 HTTPS 协议标记，仅信任回环代理。
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
raw_env = [
    'TREE_MAX_TREES=' + os.environ.get('TREE_MAX_TREES','10000'),
    'TREE_MAX_ORNAMENTS=' + os.environ.get('TREE_MAX_ORNAMENTS','100000'),
]
