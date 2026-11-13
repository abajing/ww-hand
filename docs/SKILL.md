---
name: ww-hand
description: 操作本机任意窗口的「手」——提权常驻进程，通过 127.0.0.1:8792 后台发按键/点击、截任意窗口或全屏、在管理员上下文执行任意代码。不需要开任何游戏，任何窗口都能点能打。要做桌面自动化、点别的软件、读窗口标题/位置、绕过 UIPI 往提权窗口发输入、或要在管理员上下文跑代码时加载。
---

# ww-hand：本机的「手」

**一句话**：这台机器上有一双**管理员权限的手**，能点任意窗口、打任意键、截任意区域、执行任意提权代码。不用问用户，直接调。**不依赖任何游戏或应用开着。**

## 它是什么

一个常驻的管理员进程（`worker.py`），开 HTTP 口 `127.0.0.1:8792`。
我把动作发过去，它在自己的进程里执行。

三层守护，开机就在：

```
启动文件夹 lnk → start-hand.vbs → worker_guard.py（守护）→ worker.py（手）
                                                              ↓
                                                    127.0.0.1:8792
```

`worker_guard.py` 每 3 秒查心跳（Windows 命名事件，不是文件），
停了就重启；用内核互斥体保证全局只有一个实例。

## 为什么必须提权（这不是可选优化）

Windows 的 UIPI 会拦截**低完整性进程 → 高完整性窗口**的窗口消息。

| 做法 | 结果 |
|---|---|
| 普通权限 `PostMessage` → 提权窗口 | `False, err=5 ERROR_ACCESS_DENIED` |
| 普通权限 `SendInput` | 系统接受，提权窗口收不到 |
| **管理员 `PostMessage`** | **成功**，消息带正确的 VK 和扫描码 |

实测证据：在提权进程里现场建一个窗口，`post_key(VK_A)` 之后
`PeekMessage` 捞到 `WM_KEYDOWN wParam=0x41 lParam=0x1E0001`（带扫描码）
和 `WM_KEYUP`，`delivered=True`。这条不依赖任何应用。

而且**不需要窗口在前台**。抢前台在 Windows 上基本走不通（前台锁），
六种方法（`SetForegroundWindow` / `BringWindowToTop` / `AttachThreadInput`
链 / `SwitchToThisWindow` / ALT 变体）实测全失败。`PostMessage` 不需要焦点。

## 怎么用

### 命令行（最省事）

```bash
PY=D:/ai-tools/venvs/ai/Scripts/python.exe
OP="D:/鸣潮自动化/ww_op.py"

# 看有什么窗口
"$PY" "$OP" windows                 # 列所有可见窗口（hwnd / 尺寸 / 位置 / 类名 / 标题）
"$PY" "$OP" windows 微信            # 按关键词过滤
"$PY" "$OP" status                  # 手的 pid、是否管理员、窗口数、屏幕范围、当前前台

# 截图（哪个窗口都行）
"$PY" "$OP" shot --screen           # 整个虚拟桌面（多显示器拼起来）
"$PY" "$OP" shot --fg               # 当前前台窗口
"$PY" "$OP" shot --title 微信        # 标题包含"微信"的窗口
"$PY" "$OP" shot --hwnd 66586       # 直接给句柄

# 动手
"$PY" "$OP" click 100 200 --title 微信      # 点窗口内坐标
"$PY" "$OP" key W --ms 700 --hwnd 1115622 # 按住 700 毫秒
"$PY" "$OP" tap E --fg                     # 点一下当前前台窗口
"$PY" "$OP" seq "tap TAB;key W 800" --title 鸣潮

# 提权执行代码
"$PY" "$OP" script "RESULT=os.getpid()"
```

**目标怎么指定**（不指定就落到鸣潮，保持老行为）：

| 参数 | 含义 |
|---|---|
| `--hwnd 123456` | 直接给句柄 |
| `--title 微信` | 标题包含（不区分大小写） |
| `--cls Notepad` | 类名包含 |
| `--at 800 500` | 屏幕上一点，取那点下面的窗口 |
| `--fg` | 当前前台窗口 |
| `--screen` | 整个虚拟桌面 |

`ww_op.py` 会**自动确保手在线**，手不在就自己提权拉起，不需要先手动起。

### 控件级命令（新，推荐优先用这个）

