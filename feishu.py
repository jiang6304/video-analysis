# -*- coding: utf-8 -*-
"""
飞书云文档写入模块。

两种发布模式：
- 单视频模式（publish_video）：每个视频一个独立文档
- 批量汇总模式（publish_batch）：所有视频写入一个文档，关键帧嵌入表格

依赖：
- lark-cli（全局安装：npm i -g @anthropic-ai/lark-cli 或 npm i -g @larksuite/cli）
- openpyxl（pip install openpyxl，仅批量模式读取 Excel 时需要）
- lark-cli 需先登录：lark-cli auth login --domain all
"""
import json
import subprocess
import sys
import io
import re
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, 'buffer') and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass  # 后台运行时 buffer 可能已关闭

from config import OUTPUT_DIR

PROJECT_DIR = Path(__file__).parent


def escape_xml(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run_lark(cmd: str, timeout: int = 120) -> dict:
    import os
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    r = subprocess.run(
        cmd, capture_output=True, timeout=timeout,
        encoding="utf-8", errors="replace",
        shell=True, cwd=str(PROJECT_DIR), env=env,
    )
    if r.returncode != 0:
        err = r.stderr.strip()[:500] if r.stderr else r.stdout.strip()[:500]
        return {"ok": False, "error": err}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "raw": r.stdout[:500]}


def upload_image(image_path: Path) -> str | None:
    """上传图片到飞书 Drive，返回 file_token。"""
    try:
        rel = image_path.relative_to(PROJECT_DIR)
    except ValueError:
        rel = image_path
    cmd = f'lark-cli drive +upload --file "{rel}" --as user --format json'
    r = _run_lark(cmd)
    if r.get("ok") or r.get("data"):
        return r.get("data", {}).get("file_token", "")
    return None


def find_block_id_by_keyword(doc_id: str, keyword: str) -> str | None:
    """通过关键词搜索找到 block ID。"""
    cmd = f'lark-cli docs +fetch --doc {doc_id} --scope keyword --keyword "{keyword}" --detail with-ids --as user --format json'
    r = _run_lark(cmd)
    if not (r.get("ok") or r.get("data")):
        return None
    content = r.get("data", {}).get("document", {}).get("content", "")
    # 找 keyword 所在的 <p id="xxx"> 的 block ID
    # 格式: <p id="doxcnXXX">keyword</p>
    pattern = r'<p\s+id="([^"]+)">\s*' + re.escape(keyword) + r'\s*</p>'
    m = re.search(pattern, content)
    if m:
        return m.group(1)
    # 备选：找包含 keyword 的最近 id
    m = re.search(r'id="([^"]+)"[^>]*>[^<]*' + re.escape(keyword), content)
    if m:
        return m.group(1)
    return None


def insert_image_after_block(doc_id: str, block_id: str, file_token: str) -> bool:
    """在指定 block 之后插入图片。"""
    # 写入临时文件避免 shell 转义问题
    content = f'<img src="{file_token}"/>'
    tmp = PROJECT_DIR / "_tmp_img_content.xml"
    tmp.write_text(content, encoding="utf-8")
    try:
        cmd = f'lark-cli docs +update --doc {doc_id} --command block_insert_after --block-id {block_id} --content "@_tmp_img_content.xml" --as user --format json'
        r = _run_lark(cmd)
        return r.get("ok", False) or r.get("data", {}).get("result") == "success"
    finally:
        tmp.unlink(missing_ok=True)


