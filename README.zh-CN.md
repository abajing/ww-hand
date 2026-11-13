# ww-hand

Windows 桌面自动化「手」：一个常驻的管理员进程 + 一个本机 HTTP 接口，
用两条互相独立的路子操作任意窗口 —— 原始窗口消息（不用抢焦点）和
UI Automation（按名字找控件，不靠像素坐标）。

Windows 上做桌面自动化，通常只能二选一：

- **SendInput / pyautogui** —— 真输入，但它劫持你的键鼠。目标窗口必须在前台，
  它跑起来你就没法干别的。
- **像素坐标** —— 换一次 DPI、换一次主题、改一次版式，脚本就废。

ww-hand 两条都不走。它把窗口消息连扫描码一起投进目标队列，目标不用被激活就能处理；
它还会遍历 UI Automation 树，于是你可以说「点那个叫『精选』的东西」，
而不是「点 (1420, 380) 那个像素」。

```
 ww_op.py  ──HTTP (127.0.0.1, 带 token)──▶  worker.py（提权常驻）
                                               │
                              ┌────────────────┼─────────────────┐
                        PostMessage          UIA 树           SendInput
                      （后台，无需焦点）    （按名字/角色，   （真输入，
                                             可读可写）      需前台）
```

## 能干什么

| 能力 | 做法 | 实测 |
|---|---|---|
| 不抢焦点地按键 / 点击 | `PostMessage`，`lParam` 带扫描码 | 在真游戏窗口里生效 |
| **按名字**点控件 | UI Automation | `HyperlinkControl '精选'` 定位 **11 ms** |
| 读写控件值 | UIA `ValuePattern` | 写完读回，完全一致 |
| 往几乎任何地方打中文 | `WM_CHAR` 投给真实编辑子控件 + `SendInput` 兜底 | 记事本往返验证 |
| 先问「这窗口该怎么打字」 | `probe` | 返回 `uia` / `wm_char` / `sendinput` |
| 截任意窗口 | `PrintWindow` + 虚拟屏原点换算 | 多显示器位置正确 |
| 在**管理员**上下文跑任意 Python | `/script` | 设计如此，见「安全」 |

仓库自带 **31 项端到端断言**（`_test/assert_lab.py`），对着真窗口跑完上面每一条 ——
包括「往记事本打字再读回来比对」，因为「接口返回 200」什么都证明不了。

## 做不到什么

这两条是用户态边界，不是没做完：

- **`PostMessage` 打不进 Raw Input / DirectInput 程序。** 大部分游戏直接读硬件状态，
  根本不看窗口消息。要补这个口子需要签名内核驱动（或 Interception 这类过滤驱动）。
- **UIA 看不见 Unity / Unreal 游戏窗口内部。** 它们不发布控件树，没东西可搜。

如果你的目标是这两类，那这个工具就是错的工具，怎么配置都不会变。

## 安装

Windows 10/11，Python 3.10+。

```bat
pip install -r requirements.txt

:: 告诉启动器用哪个解释器（一行，python.exe 的完整路径）
echo C:\Python312\python.exe > _ops\python.txt

:: 启动（提权；是否弹 UAC 取决于你的 ConsentPromptBehaviorAdmin 设置）
启动.bat
```

然后：

```bat
python ww_op.py status
python ww_op.py wins
python ww_op.py uia --name 精选 --role link
python ww_op.py probe --hwnd 1234567
python ww_op.py ptype --hwnd 1234567 --text 你好
```

`启动.bat` / `停止.bat` / `状态.bat` / `测试.bat` 是入口。这几个 `.bat` **故意只用 ASCII**：
`cmd.exe` 按 OEM 代码页读 `.bat`，里面写 UTF-8 中文会整行乱码。
中文文档都在 `.txt` / `.md` 里。

## HTTP 接口

除 `/health` 外都要 token，放在 `?token=...` 或 `X-Broker-Token` 头里。
token 在首次启动时自动生成到 `_ipc/broker.token`。

| 端点 | 作用 |
|---|---|
| `GET /health` | 探活，不要 token，回 `pid` / `build` / `instance` / `admin` |
| `GET /status` | 窗口数、前台窗口、屏幕尺寸 |
| `GET /windows` | 枚举可见窗口 |
| `GET /shot?hwnd=N` | 截某个窗口（或整个虚拟屏） |
| `POST /do` | 执行一个动作（按键、点击、等待……） |
| `POST /script` | 在 worker 进程里跑 Python |
| `GET /uia` | 取某个窗口的 UI Automation 快照 |

## 同时跑两个实例

