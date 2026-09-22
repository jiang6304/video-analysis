# -*- coding: utf-8 -*-
"""
主处理流程：扫描SMB视频 → 压缩 → VL API → 输出分镜逐字稿。
参考 D:\\work\\pick\\素材库\\hit_decompose.py 的流程，去除标签体系。
"""
import base64
import json
import traceback
from pathlib import Path

from ffmpeg_utils import compress_video_with_audio, video_duration, _ffmpeg_path, extract_keyframes
from vl_client import call_vl_api_mimo, parse_json
from prompt import build_storyboard_prompt
from config import (
    AI_VISION_API_KEY, AI_VISION_URL, AI_VISION_MODEL,
    MIMO_FPS, MIMO_MEDIA_RESOLUTION,
    COMPRESSED_DIR, OUTPUT_DIR, SMB_VIDEO_DIR, VIDEO_EXTENSIONS,
)


def scan_videos(root_dir: str) -> list[Path]:
    """递归扫描目录下的所有视频文件。"""
    root = Path(root_dir)
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(root.rglob(f"*{ext}"))
        videos.extend(root.rglob(f"*{ext.upper()}"))
    # 去重（大小写不敏感的文件系统）
    seen = set()
    unique = []
    for v in videos:
        key = str(v).lower()
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return sorted(unique, key=lambda p: str(p))


def process_single_video(video_path: Path) -> dict:
    """
    处理单个视频：压缩 → VL API → 解析 → 保存。

    返回：{status, source_name, output_file, shots, error?}
    """
    source_name = str(video_path)
    compressed = None
    try:
        # 1. 压缩视频
        print(f"\n{'='*60}")
        print(f"[1/4] 压缩视频: {video_path.name}")
        COMPRESSED_DIR.mkdir(parents=True, exist_ok=True)
        compressed = compress_video_with_audio(video_path, COMPRESSED_DIR)
        if not compressed:
            return {"status": "error", "source_name": source_name, "error": "视频压缩失败"}

        # 2. 构建 Prompt
        print(f"[2/4] 构建 Prompt...")
        prompt = build_storyboard_prompt()

        # 3. 调用 VL API（细致分镜输出量大，给更多 token）
        print(f"[3/4] 调用 VL API ({AI_VISION_MODEL}, fps={MIMO_FPS}, res={MIMO_MEDIA_RESOLUTION})...")
        video_b64 = base64.b64encode(compressed.read_bytes()).decode("ascii")
        blocks = [
            {"type": "input_text", "text": prompt},
            {"type": "input_video", "video_url": f"data:video/mp4;base64,{video_b64}"},
        ]
        vl_response = call_vl_api_mimo(
            blocks,
            max_tokens=32000,
            model=AI_VISION_MODEL,
            api_key=AI_VISION_API_KEY,
            url=AI_VISION_URL,
            fps=MIMO_FPS,
            media_resolution=MIMO_MEDIA_RESOLUTION,
        )
        response_text = vl_response.get("text", "")

        if response_text.startswith("[ERROR]"):
            return {"status": "error", "source_name": source_name, "error": response_text}

        # 4. 解析 JSON
        print(f"[4/4] 解析结果...")
        vl_dict = parse_json(response_text)
        if vl_dict is None:
            # 保存原始响应用于调试
            debug_path = OUTPUT_DIR / "debug" / f"{video_path.stem}_raw_response.txt"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(response_text, encoding="utf-8")
            return {
                "status": "error",
                "source_name": source_name,
                "error": f"JSON 解析失败，原始响应已保存到 {debug_path}",
            }

        # 5. 提取关键帧
        shots = vl_dict.get("shots", [])
        frames_dir = OUTPUT_DIR / f"{video_path.stem}_frames"
        print(f"[5/5] 提取关键帧 ({len(shots)} 个分镜)...")
        frame_map = extract_keyframes(video_path, shots, frames_dir)

        # 将关键帧路径写入 JSON
        for shot in shots:
            idx = shot.get("shot_index", 0)
            if idx in frame_map:
                shot["keyframe_path"] = str(frame_map[idx])

        # 6. 保存结果
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        vl_dict["_meta"] = {
            "video_name": video_path.name,
            "video_path": str(video_path),
        }

        json_path = OUTPUT_DIR / f"{video_path.stem}.json"
        json_path.write_text(json.dumps(vl_dict, ensure_ascii=False, indent=2), encoding="utf-8")

        txt_path = OUTPUT_DIR / f"{video_path.stem}.txt"
        txt_content = _format_readable(video_path.name, str(video_path), vl_dict)
        txt_path.write_text(txt_content, encoding="utf-8")

        print(f"  [OK] 完成: {len(shots)} 个分镜, {len(frame_map)} 张关键帧")
        print(f"       JSON: {json_path}")
        print(f"       文本: {txt_path}")
        print(f"       截图: {frames_dir}")

        return {
            "status": "done",
            "source_name": source_name,
            "output_file": str(json_path),
            "shots": len(shots),
            "frames": len(frame_map),
            "duration": vl_dict.get("video_duration_s", 0),
        }

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "source_name": source_name, "error": str(e)}
    finally:
        if compressed:
            try:
                compressed.unlink(missing_ok=True)
            except Exception:
                pass


