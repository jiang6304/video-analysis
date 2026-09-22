# 视频分镜逐字稿自动拆解工具

通过 SMB 获取视频文件，用 ffmpeg 压缩后调用视觉模型（mimo-v2.5）输出时间轴分镜和逐字稿，最终写入飞书云文档。

---

## 项目文件清单

```
D:\work\NEW\
├── .env                  # API 配置（mimo-v2.5 Key/URL/Model）
├── config.py             # 环境变量加载、SMB 路径、模型配置
├── ffmpeg_utils.py       # ffmpeg 工具：视频压缩、关键帧截图
├── vl_client.py          # mimo-v2.5 VL API 客户端
├── prompt.py             # 分镜逐字稿提示词
├── pipeline.py           # 主处理流程：扫描 → 压缩 → VL API → 截图 → 输出
├── batch.py              # 分批处理：compress / analyze / feishu 三步，支持多线程
├── feishu.py             # 飞书云文档写入模块
├── run.py                # CLI 入口（单文件/目录/关键词过滤）
├── publish_douyin.py     # 一次性发布脚本
├── test_model1.py        # 模型1(doubao)单视频测试
├── test_compare.py       # 双模型对比测试
├── compressed/           # 临时压缩文件
└── output/               # 输出目录
    ├── *.json            # 结构化分镜数据
    ├── *.txt             # 可读文本
    ├── *_frames/         # 关键帧截图（PNG）
    ├── debug/            # API 解析失败的原始响应
    └── publish_log.txt   # 飞书发布日志
```

---

## 核心模块

### config.py — 配置中心

加载 `.env` 环境变量，导出全局常量：

| 常量 | 说明 |
|------|------|
| `SMB_VIDEO_DIR` | SMB 网络共享视频源路径 |
| `VIDEO_EXTENSIONS` | 支持的视频格式：`.mp4 .avi .mov .mkv .flv .wmv` |
| `AI_VISION_API_KEY / URL / MODEL` | mimo-v2.5 API 配置（模型2，默认） |
| `AI_VISION1_API_KEY / URL / MODEL` | doubao-seed API 配置（模型1） |
| `OUTPUT_DIR` | 输出目录（`./output`） |
| `COMPRESSED_DIR` | 压缩缓存目录（`./compressed`） |

### ffmpeg_utils.py — ffmpeg 工具集（214行）

| 函数 | 用途 |
|------|------|
| `find_ffmpeg()` | 多级回退查找 ffmpeg：PATH → imageio_ffmpeg → ms-playwright → Python目录 |
| `video_duration(vp)` | ffprobe 获取时长，回退到 ffmpeg stderr 正则解析 |
| `compress_video_with_audio(vp, output_dir, max_size_mb=50)` | 4级递进压缩（640px/crf35 → 480px → 320px/crf45），保留音轨 |
| `extract_frame(video_path, timestamp_sec, output_path)` | 抽取指定时间戳的单帧 PNG |
| `extract_keyframes(video_path, shots, output_dir)` | 为每个分镜在 30% 时间点提取关键帧，失败回退到起始时间 |

### vl_client.py — VL API 客户端（141行）

| 函数 | 用途 |
|------|------|
| `call_vl_api_mimo(blocks, max_tokens=16000, retries=3, ...)` | 调用 mimo-v2.5 API（OpenAI 兼容格式），支持视频 base64 输入，指数退避重试 |
| `parse_json(raw)` | 从 LLM 输出提取 JSON：直接解析 → 截取 `{...}` → 修复未转义引号后重试 |

### prompt.py — Prompt 构建器（115行）

`build_storyboard_prompt()` 返回完整的分镜分析提示词，核心规则：

- **只在人物/场景明显变化时拆分**，同一人物连续动作不拆分
- 时间轴精确到 0.5 秒
- 每个分镜输出：景别、镜头运动、场景、人物、画面描述、OCR文字、音频类型、台词
- 整体输出：时长、标题建议、概要、完整逐字稿

### pipeline.py — 主处理流程（224行）

| 函数 | 用途 |
|------|------|
| `scan_videos(root_dir)` | 递归扫描目录下所有视频，大小写去重 |
| `process_single_video(video_path)` | 单视频完整流水线：压缩 → Prompt → base64 → VL API → 解析 → 关键帧 → 保存 |
| `_format_readable(filename, filepath, data)` | JSON → 人类可读分镜文本 |
| `run_batch(video_paths)` | 批量处理，输出汇总统计 |

### batch.py — 分批处理脚本（216行）

三步独立子命令，支持 `--workers` 多线程并行：

```bash
python batch.py compress  --dir "视频目录" --output "输出目录"
python batch.py analyze   --dir "视频目录" --output "输出目录" --workers 3
python batch.py feishu    --dir "输出目录"
```

