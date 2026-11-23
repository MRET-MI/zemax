"""段階2: サンプルアームの Zemax モデルを作り、評価する（コリメータを切り替えて比較できる）

構成（シーケンシャル。ガルバノは「支点で座標を傾ける」ことで走査を表す）:
    ファイバー端（点光源, NA 0.107 = 1/e², ガウス）
    → コリメータ（rc04: 理想レンズ f 15 ／ ac080: vendor/AC080-020-B.zmx を前後反転）
    → 47 mm → ガルバノ 1（X 走査, 座標を Y 軸まわりに傾ける）→ 10 mm
    → ガルバノ 2（Y 走査, X 軸まわり）＝ LSM03-BB の入射瞳（絞り, Ø4 の開口）
    → LSM03-BB（vendor/LSM03-BB.zmx の絞り面〜。無ければ理想レンズ f 36 で代用）
    → 空気（Z ステージでピント合わせ）→ 容器底（ポリスチレン 1.0 mm）→ 組織（n 1.38）0.65 mm の焦点面

評価:
    瞳でのケラレ／ピント位置（走査レンズ最終面 → 容器底）／
    色収差による焦点ずれ（組織内, 焦点深度 0.207 mm と比較）／スポット（回折スポットと比較）／
    走査 ±1.5 mm: 位置の直線性（f·θ）・テレセントリック性（組織内の主光線角）・像面の曲がり・
    光路長の変化（平らな容器底が B スキャンで曲がって見える量）

使い方（OpticStudio で Programming > Interactive Extension を押してから）:
    python step2_sample_arm.py                 # rc04 と ac080 の両方（ac080 は vendor にファイルがあるとき）
    python step2_sample_arm.py --col rc04
    python step2_sample_arm.py --no-clip       # 入射瞳 Ø4 の開口を入れない（ケラレなしの比較用）
出力: out/sample_arm_<col>.zmx, out/step2_sample_arm_report.txt
注意: OpticStudio で開いている系は保存されずに置き換わる。
"""
import argparse
import math
import os

from oct_specs import (BOT_ND, BOT_T, BOT_VD, COLLIMATORS, D_COL_M1, D_M1_M2, DIV_RAD, DOF_EXCEL, DZ_CHROM_EXCEL,
                       EP_S, F_LSM03, FOCUS_DEPTH, FOV, OUT_DIR, PRIMARY_SPEC, SCAN_LENS_FILES, TISSUE_ND,
                       TISSUE_VD, WAVES_SPEC)
from step1_spectrometer import setup, solve_collimation
from zos_connect import connect
from zos_util import (Lens, add_reversed, copy_scan_lens, describe, lens_block, open_vendor, scan_min,
                      vendor_path)

REPORT = os.path.join(OUT_DIR, "step2_sample_arm_report.txt")
RSCE_SAMP = 6
SCAN_DEG = math.degrees(math.atan(FOV / 2 / F_LSM03))        # 光学的振り角（片側）2.39°
SCANS = [("中心", 0.0, 0.0), ("+X 端", SCAN_DEG, 0.0), ("−X 端", -SCAN_DEG, 0.0),
         ("+Y 端", 0.0, SCAN_DEG), ("−Y 端", 0.0, -SCAN_DEG), ("角 +X +Y", SCAN_DEG, SCAN_DEG)]
_lines = []


def out(t=""):
    print(t)
    _lines.append(t)


