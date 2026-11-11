#!/usr/bin/env python3
"""test_lan_http_proxy.py — 中继白名单策略测试

为什么要有它：
  中继是「中控机唯一出网通道」，它的白名单就是**安全边界**。
  为了装 Node / DSH CLI / Python 包而放宽白名单时，最怕两件事：
    ① 不慎把默认姿态也放宽了（本该"仅软件源"变成"开放代理"）；
    ② 子域名匹配写错，`ubuntu.com.evil.com` 这类后缀伪装被放行。
  这两条都用测试钉死，改代码时跑一遍就能发现回归。

纯 stdlib、无网络、无文件副作用。

用法：python3 tools/test_lan_http_proxy.py   （Linux 侧）
      python tools/test_lan_http_proxy.py    （Windows 侧）
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import lan_http_proxy as relay  # noqa: E402


def case_default_posture_unchanged() -> str:
    """导入后默认白名单必须仍是"仅软件源"——install-mode 只能是显式开启。"""
    assert relay.ALLOWED_HOSTS == list(relay.BASE_ALLOWED_HOSTS), relay.ALLOWED_HOSTS
    assert relay.ALLOWED_HOSTS == ["ubuntu.com", "debian.org"], relay.ALLOWED_HOSTS
    return f"导入即默认 = {relay.ALLOWED_HOSTS}"


def case_baseline_allows_apt() -> str:
    for h in ("ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com",
              "debian.org", "deb.debian.org", "UBUNTU.COM"):
        assert relay.host_allowed(h), f"应放行却拒绝了: {h}"
    return "软件源及其子域名（含大小写）均放行"


def case_baseline_denies_everything_else() -> str:
    for h in ("registry.npmjs.org", "pypi.org", "api.deepseek.com",
              "evil.com", "raw.githubusercontent.com"):
        assert not relay.host_allowed(h), f"不该放行却放行了: {h}"
    return "非软件源域名默认全部拒绝"


def case_suffix_spoof_rejected() -> str:
    """子域名匹配不能退化成"包含"匹配。"""
    traps = ("notubuntu.com", "ubuntu.com.evil.com", "evil-ubuntu.com",
             "xdebian.org", "debian.org.evil.com")
    for h in traps:
        assert not relay.host_allowed(h), f"后缀伪装被放行了: {h}"
    return f"{len(traps)} 个后缀伪装样本全部拒绝"


def case_host_normalization() -> str:
    """带端口、带 userinfo、带大小写的主机名要正确归一。"""
    allowed = ("registry.npmjs.org",)
    assert relay.host_allowed("registry.npmjs.org:443", allowed)
    assert relay.host_allowed("https://x@registry.npmjs.org", allowed)
    assert relay.host_allowed("Registry.NPMJS.org", allowed)
    assert not relay.host_allowed("", allowed)
    assert not relay.host_allowed(None, allowed)
    return "端口 / userinfo / 大小写归一正确，空值不误放"


def case_install_mode_is_opt_in() -> str:
    default = relay.resolve_allowed_hosts()
    assert default == ["ubuntu.com", "debian.org"], default
    install = relay.resolve_allowed_hosts(install_mode=True)
    for h in relay.INSTALL_MODE_HOSTS:
        assert h in install, h
    assert install[:2] == ["ubuntu.com", "debian.org"], "基础白名单应保留在最前"
    assert relay.host_allowed("registry.npmjs.org", install)
    assert relay.host_allowed("files.pythonhosted.org", install)
    # 放宽后依然不该变成开放代理
    assert not relay.host_allowed("evil.com", install)
    assert not relay.host_allowed("npmjs.org", install)   # 父域不放行（只放行确切子域）
    return f"install-mode 只增不放（{len(install)} 条），且未变成开放代理"


def case_allow_host_adds_specific() -> str:
    hosts = relay.resolve_allowed_hosts(["api.deepseek.com", "  API.DEEPSEEK.COM  ", ""])
    assert hosts.count("api.deepseek.com") == 1, f"应去重且归一: {hosts}"
    assert relay.host_allowed("api.deepseek.com", hosts)
    assert not relay.host_allowed("deepseek.com", hosts)
    assert not relay.host_allowed("evil.com", hosts)
    return "--allow-host 支持重复传参、去重、归一化，且不放行父域"


def run() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    cases = [
        ("默认姿态未被放宽", case_default_posture_unchanged),
        ("软件源放行", case_baseline_allows_apt),
        ("其余域名默认拒绝", case_baseline_denies_everything_else),
        ("后缀伪装拒绝", case_suffix_spoof_rejected),
        ("主机名归一化", case_host_normalization),
        ("install-mode 是显式开关", case_install_mode_is_opt_in),
        ("--allow-host 精确放行", case_allow_host_adds_specific),
    ]
    results = []
    for name, fn in cases:
        try:
            results.append((name, True, fn()))
        except AssertionError as exc:
            results.append((name, False, str(exc)))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    print("=" * 62)
    print("  中继白名单策略测试（纯函数，无网络）")
    print("=" * 62)
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"       {detail}")
    failed = [r for r in results if not r[1]]
    print("-" * 62)
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
