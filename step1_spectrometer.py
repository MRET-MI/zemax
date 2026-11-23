"""段階1: 分光器の Zemax モデルを作り、評価する（コリメータを切り替えて比較できる）

構成（シーケンシャル, 分散は YZ 面）:
    ファイバー端（点光源, NA 0.107 = 1/e², ガウス）
    → コリメータ（rc08: 理想レンズ f 33 ／ ac254: vendor/AC254-035-B.zmx を前後反転）
    → 40 mm → 座標の傾き θc → GP1012L（回折格子面 1.2 本/µm ＋ N-BK7 3 mm）→ 座標の傾き（840 nm の主光線を軸に）
    → LSM05-BB（vendor/LSM05-BB.zmx の絞り面〜。無ければ理想レンズ f 110 で代用）→ センサ

評価: 各波長のセンサ上の位置／RMS スポット（重心基準）とガウスビームの回折スポットの比較／
      波長ごとのベストピント → センサの傾き・像面の曲がり（許容 z_R 0.229 mm, あおり 1.28°）／
      波長と位置の対応（センサ端の波長）／グレーティング上のビーム径（KS1RS 有効径 Ø22.9）

使い方（OpticStudio で Programming > Interactive Extension を押してから）:
    python step1_spectrometer.py               # rc08 と ac254 の両方（ac254 は vendor にファイルがあるとき）
    python step1_spectrometer.py --col rc08
出力: out/spectrometer_<col>.zmx, out/step1_spectrometer_report.txt
注意: OpticStudio で開いている系は保存されずに置き換わる。
"""
import argparse
import math
import os

from oct_specs import (COLLIMATORS, D_COL_G, DIV_RAD, F_LSM05, G_LINES_PER_UM, GRATING_GLASS, GRATING_T,
                       KS1RS_CA, LAM_HI_UM, LAM_LO_UM, NA_FIBER, OUT_DIR, PITCH_UM, PRIMARY_SPEC, SCAN_LENS_FILES,
                       SENSOR_HALF, THETA_C_DEG, TILT_TOL_DEG, WAVES_SPEC, ZR_SENSOR)
from zos_connect import connect
from zos_util import Lens, add_reversed, copy_scan_lens, describe, lens_block, open_vendor, vendor_path

REPORT = os.path.join(OUT_DIR, "step1_spectrometer_report.txt")
FOCUS_RANGE, FOCUS_STEP = 1.5, 0.025        # ピント走査 ±1.5 mm
RSCE_SAMP = 6
_lines = []


def out(t=""):
    print(t)
    _lines.append(t)


def setup(ZOSAPI, system):
    sd = system.SystemData
    sd.Aperture.ApertureType = ZOSAPI.SystemData.ZemaxApertureType.ObjectSpaceNA
    sd.Aperture.ApertureValue = NA_FIBER
    sd.Aperture.ApodizationType = ZOSAPI.SystemData.ZemaxApodizationType.Gaussian
    sd.Aperture.ApodizationFactor = 1.0          # 振幅 exp(−ρ²) → 縁で強度 1/e²
    wl = sd.Wavelengths
    wl.GetWavelength(1).Wavelength = WAVES_SPEC[0]
    for w in WAVES_SPEC[1:]:
        wl.AddWavelength(w, 1.0)
    wl.GetWavelength(PRIMARY_SPEC).MakePrimary()


def solve_collimation(lens, obj, last_surf, t0):
    """物体距離（ファイバー → コリメータ）を、主波長の輪帯光線（Py 0.7）が平行になるよう割線法で合わせる"""
    f = lambda t: (setattr(obj, "Thickness", t), lens.val("RANG", last_surf.SurfaceNumber, PRIMARY_SPEC,
                                                          0, 0, 0, 0.7))[1]
    a, b = t0, t0 * 1.01
    fa, fb = f(a), f(b)
    for _ in range(30):
        if abs(fb - fa) < 1e-15:
            break
        c = b - fb * (b - a) / (fb - fa)
        a, fa, b, fb = b, fb, c, f(c)
        if abs(fb) < 1e-9:
            break
    obj.Thickness = b
    return b