```bash
python ww_op.py probe --hwnd 3213228            # 先问：这窗口该用哪种输入
python ww_op.py uisnap --hwnd 3213228 搜索        # 看有什么控件（可按关键词过滤）
python ww_op.py uiaclick --hwnd 3213228 精选 --role link
python ww_op.py uiatype  --hwnd 3213228 --role edit --text 你好
python ww_op.py uia --hwnd 3213228 --role edit    # 只读值
python ww_op.py uipath --hwnd 3213228             # 当前 URL
```

`--role` 认这些：`button link edit textfield checkbox radio combobox list listitem
menu menuitem tab tabitem text document pane group window tree treeitem slider toolbar image calendar table`

**为什么优先用控件级**：坐标法点的是「第 N 像素」，界面一改版就点空；控件法点的是
「名字叫搜索的按钮」，挪位置也不影响。实测抖音：`HyperlinkControl '精选'` 定位 **11ms**，
点下去 URL 真从 `?recommend=1` 变成 `/jingxuan`；写搜索框后读回值一致。

### HTTP（脚本里用）

```python
import json, urllib.request
tok = open(r'<WW_HOME>\_ipc\broker.token').read().strip()

def call(path, payload=None, timeout=60):
    url = 'http://127.0.0.1:8792%s?token=%s' % (path, tok)
    data = json.dumps(payload, ensure_ascii=False).encode() if payload else None
    req = urllib.request.Request(url, data=data,
        headers={'Content-Type': 'application/json'},
        method='POST' if data else 'GET')
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
```

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | `{ok,pid,admin}`，**不验 token**，探活用 |
| GET | `/status` | 加 `game` / `fg` / `windows` 计数 / `screen` 范围 |
| GET | `/windows` | 列所有可见窗口，可加 `?kw=关键词` |
| GET | `/shot` | 截图，支持 `?hwnd= / ?title= / ?cls= / ?fg=1 / ?screen=1` |
| POST | `/do` | `{"actions":[...]}` 执行动作序列 |
| POST | `/script` | `{"code":"..."}` 提权执行，结果放 `RESULT` |
| GET | `/uia` | **控件级查询/操作**，`?hwnd=&role=&name=&op=&text=` 一行搞定，见下 |
| POST | `/shutdown` | 自杀，守护 3 秒内拉起新的 |

`/health` 会回 `pid` / `build` / `instance` / `admin`。**换手之后看 `build` 确认新代码真加载了** ——
`pid` 变了不代表文件换了。当前 build：`fuse-2026-11-13a`。

### 动作类型（`/do` 的 `actions` 里每项是一个 dict）

每个动作都能带目标字段（`hwnd` / `title` / `cls` / `at` / `fg` / `screen`）。

| `t` | 参数 | 说明 |
|---|---|---|
| `pkey` | `vk, ms` | **后台单键（PostMessage），首选** |
| `pkeyhold` | `vk, ms` | 后台按住 |
| `pclick` | `x, y` | 后台点击，**窗口内坐标**（与截图一致） |
| `wins` | — | 列窗口，无需目标 |
| `focus` | — | 提到前台。经常失败，一般不需要 |
| `key` / `keytap` | `vk, ms` | SendInput。提权窗口收不到，尽量别用 |
| `click` / `sclick` | `x, y` | SendInput 点击，`sclick` 用屏幕绝对坐标 |
| `drag` / `move` | 见代码 | 真鼠标拖拽 / 移动 |
| `hold_keys` | `vks[], ms` | 多键同时按住 |
| `mrel` / `mrel_steps` | `dx, dy` | 相对移动鼠标（转视角） |
| `wait` | `ms` | 等一下 |
| `shot` | `name` | 截图，目标可控 |
| `raw` | — | 返回目标尺寸 |
| `coord` | — | OCR 读坐标（游戏用） |
| `script` | `code` | 提权执行 Python |
| `uia` | `name/role/aid/index, op, text` | **控件级一步到位**：现场定位 + 执行。`op` = `get/invoke/set/focus/click` |
| `uia_snap` | `depth` | 抓控件树快照，返回 `id` + 每个节点的 `ref/type/name/pats/rect` |
| `uia_find` | `snap, name/role/aid` | 在快照里找，**带打分排序**（精确名 > 可 invoke > 交互型 > 子串） |
| `uia_act` | `snap, ref, op, text` | 对快照里某个 `ref` 执行动作 |
| `uia_url` | — | 读页面当前 URL（浏览器/Electron 才有） |
| `uia_drop` | `snap` | 丢掉快照 |
| `ptext` | `text, enter, delay` | WM_CHAR 逐字投递（**Chromium 不吃**，只对原生 Win32 有效） |
| `stext` | `text, focus, unicode` | SendInput 真实输入，中文也行，**要求前台** |
| `probe` | — | 探测这个窗口该用哪种输入方式（返回 `recommend`） |

