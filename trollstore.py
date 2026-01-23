import platform
import sys
import traceback
from pathlib import Path

import click
import requests
from packaging.version import parse as parse_version
from pymobiledevice3.cli.cli_common import Command
from pymobiledevice3.exceptions import NoDeviceConnectedError, PyMobileDevice3Exception
from pymobiledevice3.lockdown import LockdownClient
from pymobiledevice3.services.diagnostics import DiagnosticsService
from pymobiledevice3.services.installation_proxy import InstallationProxyService

from sparserestore import backup, perform_restore


def exit(code=0):
    # if platform.system() == "Windows" and getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    #     input("按回车键退出...")

    sys.exit(code)


@click.command(cls=Command)
@click.pass_context
@click.option("--app", default=None, help="要替换的可移除系统应用（例如 Tips）。")
def cli(ctx, service_provider: LockdownClient, app: str) -> None:
    os_names = {
        "iPhone": "iOS",
        "iPad": "iPadOS",
        "iPod": "iOS",
        "AppleTV": "tvOS",
        "Watch": "watchOS",
        "AudioAccessory": "HomePod Software Version",
        "RealityDevice": "visionOS",
    }

    device_class = service_provider.get_value(key="DeviceClass")
    device_build = service_provider.get_value(key="BuildVersion")
    device_version = parse_version(service_provider.product_version)

    if not all([device_class, device_build, device_version]):
        click.secho("无法获取设备信息！", fg="red")
        click.secho("请确保您的设备已连接并重试。", fg="red")
        return

    os_name = (os_names[device_class] + " ") if device_class in os_names else ""
    if (
        device_version < parse_version("15.0")
        or device_version > parse_version("17.0")
        or parse_version("16.7") < device_version < parse_version("17.0")
        or device_version == parse_version("16.7")
        and device_build != "20H18"  # 16.7 RC
    ):
        click.secho(f"不支持 {os_name}{device_version} ({device_build})。", fg="red")
        click.secho("此工具仅兼容 iOS/iPadOS 15.0 - 16.7 RC 和 17.0。", fg="red")
        return

    if not app:
        app = click.prompt(
            """
请指定您想要替换为 TrollStore Helper 的可移除系统应用。
如果您不知道该指定哪个应用，请输入 Tips（提示）应用。

输入应用名称"""
    )

    if not app.endswith(".app"):
        app += ".app"

    apps_json = InstallationProxyService(service_provider).get_apps(application_type="System", calculate_sizes=False)

    app_path = None
    for key, value in apps_json.items():
        if isinstance(value, dict) and "Path" in value:
            potential_path = Path(value["Path"])
            if potential_path.name.lower() == app.lower():
                app_path = potential_path
                app = app_path.name

    if not app_path:
        click.secho(f"未能找到可移除的系统应用 '{app}'！", fg="red")
        click.secho(f"请确保您输入的应用名称正确，并且系统应用 '{app}' 已安装在您的设备上。", fg="red")
        return
    elif Path("/private/var/containers/Bundle/Application") not in app_path.parents:
        click.secho(f"'{app}' 不是可移除的系统应用！", fg="red")
        click.secho("请选择一个可移除的系统应用。这些必须是可以删除并重新下载的 Apple 原生应用。", fg="red")
        return

    app_uuid = app_path.parent.name

    try:
        response = requests.get("https://gitee.com/RemotePro/RemotePro/releases/download/v1/PersistenceHelper_Embedded")
        response.raise_for_status()
        helper_contents = response.content
    except Exception as e:
        click.secho(f"下载 TrollStore Helper 失败: {e}", fg="red")
        return
    click.secho(f"正在将 {app} 替换为 TrollStore Helper。(UUID: {app_uuid})", fg="yellow")

    back = backup.Backup(
        files=[
            backup.Directory("", "RootDomain"),
            backup.Directory("Library", "RootDomain"),
            backup.Directory("Library/Preferences", "RootDomain"),
            backup.ConcreteFile("Library/Preferences/temp", "RootDomain", owner=33, group=33, contents=helper_contents, inode=0),
            backup.Directory(
                "",
                f"SysContainerDomain-../../../../../../../../var/backup/var/containers/Bundle/Application/{app_uuid}/{app}",
                owner=33,
                group=33,
            ),
            backup.ConcreteFile(
                "",
                f"SysContainerDomain-../../../../../../../../var/backup/var/containers/Bundle/Application/{app_uuid}/{app}/{app.split('.')[0]}",
                owner=33,
                group=33,
                contents=b"",
                inode=0,
            ),
            backup.ConcreteFile(
                "",
                "SysContainerDomain-../../../../../../../../var/.backup.i/var/root/Library/Preferences/temp",
                owner=501,
                group=501,
                contents=b"",
            ),  # Break the hard link
            backup.ConcreteFile("", "SysContainerDomain-../../../../../../../.." + "/crash_on_purpose", contents=b""),
        ]
    )

    try:
        perform_restore(back, reboot=False)
    except PyMobileDevice3Exception as e:
        if "Find My" in str(e):
            click.secho("必须禁用“查找我的 iPhone”才能使用此工具。", fg="red")
            click.secho("请在设置中禁用“查找我的 iPhone”（设置 -> [您的名字] -> 查找），然后重试。", fg="red")
            exit(1)
        elif "crash_on_purpose" not in str(e):
            raise e

    click.secho("正在重启设备", fg="green")

    with DiagnosticsService(service_provider) as diagnostics_service:
        diagnostics_service.restart()

    click.secho("重启后如果您使用“查找我的 iPhone”，请确保将其重新开启。", fg="green")
    click.secho("安装 TrollStore 后，请务必在您选择的应用中安装一个合适的持久化助手 (Persistence Helper)！\n", fg="green")


def main():
    try:
        cli(standalone_mode=False)
    except NoDeviceConnectedError:
        click.secho("请连接您的设备并重试。如果USB已连接，重新插入后再重试", fg="red")
        exit(1)
    except click.UsageError as e:
        click.secho(e.format_message(), fg="red")
        click.echo(cli.get_help(click.Context(cli)))
        exit(2)
    except Exception:
        click.secho("发生错误！", fg="red")
        click.secho(traceback.format_exc(), fg="red")
        exit(1)

    exit(0)


if __name__ == "__main__":
    main()