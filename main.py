"""主程序入口：从 IoC 容器获取 Downloader 并执行数据下载"""

from downloader import Downloader

if __name__ == "__main__":
    downloader = Downloader()
    downloader.download_all()