def build_table_xml(data: dict) -> str:
    """构建包含分镜表格的 XML（关键帧列用占位文本）。"""
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

    # 基本信息
    parts.append('<table><tbody>')
    parts.append(f'<tr><th>文件地址</th><td colspan="3">{escape_xml(video_path)}</td></tr>')
    parts.append(f'<tr><th>时长</th><td>{duration}秒</td><th>建议标题</th><td>{escape_xml(title_suggestion)}</td></tr>')
    parts.append(f'<tr><th>概要</th><td colspan="3">{escape_xml(summary)}</td></tr>')
    parts.append(f'<tr><th>分镜数</th><td>{len(shots)}</td><td></td><td></td></tr>')
    parts.append('</tbody></table>')

    # 分镜总表
    parts.append('<h1>分镜总表</h1>')
    parts.append('<table>')
    parts.append('<colgroup>'
                 '<col width="30"/><col width="70"/><col width="50"/><col width="50"/>'
                 '<col width="80"/><col width="60"/>'
                 '<col width="200"/><col width="60"/><col width="180"/>'
                 '</colgroup>')
    parts.append('<thead><tr>')
    parts.append('<th>#</th><th>时间</th><th>景别</th><th>镜头</th>')
    parts.append('<th>场景</th><th>人物</th>')
    parts.append('<th>画面描述</th><th>音频</th><th>台词</th>')
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

        # 占位文本（用于后续定位插入图片）
        placeholder = f"_kf_{idx}_"

        parts.append('<tr>')
        parts.append(f'<td align="center"><b>{idx}</b></td>')
        parts.append(f'<td>{start:.1f}s - {end:.1f}s<br/>({dur:.1f}s)</td>')
        parts.append(f'<td>{escape_xml(shot_type)}</td>')
        parts.append(f'<td>{escape_xml(camera)}</td>')
        parts.append(f'<td>{escape_xml(scene)}</td>')
        parts.append(f'<td>{escape_xml(key_char)}</td>')
        parts.append(f'<td>{placeholder}</td>')  # 占位

        desc = escape_xml(visual)
        if ocr:
            desc += f'<br/><b>文字:</b> {escape_xml(ocr)}'
        parts.append(f'<td>{desc}</td>')
        parts.append(f'<td>{escape_xml(audio)}</td>')
        t = escape_xml(transcript) if transcript else '<em>无声</em>'
        parts.append(f'<td>{t}</td>')
        parts.append('</tr>')

    parts.append('</tbody></table>')

    # 完整逐字稿
    parts.append('<h1>完整逐字稿</h1>')
    for line in transcript_full.split("\n"):
        line = line.strip()
        if line:
            parts.append(f'<p>{escape_xml(line)}</p>')

    return "\n".join(parts)


def publish_video(json_path: Path) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    meta = data.get("_meta", {})
    video_name = meta.get("video_name", json_path.stem)
    stem = json_path.stem
    # 帧目录和 JSON 在同一目录下
    frame_dir = json_path.parent / f"{stem}_frames"
    shots = data.get("shots", [])

    # 1. 上传关键帧，收集 token
    kf_tokens = {}  # {shot_index: file_token}
    if frame_dir.exists():
        print(f"  上传 {len(shots)} 张关键帧...")
        for shot in shots:
            idx = shot.get("shot_index", 0)
            pattern = f"{stem}_shot{idx:03d}_*.png"
            matches = list(frame_dir.glob(pattern))
            if matches:
                token = upload_image(matches[0])
                if token:
                    kf_tokens[idx] = token
                    print(f"    分镜{idx}: ✓")
                else:
                    print(f"    分镜{idx}: ✗")

    # 2. 创建文档（表格含占位文本）
    xml = build_table_xml(data)
    tmp = PROJECT_DIR / "_tmp_feishu.xml"
    tmp.write_text(xml, encoding="utf-8")
    try:
        print(f"  创建文档...")
        r = _run_lark('lark-cli docs +create --content "@_tmp_feishu.xml" --as user --format json')
        if not (r.get("ok") or r.get("data")):
            return {"ok": False, "video_name": video_name, "error": r.get("error", "")}
        doc = r.get("data", {}).get("document", {})
        doc_url = doc.get("url", "")
        doc_id = doc.get("document_id", "")
        print(f"  文档: {doc_url}")
    finally:
        tmp.unlink(missing_ok=True)

    # 3. 逐个找到占位 block ID，替换为图片
    if kf_tokens:
        print(f"  插入图片到表格...")
        for idx, token in kf_tokens.items():
            placeholder = f"_kf_{idx}_"
            block_id = find_block_id_by_keyword(doc_id, placeholder)
            if block_id:
                ok = insert_image_after_block(doc_id, block_id, token)
                print(f"    分镜{idx}: {'✓' if ok else '✗'}")
            else:
                print(f"    分镜{idx}: ✗ 未找到占位")

    return {"ok": True, "video_name": video_name, "doc_url": doc_url, "doc_id": doc_id}