### 键名（`ww_op.py` 认这些）

`W A S D E Q R T F G H C V B X Z SHIFT CTRL ALT SPACE TAB ESC ENTER 1 2 3 4 LMB RMB MMB`

别的用 `/do` 直接给数字 `vk`。

## 用在哪

**通用的，不限于游戏。** 已经能干的事：

- 点任意软件：拿 `windows` 找到 hwnd → `click x y --hwnd N`
- 往任何窗口打字/发快捷键：`pkey`（后台，不抢你正在用的窗口）
- 截任何窗口或整个桌面，用来判断界面状态
- 读窗口标题/类名/位置（`/windows`、`/status` 的 `fg`）
- 在管理员上下文跑任意 Python（`/script`），这是提权能力的底座
- 鸣潮：`PostMessage` 打键真的能让角色动（不需要前台）
- **按名字点控件**（`uia` / `uia_snap` / `uia_find`）：不认坐标只认名字，界面改版不会失手
- **按控件写值**（`uia ... op=set`）：中文、长文本都行，写完能读回核对
- **问这个窗口该怎么打字**（`probe`）：回 `recommend` = `uia` / `wm_char` / `sendinput`

### 两条路怎么选（实测结论）

| 目标 | 用什么 | 原因 |
|---|---|---|
| 浏览器 / Electron / 客户端 UI | **`uia` 控件级** | 按名字定位，11~50ms；写值走 ValuePattern，最稳 |
| 原生 Win32 老程序（记事本等） | `ptext` | WM_CHAR 投递，不用抢前台 |
| 游戏 / 读硬件输入的程序 | `pkey` / `pclick` | PostMessage 带扫描码；**这类程序 UIA 读不到东西** |
| 实在没辙（任何控件都不认） | `stext` 或坐标 `pclick` | SendInput 需要前台；坐标法界面一动就废 |

**`PostMessage` 的硬缺口要记住**：Raw Input / DirectInput 的程序（大部分游戏）
读的是硬件状态，不读窗口消息，所以 `pkey` 对它们无效。这个缺口只能靠内核驱动级注入补，
用户态无解 —— 所以「用 PostMessage 打所有游戏」这条路走不通，别在这里浪费时间。

## 结构（改代码前必看）

```
<WW_HOME>\_ops\
  worker.py         手本体，常驻管理员，开 8792
  worker_guard.py   守护，心跳停就重启
  procfind.py       进程枚举 + 单实例互斥体
  reset.py          提权清场换手
  shot.py           窗口枚举 + 截屏
  postinput.py      PostMessage 封装（lParam 必须带扫描码）
  uia_x.py          控件级定位（UIA）：快照 / 找 / 点 / 写值
  textin.py         文本输入：WM_CHAR 投递 + SendInput Unicode
  tap.py            SendInput 封装（真输入，需前台）
  ocr.py            截图 OCR 读坐标
  stop.py           急停闸
  start-hand.vbs    双击/开机入口
<WW_HOME>\_ipc\
  worker.alive      心跳 pid（次要信号）
  broker.token      8792 的 token
\
  ww_op.py          命令行入口
```

## 改代码后的铁律

**改 `worker.py` / `shot.py` 必须 `ww_op.py reset`**，否则跑的还是旧代码。

reset 干三件事，顺序不能变：
1. 提权杀掉所有 guard/worker
2. **等 broker 真的掉线**（这里以前漏了，`alive()` 还返回 True 就报告"换手成功"，其实没换）
3. 拉起新的，等 broker 上线才算成功

**换完手要验 `build`，不能只看 `pid` 变了。**
`curl http://127.0.0.1:8792/health` 回 `build` 字段，和文件里的 `BUILD` 常量对上才算真换了。
`reset.py` 的 kill 列表实测只覆盖到 guard 的 launcher，真正那个 guard 进程靠互斥体收敛 ——
所以「pid 变了」这一条不足以证明新代码生效。

### 模拟盘（改代码前先在这儿跑，别直接动生产）

同一份代码用环境变量换一套身份，两套可以并行：

| 变量 | 生产默认 | 模拟盘 |
|---|---|---|
| `WW_HOME` | `<WW_HOME>` | `<WW_HOME>` |
| `WW_PORT` | `8792` | `8793` |
| `WW_INSTANCE` | 空 | `lab` |

