# -*- coding: utf-8 -*-
"""
ffmpeg 工具：查找、时长、压缩、音频提取。
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

_FFMPEG: str | None = None

if os.name == "nt":
    import ctypes
    _kernel32 = ctypes.windll.kernel32
    _kernel32.SetErrorMode(_kernel32.GetErrorMode() | 0x0001)  # SEM_FAILCRITICALERRORS


def run_ffmpeg(cmd: list[str], timeout: int):
    """执行 ffmpeg 子进程；Windows 下偶发 0xc000012d 重试一次。"""
    import time
    r = subprocess.run(
        cmd, capture_output=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    if r.returncode < 0:
        time.sleep(2)
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    return r


def find_ffmpeg() -> str:
    """多级回退查找 ffmpeg"""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG

    f = shutil.which("ffmpeg")
    if f:
        _FFMPEG = f
        return f

    try:
        import imageio_ffmpeg
        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        return _FFMPEG
    except ImportError:
        pass

    for p in Path(os.environ.get("LOCALAPPDATA", "")).glob(
        "ms-playwright/ffmpeg-*/ffmpeg-win64.exe"
    ):
        _FFMPEG = str(p)
        return _FFMPEG

    for ver in ["Python314", "Python313", "Python312", "Python311", "Python310"]:
        p = (
            Path(os.environ.get("APPDATA", ""))
            / ver
            / "site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"
        )
        if p.exists():
            _FFMPEG = str(p)
            return _FFMPEG

    _FFMPEG = "ffmpeg"
    return _FFMPEG


def video_duration(vp) -> float:
    """ffprobe 优先 → ffmpeg stderr 正则回退。失败返回 0"""
    ffmpeg = find_ffmpeg()
    fp = str(Path(ffmpeg).parent / "ffprobe.exe")
    if not Path(fp).exists():
        fp = ffmpeg.replace("ffmpeg", "ffprobe")

    import re
    vp_str = _ffmpeg_path(vp)

    for tool in [fp, ffmpeg]:
        try:
            if "ffprobe" in tool:
                r = run_ffmpeg(
                    [tool, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", vp_str], 30)
                if r.stdout.strip():
                    return float(r.stdout.strip())
            else:
                r = run_ffmpeg([tool, "-i", vp_str], 30)
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", r.stderr or "")
                if m:
                    return (
                        int(m.group(1)) * 3600
                        + int(m.group(2)) * 60
                        + int(m.group(3))
                        + int(m.group(4)) / 100
                    )
        except Exception:
            continue
    return 0


def _ffmpeg_path(p) -> str:
    """将路径转为 ffmpeg 可用的字符串（UNC/SMB 路径保持正斜杠）"""
    s = str(p)
    if s.startswith("\\\\"):
        return "//" + s.lstrip("\\").replace("\\", "/")
    return s


def compress_video_with_audio(vp, output_dir: Path, max_size_mb: int = 50) -> Path | None:
    """压缩视频保留音轨：640px宽, crf=35, 单声道16kHz。
    超限时自动降质重试。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    vp_str = _ffmpeg_path(vp)
    path_hash = hashlib.md5(vp_str.encode("utf-8")).hexdigest()[:12]
    out = output_dir / f"{path_hash}_{Path(vp).stem}_compressed.mp4"

    ffmpeg = find_ffmpeg()
    duration = video_duration(vp_str)
    timeout = max(180, min(3600, int(duration * 3))) if duration > 0 else 600

    strategies = [
        {"vf": "scale=640:-2", "crf": "35", "label": "默认 640px/crf35"},
        {"vf": "scale=640:-2,fps=10", "crf": "35", "label": "降帧10fps"},
        {"vf": "scale=480:-2,fps=10", "crf": "40", "label": "480px/10fps/crf40"},
        {"vf": "scale=320:-2,fps=8", "crf": "45", "label": "极限 320px/8fps/crf45"},
    ]

    for strat in strategies:
        cmd = [
            ffmpeg, "-i", vp_str,
            "-vf", strat["vf"],
            "-crf", strat["crf"], "-preset", "ultrafast",
            "-ac", "1", "-ar", "16000",
            "-y", str(out),
        ]
        r = run_ffmpeg(cmd, timeout)
        if r.returncode != 0:
            tail = (r.stderr or "")[-200:].replace("\n", " ")
            print(f"  [WARN] 压缩失败 ({Path(vp).name}, {strat['label']}): rc={r.returncode} {tail}")
            continue

        size_mb = out.stat().st_size / (1024 * 1024) if out.exists() else 0
        if size_mb > 0 and size_mb < 1000:
            if size_mb <= max_size_mb:
                print(f"  [OK] 压缩完成: {strat['label']}, {size_mb:.1f}MB")
                return out
            else:
                print(f"  [WARN] 压缩后 {size_mb:.1f}MB > {max_size_mb}MB，尝试更激进策略...")
                continue

    if out.exists() and out.stat().st_size > 1000:
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"  [WARN] 所有策略均超限，使用最终结果: {size_mb:.1f}MB")
        return out
    return None