| 函数 | 用途 |
|------|------|
| `step_compress(video_dir, output_dir)` | 压缩所有视频 |
| `_analyze_one(vp, out_dir, compressed_dir, idx, total)` | 线程安全的单视频分析 |
| `step_analyze(video_dir, output_dir, workers=3)` | ThreadPoolExecutor 并行调 VL API，跳过已处理的 |
| `step_feishu(output_dir)` | 调用 feishu.publish_all 发布到飞书 |

### feishu.py — 飞书云文档写入（450+行）

通过 `lark-cli`（飞书官方 CLI）完成文档创建和图片插入。两种发布模式：

**单视频模式** — 每个视频一个独立文档：

| 函数 | 用途 |
|------|------|
| `upload_image(image_path)` | 上传图片到飞书 Drive，返回 file_token |
| `build_table_xml(data)` | 构建飞书文档 XML（元信息表 + 分镜总表 + 逐字稿） |
| `publish_video(json_path)` | 单视频发布：上传截图 → 创建文档 → 插入图片到表格单元格 |
| `publish_all(output_dir)` | 批量发布多个单视频文档 + 创建汇总文档 |

**批量汇总模式** — 所有视频写入一个文档（5列表格）：

| 函数 | 用途 |
|------|------|
| `build_batch_table_xml(videos, excel_map)` | 构建多视频汇总表格（序号/数据、分镜图、逐字稿、脚本还原、拆解结果） |
| `_upload_all_keyframes(videos)` | 批量上传所有关键帧图片到飞书 Drive |
| `_batch_insert_images(doc_id, blocks, ph_map)` | 批量将图片插入到占位符位置 |
| `_batch_delete_placeholders(doc_id, blocks)` | 批量清理占位符文本 |
| `publish_batch(output_dir, excel_path)` | 完整批量流程：上传 → 创建 → 插入 → 清理 |

---

## 依赖关系

```
config.py ← 被所有模块引用
  ↑
  ├── pipeline.py ← ffmpeg_utils + vl_client + prompt
  ├── batch.py    ← ffmpeg_utils + vl_client + prompt + feishu + pipeline._format_readable
  ├── feishu.py
  └── run.py      ← pipeline
```

---

## 处理流程

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 扫描 SMB 视频目录                                          │
│    (路径配置在 .env 的 SMB_VIDEO_DIR)                          │
│    递归查找 .mp4/.avi/.mov/.mkv/.flv/.wmv                     │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ffmpeg 压缩视频                                            │
│    640px宽 / CRF35 / ultrafast / 单声道16kHz                   │
│    超50MB自动降质：降帧→480px→320px极限压缩                      │
│    压缩后 base64 编码                                         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 调用 mimo-v2.5 VL API                                     │
│    发送：压缩视频(base64) + 分镜提示词                           │
│    接收：JSON（时间轴分镜 + 逐字稿）                             │
│    max_tokens=32000                                          │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. ffmpeg 提取关键帧                                          │
│    scene filter 在 VL 边界 ±1.5s 检测真实场景切换点              │
│    从真实切换点 +1s 截帧（避开边界残留）                          │
│    输出 PNG 到 output/{stem}_frames/                          │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. 保存输出                                                   │
│    output/{stem}.json   — 结构化数据                           │
│    output/{stem}.txt    — 可读文本                             │
│    output/{stem}_frames/ — 关键帧截图                          │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. 写入飞书云文档                                              │
│    上传截图 → 创建文档（含表格）→ 插入图片到表格单元格             │
│    创建总文档汇总所有子文档链接                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## CLI 用法

### run.py — 简单 CLI

```bash
python run.py                                    # 处理默认 SMB 目录
python run.py --dir "D:\videos"                  # 指定目录
python run.py --file "D:\video.mp4"              # 单个文件
python run.py --keyword "烤肠"                    # 关键词过滤
python run.py --list                             # 只列出不处理
python run.py --dir "D:\videos" --output "D:\out" # 指定输出目录
```

### batch.py — 分批处理（推荐）

```bash
# 第一步：压缩所有视频
python batch.py compress --dir "视频目录" --output "输出目录"

# 第二步：并行调 API 分析（3线程）
python batch.py analyze --dir "视频目录" --output "输出目录" --workers 3

# 第三步：发布到飞书
python batch.py feishu --dir "输出目录"
```

### feishu.py — 飞书发布

```bash
# 单视频模式：每个视频一个独立文档
python feishu.py --file "output/xxx.json"        # 单个文件
python feishu.py --dir "output"                  # 目录下所有JSON

# 批量汇总模式：所有视频写入一个文档（含飞瓜Excel数据概览）
python feishu.py --batch --dir "output/飞瓜鸡肉肠" --excel "播主视频数据统计_202609-飞瓜.xlsx"
```

