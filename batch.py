# -*- coding: utf-8 -*-
"""
分批处理脚本：先压缩所有视频，再逐个调 API。
用法：
  python batch.py compress --dir "飞瓜目录" --output "output/飞瓜播放量TOP"
  python batch.py analyze --dir "output/飞瓜播放量TOP"
  python batch.py feishu --dir "output/飞瓜播放量TOP"
"""
import argparse
import base64
import io
import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, 'buffer') and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass  # 后台运行时 buffer 可能已关闭

from config import COMPRESSED_DIR, OUTPUT_DIR, VIDEO_EXTENSIONS, AI_VISION_API_KEY, AI_VISION_URL, AI_VISION_MODEL
from ffmpeg_utils import compress_video_with_audio, extract_keyframes
from vl_client import call_vl_api_mimo, parse_json
from prompt import build_storyboard_prompt


def scan_videos(root_dir: str) -> list[Path]:
    root = Path(root_dir)
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(root.rglob(f"*{ext}"))
        videos.extend(root.rglob(f"*{ext.upper()}"))
    seen = set()
    unique = []
    for v in videos:
        key = str(v).lower()
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return sorted(unique, key=lambda p: str(p))


def step_compress(video_dir: str, output_dir: str):
    """第一步：压缩所有视频（保持原始帧率，不抽帧）"""
    videos = scan_videos(video_dir)
    print(f"找到 {len(videos)} 个视频，开始压缩...\n")

    compressed_dir = Path(output_dir) / "compressed"
    compressed_dir.mkdir(parents=True, exist_ok=True)

    for i, vp in enumerate(videos, 1):
        out = compressed_dir / f"{vp.stem}_compressed.mp4"
        if out.exists() and out.stat().st_size > 1000:
            size_mb = out.stat().st_size / (1024 * 1024)
            print(f"[{i}/{len(videos)}] {vp.name} → 已存在 ({size_mb:.1f}MB)")
            continue

        print(f"[{i}/{len(videos)}] {vp.name} → 压缩中...", end=" ", flush=True)
        result = compress_video_with_audio(vp, compressed_dir)
        if result:
            size_mb = result.stat().st_size / (1024 * 1024)
            print(f"✓ ({size_mb:.1f}MB)")
        else:
            print("✗ 失败")

    print(f"\n压缩完成，文件在: {compressed_dir}")


def _analyze_one(vp: Path, out_dir: Path, compressed_dir: Path, idx: int, total: int) -> dict:
    """分析单个视频（线程安全）。返回 {status, name, shots?, error?}"""
    tag = f"[{idx}/{total}] {vp.name}"
    print(f"{tag} 开始", flush=True)

    matches = list(compressed_dir.glob(f"*_{vp.stem}_compressed.mp4"))
    if not matches:
        msg = f"{tag} ✗ 压缩文件不存在"
        print(msg, flush=True)
        return {"status": "skip", "name": vp.name, "error": "压缩文件不存在"}

    compressed = matches[0]
    try:
        video_b64 = base64.b64encode(compressed.read_bytes()).decode("ascii")
        prompt = build_storyboard_prompt()
        blocks = [
            {"type": "input_text", "text": prompt},
            {"type": "input_video", "video_url": f"data:video/mp4;base64,{video_b64}"},
        ]
        print(f"{tag} API调用中...", flush=True)
        vl_response = call_vl_api_mimo(
            blocks, max_tokens=32000,
            model=AI_VISION_MODEL, api_key=AI_VISION_API_KEY, url=AI_VISION_URL,
        )
        response_text = vl_response.get("text", "")

        if response_text.startswith("[ERROR]"):
            print(f"{tag} ✗ API错误: {response_text[:200]}", flush=True)
            return {"status": "error", "name": vp.name, "error": response_text[:200]}

        vl_dict = parse_json(response_text)
        if vl_dict is None:
            debug_path = out_dir / "debug" / f"{vp.stem}_raw.txt"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(response_text, encoding="utf-8")
            print(f"{tag} ✗ JSON解析失败", flush=True)
            return {"status": "error", "name": vp.name, "error": "JSON解析失败"}

        shots = vl_dict.get("shots", [])
        frames_dir = out_dir / f"{vp.stem}_frames"
        print(f"{tag} 提取{len(shots)}张关键帧...", flush=True)
        frame_map = extract_keyframes(vp, shots, frames_dir)
        for shot in shots:
            sidx = shot.get("shot_index", 0)
            if sidx in frame_map:
                shot["keyframe_path"] = str(frame_map[sidx])

        vl_dict["_meta"] = {"video_name": vp.name, "video_path": str(vp)}
        json_path = out_dir / f"{vp.stem}.json"
        json_path.write_text(json.dumps(vl_dict, ensure_ascii=False, indent=2), encoding="utf-8")

        from pipeline import _format_readable
        txt_path = out_dir / f"{vp.stem}.txt"
        txt_path.write_text(_format_readable(vp.name, str(vp), vl_dict), encoding="utf-8")

        print(f"{tag} ✓ {len(shots)}个分镜, {len(frame_map)}张关键帧", flush=True)
        return {"status": "done", "name": vp.name, "shots": len(shots)}

    except Exception as e:
        traceback.print_exc()
        print(f"{tag} ✗ 异常: {e}", flush=True)
        return {"status": "error", "name": vp.name, "error": str(e)}


