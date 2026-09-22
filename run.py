# -*- coding: utf-8 -*-
"""
CLI 入口：批量处理 SMB 视频，输出分镜逐字稿。

用法：
  python run.py                          # 处理默认 SMB 目录下所有视频
  python run.py --dir "D:\\videos"       # 处理指定目录
  python run.py --file "D:\\a.mp4"       # 处理单个文件
  python run.py --keyword "烤肠"         # 只处理包含关键词的视频
  python run.py --list                   # 列出所有待处理视频
"""
import argparse
import io
import sys
from pathlib import Path

# Windows 控制台 UTF-8 输出
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from config import SMB_VIDEO_DIR


def main():
    parser = argparse.ArgumentParser(description="视频分镜逐字稿批量处理工具")
    parser.add_argument("--dir", type=str, help="视频目录路径（默认 SMB 目录）")
    parser.add_argument("--file", type=str, help="处理单个视频文件")
    parser.add_argument("--keyword", type=str, help="只处理文件名包含关键词的视频")
    parser.add_argument("--list", action="store_true", help="列出所有待处理视频，不实际处理")
    parser.add_argument("--output", type=str, help="输出目录（默认 ./output）")
    args = parser.parse_args()

    from pipeline import scan_videos, run_batch, process_single_video
    from config import OUTPUT_DIR

    # 覆盖输出目录
    if args.output:
        import config
        config.OUTPUT_DIR = Path(args.output)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 单文件模式
    if args.file:
        vp = Path(args.file)
        if not vp.exists():
            print(f"文件不存在: {vp}")
            sys.exit(1)
        result = process_single_video(vp)
        if result["status"] == "done":
            print(f"\n✓ 处理完成: {result['segments']} 个分镜")
        else:
            print(f"\n✗ 处理失败: {result.get('error')}")
            sys.exit(1)
        return

    # 目录模式
    root = args.dir or SMB_VIDEO_DIR
    print(f"扫描目录: {root}")
    videos = scan_videos(root)

    # 关键词过滤
    if args.keyword:
        videos = [v for v in videos if args.keyword in v.name]
        print(f"关键词过滤 '{args.keyword}'：剩余 {len(videos)} 个视频")

    if not videos:
        print("未找到任何视频文件。")
        sys.exit(0)

    # 列表模式
    if args.list:
        print(f"\n共 {len(videos)} 个视频：")
        for i, vp in enumerate(videos, 1):
            rel = vp.relative_to(Path(root)) if str(vp).startswith(str(root)) else vp
            print(f"  {i}. {rel}")
        return

    # 批量处理
    run_batch(videos)


if __name__ == "__main__":
    main()
