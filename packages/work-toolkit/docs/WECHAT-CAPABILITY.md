# 微信与企业微信能力说明（work-toolkit / Read-Only Suite）

> 适用版本：微信 PC 4.2.0.13、企业微信（WXWork）、work-toolkit 0.3.0（Windows）
> 定位：**只读**。读消息、读收件、做汇总；不发消息、不接管微信窗口、不改动微信任何数据。
> 最后实测：2026-11-10 00:24（读到该时刻的群消息）

---

## 一、能做什么

一句话：**把微信里的「谁发了什么文件、哪个群在说什么」变成 agent 可以直接调用的结构化数据。**

| 场景 | 典型问法 | 用哪个工具 |
|---|---|---|
| 收材料 | 「各科室的摸底表收齐了吗，谁没交」 | `work_wechat_files(mode='harvest')` / `work_wechat_files(mode='pull')` |
| 找文件 | 「上周群里发的那个 pdf 在哪」 | `work_wechat_files(mode='scan')` |
| 看群消息 | 「XX 群最近在聊什么」 | `work_wechat_messages` |
| 查历史发言 | 「谁说过'点名'这件事」 | `work_wechat_search` |
| 概览未读 | 「我有哪些群没看」 | `work_wechat_sessions` |
| 催办 | 「@ 一下没交的单位」 | `work_wechat_remind`（默认只出话术） |
| 看图 | 「群里那张截图写了什么」 | `work_wechat_media`（解密 .dat，可出原图） |
| 留痕 | 「这个群这周说了什么，整理成材料」 | `work_wechat_digest`（出 .docx，图片可嵌入） |

---

## 二、三条通道（按优先级自动选择）

```
work_wechat_probe()  →  判断哪条通道可用  →  自动走最优路径
```

| 通道 | 数据来源 | 覆盖范围 | 本机状态 |
|---|---|---|---|
| **A. 消息库解密** | `db_storage/message/*.db`（SQLCipher 加密）→ 取密钥 → 解密到只读副本 | 全部历史消息、会话列表、群名、发送人、文件卡片 | ✅ 可用 |
| **B. 收件文件目录** | `msg/file/YYYY-MM/`（**明文**） | 聊天中收到的文件本体（docx/xlsx/pdf/zip…） | ✅ 可用 |
| **C. 媒体解密** | `msg/attach/**/*.dat`（AES-128-ECB + 逐字节 XOR） | 聊天里收到的图片本体（缩略图 21x、原图 1280x1706） | ✅ 可用（全库 7216 张实测可解） |

> 通道 A 用于「说了什么」，通道 B 用于「文件在哪」。两者互补：A 能告诉你某条消息是个文件、叫什么名字，B 能给你文件本体。

---

## 三、通道 A：消息库只读解密

### 3.1 取密钥（关键技术点）

微信 4.x 的数据库是 SQLCipher 4 加密，密钥不落盘、只驻留内存。不同版本的存放方式不同：

| 版本 | 密钥形态 | 取法 |
|---|---|---|
| 4.0.x | 内存中直接是 ASCII 字符串 `x'<64位hex enc_key><32位hex salt>'` | 内存特征串扫描 |
| **4.1.11+（含 4.2）** | 字符串被 **XOR 掩码**打散，藏在 `com.Tencent.WCDB.Config.Cipher` 对象里 | 定位 Config.Cipher 对象 → 读 blob → 按掩码还原 |

本机实测（4.2.0.13）**23 个库全部取到密钥，0 缺失**，模式 `config-cipher`。

