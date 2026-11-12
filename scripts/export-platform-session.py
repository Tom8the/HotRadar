"""Export only first-party cookies after the operator logs in to the official site."""

import argparse
import json
from tempfile import NamedTemporaryFile

from hotradar.api.platform_sessions import LOGIN_PAGES
from hotradar.config import PROJECT_ROOT
from hotradar.session_store import SCOPES, allowed_domain, private_directory, validate_cookies
from playwright.sync_api import Error, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=SCOPES)
    parser.add_argument("--channel", choices=("chrome", "msedge", "chromium"))
    args = parser.parse_args()
    with sync_playwright() as playwright:
        for channel in (args.channel,) if args.channel else ("chrome", "msedge", "chromium"):
            try:
                browser = playwright.chromium.launch(channel=channel, headless=False)
                break
            except Error:
                continue
        else:
            raise SystemExit("无法启动浏览器。请安装 Chrome、Edge 或 Playwright Chromium。")
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(LOGIN_PAGES[args.source], wait_until="domcontentloaded")
            if args.source.endswith("-content"):
                city_page = context.new_page()
                city_page.goto(
                    LOGIN_PAGES[f"{SCOPES[args.source]}-wuxi"], wait_until="domcontentloaded"
                )
                page.bring_to_front()
            input("请在浏览器完成官方登录和来源页面设置，然后按 Enter 导出会话。")
            platform = SCOPES[args.source]
            cookies = validate_cookies(
                platform,
                [c for c in context.cookies() if allowed_domain(c["domain"], platform)],
            )
            directory = PROJECT_ROOT / "data" / "session-exports"
            private_directory(directory)
            with NamedTemporaryFile(
                dir=directory,
                prefix=args.source + "-",
                suffix=".json",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as output:
                json.dump({"cookies": cookies}, output)
                path = output.name
            print(f"会话已导出到 {path}。请在采集授权页导入，验证通过后删除该文件。")
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except (Error, OSError, ValueError):
        raise SystemExit("导出失败。请确认官方页面可访问、已登录并完成来源设置。") from None
