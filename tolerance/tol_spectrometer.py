"""分光器の公差解析用 .zmx を作る（OpticStudio 標準の公差解析で使う）

公称系: ../step1_spectrometer.py の組立処理（入射瞳 Ø8 の開口あり）→ センサ位置を公称のベストピントに合わせる
        → センサの前に「センサ取付」の座標の傾きを足す（センサのあおりの公差・高さと回転の補正に使う）
公差（Excel「公差」シートの SP_*）:
    ファイバー端の軸方向 TTHI ／ コリメータの偏心（＝ファイバーの横ずれ）・傾き TEDX/TEDY/TETX/TETY ／
    GP1012L の入射角 TETX・あおり TETY・溝の向き TETZ ／ グレーティング → 入射瞳 TTHI（センサは固定）／
    LSM05-BB の偏心・傾き ／ センサのあおり TPAR
評価基準（メリットファンクション）ごとに、補正あり／なしの 2 ファイル:
    spot: 5 波長の RMS スポット半径（RSCE）。補正 = ピント（センサまでの厚さ）
    line: 3 波長（771.5・840・904.9 nm）の主光線の高さ（REAX, 目標 0）。補正 = カメラの高さ・回転・ピント
          → 補正後に残るのは受光ラインの弓なり（許容 ±0.082 mm と比べる）

使い方（OpticStudio で Programming > Interactive Extension を押してから）:
    python tol_spectrometer.py                # rc08 と ac254（ac254 は ../vendor にファイルがあるとき）
    python tol_spectrometer.py --col rc08
出力: out/tol_spectrometer_<col>_<spot|line>[_nocomp].zmx, out/tol_spectrometer_report.txt
注意: OpticStudio で開いている系は保存されずに置き換わる。../out の .zmx は変更しない。
"""
import argparse
import os

from tol_common import GUIDE, OUT, Merit, Tde, load_tolerances
from oct_specs import COLLIMATORS, PRIMARY_SPEC, WAVES_SPEC
import step1_spectrometer as s1
from zos_connect import connect
from zos_util import describe, vendor_path

REPORT = os.path.join(OUT, "tol_spectrometer_report.txt")
_lines = []


def out(t=""):
    print(t)
    _lines.append(t)


def build_nominal(ZOSAPI, app, system, key):
    lens, g, note, _, _ = s1.build(ZOSAPI, app, system, key, clip=True)
    s1.focus_scan(lens)                                    # センサを公称のベストピントに置く
    lde = lens.lde
    img = lde.NumberOfSurfaces - 1
    cb = lde.InsertNewSurfaceAt(img)                       # 像面の直前に「センサ取付」の座標の傾き
    lens.set_type(cb, "CoordinateBreak")
    cb.Comment = "センサ取付（公差: あおり ／ 補正: 高さ・回転）"
    gi = g.SurfaceNumber
    idx = dict(col=(1, gi - 2), grating=(gi, gi + 1), pupil=gi + 3, lens=(gi + 4, img - 1),
               last=img - 1, sensor_cb=img, image=img + 1)
    return lens, idx, note


def set_tolerances(tde, idx, t):
    tde.pm("TTHI", 0, 0, t["SP_FIB_DZ"], "ファイバー端 → コリメータ")
    c0, c1 = idx["col"]
    tde.element(c0, c1, t["SP_FIB_DXY"], t["SP_COL_TILT"], "コリメータ（偏心 = ファイバーの横ずれ）")
    g0, g1 = idx["grating"]
    tde.pm("TETX", g0, g1, t["SP_GR_YAW"], "GP1012L 入射角（ヨー）")
    tde.pm("TETY", g0, g1, t["SP_GR_PITCH"], "GP1012L あおり（ピッチ）")
    tde.pm("TETZ", g0, g1, t["SP_GR_ROLL"], "GP1012L 溝の向き（面内回転）")
    tde.add("TTHI", idx["pupil"], idx["last"], -t["SP_PUPIL"], t["SP_PUPIL"],
            "グレーティング → LSM05-BB 入射瞳（センサは固定）")
    l0, l1 = idx["lens"]
    tde.element(l0, l1, t["SP_LSM_DXY"], t["SP_LSM_TILT"], "LSM05-BB")
    tde.pm("TPAR", idx["sensor_cb"], 3, t["SP_SEN_TILT"], "センサのあおり（X 軸まわり）")
    tde.pm("TPAR", idx["sensor_cb"], 4, t["SP_SEN_TILT"], "センサのあおり（Y 軸まわり）")


def set_merit(ZOSAPI, system, idx, crit):
    m = Merit(ZOSAPI, system)
    if crit == "spot":
        for w, lam in enumerate(WAVES_SPEC, start=1):
            m.add("RSCE", (6, w, 0, 0), comment=f"RMS スポット半径 {lam * 1000:.1f} nm")
    else:
        for w in (1, PRIMARY_SPEC, len(WAVES_SPEC)):
            m.add("REAX", (idx["image"], w, 0, 0, 0, 0), comment=f"受光ラインの高さ {WAVES_SPEC[w - 1] * 1000:.1f} nm")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", choices=["rc08", "ac254", "both"], default="both")
    args = ap.parse_args()
    keys = ["rc08", "ac254"] if args.col == "both" else [args.col]

    tol, src = load_tolerances()
    out(f"公差の値: {src}")
    ZOSAPI, app, system = connect()
    os.makedirs(OUT, exist_ok=True)

    for key in keys:
        col = COLLIMATORS[key]
        if col["kind"] == "vendor" and vendor_path(col["file"]) is None:
            out(f"--- {key}: ../vendor/{col['file']} が無いので省略 ---")
            continue
        out(f"=== 分光器: {col['label']} ===")
        lens, idx, note = build_nominal(ZOSAPI, app, system, key)   # 公称系は 1 回だけ組む
        out(f"  集光レンズ: {note}")
        table_txt = ""
        for crit in ("spot", "line"):
            for comp in (True, False):
                tde = Tde(ZOSAPI, system)                           # 公差データエディタ・メリットは毎回作り直す
                set_tolerances(tde, idx, tol)
                if comp:
                    tde.comp_thickness(idx["last"], tol["SP_COMP_FOCUS"], "ピント（TSD-40801S）")
                    if crit == "line":
                        tde.comp_param(idx["sensor_cb"], 1, tol["SP_COMP_HEIGHT"], "カメラの高さ（S6）")
                        tde.comp_param(idx["sensor_cb"], 5, tol["SP_COMP_ROLL"], "カメラの回転（S7）")
                        table_txt = tde.table()
                m = set_merit(ZOSAPI, system, idx, crit)
                name = f"tol_spectrometer_{key}_{crit}{'' if comp else '_nocomp'}.zmx"
                system.SaveAs(os.path.join(OUT, name))
                unit = "mm（RMS 半径の二乗平均）" if crit == "spot" else "mm（受光ラインの高さの二乗平均）"
                out(f"  {name}: 公称のメリット値 {m.value():.5f} {unit}")
        out("  公差データエディタ（line・補正ありの内容。他のファイルは補正の行が違うだけ）:")
        out(table_txt)
        out(describe(system, "公称系（面番号の確認用）"))
        out("")

    out(GUIDE)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print(f"レポートを保存しました: {REPORT}")


if __name__ == "__main__":
    main()
