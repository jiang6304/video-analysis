# -*- coding: utf-8 -*-
"""
VL API 客户端 — 支持 mimo-v2.5 和 doubao-seed 多个模型。
来源：D:\\work\\pick\\knowledge\\utils\\vl_client_mimo.py，按需精简。
"""
import json
import time
import requests


def call_vl_api_mimo(
    blocks: list[dict],
    max_tokens: int = 16000,
    retries: int = 3,
    model: str | None = None,
    api_key: str | None = None,
    url: str | None = None,
    fps: int = 2,
    media_resolution: str = "default",
) -> dict:
    """调用 mimo-v2.5 VL API（OpenAI 兼容格式，支持视频）。

    blocks 格式：
    - {"type": "input_text", "text": "..."}
    - {"type": "input_video", "video_url": "data:video/mp4;base64,..."}

    返回 {"text": str, "usage": dict}
    """
    if not api_key:
        return {"text": "[ERROR] API key missing", "usage": {}}

    mimo_content = []
    for block in blocks:
        if block.get("type") == "input_text":
            mimo_content.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "input_video":
            video_url = block.get("video_url", "")
            mimo_content.append({
                "type": "video_url",
                "video_url": {"url": video_url},
                "fps": fps,
                "media_resolution": media_resolution,
            })

    last_err = ""
    for attempt in range(retries):
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": mimo_content}],
                "max_tokens": max_tokens,
            }
            r = requests.post(
                url, headers=headers, json=payload, timeout=300,
                proxies={"http": "", "https": ""},
            )
            if r.status_code == 200:
                data = r.json()
                text = ""
                for choice in data.get("choices", []):
                    msg = choice.get("message", {})
                    text += msg.get("content", "")
                text = text.strip()
                if not text and attempt < retries - 1:
                    wait = 5 * (3 ** attempt)
                    print(f"  [WARN] mimo API 返回空内容，{wait}s 后重试 ({attempt+1}/{retries})")
                    time.sleep(wait)
                    continue
                usage = data.get("usage", {})
                return {"text": text, "usage": usage}
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = 5 * (3 ** attempt)
                print(f"  [WARN] mimo API {r.status_code}，{wait}s 后重试 ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            return {"text": f"[ERROR] {last_err}", "usage": {}}
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = str(e)
            if attempt < retries - 1:
                wait = 5 * (3 ** attempt)
                print(f"  [WARN] mimo 连接错误，{wait}s 后重试 ({attempt+1}/{retries}): {e}")
                time.sleep(wait)
                continue
        except Exception as e:
            return {"text": f"[ERROR] {e}", "usage": {}}
    return {"text": f"[ERROR] 重试 {retries} 次仍失败: {last_err}", "usage": {}}


def call_vl_api_generic(
    blocks: list[dict],
    max_tokens: int = 16000,
    retries: int = 3,
    model: str | None = None,
    api_key: str | None = None,
    url: str | None = None,
    fps: int = 2,
) -> dict:
    """通用 VL API 调用，自动检测格式（OpenAI chat / Responses API）。"""
    if not api_key:
        return {"text": "[ERROR] API key missing", "usage": {}}

    last_err = ""
    for attempt in range(retries):
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            # 尝试 chat completions 格式
            messages_content = []
            for block in blocks:
                if block.get("type") == "input_text":
                    messages_content.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "input_video":
                    video_url = block.get("video_url", "")
                    messages_content.append({
                        "type": "video_url",
                        "video_url": {"url": video_url},
                    })

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": messages_content}],
                "max_tokens": max_tokens,
            }

            r = requests.post(
                url, headers=headers, json=payload, timeout=300,
                proxies={"http": "", "https": ""},
            )
            if r.status_code == 200:
                data = r.json()
                # 解析 Responses API 格式
                text = ""
                for item in data.get("output", []):
                    if item.get("type") == "message":
                        for part in item.get("content", []):
                            text += part.get("text", "")
                if not text:
                    # 回退到 choices 格式
                    for choice in data.get("choices", []):
                        msg = choice.get("message", {})
                        text += msg.get("content", "")
                usage = data.get("usage", {})
                return {"text": text.strip(), "usage": usage}

            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = 5 * (3 ** attempt)
                print(f"  [WARN] VL API {r.status_code}，{wait}s 后重试 ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            return {"text": f"[ERROR] {last_err}", "usage": {}}
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = str(e)
            if attempt < retries - 1:
                wait = 5 * (3 ** attempt)
                print(f"  [WARN] VL 连接错误，{wait}s 后重试 ({attempt+1}/{retries}): {e}")
                time.sleep(wait)
                continue
        except Exception as e:
            return {"text": f"[ERROR] {e}", "usage": {}}
    return {"text": f"[ERROR] 重试 {retries} 次仍失败: {last_err}", "usage": {}}


def _escape_ctrl_in_json_strings(s: str) -> str:
    """将 JSON 字符串值中的非法控制字符转义为 JSON 转义序列"""
    out = []
    in_string = False
    escaped = False
    for c in s:
        if escaped:
            out.append(c)
            escaped = False
            continue
        if c == '\\':
            out.append(c)
            escaped = True
            continue
        if c == '"':
            in_string = not in_string
            out.append(c)
            continue
        if in_string:
            code = ord(c)
            if code < 32:
                if c == '\n':
                    out.append('\\n')
                elif c == '\r':
                    out.append('\\r')
                elif c == '\t':
                    out.append('\\t')
                else:
                    out.append(f'\\u{code:04x}')
                continue
        out.append(c)
    return ''.join(out)


def parse_json(raw: str) -> dict | None:
    """从 LLM 输出中提取 JSON"""
    if not raw:
        return None
    # 去除 markdown 代码块包裹
    stripped = raw.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    sub = stripped[start:end + 1]
    sub = _escape_ctrl_in_json_strings(sub)
    try:
        return json.loads(sub)
    except json.JSONDecodeError:
        try:
            return json.loads(_repair_unescaped_string_quotes(sub))
        except json.JSONDecodeError:
            return None


def _repair_unescaped_string_quotes(s: str) -> str:
    """修复 JSON 字符串值内部未转义的 ASCII 引号"""
    out = []
    in_string = False
    escaped = False
    for i, c in enumerate(s):
        if not in_string:
            out.append(c)
            if c == '"':
                in_string = True
            continue
        if escaped:
            out.append(c)
            escaped = False
            continue
        if c == '\\':
            out.append(c)
            escaped = True
            continue
        if c == '"':
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j < len(s) and s[j] in ",:}]":
                out.append(c)
                in_string = False
            else:
                out.append('\\"')
            continue
        out.append(c)
    return "".join(out)