def step_analyze(video_dir: str, output_dir: str, workers: int = 3):
    """第二步：并行调用 VL API 分析（跳过已处理的）"""
    videos = scan_videos(video_dir)
    out_dir = Path(output_dir)
    compressed_dir = out_dir / "compressed"
    out_dir.mkdir(parents=True, exist_ok=True)

    pending = []
    for vp in videos:
        json_path = out_dir / f"{vp.stem}.json"
        if json_path.exists():
            print(f"跳过（已有）: {vp.name}")
        else:
            pending.append(vp)

    if not pending:
        print("所有视频已处理完毕。")
        return []

    print(f"\n待处理 {len(pending)} 个视频，并行分析（{workers}线程）...\n")

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_analyze_one, vp, out_dir, compressed_dir, i, len(pending)): vp
            for i, vp in enumerate(pending, 1)
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                vp = futures[future]
                results.append({"status": "error", "name": vp.name, "error": str(e)})

    done = sum(1 for r in results if r["status"] == "done")
    failed = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skip")
    print(f"\n分析完成: {done}成功, {failed}失败, {skipped}跳过, 共{len(results)}个")
    print(f"输出目录: {out_dir}")
    return results


def step_feishu(output_dir: str):
    """第三步：发布到飞书"""
    from feishu import publish_all
    publish_all(Path(output_dir))


def main():
    parser = argparse.ArgumentParser(description="视频分镜分批处理")
    sub = parser.add_subparsers(dest="command")

    p_compress = sub.add_parser("compress", help="第一步：压缩所有视频")
    p_compress.add_argument("--dir", required=True, help="视频源目录")
    p_compress.add_argument("--output", required=True, help="输出目录")

    p_analyze = sub.add_parser("analyze", help="第二步：并行调 API 分析")
    p_analyze.add_argument("--dir", required=True, help="视频源目录（同 compress --dir）")
    p_analyze.add_argument("--output", required=True, help="输出目录（同 compress --output）")
    p_analyze.add_argument("--workers", type=int, default=3, help="并行线程数（默认3）")

    p_feishu = sub.add_parser("feishu", help="第三步：发布到飞书")
    p_feishu.add_argument("--dir", required=True, help="输出目录（含 .json 文件）")

    args = parser.parse_args()

    if args.command == "compress":
        step_compress(args.dir, args.output)
    elif args.command == "analyze":
        step_analyze(args.dir, args.output, args.workers)
    elif args.command == "feishu":
        step_feishu(args.dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