def build(ZOSAPI, app, system, key, clip):
    col = COLLIMATORS[key]
    system.New(False)
    setup(ZOSAPI, system)
    lens = Lens(ZOSAPI, system)
    lde = system.LDE
    obj = lde.GetSurfaceAt(0)
    obj.Comment = "ファイバー端（FC/APC, MFD 5.0 µm）"

    # コリメータ
    if col["kind"] == "paraxial":
        obj.Thickness = col["f"]
        c_last = lens.paraxial(f"コリメータ {col['label']}（軸上は無収差なので理想レンズ）", col["f"], D_COL_M1)
    else:
        path = vendor_path(col["file"])
        if path is None:
            raise FileNotFoundError(f"vendor/{col['file']} がありません（vendor/README.md 参照）")
        src = open_vendor(ZOSAPI, app, path)
        block = lens_block(src)
        src.Close(False)
        obj.Thickness = block[-1][1]
        _, c_last = add_reversed(lens, block, col["label"], D_COL_M1)
        solve_collimation(lens, obj, c_last, block[-1][1])

    # ガルバノ（支点で座標を傾ける）
    m1 = lens.coord_break("ガルバノ 1（X 走査）", 0.0, D_M1_M2)
    m2 = lens.coord_break("ガルバノ 2（Y 走査）", 0.0, 0.0)

    # 走査レンズ LSM03-BB
    scan_file = vendor_path(SCAN_LENS_FILES["LSM03-BB"])
    if scan_file:
        src = open_vendor(ZOSAPI, app, scan_file)
        out(describe(src, "vendor/LSM03-BB.zmx（取り込み元）"))
        pupil, _ = copy_scan_lens(lens, src)
        src.Close(False)
        note = "ベンダーファイル（絞り面から取り込み）"
    else:
        pupil = lens.add("LSM03-BB 入射瞳（理想レンズで代用）", F_LSM03)
        lens.paraxial("LSM03-BB（理想レンズ f 36 で代用）", F_LSM03, F_LSM03)
        note = "vendor/LSM03-BB.zmx が無いので理想レンズ f 36 で代用（収差なし）"
    pupil.IsStop = True
    pupil.Comment = "LSM03-BB 入射瞳 ＝ ガルバノ 2 の位置"
    if clip:
        try:
            lens.circular_aperture(pupil, EP_S / 2)
        except Exception as e:                        # API の違いで設定できないときは計算値だけ報告する
            out(f"  （注意）入射瞳 Ø{EP_S} の開口を設定できませんでした: {e}")
            clip = False
    last_air = lde.GetSurfaceAt(lde.NumberOfSurfaces - 2)   # 走査レンズの最終面（厚さ = 容器底までの空気）

    # 試料
    bot = lens.add(f"容器底 下面（ポリスチレン {BOT_T} mm）", BOT_T)
    lens.model_material(bot, BOT_ND, BOT_VD)
    tis = lens.add(f"容器底 上面 → 組織（n {TISSUE_ND}）", FOCUS_DEPTH)
    lens.model_material(tis, TISSUE_ND, TISSUE_VD)
    lde.GetSurfaceAt(lde.NumberOfSurfaces - 1).Comment = f"焦点面（組織内 {FOCUS_DEPTH} mm）"
    return lens, dict(c_last=c_last, m1=m1, m2=m2, pupil=pupil, last_air=last_air, bot=bot, tis=tis,
                      note=note, clip=clip)


def rsce_um(lens, w):
    return lens.val("RSCE", RSCE_SAMP, w, 0, 0) * 1000


def set_scan(lens, p, tx, ty):
    lens.par(p["m1"], 4, tx)     # Par4 = Tilt About Y（X 方向に振る）
    lens.par(p["m2"], 3, ty)     # Par3 = Tilt About X（Y 方向に振る）


def focus_air(lens, p):
    """空気の間隔（Z ステージ）を、主波長のスポットが焦点面で最小になるよう合わせる（粗 → 細）"""
    s = p["last_air"]
    a0 = s.Thickness
    setv = lambda v: setattr(s, "Thickness", v)
    metric = lambda: rsce_um(lens, PRIMARY_SPEC)
    a1, _ = scan_min(setv, metric, [a0 + 0.05 * k for k in range(-40, 41)])
    a2, _ = scan_min(setv, metric, [a1 + 0.002 * k for k in range(-30, 31)])
    return a2


def best_depth(lens, p, w, span=0.4, step=0.01):
    """組織の厚さ（焦点面の深さ）を動かして、波長 w のベストピントの深さを返す（元に戻す）"""
    s = p["tis"]
    setv = lambda v: setattr(s, "Thickness", v)
    vals = [FOCUS_DEPTH + step * k for k in range(-int(span / step), int(span / step) + 1)]
    z, _ = scan_min(setv, lambda: rsce_um(lens, w), vals)
    s.Thickness = FOCUS_DEPTH
    return z