def publish_all(output_dir: Path | None = None):
    if output_dir is None:
        output_dir = PROJECT_DIR / "output"

    json_files = sorted(output_dir.glob("*.json"))
    if not json_files:
        print(f"未找到 JSON 文件: {output_dir}")
        return

    print(f"共 {len(json_files)} 个视频\n")
    results = []
    for i, jf in enumerate(json_files, 1):
        print(f"[{i}/{len(json_files)}] {jf.name}")
        r = publish_video(jf)
        results.append(r)
        print()

    # 总文档
    ok = [r for r in results if r.get("ok")]
    if ok:
        parts = ['<title>参考视频分镜逐字稿汇总</title>']
        parts.append('<h1>视频列表</h1>')
        parts.append('<table><thead><tr><th>#</th><th>视频</th><th>文档</th></tr></thead><tbody>')
        for i, r in enumerate(ok, 1):
            parts.append(f'<tr><td>{i}</td><td>{escape_xml(r["video_name"])}</td><td><a href="{r["doc_url"]}">查看</a></td></tr>')
        parts.append('</tbody></table>')

        tmp = PROJECT_DIR / "_tmp_feishu.xml"
        tmp.write_text("\n".join(parts), encoding="utf-8")
        try:
            print("创建总文档...")
            r = _run_lark('lark-cli docs +create --content "@_tmp_feishu.xml" --as user --format json')
            if r.get("ok") or r.get("data"):
                url = r.get("data", {}).get("document", {}).get("url", "")
                print(f"[OK] 总文档: {url}")
        finally:
            tmp.unlink(missing_ok=True)


def build_batch_table_xml(videos: list[dict], excel_map: dict) -> str:
    """构建多视频汇总表格的 XML（关键帧列用占位文本）。

    Args:
        videos: [{vid_id, json_data}, ...]
        excel_map: {vid_id: {播主昵称, 视频链接, ...}}
    Returns:
        XML 字符串，关键帧列用 _kf_N_ 占位
    """
    rows_xml = []
    n = 0
    for seq, (vid_id, jdata) in enumerate(videos, 1):
        erow = excel_map.get(vid_id, {})
        title = erow.get("视频标题") or jdata.get("_meta", {}).get("video_name", vid_id)
        video_link = erow.get("视频链接", "")
        shots = jdata.get("shots", [])
        transcript = jdata.get("transcript_full", "")

        # 列1: 序号 + 链接 + 数据概览
        col1 = f'<p><b>#{seq}</b></p>'
        if video_link:
            col1 += f'<p><a href="{escape_xml(video_link)}">{escape_xml(title)}</a></p>'
        else:
            col1 += f'<p>{escape_xml(title)}</p>'
        if erow:
            for key in ("播主昵称", "抖音号", "播主粉丝数", "发布时间", "视频时长",
                         "传播指数", "商品标题", "商品品牌", "商品佣金率",
                         "视频销量", "视频销售额", "性别分布TOP1", "年龄分布TOP1"):
                val = erow.get(key, "")
                if val and val != "/":
                    col1 += f'<p>{escape_xml(key)}：{escape_xml(str(val))}</p>'
            engagement = (f"点赞：{erow.get('点赞', '')} | 评论：{erow.get('评论', '')} | "
                          f"分享：{erow.get('分享', '')} | 收藏：{erow.get('收藏', '')} | "
                          f"播放：{erow.get('播放', '')}")
            col1 += f'<p>{engagement}</p>'

        # 列2: 分镜 — 每个镜头一个占位文本
        col2 = ""
        for shot in shots:
            n += 1
            col2 += f'<p>_kf_{n}_</p>'

        # 列3: 逐字稿
        col3 = f'<p>{escape_xml(transcript)}</p>'

        # 列4: 脚本还原
        col4 = f'<p><b>【标题】</b>{escape_xml(title)}</p><p><b>【画面】</b></p>'
        for shot in shots:
            ts = shot["time_range"]["start"]
            te = shot["time_range"]["end"]
            st = shot.get("shot_type", "")
            cam = shot.get("camera_movement", "")
            scene = shot.get("scene", "")
            visual = shot.get("visual_content", "")
            ocr = shot.get("ocr_text", "")
            person = shot.get("key_人物", "")
            parts = [p for p in (scene, f"人物：{person}" if person and person != "无" else "", visual, f"画面文字：{ocr}" if ocr else "") if p]
            desc = escape_xml("，".join(parts))
            col4 += f'<p><b>分镜{shot["shot_index"]}（{ts:.0f}-{te:.0f}s）{st}/{cam}：</b>{desc}</p>'
        col4 += '<p><b>【逐字稿】</b></p>'
        for shot in shots:
            speaker = shot.get("key_人物", "")
            t = shot.get("transcript", "")
            prefix = f"{escape_xml(speaker)}：" if speaker and speaker != "无" else ""
            col4 += f'<p>{prefix}{escape_xml(t)}</p>'

        # 列5: 拆解结果 — 留空
        col5 = "<p></p>"

        rows_xml.append(
            f'<tr><td vertical-align="top">{col1}</td>'
            f'<td vertical-align="top">{col2}</td>'
            f'<td vertical-align="top">{col3}</td>'
            f'<td vertical-align="top">{col4}</td>'
            f'<td vertical-align="top">{col5}</td></tr>'
        )

    header = ('<tr>'
              '<td vertical-align="top"><p><b>序号/原链接/数据概览</b></p></td>'
              '<td vertical-align="top"><p><b>分镜（按时间顺序）</b></p></td>'
              '<td vertical-align="top"><p><b>逐字稿</b></p></td>'
              '<td vertical-align="top"><p><b>脚本还原</b></p></td>'
              '<td vertical-align="top"><p><b>拆解结果</b></p></td>'
              '</tr>')

    return (
        f'<title>爆款脚本库</title>'
        f'<p>视频数量：{len(videos)}条</p>'
        f'<table><colgroup><col/><col/><col/><col/><col/></colgroup>'
        f'<tbody>{header}{"".join(rows_xml)}</tbody></table>'
    )