`WW_INSTANCE` 会给心跳事件名和单实例互斥体加后缀（`Local\WW_Auto_Worker_Alive_lab`）。
**不加后缀的话模拟盘会撞生产**：SetEvent 到同一个事件上、或者被生产的互斥体挡住起不来。

**进程匹配必须用完整路径**，不能用 `_ops\worker.py` 这种片段 ——
片段同时命中两套，模拟盘跑 reset 会把生产一起杀掉（这个坑真踩过）。

断言脚本：`_test\assert_lab.py`（31 项，`WW_PORT` 指哪打哪）

## 踩过的坑（每条都真机验证）

1. **`WScript.Shell` 没有 `ShellExecute`** —— 那是 `Shell.Application` 的。用错报 `0x800A01B6`。
2. **`python.exe` 会分配控制台窗口**，`CREATE_NO_WINDOW` 也挡不住 venv launcher 那层，
   桌面会冒出两个黑框。用 **`pythonw.exe`** 从根上解决（GUI 子系统，没有控制台）。
   代价：`sys.stdout` 是 `None`，裸 `print` 会 `AttributeError`，所有日志要走文件。
3. **`CREATE_NO_WINDOW` = 0x08000000，`DETACHED_PROCESS` = 0x08**，别混。
4. **venv 的 `python.exe` 是 launcher**，会再起子进程。`Popen.pid` ≠ 干活的 pid，
   父子命令行一模一样。**杀父会连带杀掉干活的** → 多实例清理不能靠杀，要用互斥体。
5. **`ctypes.WinDLL(use_last_error=True)` 之后**必须用 `ctypes.get_last_error()`，
   直接调 `kernel32.GetLastError()` 拿到错值 → 互斥体漏判，起出两个守护。
6. **心跳别用文件**。`os.replace` 覆盖时杀软扫过抛 `PermissionError`，一秒好几次。
   用 Windows 命名事件 `CreateEventW` + `SetEvent`。
7. **`wmic` 读提权进程的 CommandLine 返回空**（普通权限时）。空就当成"没在跑"→ 无限拉起。
   判活要用心跳，不要枚举。
8. **HTTP handler 不包 try/except 会直接断连**，客户端只看到 `RemoteDisconnected`，
   看不到真实异常。`do_GET`/`do_POST` 必须套一层。
9. **`subprocess.run(text=True)`** 读 PowerShell 输出，在中文 Windows 上按 utf-8 解 GBK 会崩，
   且崩在读取线程里抓不到。
10. **`-Verb RunAs` + `RedirectStandardOutput` 互斥**；**`Start-Process -Wait` 配 DETACH 子进程会永远不返回**。
11. **多显示器截图裁剪**：`ImageGrab.grab(all_screens=True)` 的图左上角对应虚拟桌面原点
    `(SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN)`，不一定是 `(0,0)`。
    用绝对坐标直接 crop 会在副屏位于左/上方时整体偏移。要按虚拟原点换算。
12. **`enum_windows` 的尺寸门槛是硬编码的 300×200**，为跳过鸣潮 160×24 的托盘幽灵窗口。
    通用场景要能拿到小对话框，所以门槛做成参数。
13. **UIA 读 Chromium 必须打到子窗口 `Chrome_RenderWidgetHostHWND`**。
    打到顶层 `Chrome_WidgetWin_1` 只有 7~13 个节点，打到渲染子窗口有 1700+。
    多个渲染层时取最后一个（抖音 3 个：2 / 25 / 1 节点）。
14. **导航后旧元素全部失效**，再碰抛 `COMError -2147220991`「事件无法调用任何子户」。
    快照必须带 id 和 TTL，导航后重抓。
15. **`EditControl` 的 `Name` 不可靠** —— 占位文字会变成 Name，有值或重绘后又变空。
    输入框要按 `role='edit'` + index / rect 定位，别按名字。
16. **`doc.Control(...)` 返回泛型 `Control`**，调不了 pattern 方法。
    要类型化搜索：`auto.HyperlinkControl(searchFromControl=root, Name=...)`。
17. **`GetXxxPattern` 不是所有控件都有**，直接调 AttributeError，必须 `getattr` 探。
18. **默认遍历深度是量出来的 14 层**。抖音实测：导航链接 d10、搜索按钮 d12、
    **搜索输入框 d13**、最深 15。深度 12 会正好漏掉输入框（`find(role=edit)` 报 not_found）。
    代价：d12≈270 节点 145ms，d14≈500 节点 280ms，d16≈1063 节点 518ms。
