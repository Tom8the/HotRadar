"""Initialize the session encryption key in the ignored project .env."""

import os

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from hotradar.config import PROJECT_ROOT


def main():
    path = PROJECT_ROOT / ".env"
    name = "HOTRADAR_SESSION_KEY"
    key = os.environ.get(name) or dotenv_values(path).get(name)
    if key:
        try:
            Fernet(key)
        except ValueError:
            raise SystemExit("现有会话加密密钥格式无效，请检查部署配置。") from None
    else:
        with open(
            path, "a", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)
        ) as output:
            output.write(f"\n{name}={Fernet.generate_key().decode()}\n")
    print("会话加密配置已就绪。请重启 API 和 Worker。")


if __name__ == "__main__":
    main()