三个环境变量能把一个实例完全参数化。都不设时：根目录按文件位置推导，端口 8792，
实例后缀为空。

| 变量 | 含义 |
|---|---|
| `WW_HOME` | 实例根目录（装 `_ops/`、`_ipc/`、`_log/`、`_shots/`） |
| `WW_PORT` | HTTP 端口 |
| `WW_INSTANCE` | 心跳事件名与单实例互斥体的后缀 |

跑第二份副本时**必须**改 `WW_INSTANCE`，否则两个实例共用同一个命名内核事件，
第二个会把第一个误标成「还活着」。另外按命令行匹配进程时**一定要用完整路径** ——
像 `_ops\worker.py` 这种片段会同时命中所有实例，导致一个实例的 reset 把另一个杀掉。

## 安全（请读完）

这是一个本机自动化代理，设计上就很强：

- 它**以管理员身份**运行（必须如此：Windows 会阻止低完整性进程往高完整性窗口投输入）。
- `POST /script` 在那个提权进程里执行**任意 Python**。这不是漏洞，
  这正是它有用的原因。
- HTTP 服务只绑 `127.0.0.1`，且要求 token。token 文件对你的账户可读。

所以：**任何以你身份运行的进程，都能通过这个工具拿到完整的 Administrator 代码执行。**
把它当成你机器的 SSH 私钥，别当成一个普通小工具。不要把这个端口暴露出去，
不要在共享或不受信的机器上跑；介意的话，用完把 `_ipc/broker.token` 删掉。

## 目录结构

```
启动.bat  停止.bat  状态.bat  测试.bat     入口（纯 ASCII）
说明.txt                                  中文操作说明
清单.md5                                  所有静态文件的 md5
ww_op.py                                  命令行客户端
_ops/
  worker.py            手本体：提权常驻，HTTP 服务
  worker_guard.py      守护：3 秒轮询心跳，worker 死了就拉起
  procfind.py          进程枚举、单实例互斥体、心跳事件
  reset.py             停 → 等掉线 → 再起（顺序不能改）
  start-hand.vbs       提权启动器（纯 ASCII；WScript.Shell 没有 ShellExecute）
  postinput.py         PostMessage 封装（lParam 必须带扫描码）
  tap.py               SendInput 封装（真输入，需前台）
  shot.py              窗口枚举 + 截图
  ocr.py               截图 OCR（可选依赖）
  stop.py              急停（全局热键）
  uia_x.py             UI Automation：快照 / 定位 / 点击 / 读值 / 写值
  textin.py            文本输入：WM_CHAR 投给编辑子控件 + SendInput Unicode
_test/assert_lab.py    31 项端到端断言
docs/SKILL.md          长文档：接口细节 + 23 条已经付过学费的坑
```

## 已经付过学费的坑（摘几条）

完整清单在 `docs/SKILL.md`。最贵的几条：

- `WScript.Shell` **没有** `ShellExecute`，那是 `Shell.Application` 的方法。
  写错直接 `0x800A01B6`。
- venv 的 `python.exe` 是个 launcher：`Popen.pid` 不是真正绑端口的那个 pid。
- `CREATE_NO_WINDOW` 挡不住黑框闪烁，要用 `pythonw.exe` ——
  venv 的 launcher 那一层照样会分配控制台。
- `WM_CHAR` 投给顶层窗口会被子控件静默丢弃，必须先用 `EnumChildWindows` 找到真正的
  编辑子控件。接口返回成功什么都证明不了 —— 要读回来比对。
- Chromium 只在子窗口 `Chrome_RenderWidgetHostHWND` 上暴露有用的无障碍树，
  顶层 `Chrome_WidgetWin_1` 上只有几个节点。
- 所有 x64 `user32` 调用都必须显式给 `argtypes`，否则传 HWND 会
  `OverflowError: int too long to convert`。
- **Windows 的 `SO_REUSEADDR` 不是「快速重用」，是「允许重复绑定」。**
  `HTTPServer.allow_reuse_address` 默认 1，于是第二个实例绑同一端口不报错，
  而是悄悄分走第一个实例的请求。症状是：`/health` 活着，但带 token 的接口随机 403，
  看着像鉴权坏了。真正独占要用 `SO_EXCLUSIVEADDRUSE`；另外绑不上端口时进程必须退出，
  不能继续发心跳变成「活着但没在听」。

## 许可

MIT，免费用。没有授权码、没有机器码、没有激活步骤 —— 解压就能跑。

可以随便改、随便分发、拿去商用也行，把 [LICENSE](LICENSE) 一起带上就行。

---

English version: [README.md](README.md).
