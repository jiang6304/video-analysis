# -*- coding: utf-8 -*-
"""直接调 lark CLI 写入飞书，不插入图片。"""
import json, subprocess, sys, io
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "output" / "巨量云图视频"


def escape_xml(text):
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_xml(data):
    meta = data.get("_meta", {})
    video_name = meta.get("video_name", "未知视频")
    video_path = meta.get("video_path", "")
    duration = data.get("video_duration_s", 0)
    title_suggestion = data.get("title_suggestion", "")
    summary = data.get("overall_summary", "")
    shots = data.get("shots", [])
    transcript_full = data.get("transcript_full", "")

    parts = []
    parts.append(f'<title>{escape_xml(video_name)}</title>')

    # 基本信息表
    parts.append('<table><tbody>')
    parts.append(f'<tr><th>文件地址</th><td colspan="3">{escape_xml(video_path)}</td></tr>')
    parts.append(f'<tr><th>时长</th><td>{duration}秒</td><th>建议标题</th><td>{escape_xml(title_suggestion)}</td></tr>')
    parts.append(f'<tr><th>概要</th><td colspan="3">{escape_xml(summary)}</td></tr>')
    parts.append(f'<tr><th>分镜数</th><td>{len(shots)}</td><td></td><td></td></tr>')
    parts.append('</tbody></table>')

    # 分镜总表（无关键帧列）
    parts.append('<h1>分镜总表</h1>')
    parts.append('<table>')
    parts.append('<thead><tr>')
    parts.append('<th>#</th><th>时间</th><th>景别</th><th>镜头</th>')
    parts.append('<th>场景</th><th>人物</th><th>画面描述</th><th>音频</th><th>台词</th>')
    parts.append('</tr></thead><tbody>')

    for shot in shots:
        idx = shot.get("shot_index", "?")
        tr = shot.get("time_range", {})
        start = tr.get("start", 0)
        end = tr.get("end", 0)
        dur = shot.get("duration", end - start)
        shot_type = shot.get("shot_type", "")
        camera = shot.get("camera_movement", "")
        scene = shot.get("scene", "")
        key_char = shot.get("key_人物", "")
        visual = shot.get("visual_content", "")
        ocr = shot.get("ocr_text", "")
        audio = shot.get("audio_type", "")
        transcript = shot.get("transcript", "")

        parts.append('<tr>')
        parts.append(f'<td align="center"><b>{idx}</b></td>')
        parts.append(f'<td>{start:.1f}s - {end:.1f}s<br/>({dur:.1f}s)</td>')
        parts.append(f'<td>{escape_xml(shot_type)}</td>')
        parts.append(f'<td>{escape_xml(camera)}</td>')
        parts.append(f'<td>{escape_xml(scene)}</td>')
        parts.append(f'<td>{escape_xml(key_char)}</td>')
        desc = escape_xml(visual)
        if ocr:
            desc += f'<br/><b>文字:</b> {escape_xml(ocr)}'
        parts.append(f'<td>{desc}</td>')
        parts.append(f'<td>{escape_xml(audio)}</td>')
        t = escape_xml(transcript) if transcript else '<em>无声</em>'
        parts.append(f'<td>{t}</td>')
        parts.append('</tr>')

    parts.append('</tbody></table>')

    # 逐字稿
    parts.append('<h1>完整逐字稿</h1>')
    for line in transcript_full.split("\n"):
        line = line.strip()
        if line:
            parts.append(f'<p>{escape_xml(line)}</p>')

    return "\n".join(parts)


def create_doc(xml_content):
    tmp = PROJECT_DIR / "_tmp_feishu.xml"
    tmp.write_text(xml_content, encoding="utf-8")
    try:
        r = subprocess.run(
            'npx @larksuite/cli docs +create --content "@_tmp_feishu.xml"',
            capture_output=True, timeout=120, encoding="utf-8", errors="replace",
            shell=True, cwd=str(PROJECT_DIR),
        )
        if r.returncode != 0:
            return None, r.stderr[:300] if r.stderr else r.stdout[:300]
        result = json.loads(r.stdout)
        doc = result.get("data", {}).get("document", {})
        return doc.get("url", ""), doc.get("document_id", "")
    except Exception as e:
        return None, str(e)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    json_files = sorted(OUTPUT_DIR.glob("*.json"))
    print(f"共 {len(json_files)} 个视频\n")

    results = []
    for i, jf in enumerate(json_files, 1):
        data = json.loads(jf.read_text(encoding="utf-8"))
        video_name = data.get("_meta", {}).get("video_name", jf.stem)
        print(f"[{i}/{len(json_files)}] {video_name}")

        xml = build_xml(data)
        url, doc_id_or_err = create_doc(xml)
        if url:
            print(f"  OK: {url}")
            results.append({"name": video_name, "url": url})
        else:
            print(f"  FAIL: {doc_id_or_err}")
            results.append({"name": video_name, "url": ""})

    # 汇总文档
    ok = [r for r in results if r["url"]]
    if ok:
        parts = ['<title>巨量云图视频 - 分镜逐字稿汇总</title>']
        parts.append('<h1>视频列表</h1>')
        parts.append('<table><thead><tr><th>#</th><th>视频</th><th>文档</th></tr></thead><tbody>')
        for i, r in enumerate(ok, 1):
            parts.append(f'<tr><td>{i}</td><td>{escape_xml(r["name"])}</td><td><a href="{r["url"]}">查看</a></td></tr>')
        parts.append('</tbody></table>')
        xml = "\n".join(parts)
        url, _ = create_doc(xml)
        if url:
            print(f"\n汇总文档: {url}")

    print(f"\n完成: {len(ok)}/{len(json_files)} 成功")


if __name__ == "__main__":
    main()