def fix_grating_direction(lens, g, cb2, pupil):
    """回折次数の符号と出射側の座標の傾きの符号を、840 nm の主光線が軸に沿い、端の波長が ±に分かれる組み合わせに決める"""
    best = None
    for order in (1, -1):
        for sign in (1, -1):
            lens.par(g, 2, order)
            lens.par(cb2, 3, sign * THETA_C_DEG)
            a0 = lens.val("RANG", pupil.SurfaceNumber, PRIMARY_SPEC, 0, 0, 0, 0)
            a_lo = lens.val("RANG", pupil.SurfaceNumber, 1, 0, 0, 0, 0)
            a_hi = lens.val("RANG", pupil.SurfaceNumber, len(WAVES_SPEC), 0, 0, 0, 0)
            ok = abs(a0) < math.radians(0.5) and abs(a_lo) > math.radians(1) and a_lo * a_hi < 0
            if ok and (best is None or abs(a0) < best[0]):
                best = (abs(a0), order, sign)
    if best is None:
        raise RuntimeError("回折の向きが決まりません（光線が通っていない可能性）。系を OpticStudio で確認してください")
    _, order, sign = best
    lens.par(g, 2, order)
    lens.par(cb2, 3, sign * THETA_C_DEG)
    return order, sign


def build(ZOSAPI, app, system, key):
    col = COLLIMATORS[key]
    system.New(False)
    setup(ZOSAPI, system)
    lens = Lens(ZOSAPI, system)
    obj = system.LDE.GetSurfaceAt(0)

    # コリメータ
    if col["kind"] == "paraxial":
        obj.Thickness = col["f"]
        c_last = lens.paraxial(f"コリメータ {col['label']}（軸上は無収差なので理想レンズ）", col["f"], D_COL_G)
    else:
        path = vendor_path(col["file"])
        if path is None:
            raise FileNotFoundError(f"vendor/{col['file']} がありません（vendor/README.md 参照）")
        src = open_vendor(ZOSAPI, app, path)
        block = lens_block(src)
        src.Close(False)
        obj.Thickness = block[-1][1]
        _, c_last = add_reversed(lens, block, col["label"], D_COL_G)
        solve_collimation(lens, obj, c_last, block[-1][1])
    obj.Comment = "ファイバー端（FC/APC, MFD 5.0 µm）"

    # グレーティング
    lens.coord_break("グレーティングの傾き θc", THETA_C_DEG)
    g = lens.grating("GP1012L（VPH 1200 本/mm）", G_LINES_PER_UM, 1, GRATING_GLASS, GRATING_T)
    lens.add("GP1012L 裏面", 0.0)
    cb2 = lens.coord_break("回折光の向き（840 nm を軸に）", THETA_C_DEG)

    # 集光レンズ LSM05-BB
    scan_file = vendor_path(SCAN_LENS_FILES["LSM05-BB"])
    if scan_file:
        src = open_vendor(ZOSAPI, app, scan_file)
        out(describe(src, "vendor/LSM05-BB.zmx（取り込み元）"))
        pupil, _ = copy_scan_lens(lens, src)
        src.Close(False)
        scan_note = "ベンダーファイル（絞り面から取り込み）"
    else:
        pupil = lens.add("LSM05-BB 入射瞳（理想レンズで代用）", F_LSM05)
        lens.paraxial("LSM05-BB（理想レンズ f 110 で代用）", F_LSM05, F_LSM05)
        scan_note = "vendor/LSM05-BB.zmx が無いので理想レンズ f 110 で代用（収差なし）"
    pupil.IsStop = True
    order, sign = fix_grating_direction(lens, g, cb2, pupil)
    system.LDE.GetSurfaceAt(system.LDE.NumberOfSurfaces - 1).Comment = "センサ受光面"
    return lens, g, scan_note, order, sign