---

## 分镜拆分规则

| 情况 | 是否拆分 |
|------|----------|
| 人物换了（A→B，或人物消失/出现） | ✅ 拆 |
| 场景换了（室内→室外，A房间→B房间） | ✅ 拆 |
| 内容跳转（说话→突然在吃东西） | ✅ 拆 |
| 插入画面（主画面→产品特写/配料表/工厂） | ✅ 拆 |
| 同一人物连续动作（放鸡排→翻鸡排→装盘） | ❌ 不拆 |
| 同一场景内推拉摇移 | ❌ 不拆 |
| 表情变化（微笑→大笑） | ❌ 不拆 |
| 字幕随台词出现/消失 | ❌ 不拆 |
| 同一段对话的多句台词 | ❌ 不拆 |

通常 3-15 秒一个分镜，人物场景不变可长达 20-30 秒。

---

## 模型对比

| 模型 | API端点 | 分镜粒度 | 时间精度 | 幻觉风险 |
|------|---------|---------|---------|---------|
| mimo-v2.5 | xiaomimimo.com | 细（6-10段） | 中（新prompt后改善） | 长视频有风险 |
| doubao-seed-2-0-lite | ark.cn-beijing.volces.com | 粗（3-6段） | 高（边界偏差<1s） | 低 |

- **mimo-v2.5**：分镜粒度细，能拆分出动作单元，但长视频时间边界易漂移，需在prompt中加0.1秒精度约束+反幻觉规则
- **doubao-seed**：时间边界准确，拆分合理，但粒度粗；不支持所有视频格式
- **关键帧截图**：使用 ffmpeg scene filter 在VL边界附近检测真实场景切换点，确保截帧在正确分镜内

---

## 飞书文档格式

每个视频的子文档结构：

```xml
<title>{视频名称}</title>

<!-- 基本信息表 -->
<table>
  <tr><th>文件地址</th><td colspan="3">{SMB完整路径}</td></tr>
  <tr><th>时长</th><td>{秒数}</td><th>建议标题</th><td>{标题}</td></tr>
  <tr><th>概要</th><td colspan="3">{内容概要}</td></tr>
  <tr><th>分镜数</th><td>{数量}</td><td></td><td></td></tr>
</table>

<!-- 分镜总表 -->
<h1>分镜总表</h1>
<table>
  <thead>
    <tr><th>#</th><th>时间</th><th>景别</th><th>镜头</th><th>场景</th>
        <th>人物</th><th>关键帧</th><th>画面描述</th><th>音频</th><th>台词</th></tr>
  </thead>
  <tbody>
    <tr><td>1</td><td>0.0s - 5.0s (5.0s)</td><td>中景</td>...
        <td><img src="{file_token}"/></td>...</tr>
  </tbody>
</table>

<!-- 完整逐字稿 -->
<h1>完整逐字稿</h1>
```

---

## 依赖

### 系统依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| Python 3.10+ | 运行环境 | — |
| ffmpeg | 视频压缩、关键帧截图 | `winget install ffmpeg` 或从 ffmpeg.org 下载 |
| Node.js | 运行 lark-cli | `winget install OpenJS.NodeJS` |
| lark-cli | 飞书文档 API（创建/编辑/上传） | `npm install -g @larksuite/cli` |

### Python 包依赖

```
pip install -r requirements.txt
```

| 包 | 用途 | 是否必需 |
|----|------|---------|
| `openpyxl` | 读取飞瓜 Excel 数据 | 仅批量模式（`--excel`）需要 |
| `requests` | 调用 mimo-v2.5 VL API | 分析步骤需要 |

### lark-cli 配置

首次使用需登录飞书账号：

```bash
lark-cli auth login --domain all    # 授权所有权限
lark-cli auth status --verify       # 确认登录状态
```

### 环境变量（.env）

```
SMB_VIDEO_DIR=//server/share/path   # SMB 视频源路径（内部地址，保密）
AI_VISION2_API_KEY=xxx              # mimo-v2.5 API Key
AI_VISION2_URL=https://...          # API 端点
AI_VISION2_MODEL=mimo-v2.5          # 模型名
```

---

## 处理计划

| 目录 | 视频数 | 分析 | 飞书 | 状态 |
|------|--------|------|------|------|
| 蝉圈圈 | 3 | ✅ | ✅ | 已完成 |
| 飞瓜播放量TOP | 6 | ✅ | ✅ | 已完成 |
| 抖音关键词/烤肠 | 15 | ✅ | ✅ | 已完成 |
| **合计** | **24** | **24** | **24** | ✅ |