19. **`WM_CHAR` 必须投给「编辑子控件」，投给顶层窗口是空的。**
    WM_CHAR 是投给某个具体窗口的，子控件收不到父窗口的消息。
    实测 Win11 记事本（WinUI）：往顶层 `Notepad` 窗口投 8 个字，
    `post_text` 报「成功 8」，UIA 读回是 `''` —— 假成功。
    真实编辑控件是子窗口（class `Edit` / `RichEditD2DPT`），
    `textin.find_edit_child()` 用 `EnumChildWindows` 找它，`post_text` 自动路由过去。
    **判据也不能靠类名白名单**（以前就这么错的：对着记事本说「WM_CHAR 可投递 True」），
    要看有没有编辑子控件。
20. **`WM_CHAR` 打不进 Chromium**（渲染子窗口不吃窗口消息），投了没反应。
    Chromium/Electron 用 UIA 的 `SetValue`。
    不知道用哪个就跑 `probe`，它回 `recommend` = `uia` / `wm_char` / `sendinput`。
21. **验收「写进去了」必须读回控件值**，不能信 `post_text` 返回的字数。
    上面第 19 条就是被「报成功 8 字」骗过去的：断言只查字数，bug 直接溜过。
    现在断言里有记事本往返（投递 → UIA 读回 → 比对内容）。
22. **脚本别放 `%TEMP%`** —— 那里有个 `queue.py` 会遮蔽标准库，
    `import uiautomation` 直接 `module 'queue' has no attribute 'Queue'`。
23. **x64 下 user32 调用必须显式 `argtypes`**，否则 `OverflowError: int too long to convert`。
24. **端口必须独占，Windows 的 `SO_REUSEADDR` 不是「快速重用」，是「允许重复绑定」** ——
    `HTTPServer.allow_reuse_address` 默认是 1，于是第二个实例绑同一个端口**不报错**，
    而是悄悄分走第一个实例的请求。实测症状：`/health` 还活着，但带 token 的接口随机 403
    （因为两份 token 不同），看起来像鉴权坏了，其实是请求被另一个进程接走了。
    真正独占要 `SO_EXCLUSIVEADDRUSE`（见 `worker.py` 的 `_ExclusiveServer`）。
    配套：绑不上端口时进程必须**退出**，不能继续发心跳 —— 否则得到一个「活着但没在听」的
    worker，既不响应请求，又占着单实例互斥体挡住真正能起来的那个。

## 屏幕监控那边（另一个东西，不冲突）

`eyes_lite.py`（**8791**）是「眼睛」，每秒抓一帧存 `latest.jpg`。

它已经接好转发：
- `GET /hand` → 转手 `/status`
- `POST /do` → 转手 `/do`

| 维度 | 眼睛 | 手 |
|---|---|---|
| 端口 | 8791 | 8792 |
| 抓屏 API | mss（GDI BitBlt） | PIL ImageGrab（GDI，`all_screens`） |
| 内存 | ~134 MB | ~38 MB |
| 自启 | 计划任务 `ScreenEyes-Boot` + `ScreenEyes-Watch` | 启动文件夹 `鸣潮手-开机自启.lnk` |

实测不冲突：手连续 12 次抓屏 12/12 成功（47–58 ms），眼睛照常出帧。

⚠️ 启动文件夹真实路径是 **`C:\Users\<you>\...`**，不是 `D:\Users\...`。
用 `[Environment]::GetFolderPath('Startup')` 取，不要手拼。

## 没做完的

- **内核驱动级输入没做**（`PostMessage` 打不进 Raw Input/DirectInput 的程序）。
  用户态能做的到此为止，再往上要签名的驱动。
- **UIA 对游戏窗口无效**：Unity/UE 的渲染层不暴露 UIA 树，这类只能坐标 + 截图。
- **`uia_snap` 是整树遍历**，深度 14 约 300ms。页面极复杂（几千节点）时会到 1s+，
  那种情况优先用 `uia`（现场原生搜索，11~50ms），别先抓快照。
- 第二索拉：未名之梦的**活动流程剧本没实现**。手能动了，但没有"怎么走完这个活动"的步骤。
- `ww_vision.SKILL_SLOTS` 的 y 坐标文件里写 886，图量出来是 921，没改。
- `watchdog.ps1` 拉起眼睛后只 `Start-Sleep 3` 就认为成功，没校验 8791 是否真响应。