def rsce_um(lens, w):
    return lens.val("RSCE", RSCE_SAMP, w, 0, 0) * 1000


def focus_scan(lens):
    """最終面の厚さ（→ センサ）を走査し、波長ごとのベストピントと全波長平均が最小の位置を求める"""
    nw = len(WAVES_SPEC)
    last = lens.lde.GetSurfaceAt(lens.lde.NumberOfSurfaces - 2)
    t0 = last.Thickness
    n = int(round(FOCUS_RANGE / FOCUS_STEP))
    zs = [k * FOCUS_STEP for k in range(-n, n + 1)]
    table = []
    for dz in zs:
        last.Thickness = t0 + dz
        table.append([rsce_um(lens, w) for w in range(1, nw + 1)])

    def vertex(col):
        k = min(range(len(zs)), key=lambda i: col[i])
        if 0 < k < len(zs) - 1:                        # 3 点の放物線で頂点を補間
            y0, y1, y2 = col[k - 1], col[k], col[k + 1]
            den = y0 - 2 * y1 + y2
            if den > 0:
                return zs[k] + 0.5 * FOCUS_STEP * (y0 - y2) / den
        return zs[k]

    per_wave = [vertex([row[w] for row in table]) for w in range(nw)]
    mean_best = vertex([sum(row) / nw for row in table])
    last.Thickness = t0 + mean_best
    return t0 + mean_best, [z - mean_best for z in per_wave]


def wavelength_map(lens, img):
    """主光線のセンサ上の位置 y(λ)。波長 1 を一時的に書き換えて調べる"""
    wl = lens.system.SystemData.Wavelengths.GetWavelength(1)
    orig = wl.Wavelength
    lams = [0.760 + 0.002 * k for k in range(81)]   # 760〜920 nm
    ys = []
    for lam in lams:
        wl.Wavelength = lam
        ys.append(lens.val("REAY", img, 1, 0, 0, 0, 0))
    wl.Wavelength = orig
    return lams, ys


def interp_lam(lams, ys, y_target):
    for i in range(len(ys) - 1):
        y0, y1 = ys[i], ys[i + 1]
        if (y0 - y_target) * (y1 - y_target) <= 0 and y1 != y0:
            return lams[i] + (lams[i + 1] - lams[i]) * (y_target - y0) / (y1 - y0)
    return float("nan")


