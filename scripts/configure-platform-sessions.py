"""Initialize missing session credentials in the ignored project .env."""

import os
from secrets import token_urlsafe

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from hotradar.config import PROJECT_ROOT


def main():
    path = PROJECT_ROOT / ".env"
    current = dotenv_values(path)
    values = {
        "HOTRADAR_SESSION_KEY": Fernet.generate_key().decode(),
        "HOTRADAR_SESSION_ADMIN_TOKEN": token_urlsafe(32),
    }
    existing = {name: os.environ.get(name) or current.get(name) for name in values}
    if existing["HOTRADAR_SESSION_KEY"]:
        try:
            Fernet(existing["HOTRADAR_SESSION_KEY"])
        except ValueError:
            raise SystemExit("现有会话加密密钥格式无效，请检查部署配置。") from None
    token = existing["HOTRADAR_SESSION_ADMIN_TOKEN"]
    if token and len(token) < 32:
        raise SystemExit("现有会话管理令牌不足 32 字符，请检查部署配置。")
    missing = [name for name in values if not existing[name]]
    if missing:
        with open(
            path, "a", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)
        ) as output:
            output.write("\n")
            for name in missing:
                output.write(f"{name}={values[name]}\n")
    print("会话配置已就绪。请重启 API 和 Worker，管理令牌可从服务端 .env 私下读取。")


if __name__ == "__main__":
    main()