def extract_frame(video_path, timestamp_sec: float, output_path: Path) -> bool:
    """从视频中抽取单帧（PNG），用于分镜关键帧截图。"""
    vp_str = _ffmpeg_path(video_path)
    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg, "-ss", f"{timestamp_sec:.3f}",
        "-i", vp_str,
        "-frames:v", "1",
        "-q:v", "2",
        "-y", str(output_path),
    ]
    r = run_ffmpeg(cmd, 30)
    if r.returncode != 0:
        tail = (r.stderr or "")[-200:].replace("\n", " ")
        print(f"  [WARN] 抽帧失败 (t={timestamp_sec:.1f}s): {tail}")
        return False
    return output_path.exists() and output_path.stat().st_size > 1000


def _find_scene_changes(video_path, start_sec: float, end_sec: float, threshold: float = 0.3) -> list[float]:
    """用 ffmpeg scene filter 检测 [start, end] 区间内的场景切换时间点。"""
    vp_str = _ffmpeg_path(video_path)
    ffmpeg = find_ffmpeg()
    # 只分析区间内，用 trim 裁剪 + scene filter
    vf = f"trim=start={start_sec}:end={end_sec},setpts=PTS-STARTPTS,select='gt(scene\\,{threshold})',showinfo"
    cmd = [
        ffmpeg, "-ss", f"{start_sec:.3f}",
        "-i", vp_str,
        "-t", f"{end_sec - start_sec:.3f}",
        "-vf", vf,
        "-f", "null", "-",
    ]
    r = run_ffmpeg(cmd, 60)
    if r.returncode != 0:
        return []
    # 从 stderr 解析 showinfo 输出的 pts_time
    import re
    times = []
    for m in re.finditer(r"pts_time:(\d+\.?\d*)", r.stderr or ""):
        t = float(m.group(1)) + start_sec  # 还原到全局时间
        times.append(round(t, 3))
    return times


def extract_keyframes(video_path, shots: list[dict], output_dir: Path) -> dict[int, Path]:
    """
    为每个分镜提取关键帧截图。
    策略：在分镜边界附近用 ffmpeg scene filter 检测真实切换点，
    确保截帧在真实切换之后、不会截到上一个分镜的画面。

    返回 {shot_index: frame_path}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    frame_map = {}

    if not shots:
        return frame_map

    # 预算所有分镜边界的真实切换点
    # 只检测分镜之间的边界（前一个分镜end附近）
    boundary_offsets = {}  # boundary_time -> real_transition_time
    for i in range(1, len(shots)):
        prev_end = shots[i - 1].get("time_range", {}).get("end", 0)
        # 在 VL 边界前后 1.5s 检测场景切换
        search_start = max(0, prev_end - 1.5)
        search_end = prev_end + 1.5
        changes = _find_scene_changes(video_path, search_start, search_end, threshold=0.3)
        if changes:
            # 取最接近 VL 边界的切换点
            best = min(changes, key=lambda t: abs(t - prev_end))
            boundary_offsets[i] = best

    for shot in shots:
        idx = shot.get("shot_index", 0)
        tr = shot.get("time_range", {})
        start = tr.get("start", 0)
        end = tr.get("end", start + 1)
        dur = end - start

        # 确定真实的起始时间：用检测到的切换点，否则用 VL 给的 start
        real_start = boundary_offsets.get(idx, start)
        # 确保 real_start 在分镜范围内
        real_start = max(start, min(real_start, start + 1.5))

        # 截帧位置：从真实起始点偏移，避开切换残留
        remaining = end - real_start
        if remaining <= 0:
            target_time = start + dur * 0.3
        else:
            # 至少偏移 1 秒，或剩余时长的 30%
            offset = max(1.0, remaining * 0.3)
            target_time = real_start + min(offset, remaining * 0.8)

        out_path = output_dir / f"{stem}_shot{idx:03d}_{target_time:.1f}s.png"

        if extract_frame(video_path, target_time, out_path):
            frame_map[idx] = out_path
        else:
            # 失败回退到 VL 起始时间 + 30%
            fallback = start + dur * 0.3
            out_path2 = output_dir / f"{stem}_shot{idx:03d}_{fallback:.1f}s.png"
            if extract_frame(video_path, fallback, out_path2):
                frame_map[idx] = out_path2

    return frame_map
