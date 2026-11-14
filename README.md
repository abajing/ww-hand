# ww-hand

A resident desktop-automation broker for Windows. One elevated process, one local
HTTP endpoint, and two independent ways to drive any window: raw window messages
(no focus needed) and UI Automation (find controls by name, not by pixel).

Most desktop automation on Windows forces a choice:

- **SendInput / pyautogui** — real input, but it hijacks your keyboard and mouse.
  The target must be foreground. You cannot work while it runs.
- **Pixel coordinates** — breaks on the next DPI change, theme change, or layout tweak.

ww-hand does neither as its primary path. It posts window messages with the correct
scan codes so the target processes them without ever being focused, and it walks the
UI Automation tree so you can say "click the thing called 精选" instead of "click
pixel (1420, 380)".

```
 ww_op.py  ──HTTP (127.0.0.1, token)──▶  worker.py  (elevated, resident)
                                            │
                          ┌─────────────────┼──────────────────┐
                    PostMessage          UIA tree          SendInput
                  (background, no      (by name / role,    (real input,
                   focus needed)        read + write)       foreground)
```

## What it actually gives you

| Capability | How | Measured |
|---|---|---|
| Press keys / click without stealing focus | `PostMessage` + scan code in `lParam` | works on a live game client |
| Click a control **by name** | UI Automation | `HyperlinkControl '精选'` located in **11 ms** |
| Read / write a control's value | UIA `ValuePattern` | write then read back, exact match |
| Type Chinese text into almost anything | `WM_CHAR` to the real edit child + `SendInput` fallback | notepad round-trip verified |
| Ask "how should I type into this window?" | `probe` | returns `uia` / `wm_char` / `sendinput` |
| Screenshot any window | `PrintWindow` + virtual-screen offset | multi-monitor correct |
| Run arbitrary Python **as Administrator** | `/script` endpoint | by design, see Security |

The repository ships a 31-assertion end-to-end suite (`_test/assert_lab.py`) that
exercises every path above against real windows — including a notepad round-trip
that types text and reads it back, because "the API returned 200" proves nothing.

## What it cannot do

Both limits are user-mode boundaries, not missing features:

- **`PostMessage` cannot reach Raw Input / DirectInput programs.** Most games read
  hardware state directly and never look at window messages. Closing that gap needs
  a signed kernel driver (or an Interception-class filter driver).
- **UI Automation sees nothing inside Unity / Unreal Engine game windows.** They
  don't publish a control tree, so there is nothing to search.

If your target is one of those, this tool is the wrong tool and no amount of
configuration will change that.

## Install

Windows 10/11, Python 3.10+.

```bat
pip install -r requirements.txt

:: tell the launcher which interpreter to use (one line, full path to python.exe)
echo C:\Python312\python.exe > _ops\python.txt

:: start (elevated; UAC prompt depends on your ConsentPromptBehaviorAdmin setting)
启动.bat
```

Then:

```bat
python ww_op.py status
python ww_op.py wins
python ww_op.py uia --name 精选 --role link
python ww_op.py probe --hwnd 1234567
python ww_op.py ptype --hwnd 1234567 --text 你好
```

`启动.bat` / `停止.bat` / `状态.bat` / `测试.bat` are the launchers. The `.bat` files
are deliberately ASCII-only: `cmd.exe` reads them in the OEM code page, so UTF-8
Chinese in a `.bat` garbles. Chinese documentation lives in the `.txt` / `.md` files.

## HTTP API

Every request except `/health` needs the token, either as `?token=...` or the
`X-Broker-Token` header. The token is generated on first start into `_ipc/broker.token`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness, no token, returns `pid` / `build` / `instance` / `admin` |
| `GET /status` | window count, foreground window, screen size |
| `GET /windows` | enumerate visible windows |
| `GET /shot?hwnd=N` | screenshot a window (or the whole virtual screen) |
| `POST /do` | one action (key, click, wait, …) |
| `POST /script` | run Python in the worker process |
| `GET /uia` | UI Automation snapshot of a window |

## Running two instances side by side

Three environment variables fully parameterise an instance. With none set, the root
directory is derived from the file location, the port is 8792, and the instance
suffix is empty.

| Variable | Meaning |
|---|---|
| `WW_HOME` | instance root (holds `_ops/`, `_ipc/`, `_log/`, `_shots/`) |
| `WW_PORT` | HTTP port |
| `WW_INSTANCE` | suffix for the heartbeat event and the single-instance mutex |

