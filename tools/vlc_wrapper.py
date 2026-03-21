
"""
VLC 包装器 - 解决非标准路径问题
使用前请确保已安装 python-vlc: pip install python-vlc
"""

import os
import sys
import ctypes
from pathlib import Path

class VLCWrapper:
    """VLC 包装器类"""

    def __init__(self, vlc_path=None):
        """
        初始化 VLC 包装器

        Args:
            vlc_path: VLC 安装路径，默认为 X:\Program Files (x86)\VideoLAN\VLC
        """
        self.vlc_path = vlc_path or r"X:\Program Files (x86)\VideoLAN\VLC"
        self._configure_environment()

    def _configure_environment(self):
        """配置 VLC 环境"""
        # 设置插件路径
        plugins_path = os.path.join(self.vlc_path, "plugins")
        if os.path.exists(plugins_path):
            os.environ['VLC_PLUGIN_PATH'] = plugins_path
            print(f"✅ 设置插件路径: {plugins_path}")

        # 添加到系统 PATH
        if self.vlc_path not in os.environ['PATH']:
            os.environ['PATH'] = self.vlc_path + ";" + os.environ['PATH']

        # 添加到 Python 路径
        if self.vlc_path not in sys.path:
            sys.path.insert(0, self.vlc_path)

    def get_vlc_instance(self, args=None):
        """
        获取 VLC 实例

        Args:
            args: VLC 命令行参数列表

        Returns:
            vlc.Instance 对象
        """
        # 配置环境
        self._configure_environment()

        try:
            import vlc
        except ImportError as e:
            raise ImportError(f"无法导入 VLC: {e}\n请确保已安装 python-vlc: pip install python-vlc")

        # 默认参数
        if args is None:
            args = [
                '--no-xlib',
                '--quiet',
                '--no-video-title-show',
                '--no-sub-autodetect-file',
            ]

        # 添加插件路径参数
        plugins_path = os.path.join(self.vlc_path, "plugins")
        if os.path.exists(plugins_path):
            args.append(f'--plugin-path={plugins_path}')

        try:
            instance = vlc.Instance(args)
            print(f"✅ 成功创建 VLC 实例")
            print(f"   LibVLC 版本: {vlc.libvlc_get_version()}")
            return instance
        except Exception as e:
            raise RuntimeError(f"创建 VLC 实例失败: {e}")

    def test_vlc(self):
        """测试 VLC 功能"""
        print("="*50)
        print("VLC 功能测试")
        print("="*50)

        try:
            # 测试导入
            import vlc
            print(f"✅ python-vlc 版本: {vlc.__version__}")

            # 测试实例创建
            instance = self.get_vlc_instance()

            # 测试媒体播放器创建
            player = instance.media_player_new()
            print("✅ 媒体播放器创建成功")

            # 测试媒体创建
            media = instance.media_new("")
            print("✅ 媒体对象创建成功")

            print("\n✅ VLC 功能测试通过！")
            return True

        except Exception as e:
            print(f"❌ VLC 测试失败: {e}")
            return False

# 使用示例
if __name__ == "__main__":
    # 创建包装器
    wrapper = VLCWrapper()

    # 测试 VLC
    if wrapper.test_vlc():
        print("\n✅ VLC 配置成功，可以在项目中使用")
    else:
        print("\n❌ VLC 配置失败，请检查安装")

    # 在项目中使用
    # instance = wrapper.get_vlc_instance()
    # player = instance.media_player_new()
    # ... 其他代码
