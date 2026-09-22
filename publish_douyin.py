# -*- coding: utf-8 -*-
"""单独发布抖音烤肠到飞书，日志写文件"""
import sys
import io
from pathlib import Path

# 强制 utf-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

LOG = Path(__file__).parent / "publish_log.txt"

def log(msg):
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

from feishu import publish_video

out_dir = Path(__file__).parent / "output" / "抖音关键词_烤肠"
json_files = sorted(out_dir.glob("*.json"))

# 清空日志
LOG.write_text("", encoding="utf-8")

log(f"共 {len(json_files)} 个视频")

results = []
for i, jf in enumerate(json_files, 1):
    log(f"\n[{i}/{len(json_files)}] {jf.name}")
    r = publish_video(jf)
    results.append(r)
    status = "✓" if r.get("ok") else "✗"
    url = r.get("doc_url", r.get("error", ""))
    log(f"  {status} {url}")

ok = [r for r in results if r.get("ok")]
log(f"\n{'='*50}")
log(f"完成: {len(ok)}/{len(results)}")
for r in ok:
    log(f"  {r['video_name'][:40]}... → {r['doc_url']}")