def evaluate(lens, g, key, scan_note, order, sign):
    col = COLLIMATORS[key]
    lde = lens.lde
    img = lde.NumberOfSurfaces - 1
    out(f"=== 分光器: コリメータ {col['label']} ===")
    out(f"  集光レンズ: {scan_note}")
    out(f"  ファイバー → コリメータ: {lde.GetSurfaceAt(0).Thickness:.3f} mm、回折次数 {order:+d}、出射側の傾きの符号 {sign:+d}")

    t_best, dz_wave = focus_scan(lens)
    out(f"  センサの位置（最終面 → 受光面, 全波長の RMS 平均が最小）: {t_best:.3f} mm")

    w_beam = col["f"] * DIV_RAD                               # コリメート後の 1/e² 半径
    f_scan = F_LSM05
    out("")
    out("■ 各波長（センサ上の位置・スポット）")
    out(f"  {'波長 nm':>8} {'位置 y mm':>10} {'画素':>8} {'RMS 半径 µm':>12} {'回折 1/e² 半径 µm':>17} {'ベストピント差 mm':>17}")
    ys, worst_ratio = [], 0.0
    for w, lam in enumerate(WAVES_SPEC, start=1):
        y = lens.val("REAY", img, w, 0, 0, 0, 0)
        rms = rsce_um(lens, w)
        diff = lam * 1e-3 * f_scan / (math.pi * w_beam) * 1000     # ガウスビームの集光スポット 1/e² 半径 (µm)
        worst_ratio = max(worst_ratio, rms / diff)
        ys.append(y)
        out(f"  {lam * 1000:8.1f} {y:+10.3f} {y / PITCH_UM * 1000:+8.0f} {rms:12.2f} {diff:17.2f} {dz_wave[w - 1]:+17.3f}")
    out(f"  RMS 半径 / 回折スポット半径 の最大: {worst_ratio:.2f}（1 より十分小さければ回折で決まる）")

    # 像面の傾き・曲がり
    n = len(ys)
    my, mz = sum(ys) / n, sum(dz_wave) / n
    sxx = sum((y - my) ** 2 for y in ys)
    slope = sum((y - my) * (z - mz) for y, z in zip(ys, dz_wave)) / sxx if sxx else 0.0
    resid = [z - (mz + slope * (y - my)) for y, z in zip(ys, dz_wave)]
    tilt = math.degrees(math.atan(slope))
    out("")
    out("■ 像面（波長ごとのベストピントの並び）")
    out(f"  傾けない場合の最大ずれ: {max(abs(z) for z in dz_wave):.3f} mm（許容 z_R ±{ZR_SENSOR} mm）"
        f"  {'OK' if max(abs(z) for z in dz_wave) <= ZR_SENSOR else '要対策'}")
    out(f"  最適なセンサの傾き: {tilt:+.2f}°（加工で許せるあおり ±{TILT_TOL_DEG}°）、傾けた後の残り {max(abs(r) for r in resid):.3f} mm")

    # 波長と位置の対応
    lams, ymap = wavelength_map(lens, img)
    lam_m, lam_p = interp_lam(lams, ymap, -SENSOR_HALF), interp_lam(lams, ymap, SENSOR_HALF)
    lo, hi = sorted((lam_m, lam_p))
    out("")
    out("■ 波長と位置の対応")
    out(f"  センサ端（±{SENSOR_HALF} mm）の波長: {lo * 1000:.1f} 〜 {hi * 1000:.1f} nm"
        f"（Excel の理想計算 {LAM_LO_UM * 1000:.1f} 〜 {LAM_HI_UM * 1000:.1f} nm）")
    out(f"  受光幅 {(hi - lo) * 1000:.1f} nm、平均の 1 画素 {(hi - lo) * 1e6 / 2048:.1f} pm")

    sd = g.SemiDiameter
    out("")
    out("■ グレーティング")
    out(f"  入射面のビーム（1/e², 半径 = 半径方向の最大）: Ø{2 * sd:.2f} mm（KS1RS 有効径 Ø{KS1RS_CA}）")
    return dict(lo=lo, hi=hi, worst=worst_ratio, tilt=tilt, flat=max(abs(z) for z in dz_wave),
                resid=max(abs(r) for r in resid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", choices=["rc08", "ac254", "both"], default="both")
    args = ap.parse_args()
    keys = ["rc08", "ac254"] if args.col == "both" else [args.col]

    ZOSAPI, app, system = connect()
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    for key in keys:
        if COLLIMATORS[key]["kind"] == "vendor" and vendor_path(COLLIMATORS[key]["file"]) is None:
            out(f"--- {key}: vendor/{COLLIMATORS[key]['file']} が無いので省略（vendor/README.md 参照）---")
            continue
        lens, g, note, order, sign = build(ZOSAPI, app, system, key)
        results[key] = evaluate(lens, g, key, note, order, sign)
        path = os.path.join(OUT_DIR, f"spectrometer_{key}.zmx")
        system.SaveAs(path)
        out(describe(system, f"作成した系（{os.path.basename(path)}）"))
        out(f"保存しました: {path}")
        out("")

    if len(results) > 1:
        out("■ 比較")
        out(f"  {'':8} {'センサ端の波長 nm':>18} {'RMS/回折 最大':>14} {'平らなセンサの最大ずれ mm':>24} {'最適傾き °':>10}")
        for k, r in results.items():
            out(f"  {k:8} {r['lo'] * 1000:8.1f}〜{r['hi'] * 1000:6.1f} {r['worst']:14.2f} {r['flat']:24.3f} {r['tilt']:+10.2f}")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print(f"レポートを保存しました: {REPORT}")


if __name__ == "__main__":
    main()
