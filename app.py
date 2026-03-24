import logging

from src.app.bootstrap import build_app


def _configure_logging() -> None:
    # 默认 root level 是 WARNING，未配置时 INFO/DEBUG 日志不会显示。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


if __name__ == "__main__":
    _configure_logging()
    app = build_app()
    app.mainloop()

