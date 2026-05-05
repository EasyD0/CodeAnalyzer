import logging


def logSetup():
    # 1. 创建 logger 对象
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # 防止重复添加 Handler（如果多次调用该函数）
    if not logger.handlers:
        # 2. 创建控制台处理器
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)

        # 3. 设置格式化模板
        # %(levelname)s  -> 日志等级
        # %(asctime)s    -> 时间
        # x              -> 你传入的字符串
        # %(filename)s   -> 文件名
        # %(lineno)d     -> 行号
        # %(funcName)s   -> 函数名
        # %(message)s    -> 日志内容
        log_format = f"[%(levelname)-8s] %(asctime)s [%(filename)s:%(lineno)d--%(funcName)s]: %(message)s"
        date_format = "%H:%M:%S"

        formatter = logging.Formatter(log_format, datefmt=date_format)
        ch.setFormatter(formatter)

        # 4. 将处理器添加到 logger
        logger.addHandler(ch)

    return logger


if __name__ == "__main__":
    logger = logSetup()
    logger.info("这是一条测试日志")
    logger.error("发现一个错误")