- 实现：`wechat/wxkey.py`
- 算法来源：[`SmileGrassSky620/wechat-local-reader`](https://github.com/SmileGrassSky620/wechat-local-reader)（MIT），`docs/key-extraction.md`
- 纯标准库 + `ctypes` 读进程内存，**不注入 DLL、不 Hook、不修改微信进程**

### 3.2 解密（SQLCipher 4 参数）

```
enc_key = PBKDF2-SHA512(passphrase, salt, 256000, 32B)
mac_key = PBKDF2-SHA512(enc_key, salt XOR 0x3a, 2, 32B)
每页：reserve=80B = 16B IV + 64B HMAC
      正文 offsets = 16(salt 仅第1页) ~ page_size-80
      HMAC-SHA512(mac_key, ciphertext || iv || page_no)  → 逐页校验
```

- 实现：`wechat/wxdecrypt.py`，先读页头判定参数，逐页 HMAC 校验，任一页失败即整体拒绝
- 输出目录：`workspace/07_微信只读缓存/`（**副本**，源库只读打开，绝不回写）
- 增量刷新：对比源库 mtime，只重解变化的库

### 3.3 查询层

| 工具 | 说明 |
|---|---|
| `work_wechat_sync` | 取密钥 + 解密到只读缓存；`force=true` 强制重解 |
| `work_wechat_sessions` | 会话列表：名称、是否群、未读数、最后摘要、最后时间 |
| `work_wechat_messages` | 按会话读消息：`chat` 支持群名/备注/wxid/群 id 模糊匹配，支持 `since`/`until` |
| `work_wechat_search` | 全文搜索，可限定会话与时间；zstd 压缩的长消息**先解压再匹配** |

实现要点（都有踩坑记录）：

- 群消息表名 = `Msg_` + `md5(群id)`，自动按 `create_time` 定位到 `message_0/1/2…` 分库
- 发送人 `real_sender_id` → `Name2Id.user_name` → `contact.db` 还原昵称/备注
- 群内正文首行是 `发送者id:\n`，需剥离（id 形态不止 `wxid_xxx`，还有 `25984982989620620@openim`）
- 长文本被 zstd 压缩时，压缩字节放在 `message_content` 列本身（不是 `compress_content` 列），两个位置都要试
- 单条超 600 字会标注「已截断」，避免把整篇转发记录灌进上下文

---

## 四、通道 B：收件文件目录

微信把聊天中收到的文件以**明文**存在 `msg/file/YYYY-MM/`，不需要密钥、不碰进程。

| 工具 | 说明 |
|---|---|
| `work_wechat_files(mode='scan')` | 按时间/关键字/扩展名/类型筛选并统计（pdf/docx/xlsx/pptx/zip…） |
| `work_wechat_files(mode='pull')` | 复制到 `01_微信收回原件/`，`{month}` 前缀 + sha256 去重；传 `roster` 可出已交/未交名单 |
| `work_wechat_files(mode='harvest')` | 一键：收件 → 按「科室_姓名_原名」改名归档 → 对照底册出未交名单 + 催办话术 |

本机实测：**537 个文件 / 1.56 GB**（2026-01 ~ 2026-11），时间取文件 mtime，与微信接收时间一致。

安全约束：

- **只复制，不移动、不删除**原件（`wx_helper.py` 的 `--move` 默认关闭）
- 微信原件多为只读属性，复制后自动 `chmod 644`，否则后续排版/汇总会因只读失败
- 去重按 sha256，重复文件跳过并记录 `same_as`

---

## 五、桌面自动化（已移除，统一走只读库）

已移除失效的微信 3.x 桌面自动化与 UIA 分支，统一通过只读本地消息库与 WAL 增量刷新读取（`channel=local_db`，
内容与界面一致，只是拿不到「界面上正在滚动的那一屏」），不再直接失败；
`work_wechat_remind` 在 `dry_run=false` 时同样自动降级：
生成话术 → 写入 `workspace/06_运行状态/待发催办通知.md` → 用户复制粘贴。

---

## 六、安全与只读承诺

| 项目 | 做法 |
|---|---|
| 源数据库 | 以只读方式打开，**从不写回**；解密产物落在 workspace 副本 |
| 微信进程 | 只读内存（`PROCESS_VM_READ`），不注入、不 Hook、不改内存 |
| 网络 | 全程本地回环，**不上传任何数据** |
| 原文件 | 只复制，不移动、不删除、不改名原件 |
| 凭证 | 密钥只存在内存与本机，不写日志、不外发 |

---

## 七、能力边界（如实说明）

| 能力 | 状态 | 说明 |
|---|---|---|
| 读历史消息 / 群名 / 发送人 | ✅ | 已解全部消息库，含实时新增 |
| 全文搜索 / 长消息解压 | ✅ | zstd 长文可搜 |
| 读取收到的文件本体 | ✅ | `msg/file` 明文目录 |
| 消息里的图片 | ✅ | `work_wechat_media` 解密 `.dat`：缩略图直出 JPEG；原图多为 WXGF(HEVC)，交 ffmpeg 转 PNG |
| 消息里的语音 / 视频 | ❌ | 本体在 `msg/media`、`msg/video`，编码杂，未做解析 |
| 发送消息 / @ 催办 | ❌ | 只读架构不自动发送；由 `work_wechat_remind` 生成待发话术供人工粘贴 |
| 自动点开群 | ❌ | 只读架构不操控桌面窗口 |
| 导出聊天记录为文件 | ✅ 间接 | 用 `work_wechat_messages` 取数后可自行落盘 |

> 微信版本升级（如 4.3）可能改变 Config.Cipher 的对象结构，届时 `wxkey.py` 需同步更新；
> 若取密钥失败，`work_wechat_probe` 会返回 `message_db_readable=false` 并附原因，此时通道 B 仍可用。

### 行为契约（端到端实测后确定）

| 字段 / 行为 | 约定 |
|---|---|
| `inbox_pull` 的 `dedupe` | **跨次幂等**。目标目录已有同 sha256 文件时判 `duplicate` 跳过，重复执行不会产生 `(2)` 副本；`sha256` 返回完整 64 位 |
| `harvest` 的 `data_source` | `wechat_inbox` 表示真实收件原件；`staging_demo` 表示来自 `00_模拟微信收件缓存` 的演示件，此时 `is_demo_data=true` 且 `data_source_note` 会警告结论不可当真 |
| `harvest` 的 `since_time` | 留空时默认**最近 7 天**（早期是「今天 0 点」，今天没文件就直接判失败） |
| `messages` 的系统消息 | `text` 形如 `[系统] "某某" 撤回了一条消息`，不再带 `<revoketime>` 残留的尾巴；`sender` 标为 `系统` |
| `search` 的 `text` | 超 600 字截断并补 `…`（早期 400 字硬切，会把 `mp://` 短链切成半截） |
| `media` 的 `ref` | 只导单张，必须是 32 位 hex（先卡格式再落地，挡 `../` 这类路径）；`prefer=orig` 出原图，WXGF 需 ffmpeg，缺 ffmpeg 时自动退回缩略图并标 `from_thumb` |
| CLI 退出码 | `run_tool.py` 成功 0 / 工具业务失败 1 / 调用错误（未知工具、坏 JSON、缺参）2 |

---

## 八、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `未找到微信账号数据目录` | 微信未登录 / 数据目录被移动 | 确认 `Documents\xwechat_files` 或自定义路径存在 |
| 取密钥 0 命中 | 微信大版本升级改了结构 | 更新 `wxkey.py`；期间用通道 B |
| 解密页校验失败 | 密钥与库不匹配（换过账号/重装） | `work_wechat_sync(force=true)` 重取重解 |
| 消息只到某年某月 | 该会话数据在 `message_1/2…` 分库 | 查询层已自动跨库，若仍缺说明该库未解密成功 |
| 收件目录为空 | 微信「文件保存位置」是自定义目录 | `work_wechat_probe` 返回的 `local_inbox_root` 会指向实际路径 |

---

## 九、文件索引

```
wechat/
├── wxkey.py          # 取密钥（4.0.x 内存串 / 4.1.11+ Config.Cipher+XOR）
├── wxdecrypt.py      # SQLCipher4 解密 + 逐页 HMAC 校验
├── wxread.py         # 查询层：会话/消息/搜索/发送人/群名/压缩解码
├── inbox.py          # 收件目录扫描、复制、名单核对
├── key_extract.py    # 早期内存扫描实现（保留，用于 4.0.x 与排查）
├── decrypt.py        # 早期解密实现（保留，含 reserve 自适应）
└── probe.py          # 库结构与加密参数探查
```

MCP 工具定义：`mcp/work_toolkit_server.py`
只读缓存：`workspace/07_微信只读缓存/`
归档产出：`workspace/01_微信收回原件/`

---

## 十、企业微信：另一套加密，另一条通道

企业微信（WXWork）和上面的微信是**完全不同的两套东西**，别把参数混着用。

### 10.1 加密：wxSQLite3，逐页 AES-128-CBC

| 项 | 微信 4.x | 企业微信 |
| --- | --- | --- |
| 算法 | SQLCipher 4（AES-256-CBC + HMAC-SHA512） | AES-128-CBC |
| 页结构 | 密文 + reserve 区 + HMAC | 密文长度 = 页大小，**无 HMAC、无 reserve** |
| 每页密钥 | 同一个库密钥 | `MD5(raw_key ‖ 页号小端 4B ‖ "sAlT")`，**逐页不同** |
| IV | 由页号派生 | `MD5(LCG 连算 4 次)`，LCG 参数 `52774 / 40692 / 3791 / 2147483399` |
| 第 1 页 | 单独处理 | `[0:8]` 原库头、`[8:16]` 挪到 `[16:24]`、`[16:24]` 是**明文**头片段、`[16:]` 才加密 |
| 页大小 | 4096 | 4096（页头 `1000020200402020` 可判定） |

第 1 页的特殊布局带来一个好处：`[16:24]` 的明文片段就是「解密后 `[16:24]` 应该长什么样」，
所以**验一把密钥只需要解一个 AES 块**，不用整页解。

### 10.2 取密钥：只在进程内存里，两条策略

企业微信不把密钥写进任何配置文件，只在 `WXWork.exe` 的内存里。

1. `x'<64 hex>'` 正则特征 —— 理论上最省事，本机**实测 0 命中**（这版客户端不留这种残迹）。
2. **cipher 结构体特征** —— `(flags, flags2)` 落在
   `(1|2, 1|2|4096|8192|16384)`，密钥固定在结构体 **+8** 处。本机 6.3 万候选、32 处命中，
   逐个用「解一个 AES 块比对明文头片段」筛，亚秒出结果（全页解要 40 秒）。

一把密钥开全部库——这点比微信简单，微信那边每个库的 salt 和派生都不一样。

### 10.3 读正文：protobuf 无 schema

正文不是纯文本字段，塞在 protobuf 的嵌套字节里，没有任何公开的 `.proto`。做法是
「按 wire format 走一遍 → 逐层下钻 → 只收能整块解码成干净文本的那一层」，
不假设字段号（不同版本会挪位）。两个必须踩过的坑：

- **长度字节粘进正文**：把长度前缀当单字符 ascii 解出来，会得到「c普通话的考试时间…」「1 概率论课教室…」。
  修法是拒绝首字符是控制字符/空白的片段，并优先取整块可解码的那层。
- **卡片消息取到图标地址**：文档/接龙/投票/会议卡片的字段顺序是「链接 → 图标 → 标题 → 摘要」，
  取最长会把 CDN 图片地址或内部 id 当正文。改成排除 URL / 纯数字 id / appid / 小程序路径后
  取第一个像人话的字段，190 条文档卡片、112 条图文链接的标题就正常了。

### 10.4 能力边界

- **必须开着客户端**：密钥在内存里，关了就取不到。有缓存时只能读**之前已解过**的部分。
- **云端文件不一定在本地**：企业微信的文件要在客户端点开才落地，`on_disk=false` 是常态，
  `work_wecom_harvest` 会把它们列进 `not_downloaded`，不当成已收。
- **FTS 库解不出**：几个全文索引库用私有 `WeWorkFtsTokenizer`，明文副本打不开也不影响会话/消息/文件。
- **只读**：不发消息、不点窗口、不改源库；副本落在 `workspace/07_企业微信缓存/`，可随时删。

### 10.5 文件索引

```
wecom/
├── wcrypto.py        # 逐页解密：页密钥派生、IV、第 1 页特殊布局、批量解密
├── wckey.py          # 进程内存取密钥：cipher 结构体扫描、单块快验、指纹缓存
└── wcread.py         # 查询层：会话/消息/搜索/通讯录/文件/收材料
```

MCP 工具：`work_wecom_*`（8 个）
只读缓存：`workspace/07_企业微信缓存/Data/`
归档产出：`workspace/01_企业微信收回原件/`
密钥缓存：`workspace/06_运行状态/wecom_key.json`（`chmod 600`，指纹不符自动失效）

---

## 十一、通讯：合视图，不是同一个账号

控制台把微信、企业微信两列合成一列「通讯」，并加了 `work_comms_overview`。
要讲清楚的一件事：**合并的是「去哪找功能」，不是底层通道**——两套加密、两份密钥、
两份缓存、两套可用性判断，都还在。

| | 微信 | 企业微信 |
| --- | --- | --- |
| 加密 | SQLCipher 4（AES-256-CBC + HMAC） | wxSQLite3（AES-128-CBC 逐页） |
| 密钥 | `Config.Cipher` 的 XOR 掩码 blob | `WXWork.exe` 进程内存 |
| 客户端关着 | 有解密副本就能读 | 取不到密钥，读不了 |
| 工具前缀 | `work_wechat_*` | `work_wecom_*` |

总览做的事只有两件：把两条通道的 `probe` 结论并排摆出来，再把各自的 `sessions`
抹平成同一形状、按时间合成一条 `recent` 时间线（每行标 `channel`）。某一侧失败时
只让那张卡变灰，另一侧照常显示，总览本身仍返回 `ok:true`——这比整页报一个错有用得多。

因此**没有**「统一搜索」「统一收件」这种东西：`work_comms_overview` 只读状态和会话，
搜某句话、收某个群的文件仍按通道各走各的工具。路由上「通讯录」也做了互斥，
不会被「通讯总览」这个泛称抢走。
