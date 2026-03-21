#!/usr/bin/env python3
"""
VLC 手动配置脚本 - 针对非标准安装路径
"""

import os
import sys
import ctypes
import platform
from pathlib import Path


def configure_vlc_manual():
    """手动配置 VLC 环境"""

    # 您的 VLC 安装路径
    VLC_INSTALL_PATH = r"X:\Program Files (x86)\VideoLAN\VLC"

    if not os.path.exists(VLC_INSTALL_PATH):
        print(f"❌ VLC 路径不存在: {VLC_INSTALL_PATH}")
        print("请确认 VLC 已正确安装在该路径")
        return False

    print(f"✅ 找到 VLC 安装路径: {VLC_INSTALL_PATH}")

    # 设置环境变量
    vlc_plugins = os.path.join(VLC_INSTALL_PATH, "plugins")
    vlc_lib = os.path.join(VLC_INSTALL_PATH, "libvlc.dll")

    if os.path.exists(vlc_plugins):
        os.environ['VLC_PLUGIN_PATH'] = vlc_plugins
        print(f"✅ 设置插件路径: {vlc_plugins}")
    else:
        print(f"⚠️  插件目录不存在: {vlc_plugins}")

    # 将 VLC 路径添加到系统 PATH
    if VLC_INSTALL_PATH not in os.environ['PATH']:
        os.environ['PATH'] = VLC_INSTALL_PATH + ";" + os.environ['PATH']
        print(f"✅ 添加 VLC 到系统 PATH")

    # 检查关键文件
    required_files = [
        "libvlc.dll",
        "libvlccore.dll",
        "vlc.exe"
    ]

    for file in required_files:
        file_path = os.path.join(VLC_INSTALL_PATH, file)
        if os.path.exists(file_path):
            print(f"✅ 找到 {file}")
        else:
            print(f"❌ 缺少 {file}")

    return True


def test_vlc_import():
    """测试 VLC 导入"""
    try:
        # 临时修改 sys.path
        vlc_path = r"X:\Program Files (x86)\VideoLAN\VLC"
        if vlc_path not in sys.path:
            sys.path.insert(0, vlc_path)

        # 尝试导入
        import vlc
        print(f"✅ 成功导入 python-vlc: {vlc.__version__}")

        # 测试创建实例
        instance = vlc.Instance('--no-xlib', '--quiet')
        print(f"✅ 成功创建 VLC 实例")
        print(f"✅ LibVLC 版本: {vlc.libvlc_get_version()}")

        return True

    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        print("\n可能的原因:")
        print("1. python-vlc 未安装: pip install python-vlc")
        print("2. VLC 路径不正确")
        print("3. 系统架构不匹配 (32位/64位)")
        return False

    except Exception as e:
        print(f"❌ 其他错误: {e}")
        return False


def check_system_architecture():
    """检查系统架构"""
    print("\n" + "=" * 50)
    print("系统架构检查")
    print("=" * 50)

    # Python 架构
    is_64bit = sys.maxsize > 2 ** 32
    print(f"Python 架构: {'64位' if is_64bit else '32位'}")

    # 操作系统架构
    system = platform.architecture()
    print(f"操作系统: {platform.system()} {platform.release()}")
    print(f"系统架构: {system}")

    # 检查 VLC 架构
    vlc_path = r"X:\Program Files (x86)\VideoLAN\VLC\vlc.exe"
    if os.path.exists(vlc_path):
        try:
            # 使用 PE 文件头检查
            with open(vlc_path, 'rb') as f:
                f.seek(60)  # PE 头偏移
                pe_offset = int.from_bytes(f.read(4), 'little')
                f.seek(pe_offset + 4)  # Machine 字段
                machine = int.from_bytes(f.read(2), 'little')

                if machine == 0x014c:
                    print("✅ VLC 架构: 32位 (x86)")
                elif machine == 0x8664:
                    print("✅ VLC 架构: 64位 (x64)")
                else:
                    print(f"⚠️  VLC 架构: 未知 (0x{machine:04x})")
        except:
            print("⚠️  无法确定 VLC 架构")

    # 架构匹配建议
    if is_64bit:
        print("\n💡 建议: 使用 64位 VLC 以获得最佳兼容性")
        print("   下载地址: https://www.videolan.org/vlc/download-windows.html")
    else:
        print("\n✅ 当前为 32位环境，与您的 VLC 版本匹配")


def create_vlc_wrapper():
    """创建 VLC 包装器，解决导入问题"""

    wrapper_code = '''
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
            vlc_path: VLC 安装路径，默认为 X:\\Program Files (x86)\\VideoLAN\\VLC
        """
        self.vlc_path = vlc_path or r"X:\\Program Files (x86)\\VideoLAN\\VLC"
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
            raise ImportError(f"无法导入 VLC: {e}\\n请确保已安装 python-vlc: pip install python-vlc")

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

            print("\\n✅ VLC 功能测试通过！")
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
        print("\\n✅ VLC 配置成功，可以在项目中使用")
    else:
        print("\\n❌ VLC 配置失败，请检查安装")

    # 在项目中使用
    # instance = wrapper.get_vlc_instance()
    # player = instance.media_player_new()
    # ... 其他代码
'''

    # 保存包装器
    with open("vlc_wrapper.py", "w", encoding="utf-8") as f:
        f.write(wrapper_code)

    print("✅ 已创建 vlc_wrapper.py")
    print("   在您的项目中导入: from vlc_wrapper import VLCWrapper")


def main():
    """主函数"""
    print("=" * 50)
    print("VLC 手动配置工具")
    print("=" * 50)

    # 1. 配置环境
    print("\n1. 配置 VLC 环境...")
    if not configure_vlc_manual():
        print("❌ 环境配置失败")
        return

    # 2. 检查系统架构
    check_system_architecture()

    # 3. 测试导入
    print("\n2. 测试 VLC 导入...")
    if test_vlc_import():
        print("\n✅ VLC 配置成功！")
    else:
        print("\n⚠️  VLC 导入失败，尝试创建包装器...")

        # 4. 创建包装器
        print("\n3. 创建 VLC 包装器...")
        create_vlc_wrapper()

        print("\n💡 使用方法:")
        print("   1. 将 vlc_wrapper.py 复制到您的项目")
        print("   2. 在代码中导入: from vlc_wrapper import VLCWrapper")
        print("   3. 使用 wrapper.get_vlc_instance() 获取实例")

    # 5. 安装 python-vlc 检查
    print("\n4. 检查 python-vlc 安装...")
    try:
        import pip
        from pip._internal.utils.misc import get_installed_distributions

        installed = [pkg.key for pkg in get_installed_distributions()]
        if 'python-vlc' in installed:
            print("✅ python-vlc 已安装")
        else:
            print("❌ python-vlc 未安装")
            print("   请运行: pip install python-vlc")
    except:
        print("⚠️  无法检查 pip 安装状态")

    print("\n" + "=" * 50)
    print("配置完成")
    print("=" * 50)


if __name__ == "__main__":
    main()