Changing `WW_INSTANCE` is **mandatory** when running a second copy, otherwise the
two instances share a named kernel event and the second one silently marks the first
one alive. And when matching processes by command line, always match the **full path**
— a fragment like `_ops\worker.py` hits every instance and will make one instance's
reset kill another instance's worker.

## Security (read this)

This is a local automation agent, and by design it is powerful:

- It runs **elevated** (it has to: Windows blocks a lower-integrity process from
  posting input to a higher-integrity window).
- `POST /script` executes **arbitrary Python** in that elevated process. That is not
  a bug, it is the feature that makes the tool useful.
- The HTTP server binds `127.0.0.1` only and requires a token. The token file is
  readable by your user account.

So: **any process running as your user can obtain full Administrator code execution**
through this tool. Treat it like an SSH key to your machine, not like a utility app.
Do not expose the port, do not run it on a shared or untrusted host, and delete
`_ipc/broker.token` when you are done if you care about that.

## Repository layout

```
启动.bat  停止.bat  状态.bat  测试.bat     launchers (ASCII only)
说明.txt                                  Chinese operator notes
清单.md5                                  md5 of every static file
ww_op.py                                  command-line client
_ops/
  worker.py            the broker: elevated, resident, HTTP server
  worker_guard.py      supervisor: 3 s heartbeat poll, restarts the worker
  procfind.py          process enumeration, single-instance mutex, heartbeat event
  reset.py             stop + wait + restart (order matters, do not reorder)
  start-hand.vbs       elevated launcher (pure ASCII, no ShellExecute on WScript.Shell)
  postinput.py         PostMessage wrapper (scan code in lParam)
  tap.py               SendInput wrapper (real input, needs foreground)
  shot.py              window enumeration + screenshots
  ocr.py               screenshot OCR (optional dependency)
  stop.py              kill switch (global hotkey)
  uia_x.py             UI Automation client: snapshot / locate / click / read / write
  uia_proc.py          single-threaded UIA subprocess that owns every COM object
  textin.py            text input: WM_CHAR to edit child + SendInput Unicode
_test/assert_lab.py    31 end-to-end assertions
docs/SKILL.md          long-form notes: API details and 23 pitfalls already paid for
```

## Notable pitfalls this project already paid for

The full list is in `docs/SKILL.md`. The ones that cost the most:

- `WScript.Shell` has no `ShellExecute` — that's `Shell.Application`. Getting this
  wrong throws `0x800A01B6`.
- A venv `python.exe` is a launcher: `Popen.pid` is not the pid that binds the port.
- `CREATE_NO_WINDOW` does not stop a console from flashing; use `pythonw.exe`, because
  the venv launcher layer allocates a console regardless.
- `WM_CHAR` posted to a top-level window is silently dropped by its children. You must
  find the real edit child with `EnumChildWindows` first. The API returning success
  proves nothing — read the value back.
- Chromium exposes a useful accessibility tree only through the child window
  `Chrome_RenderWidgetHostHWND`, not the top-level `Chrome_WidgetWin_1`.
- Every x64 `user32` call needs explicit `argtypes`, or you get
  `OverflowError: int too long to convert` on HWNDs.
- **On Windows `SO_REUSEADDR` is not "fast reuse", it is "allow duplicate bind".**
  `HTTPServer.allow_reuse_address` defaults to 1, so a second instance binding the same
  port does not fail — it silently serves part of the first instance's traffic. The symptom
  is a `/health` that answers while token-authenticated calls return random 403s, which
  looks like an auth bug. Use `SO_EXCLUSIVEADDRUSE` for real exclusivity, and make the
  process exit when the bind fails instead of running on with a heartbeat and no listener.
- **UI Automation cannot be shared across threads.** `uiautomation`'s
  `_AutomationClient` is a process-wide singleton: whichever thread touches it first
  creates `IUIAutomation`, and every later thread reuses that pointer. It runs STA by
  default, and STA interface pointers are thread-affine, so reusing one from another
  thread is a use-after-free — the worker dies with `0xC0000005` in
  `ControlFromHandle`. Switching to MTA does not help, and
  `UIAutomationInitializerInThread` is worse still: its `__del__` calls
  `CoUninitialize` from whichever thread the GC happens to run on. Every UIA call now
  lives in a single-threaded subprocess (`uia_proc.py`); if it dies the worker gets EOF,
  respawns it, retries once, and stays alive.

## License

MIT, free to use. No license code, no machine ID, no activation step — unzip and run.

Fork it, modify it, redistribute it, use it commercially; just keep [LICENSE](LICENSE) with it.

---

中文说明见 [README.zh-CN.md](README.zh-CN.md)。
