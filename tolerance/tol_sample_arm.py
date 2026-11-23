"""サンプルアームの公差解析用 .zmx を作る（OpticStudio 標準の公差解析で使う）

公称系: ../step2_sample_arm.py の組立処理（入射瞳 Ø4 の開口あり）→ Z ステージ（空気の間隔）を公称のピントに合わせる
マルチコンフィグ: 1 = 走査の中心、2 = 走査の角（X・Y とも ±1.5 mm 相当の振り角）
公差（Excel「公差」シートの SA_*）:
    ファイバー端の軸方向 TTHI ／ コリメータの偏心（＝ファイバーの横ずれ）・傾き ／
    ガルバノ 1 → 2 の距離 TTHI（瞳 → レンズで吸収）／ ガルバノ 2 → 入射瞳 TTHI（容器までの空気で吸収）／
    LSM03-BB の偏心・傾き ／ 容器底の厚さ TTHI・屈折率 TIND ／ 容器の傾き TETX/TETY
評価基準（メリットファンクション）: 中心の 5 波長と、角の 840 nm の RMS スポット半径（RSCE）
補正あり（Z ステージ = 走査レンズ → 容器底の空気）と補正なしの 2 ファイル

使い方（OpticStudio で Programming > Interactive Extension を押してから）:
    python tol_sample_arm.py                  # rc04 と ac080（ac080 は ../vendor にファイルがあるとき）
    python tol_sample_arm.py --col rc04
出力: out/tol_sample_arm_<col>_spot[_nocomp].zmx, out/tol_sample_arm_report.txt
注意: OpticStudio で開いている系は保存されずに置き換わる。../out の .zmx は変更しない。
"""
import argparse
import os

from tol_common import GUIDE, OUT, Merit, Tde, load_tolerances, multiconfig
from oct_specs import COLLIMATORS, PRIMARY_SPEC, WAVES_SPEC
import step2_sample_arm as s2
from zos_connect import connect
from zos_util import describe, vendor_path

REPORT = os.path.join(OUT, "tol_sample_arm_report.txt")
_lines = []


def out(t=""):
    print(t)
    _lines.append(t)


def build_nominal(ZOSAPI, app, system, key):
    lens, p = s2.build(ZOSAPI, app, system, key, clip=True)
    s2.set_scan(lens, p, 0, 0)
    s2.focus_air(lens, p)                                  # Z ステージを公称のピントに
    m1, m2, pupil = p["m1"].SurfaceNumber, p["m2"].SurfaceNumber, p["pupil"].SurfaceNumber
    last_air, bot, tis = p["last_air"].SurfaceNumber, p["bot"].SurfaceNumber, p["tis"].SurfaceNumber
    idx = dict(col=(1, m1 - 1), m1=m1, m2=m2, pupil=pupil, lens=(pupil + 1, last_air), last_air=last_air,
               bot=bot, tis=tis, image=lens.lde.NumberOfSurfaces - 1)
    multi_ok = True
    try:
        multiconfig(ZOSAPI, system, [(m1, 4, [0.0, s2.SCAN_DEG]), (m2, 3, [0.0, s2.SCAN_DEG])], 2)
    except Exception as e:                                 # マルチコンフィグが設定できないときは中心だけで評価する
        out(f"  （注意）マルチコンフィグを設定できませんでした（中心だけで評価）: {e}")
        multi_ok = False
    return lens, idx, p["note"], multi_ok


def set_tolerances(tde, idx, t):
    tde.pm("TTHI", 0, 0, t["SA_FIB_DZ"], "ファイバー端 → コリメータ")
    c0, c1 = idx["col"]
    tde.element(c0, c1, t["SA_FIB_DXY"], t["SA_COL_TILT"], "コリメータ（偏心 = ファイバーの横ずれ）")
    tde.add("TTHI", idx["m1"], idx["pupil"], -t["SA_M1M2"], t["SA_M1M2"], "ガルバノ 1 → 2（瞳 → レンズで吸収）")
    tde.add("TTHI", idx["pupil"], idx["last_air"], -t["SA_PUPIL"], t["SA_PUPIL"],
            "ガルバノ 2 → LSM03-BB 入射瞳（容器までの空気で吸収）")
    l0, l1 = idx["lens"]
    tde.element(l0, l1, t["SA_LSM_DXY"], t["SA_LSM_TILT"], "LSM03-BB")
    tde.pm("TTHI", idx["bot"], 0, t["SA_BOT_T"], "容器底の厚さ")
    tde.pm("TIND", idx["bot"], 0, t["SA_BOT_N"], "容器底の屈折率")
    tde.element(idx["bot"], idx["tis"], 0, t["SA_DISH_TILT"], "容器（底）")


def set_merit(ZOSAPI, system, multi_ok):
    m = Merit(ZOSAPI, system)
    if multi_ok:
        m.add("CONF", (1,), comment="走査の中心")
    for w, lam in enumerate(WAVES_SPEC, start=1):
        m.add("RSCE", (6, w, 0, 0), comment=f"中心 RMS スポット半径 {lam * 1000:.1f} nm")
    if multi_ok:
        m.add("CONF", (2,), comment="走査の角（±1.5 mm 相当）")
        m.add("RSCE", (6, PRIMARY_SPEC, 0, 0), comment="角 RMS スポット半径 840 nm")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", choices=["rc04", "ac080", "both"], default="both")
    args = ap.parse_args()
    keys = ["rc04", "ac080"] if args.col == "both" else [args.col]

    tol, src = load_tolerances()
    out(f"公差の値: {src}")
    ZOSAPI, app, system = connect()
    os.makedirs(OUT, exist_ok=True)

    for key in keys:
        col = COLLIMATORS[key]
        if col["kind"] == "vendor" and vendor_path(col["file"]) is None:
            out(f"--- {key}: ../vendor/{col['file']} が無いので省略 ---")
            continue
        out(f"=== サンプルアーム: {col['label']} ===")
        lens, idx, note, multi_ok = build_nominal(ZOSAPI, app, system, key)
        out(f"  走査レンズ: {note}")
        table_txt = ""
        for comp in (True, False):
            tde = Tde(ZOSAPI, system)
            set_tolerances(tde, idx, tol)
            if comp:
                tde.comp_thickness(idx["last_air"], tol["SA_COMP_Z"], "Z ステージ（走査レンズ → 容器底）")
                table_txt = tde.table()
            m = set_merit(ZOSAPI, system, multi_ok)
            name = f"tol_sample_arm_{key}_spot{'' if comp else '_nocomp'}.zmx"
            system.SaveAs(os.path.join(OUT, name))
            out(f"  {name}: 公称のメリット値 {m.value():.5f} mm（RMS 半径の二乗平均）")
        out("  公差データエディタ（補正ありの内容。補正なしは最後の COMP の行が無いだけ）:")
        out(table_txt)
        out(describe(system, "公称系（面番号の確認用）"))
        out("")

    out(GUIDE)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print(f"レポートを保存しました: {REPORT}")


if __name__ == "__main__":
    main()