def evaluate(lens, p, key):
    col = COLLIMATORS[key]
    lde = lens.lde
    img = lde.NumberOfSurfaces - 1
    out(f"=== サンプルアーム: コリメータ {col['label']} ===")
    out(f"  走査レンズ: {p['note']}")
    out(f"  ファイバー → コリメータ: {lde.GetSurfaceAt(0).Thickness:.3f} mm")

    # 瞳
    w_beam = col["f"] * DIV_RAD
    t_pupil = 1 - math.exp(-2 * (EP_S / 2) ** 2 / w_beam ** 2)
    out("")
    out("■ 入射瞳")
    out(f"  ビーム径（1/e²）Ø{2 * w_beam:.2f} mm、入射瞳 Ø{EP_S} → 片道の透過 {t_pupil:.3f}（往復 {t_pupil ** 2:.3f}）"
        f"  開口 {'あり' if p['clip'] else 'なし'}")

    # ピント
    set_scan(lens, p, 0, 0)
    a = focus_air(lens, p)
    out("")
    out("■ ピント（中心, 主波長）")
    out(f"  走査レンズ最終面 → 容器底 下面: {a:.3f} mm（Z ステージで合わせる距離）")

    # 色収差・スポット
    out("")
    out("■ 中心の各波長（焦点面 = 組織内 0.65 mm）")
    f_scan = F_LSM03
    out(f"  {'波長 nm':>8} {'RMS 半径 µm':>12} {'回折 1/e² 半径 µm':>17} {'ベストピントの深さ差 µm':>22}")
    dz = []
    for w, lam in enumerate(WAVES_SPEC, start=1):
        rms = rsce_um(lens, w)
        diff = lam * 1e-3 * f_scan / (math.pi * w_beam) * 1000
        z = (best_depth(lens, p, w) - FOCUS_DEPTH) * 1000
        dz.append(z)
        out(f"  {lam * 1000:8.1f} {rms:12.2f} {diff:17.2f} {z:+22.1f}")
    spread = max(dz) - min(dz)
    out(f"  色収差による焦点ずれ（771.5〜904.9 nm の幅）: {spread:.1f} µm（組織内）"
        f" ／ 焦点深度 {DOF_EXCEL * 1000:.0f} µm の {spread / (DOF_EXCEL * 1000) * 100:.0f} %"
        + (f" ／ Excel の見積り（コリメータ分のみ）{DZ_CHROM_EXCEL * 1000:.0f} µm" if key == "ac080" else ""))

    # 走査
    out("")
    out(f"■ 走査（光学的振り角 ±{SCAN_DEG:.2f}°, 視野 {FOV} mm）")
    out(f"  {'位置':<9} {'x mm':>8} {'y mm':>8} {'f·θ からのずれ %':>15} {'主光線角 °':>10} {'像面の曲がり µm':>15}"
        f" {'RMS 半径 µm':>11} {'光路長の変化 µm':>15}")
    opl0 = None
    worst = dict(tel=0.0, curv=0.0, opl=0.0, dist=0.0)
    for name, tx, ty in SCANS:
        set_scan(lens, p, tx, ty)
        x = lens.val("REAX", img, PRIMARY_SPEC, 0, 0, 0, 0)
        y = lens.val("REAY", img, PRIMARY_SPEC, 0, 0, 0, 0)
        r = math.hypot(x, y)
        th = math.radians(math.hypot(tx, ty))
        dist = (r / (f_scan * th) - 1) * 100 if th > 0 else 0.0
        tel = math.degrees(lens.val("RANG", img, PRIMARY_SPEC, 0, 0, 0, 0))
        curv = (best_depth(lens, p, PRIMARY_SPEC, span=0.3) - FOCUS_DEPTH) * 1000
        rms = rsce_um(lens, PRIMARY_SPEC)
        opl = lens.val("PLEN", p["c_last"].SurfaceNumber, p["bot"].SurfaceNumber, 0, 0, 0, 0) * 1000
        opl0 = opl if opl0 is None else opl0
        out(f"  {name:<9} {x:+8.3f} {y:+8.3f} {dist:+15.2f} {tel:+10.2f} {curv:+15.1f} {rms:11.2f} {opl - opl0:+15.1f}")
        worst["tel"] = max(worst["tel"], abs(tel))
        worst["curv"] = max(worst["curv"], abs(curv))
        worst["opl"] = max(worst["opl"], abs(opl - opl0))
        worst["dist"] = max(worst["dist"], abs(dist))
    set_scan(lens, p, 0, 0)
    out(f"  光路長の変化 = 平らな容器底が B スキャン画像で曲がって見える量（空気換算, 片道）。"
        f"最大 {worst['opl']:.1f} µm（軸方向分解能 4.45 µm と比べる）")
    out(f"  像面の曲がり 最大 {worst['curv']:.1f} µm（焦点深度の半分 {DOF_EXCEL * 500:.0f} µm と比べる）")
    return dict(t_pupil=t_pupil, spread=spread, **worst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", choices=["rc04", "ac080", "both"], default="both")
    ap.add_argument("--no-clip", action="store_true", help="入射瞳 Ø4 の開口を入れない")
    args = ap.parse_args()
    keys = ["rc04", "ac080"] if args.col == "both" else [args.col]

    ZOSAPI, app, system = connect()
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    for key in keys:
        if COLLIMATORS[key]["kind"] == "vendor" and vendor_path(COLLIMATORS[key]["file"]) is None:
            out(f"--- {key}: vendor/{COLLIMATORS[key]['file']} が無いので省略（vendor/README.md 参照）---")
            continue
        lens, p = build(ZOSAPI, app, system, key, clip=not args.no_clip)
        results[key] = evaluate(lens, p, key)
        path = os.path.join(OUT_DIR, f"sample_arm_{key}.zmx")
        system.SaveAs(path)
        out(describe(system, f"作成した系（{os.path.basename(path)}）"))
        out(f"保存しました: {path}")
        out("")

    if len(results) > 1:
        out("■ 比較")
        out(f"  {'':6} {'瞳透過(片道)':>11} {'色の焦点ずれ µm':>15} {'主光線角 最大 °':>15} {'像面の曲がり µm':>15}"
            f" {'光路長変化 µm':>13}")
        for k, r in results.items():
            out(f"  {k:6} {r['t_pupil']:11.3f} {r['spread']:15.1f} {r['tel']:15.2f} {r['curv']:15.1f} {r['opl']:13.1f}")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print(f"レポートを保存しました: {REPORT}")


if __name__ == "__main__":
    main()
