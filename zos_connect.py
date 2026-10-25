"""OpticStudio (ZOS-API) への Interactive Extension 接続ヘルパー (pythonnet 直接)

使い方:
    from zos_connect import connect
    ZOSAPI, app, system = connect()

事前に OpticStudio を起動し、Programming タブ > Interactive Extension を押して
接続待ち状態にしておくこと。
"""
import os
import winreg

_conn = None  # GC で接続が切れないよう保持


def _load_zosapi():
    import clr  # pythonnet (Zemax なしでも他スクリプトが import できるよう遅延読み込み)

    key = winreg.OpenKey(
        winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER),
        r"Software\Zemax", 0, winreg.KEY_READ)
    zemax_data = winreg.QueryValueEx(key, "ZemaxRoot")[0]
    winreg.CloseKey(key)

    clr.AddReference(os.path.join(zemax_data, r"ZOS-API\Libraries\ZOSAPI_NetHelper.dll"))
    import ZOSAPI_NetHelper

    if not ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize():
        raise RuntimeError("OpticStudio のインストール場所が見つかりません")
    zemax_dir = ZOSAPI_NetHelper.ZOSAPI_Initializer.GetZemaxDirectory()

    clr.AddReference(os.path.join(zemax_dir, "ZOSAPI.dll"))
    clr.AddReference(os.path.join(zemax_dir, "ZOSAPI_Interfaces.dll"))
    import ZOSAPI
    return ZOSAPI


def connect(instance=0):
    """Interactive Extension で接続し (ZOSAPI, app, system) を返す"""
    global _conn
    ZOSAPI = _load_zosapi()
    _conn = ZOSAPI.ZOSAPI_Connection()
    app = _conn.ConnectAsExtension(instance)
    if app is None:
        raise RuntimeError(
            "接続できません。OpticStudio で Programming > Interactive Extension を押してから実行してください")
    if not app.IsValidLicenseForAPI:
        raise RuntimeError("このライセンスでは ZOS-API を使用できません")
    return ZOSAPI, app, app.PrimarySystem
