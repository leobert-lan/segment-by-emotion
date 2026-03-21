#!/usr/bin/env python3
"""
VLC 安装检查与配置脚本
"""

import sys
import subprocess
import platform


def check_vlc_installed():
    """检查 VLC 是否已安装"""
    system = platform.system()

    if system == 'Windows':
        # 检查 Windows 注册表
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\VideoLAN\VLC")
            install_path = winreg.QueryValueEx(key, "InstallDir")[0]
            print(f"✓ VLC 已安装: {install_path}")
            return True
        except:
            return False

    elif system in ['Darwin', 'Linux']:
        # 检查命令行
        try:
            result = subprocess.run(['which', 'vlc'],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✓ VLC 已安装: {result.stdout.strip()}")
                return True
        except:
            pass

    return False


def install_vlc():
    """指导用户安装 VLC"""
    system = platform.system()

    print("\n" + "=" * 50)
    print("VLC 安装指南")
    print("=" * 50)

    if system == 'Windows':
        print("1. 访问 https://www.videolan.org/vlc/")
        print("2. 下载 Windows 版本")
        print("3. 运行安装程序")
        print("4. 安装时选择 '安装所有组件'")
        print("5. 确保勾选 '关联文件类型'")

    elif system == 'Darwin':
        print("方法一: 使用 Homebrew")
        print(
            "  1. 安装 Homebrew: /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
        print("  2. 安装 VLC: brew install vlc")
        print("\n方法二: 下载 DMG")
        print("  1. 访问 https://www.videolan.org/vlc/download-macosx.html")
        print("  2. 下载并拖拽到应用程序文件夹")

    elif system == 'Linux':
        print("Ubuntu/Debian:")
        print("  sudo apt update && sudo apt install vlc")
        print("\nFedora:")
        print("  sudo dnf install vlc")
        print("\nArch Linux:")
        print("  sudo pacman -S vlc")

    print("\n安装完成后重新运行此脚本检查")


def install_python_vlc():
    """安装 python-vlc 库"""
    print("\n安装 python-vlc 库...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-vlc"])
        print("✓ python-vlc 安装成功")
        return True
    except subprocess.CalledProcessError:
        print("✗ python-vlc 安装失败")
        return False


def main():
    print("VLC 安装检查工具")
    print("=" * 50)

    # 检查 VLC
    if not check_vlc_installed():
        print("✗ VLC 未安装")
        install_vlc()
        return

    # 检查 python-vlc
    try:
        import vlc
        print(f"✓ python-vlc 已安装: {vlc.__version__}")
    except ImportError:
        print("✗ python-vlc 未安装")
        if install_python_vlc():
            try:
                import vlc
                print(f"✓ python-vlc 导入成功: {vlc.__version__}")
            except ImportError as e:
                print(f"✗ 导入失败: {e}")

    # 测试 VLC
    print("\n测试 VLC 功能...")
    try:
        import vlc
        instance = vlc.Instance('--no-xlib', '--quiet')
        print("✓ VLC 实例创建成功")
        print(f"  LibVLC 版本: {vlc.libvlc_get_version()}")
        print("✓ VLC 配置完成，可以正常使用")
    except Exception as e:
        print(f"✗ VLC 测试失败: {e}")


if __name__ == "__main__":
    main()