def _upload_all_keyframes(videos: list[dict]) -> dict:
    """批量上传关键帧图片，返回 {vid_id: [(filename, file_token), ...]}。"""
    token_map = {}
    total = sum(len(v.get("shots", [])) for _, v in videos)
    done = 0
    for vid_id, jdata in videos:
        meta = jdata.get("_meta", {})
        stem = Path(meta.get("video_name", vid_id)).stem
        # 帧目录: 与 JSON 同级的 {stem}_frames/
        # 实际目录名可能截断，用 glob 匹配
        frame_dirs = list(Path(OUTPUT_DIR).glob(f"*_{vid_id}_frames"))
        frame_dir = frame_dirs[0] if frame_dirs else None
        token_map[vid_id] = []
        for shot in jdata.get("shots", []):
            idx = shot.get("shot_index", 0)
            if frame_dir:
                pngs = sorted(frame_dir.glob(f"*_{vid_id}_shot{idx:03d}_*.png"))
            else:
                pngs = []
            done += 1
            if pngs:
                token = upload_image(pngs[0])
                if token:
                    token_map[vid_id].append((pngs[0].name, token))
                    print(f"  [{done}/{total}] ✓ {pngs[0].name[:40]}")
                    continue
            token_map[vid_id].append(("", None))
            print(f"  [{done}/{total}] ✗ shot{idx:03d}")
    return token_map


def _build_placeholder_map(videos: list[dict], token_map: dict) -> dict:
    """根据 token_map 构建 {_kf_N_: file_token} 映射。"""
    ph = {}
    n = 0
    for vid_id, _ in videos:
        for _, ftoken in token_map.get(vid_id, []):
            n += 1
            if ftoken:
                ph[f"_kf_{n}_"] = ftoken
    return ph


def _find_all_placeholders(doc_id: str) -> list[tuple[str, str]]:
    """获取文档中所有 _kf_N_ 占位符的 (block_id, placeholder_text)。"""
    cmd = (f'lark-cli docs +fetch --doc {doc_id} --scope keyword '
           f'--keyword "_kf_" --detail with-ids --as user --format json')
    r = _run_lark(cmd)
    content = r.get("data", {}).get("document", {}).get("content", "")
    return re.findall(r'id="(doxcn[^"]+)">(_kf_\d+_)</p>', content)


def _batch_insert_images(doc_id: str, placeholder_blocks: list[tuple[str, str]], ph_map: dict):
    """批量将图片插入到占位符之后。"""
    total = len(placeholder_blocks)
    ok = 0
    for i, (block_id, placeholder) in enumerate(placeholder_blocks):
        ftoken = ph_map.get(placeholder)
        if not ftoken:
            continue
        if insert_image_after_block(doc_id, block_id, ftoken):
            ok += 1
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{total}] {ok} ok")
    print(f"  图片插入完成: {ok}/{total}")


