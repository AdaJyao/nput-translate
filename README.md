# NPU 常驻翻译

在 Intel **NPU**（Intel AI Boost）上跑极轻量中英翻译，目标是**省电 / 可常驻**，不是拼速度。

纯本地推理，不联网、不调云 API；把 NPU 交给翻译，独显可以完全休眠。

![界面](gui-exe.png)

## 实测数据

测试平台：Intel Core Ultra 9 275HX（Intel AI Boost NPU）+ OpenVINO 2026.4 + MarianMT `opus-mt-zh-en`

| 指标 | NPU | CPU 对照 |
|---|---|---|
| 吞吐 | **375.8 句/分钟** | 129.5 句/分钟 |
| 延迟均值 | **158 ms** | 460 ms |
| 延迟 P50 | **141 ms** | 427 ms |
| 最快单句 | 89 ms | 251 ms |
| 空闲 CPU | **0 %** | — |
| 常驻内存 | 约 5 MB（WorkingSet） | — |

**NPU 比同模型 CPU 快 46%**，同时几乎不占 CPU —— 这正是「省电常驻」的实测依据。

### 序列长度分档（关键优化）

NPU 只接受全静态形状，padding 浪费会直接变成延迟。因此按源句 token 数自动选档：

| 档位 | 延迟（20 句均值） |
|---|---|
| 64x64 | 153 ms |
| 96x96 | 190 ms |
| 128x128 | 248 ms |

分档后压测均值从 248 ms 降到 **158 ms**，吞吐翻倍。

## 特性

- **NPU 推理**：OpenVINO 在 Intel AI Boost 上跑 MarianMT，无需独显
- **图形界面**：CustomTkinter 现代界面，可调设备 / 档位 / 端口 / 开机自启 / 后台服务
- **模型管理**：界面内查看已装模型、一键切换、下载并转换新模型（中→英 / 英→中 / 日→中等）
- **常驻服务**：界面关闭后服务继续跑；空闲 CPU 0%
- **双协议**：TCP 8715（GUI/客户端）+ HTTP 8716（浏览器插件）
- **浏览器插件兼容**：实现 [kiss-translator 自定义接口规范](https://github.com/fishjar/kiss-translator/blob/master/custom-api_v2.md)，Hook 全留空即可用
- **系统托盘**：右键菜单开界面 / 起停服务
- **可打包**：`build.ps1` 一键生成独立 exe（PyInstaller）

## 快速开始

### 1. 准备环境

```powershell
git clone https://github.com/djj12589/nput-translate.git
cd nput-translate
.\setup.ps1            # 创建 .venv 并安装运行时依赖
```

### 2. 导出模型（约 445 MB，不入库）

```powershell
.\export_model.ps1     # Helsinki-NLP/opus-mt-zh-en → models\opus-mt-zh-en-ov
```

### 3. 运行

```powershell
.\启动服务.bat                                    # 起后台服务
.\.venv\Scripts\python.exe client.py --text "外面下雨了，记得带伞。"
.\.venv\Scripts\python.exe gui.py                 # 图形界面
```

起托盘常驻：

```powershell
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File .\tray.ps1
```

### 打包成 exe

```powershell
.\build.ps1            # 产物在 dist\NPU翻译\
```

详见 [BUILD.md](BUILD.md)。

## 服务协议

### TCP 8715（换行分隔 JSON）

```
{"text": "今天天气真好"}   →   {"ok":true,"src":"...","dst":"...","ms":158.0,"tier":"64x64"}
{"cmd": "status"}          →   {"ok":true,"device":"NPU","tiers":[...],"uptime_s":12.3}
```

### HTTP 8716（浏览器插件 / 调试）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/translate` | POST / GET | kiss 非聚合翻译 |
| `/translate/batch` | POST / GET | 聚合翻译 |
| `/status` | GET | 设备、档位、运行时长 |
| `/selftest` | GET | 完整自检（含失败 traceback） |
| `/devices` | GET | 可用 OpenVINO 设备 |
| `/ping` | GET | 存活探测 |
| `/shutdown` | POST | 优雅停止 |

地址栏直接测：

```
http://127.0.0.1:8716/translate?text=今天天气真好&to=en
```

浏览器插件接入见 [KISS.md](KISS.md)。

## 语言方向

当前只装 `opus-mt-zh-en`（中→英）一个模型，因此：

| 源文 | 目标 | 行为 |
|---|---|---|
| 中文 | 英文 | **真正翻译** |
| 中文 | 其他语言 | 原样返回，不报错 |
| 英文 | 中文 | 原样返回，不报错 |

即「中→英才翻，其余原样返回」，插件装上即可用。

## 模型管理

界面「模型」页可以：

- 查看已安装模型（语言方向、体积、当前使用中）
- **一键切换**模型 → 自动重启服务生效
- **下载并转换**新模型（内置中→英、英→中、日→中、英→德等预设）
- 删除不用的模型

语言方向由模型目录里的 `meta.json` 决定（服务启动时读取），
所以切到 `opus-mt-en-zh` 后会自动变成「英→中」。

> 下载与转换需要 `optimum-intel + torch`（约 500 MB 依赖），只在**源码环境**可用；
> 打包版会提示改用 `export_model.ps1` 导出后把目录放进 `models\`。

## 架构

```
GUI (CustomTkinter)  ──TCP 8715──┐
浏览器插件            ──HTTP 8716──┼──> 常驻服务进程（模型 + NPU 上下文）
client.py / 托盘      ──TCP 8715──┘
```

界面进程只做交互，不加载模型 —— 启动快、内存小；改设置只重启服务；服务可开机常驻而不必开界面。

## 文档

| 文档 | 内容 |
|---|---|
| [BUILD.md](BUILD.md) | 打包流程与 6 个 PyInstaller 坑 |
| [SERVICE.md](SERVICE.md) | 部署、开机自启、设置开关说明 |
| [KISS.md](KISS.md) | kiss-translator 浏览器插件接入 |

## 踩坑记录

NPU + OpenVINO 有几个不显然的约束，都已写进文档，这里只列要点：

1. **NPU 拒绝「有界动态」，只要「全静态」** —— `Dimension(1, 512)` 的上界不会传播到图内部节点，内部仍是 int64-max。必须 reshape 成完全静态形状。
2. **`encoder_hidden_states` 的隐藏维度不能打开** —— 打开会触发 `512 vs 1024` 维度不匹配。
3. **`decoder_with_past` 永远无法 NPU 编译** —— KV 缓存维度是 int64-max，只能弃用缓存走每步重算。
4. **中文路径下 sentencepiece 打不开 `.spm`** —— C++ 侧用 ANSI 窄字符路径，且 transformers 会把异常吞成 `return False`，最终只看到 `'bool' object is not callable`。

细节与解决办法见 [BUILD.md](BUILD.md)。

## 环境要求

- Windows 10/11 x64
- Intel NPU（Core Ultra 系列）或退回 CPU
- Python 3.11
- 首次运行需联网下载依赖与模型

## 许可证

[MIT](LICENSE)

## 致谢

- 模型：[Helsinki-NLP/opus-mt-zh-en](https://huggingface.co/Helsinki-NLP/opus-mt-zh-en)
- 推理：[OpenVINO](https://github.com/openvinotoolkit/openvino)
- 插件协议：[kiss-translator](https://github.com/fishjar/kiss-translator)
