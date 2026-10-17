# kiss-translator 浏览器插件接入

本服务实现了 [kiss-translator 自定义接口规范 v2](https://github.com/fishjar/kiss-translator/blob/master/custom-api_v2.md)，
可直接作为该插件的本地翻译后端，全部推理在本机 NPU 上完成，不经过任何云端。

## 一、启动

服务默认同时监听两个端口：

| 端口 | 协议 | 用途 |
|---|---|---|
| 8715 | TCP（换行分隔 JSON） | 本项目的 GUI 与 `client.py` |
| 8716 | HTTP + JSON | kiss-translator 等浏览器插件 |

确认服务在跑：

```powershell
H:\Code_project\deepseek harness\Project\npu-translate\start-service.ps1 -Status
```

## 二、kiss-translator 配置

插件设置里新增自定义接口：

| 配置项 | 值 |
|---|---|
| 接口名称 | 随意，如 `NPU 本地` |
| URL | `http://127.0.0.1:8716/translate` |
| 方法 | POST |
| 请求体 | 留空（已符合默认规范） |
| Request Hook | 留空 |
| Response Hook | 留空 |

**关键点：三项全留空。** 因为服务的请求/响应已符合规范的「默认接口规范」，
不需要写任何 Hook。这是最省事的接法。

### 想用聚合翻译（批量，更快）

| 配置项 | 值 |
|---|---|
| URL | `http://127.0.0.1:8716/translate/batch` |
| 请求体 | 留空 |
| Hook | 留空 |

聚合模式的响应是数组 `[{"text": "...", "src": "..."}]`，规范里 v2.0.4+ 同样支持。

## 三、接口细节

### 非聚合翻译（默认）
请求：

```json
{
  "text": "今天天气真好，我们一起去公园散步吧。",
  "from": "auto",
  "to": "en"
}
```

响应：

```json
{
  "text": "It's a nice day. Let's go for a walk in the park.",
  "src": "zh-CN",
  "_ms": 409.7,
  "_tier": "64x64"
}
```

`_ms` 和 `_tier` 是本服务附带的调试字段（下划线开头），规范只要求 `text` 和 `src`，
插件忽略多余字段即可。

### 用浏览器地址栏快速测试（GET）

`/translate` 同时支持 GET + 查询参数，方便不开开发者工具直接测：

```
http://127.0.0.1:8716/translate?text=今天天气真好&to=en
http://127.0.0.1:8716/translate/batch?texts=外面下雨了&texts=记得带伞&to=en
```

注意：**插件本身用的是 POST**（规范如此），GET 只是给人手动测试用的。
POST 与 GET 的响应格式完全一致。

### 聚合翻译

请求：

```json
{
  "texts": ["今天天气真好", "hello world"],
  "from": "auto",
  "to": "en"
}
```

响应：

```json
[
  {"text": "It's a beautiful day.", "src": "zh-CN"},
  {"text": "hello world", "src": "en"}
]
```

**注意：响应数组的顺序与请求 `texts` 的顺序严格一致**，这是插件正确配对译文的依据。

### 语言方向策略

本机 NPU 目前只装了 `opus-mt-zh-en`（中→英）一个模型。因此：

| 源文 | 目标 | 行为 |
|---|---|---|
| 中文 | `en` / `en-*` | **真正翻译** |
| 中文 | `ja` / `fr` / 其他 | 原样返回，`src: "zh-CN"` |
| 英文 | `zh-CN` / 任何 | 原样返回，`src: "en"` |

即「中→英才翻，其余原样返回」。这样插件装上即可用，不会报错；
后续装上 `opus-mt-en-zh` 后可再扩展反向翻译。

### CORS

已开启，预检返回 204，允许任意来源：

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: POST, GET, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
```

## 四、辅助端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/status` | GET | 服务状态 + kiss 兼容信息 |
| `/ping` | GET | 存活探测 |
| `/langs` | GET | 支持的语言代码表（规范末尾那份） |

```powershell
(Invoke-WebRequest 'http://127.0.0.1:8716/status' -UseBasicParsing).Content
(Invoke-WebRequest 'http://127.0.0.1:8716/ping' -UseBasicParsing).Content
```

## 五、常见问题

**Q：插件报网络错误 / 连接被拒绝？**
A：确认服务在跑（`start-service.ps1 -Status`），且 URL 端口是 8716 不是 8715。

**Q：译文是原文，没翻？**
A：目标语言不是英文，或源文不是中文 —— 见上面「语言方向策略」。这是预期行为。

**Q：想关掉 HTTP 端口？**
A：启动时加 `--no-http`，只保留 TCP。

**Q：插件能用 OpenAI 兼容接口吗？**
A：规范文档里 Ollama / 硅基流动示例都是 OpenAI 兼容格式，需要写 Hook 转换。
本服务走的是规范默认格式，比 OpenAI 兼容更简单，无需 Hook。