def _format_readable(filename: str, filepath: str, data: dict) -> str:
    """将 JSON 结果格式化为可读的分镜文本。"""
    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"视频名称: {filename}")
    lines.append(f"文件地址: {filepath}")
    lines.append(f"视频时长: {data.get('video_duration_s', '?')}秒")
    lines.append(f"建议标题: {data.get('title_suggestion', '')}")
    lines.append(f"内容概要: {data.get('overall_summary', '')}")
    lines.append(f"总分镜数: {data.get('total_shots', len(data.get('shots', [])))}")
    lines.append(f"{'='*70}")
    lines.append("")

    for shot in data.get("shots", []):
        idx = shot.get("shot_index", "?")
        tr = shot.get("time_range", {})
        start = tr.get("start", 0)
        end = tr.get("end", 0)
        dur = shot.get("duration", end - start)
        shot_type = shot.get("shot_type", "")
        camera = shot.get("camera_movement", "")
        scene = shot.get("scene", "")

        lines.append(f"━━━ 分镜 {idx} ━━━ {start:.1f}s - {end:.1f}s（{dur:.1f}秒）")
        lines.append(f"  景别: {shot_type}  |  镜头: {camera}")
        lines.append(f"  场景: {scene}")
        lines.append(f"  画面: {shot.get('visual_content', '')}")
        if shot.get("ocr_text"):
            lines.append(f"  文字: {shot.get('ocr_text', '')}")
        lines.append(f"  音频: {shot.get('audio_type', '')}")
        if shot.get("sound_effects"):
            lines.append(f"  音效: {shot.get('sound_effects', '')}")
        lines.append(f"  台词: {shot.get('transcript', '')}")
        lines.append("")

    lines.append(f"{'='*70}")
    lines.append("【完整逐字稿】")
    lines.append("")
    lines.append(data.get("transcript_full", ""))
    lines.append("")
    lines.append(f"{'='*70}")

    return "\n".join(lines)


def run_batch(video_paths: list[Path] | None = None) -> list[dict]:
    """批量处理视频。不传参则扫描 SMB 默认目录。"""
    if video_paths is None:
        print(f"扫描 SMB 视频目录: {SMB_VIDEO_DIR}")
        video_paths = scan_videos(SMB_VIDEO_DIR)

    if not video_paths:
        print("未找到任何视频文件。")
        return []

    print(f"共找到 {len(video_paths)} 个视频文件：")
    for i, vp in enumerate(video_paths, 1):
        print(f"  {i}. {vp.name}")
    print()

    results = []
    for i, vp in enumerate(video_paths, 1):
        print(f"\n[{i}/{len(video_paths)}] 处理: {vp.name}")
        result = process_single_video(vp)
        results.append(result)

        if result["status"] == "done":
            print(f"  ✓ 成功 ({result['shots']} 个分镜)")
        else:
            print(f"  ✗ 失败: {result.get('error', '未知错误')}")

    # 汇总
    done = sum(1 for r in results if r["status"] == "done")
    failed = sum(1 for r in results if r["status"] == "error")
    print(f"\n{'='*70}")
    print(f"处理完成: {done} 成功, {failed} 失败, 共 {len(results)} 个")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"{'='*70}")

    return results