def _batch_delete_placeholders(doc_id: str, placeholder_blocks: list[tuple[str, str]]):
    """批量删除占位符文本块。"""
    total = len(placeholder_blocks)
    ok = 0
    for i, (block_id, _) in enumerate(placeholder_blocks):
        cmd = (f'lark-cli docs +update --doc {doc_id} --command block_delete '
               f'--block-id {block_id} --as user --format json')
        r = _run_lark(cmd)
        if r.get("ok"):
            ok += 1
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{total}] {ok} ok")
    print(f"  占位符删除完成: {ok}/{total}")


def publish_batch(output_dir: Path | None = None, excel_path: str | None = None) -> dict:
    """将 output_dir 下所有视频的分镜结果写入一个飞书文档。

    Args:
        output_dir: JSON 文件所在目录
        excel_path: 飞瓜 Excel 文件路径（可选，提供则显示数据概览）
    Returns:
        {"ok": True, "doc_url": "..."} 或 {"ok": False, "error": "..."}
    """
    import openpyxl

    if output_dir is None:
        output_dir = PROJECT_DIR / "output"

    # 1. 加载 JSON
    filters = ("doc_content", "token_map", "placeholder", "insert_progress")
    json_files = sorted(
        f for f in output_dir.glob("*.json")
        if not any(x in f.name for x in filters)
    )
    if not json_files:
        return {"ok": False, "error": f"未找到 JSON: {output_dir}"}

    videos = []
    for f in json_files:
        jdata = json.loads(f.read_text(encoding="utf-8"))
        vid_id = f.stem.rsplit("_", 1)[-1]
        videos.append((vid_id, jdata))
    print(f"加载 {len(videos)} 个视频")

    # 2. 加载 Excel（可选）
    excel_map = {}
    if excel_path and Path(excel_path).exists():
        wb = openpyxl.load_workbook(excel_path, read_only=True)
        rows = list(wb["Sheet1"].iter_rows(values_only=True))
        headers = rows[0]
        col_map = {h: i for i, h in enumerate(headers) if h}
        for row in rows[1:]:
            if not row[0]:
                continue
            link = row[col_map["视频链接"]] or ""
            if "/video/" not in link:
                continue
            vid = link.split("/video/")[1].split("/")[0].split("?")[0]
            excel_map[vid] = {k: row[col_map[k]] or "" for k in col_map}
        wb.close()
        print(f"Excel 匹配 {len(excel_map)} 条")

    # 3. 上传关键帧
    print("上传关键帧图片...")
    token_map = _upload_all_keyframes(videos)

    # 4. 构建占位符映射
    ph_map = _build_placeholder_map(videos, token_map)
    print(f"占位符: {len(ph_map)} 个")

    # 5. 创建文档
    xml = build_batch_table_xml(videos, excel_map)
    tmp = PROJECT_DIR / "_tmp_batch.xml"
    tmp.write_text(xml, encoding="utf-8")
    try:
        print("创建文档...")
        r = _run_lark('lark-cli docs +create --content "@_tmp_batch.xml" --as user --format json')
        if not (r.get("ok") or r.get("data")):
            return {"ok": False, "error": r.get("error", "创建失败")}
        doc = r["data"]["document"]
        doc_id = doc["document_id"]
        doc_url = doc.get("url", "")
        print(f"文档: {doc_url}")
    finally:
        tmp.unlink(missing_ok=True)

    # 6. 插入图片
    print("插入图片...")
    ph_blocks = _find_all_placeholders(doc_id)
    _batch_insert_images(doc_id, ph_blocks, ph_map)

    # 7. 删除占位符文本
    print("清理占位符...")
    ph_blocks = _find_all_placeholders(doc_id)
    _batch_delete_placeholders(doc_id, ph_blocks)

    print(f"\n✓ 完成: {doc_url}")
    return {"ok": True, "doc_url": doc_url, "doc_id": doc_id}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, help="单个 JSON 文件路径")
    parser.add_argument("--dir", type=str, help="JSON 文件目录")
    parser.add_argument("--batch", action="store_true", help="批量模式：所有视频写入一个文档")
    parser.add_argument("--excel", type=str, help="飞瓜 Excel 路径（配合 --batch）")
    args = parser.parse_args()

    if args.batch:
        r = publish_batch(
            Path(args.dir) if args.dir else None,
            excel_path=args.excel,
        )
    elif args.file:
        r = publish_video(Path(args.file))
        if r.get("ok"):
            print(f"\n✓ {r['doc_url']}")
    else:
        publish_all(Path(args.dir) if args.dir else None)
