"""段階3: 参照アームの Zemax モデルを作り、サンプルアームとの分散の収支と光路長の一致を調べる

参照アーム（片道。ミラーで同じ道を戻るので往復はこの 2 倍。像面 = ミラー面）:
    ファイバー端 → コリメータ（サンプル側と同じ: rc04 / ac080）→ LSM03DC（vendor/LSM03DC.zmx。無ければ N-SK4 15 mm で仮置き）
    → 追加の N-SK4 板（厚さ 0。分散をゼロにする厚さを求めるため）→ 可変 ND（N-BK7 3 mm, 10° 傾け）→ ミラー（Xr 120.5）
サンプルアーム: step2_sample_arm.py と同じ系（中心, ピント合わせ後）。容器底の下面までと、焦点面（組織 0.65 mm）まで。

計算（主光線, 波長を 760〜920 nm で振る）:
    片道の光路長 OPL(λ)（OPTH）→ 往復の位相 φ = 2π/λ · 2·OPL → 両アームの位相差 Δφ(ω) を 3 次式で近似
    → 群遅延分散 GDD・3 次分散 TOD（往復）、群光路長の差（ミラー位置の修正量）
    → 位相差を入れて FFT した深さ方向プロファイルの半値全幅（軸分解能の広がり）
    ND を外した場合・N-SK4 を 1 mm 足した場合・組織 1 mm あたりの GDD も出す。
ミラー傾きの許容（ファイバーへの結合 85 %）はガウスビームの式で計算する。

使い方（OpticStudio で Programming > Interactive Extension を押してから）:
    python step3_reference_arm.py              # rc04 と ac080 の両方（ac080 は vendor にファイルがあるとき）
    python step3_reference_arm.py --col rc04
出力: out/reference_arm_<col>.zmx, out/step3_reference_arm_report.txt
注意: OpticStudio で開いている系は保存されずに置き換わる。
"""
import argparse
import math
import os

import numpy as np

from oct_specs import (BW_UM, COLLIMATORS, CWL_UM, DC_GLASS, DZ_AIR_UM, FOCUS_DEPTH, KXG_HALF, L_DC, LAM_HI_UM,
                       LAM_LO_UM, MFD_UM, ND_GLASS, ND_TILT, OUT_DIR, T_ND, X_DC, X_MIRROR, X_ND)
from step1_spectrometer import setup, solve_collimation
from step2_sample_arm import build as build_sample
from step2_sample_arm import focus_air
from zos_connect import connect
from zos_util import Lens, add_reversed, copy_scan_lens, describe, lens_block, open_vendor, vendor_path

REPORT = os.path.join(OUT_DIR, "step3_reference_arm_report.txt")
LAMS = [0.760 + 0.004 * k for k in range(41)]           # µm
C_UM_FS = 0.299792458                                    # 光速 µm/fs
_lines = []


def out(t=""):
    print(t)
    _lines.append(t)


def opl_curve(lens, surf_no):
    """主光線の片道光路長 OPL(λ)（mm）。波長 1 を一時的に書き換えて調べる"""
    wl = lens.system.SystemData.Wavelengths.GetWavelength(1)
    orig = wl.Wavelength
    ys = []
    for lam in LAMS:
        wl.Wavelength = lam
        ys.append(lens.val("OPTH", surf_no, 1, 0, 0, 0, 0))
    wl.Wavelength = orig
    return np.array(ys)


def phase_fit(opl_s, opl_r):
    """往復の位相差 Δφ(ω) = 2π/λ · 2 (OPL_s − OPL_r) を ω − ω0 の 3 次式で近似 → (群遅延 fs, GDD fs², TOD fs³)"""
    lam = np.array(LAMS)
    w = 2 * math.pi * C_UM_FS / lam                      # rad/fs
    w0 = 2 * math.pi * C_UM_FS / CWL_UM
    dphi = 2 * math.pi / lam * 2 * (opl_s - opl_r) * 1000    # OPL mm → µm
    c = np.polyfit(w - w0, dphi, 3)                      # c[0] x³ + c[1] x² + c[2] x + c[3]
    return c[2], 2 * c[1], 6 * c[0]


def psf_fwhm(gdd, tod):
    """受光範囲のガウススペクトル（70 nm）に残留位相をのせて FFT した深さ方向プロファイルの半値全幅（µm, 空気中）"""
    n = 2048
    lam = np.linspace(LAM_LO_UM, LAM_HI_UM, n)
    k = np.linspace(2 * math.pi / lam[-1], 2 * math.pi / lam[0], n)       # k 等間隔（rad/µm）
    lk = 2 * math.pi / k
    s = np.exp(-4 * math.log(2) * ((lk - CWL_UM) / BW_UM) ** 2)
    x = (k - 2 * math.pi / CWL_UM) * C_UM_FS             # Δω（rad/fs）
    field = s * np.exp(1j * (gdd / 2 * x ** 2 + tod / 6 * x ** 3))
    m = 1 << 17
    a = np.abs(np.fft.fft(field, m))
    dk = k[1] - k[0]
    z = np.fft.fftfreq(m, d=dk) * math.pi                # 深さ（片道, µm）: 位相 2kz
    order = np.argsort(z)
    z, a = z[order], a[order] / a.max()
    above = z[a >= 0.5]
    return above.max() - above.min()


def group_path_mm(opl):
    """片道の群光路長（mm）= OPL − λ dOPL/dλ（λ0 で）"""
    lam = np.array(LAMS)
    i = int(np.argmin(abs(lam - CWL_UM)))
    d = (opl[i + 1] - opl[i - 1]) / (lam[i + 1] - lam[i - 1])
    return opl[i] - lam[i] * d


def build_reference(ZOSAPI, app, system, key):
    col = COLLIMATORS[key]
    system.New(False)
    setup(ZOSAPI, system)
    lens = Lens(ZOSAPI, system)
    lde = system.LDE
    obj = lde.GetSurfaceAt(0)
    obj.Comment = "ファイバー端（FC/APC, MFD 5.0 µm）"

    # コリメータ（Xr の原点 = ファイバー側の頂点）
    if col["kind"] == "paraxial":
        obj.Thickness = col["f"]
        lens.paraxial(f"コリメータ {col['label']}（理想レンズ）", col["f"], X_DC)
    else:
        src = open_vendor(ZOSAPI, app, vendor_path(col["file"]))
        block = lens_block(src)
        src.Close(False)
        t_lens = sum(b[1] for b in block[:-1])
        obj.Thickness = block[-1][1]
        _, c_last = add_reversed(lens, block, col["label"], X_DC - t_lens)
        solve_collimation(lens, obj, c_last, block[-1][1])

    # LSM03DC
    dc_file = vendor_path("LSM03DC.zmx")
    if dc_file:
        src = open_vendor(ZOSAPI, app, dc_file)
        out(describe(src, "vendor/LSM03DC.zmx（取り込み元）"))
        first_dc, n = copy_scan_lens(lens, src, first=1)
        src.Close(False)
        last_dc = lde.GetSurfaceAt(first_dc.SurfaceNumber + n - 1)
        l_dc = sum(lde.GetSurfaceAt(first_dc.SurfaceNumber + i).Thickness for i in range(n - 1))
        last_dc.Thickness = 0.0
        dc_note = f"ベンダーファイル（長さ {l_dc:.2f} mm）"
    else:
        lens.add(f"LSM03DC（仮: {DC_GLASS} {L_DC} mm）", L_DC, material=DC_GLASS)
        l_dc = L_DC
        lens.add("LSM03DC 出射面", 0.0)
        dc_note = f"vendor/LSM03DC.zmx が無いので {DC_GLASS} {L_DC} mm で仮置き（長さは要確認）"

    extra = lens.add(f"追加の {DC_GLASS} 板（調整用, 通常 0）", 0.0, material=DC_GLASS)
    gap = lens.add("（空気）", X_ND - X_DC - l_dc)
    lens.coord_break(f"可変 ND の傾き {ND_TILT}°", ND_TILT)
    nd = lens.add(f"可変 ND（{ND_GLASS} {T_ND} mm, VND-50）", T_ND, material=ND_GLASS)
    lens.add("ND 裏面", 0.0)
    lens.coord_break("傾きを戻す", -ND_TILT, X_MIRROR - X_ND - T_ND)
    lde.GetSurfaceAt(lde.NumberOfSurfaces - 1).Comment = f"ミラー面（Xr {X_MIRROR}）"
    lde.GetSurfaceAt(1).IsStop = True
    return lens, dict(extra=extra, nd=nd, gap=gap, dc_note=dc_note)


_FW0 = []


def report_case(label, opl_s, opl_r):
    if not _FW0:
        _FW0.append(psf_fwhm(0.0, 0.0))      # 分散なし（受光範囲で切ったスペクトル, 約 4.65 µm）
    gd, gdd, tod = phase_fit(opl_s, opl_r)
    fw = psf_fwhm(gdd, tod)
    out(f"  {label:<34} GDD {gdd:+8.0f} fs²  TOD {tod:+9.0f} fs³  軸分解能 {fw:5.2f} µm"
        f"（分散なし {_FW0[0]:.2f} µm の {fw / _FW0[0]:.2f} 倍, 空気中）")
    return gdd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", choices=["rc04", "ac080", "both"], default="both")
    args = ap.parse_args()
    keys = ["rc04", "ac080"] if args.col == "both" else [args.col]
    ZOSAPI, app, system = connect()
    os.makedirs(OUT_DIR, exist_ok=True)

    for key in keys:
        col = COLLIMATORS[key]
        if col["kind"] == "vendor" and vendor_path(col["file"]) is None:
            out(f"--- {key}: vendor/{col['file']} が無いので省略（vendor/README.md 参照）---")
            continue

        # サンプルアーム（中心, ピント合わせ後）
        lens_s, p = build_sample(ZOSAPI, app, system, key, clip=False)
        focus_air(lens_s, p)
        img_s = lens_s.lde.NumberOfSurfaces - 1
        opl_bot = opl_curve(lens_s, p["bot"].SurfaceNumber)          # 容器底の下面まで
        opl_foc = opl_curve(lens_s, img_s)                            # 焦点面（組織 0.65 mm）まで
        p["tis"].Thickness = FOCUS_DEPTH + 1.0
        opl_foc1 = opl_curve(lens_s, img_s)                           # 組織を 1 mm 増やしたとき
        p["tis"].Thickness = FOCUS_DEPTH

        # 参照アーム
        lens_r, q = build_reference(ZOSAPI, app, system, key)
        img_r = lens_r.lde.NumberOfSurfaces - 1
        opl_ref = opl_curve(lens_r, img_r)
        q["nd"].Thickness = 0.0
        opl_ref_nond = opl_curve(lens_r, img_r)
        q["nd"].Thickness = T_ND
        q["extra"].Thickness = 1.0
        opl_ref_sk4 = opl_curve(lens_r, img_r)
        q["extra"].Thickness = 0.0

        out(f"=== 参照アーム: コリメータ {col['label']} ===")
        out(f"  LSM03DC: {q['dc_note']}")
        out("")
        out("■ 分散の収支（往復, サンプル − 参照。数値は主光線, 760〜920 nm の 3 次近似）")
        g_bot = report_case("容器底の下面まで（ND あり）", opl_bot, opl_ref)
        report_case("焦点面 組織 0.65 mm まで（ND あり）", opl_foc, opl_ref)
        report_case("容器底の下面まで（ND なし）", opl_bot, opl_ref_nond)
        _, g_sk4, _ = phase_fit(opl_ref_sk4, opl_ref)                  # N-SK4 1 mm 足したときの参照側の増分
        _, g_tis, _ = phase_fit(opl_foc1, opl_foc)                     # 組織 1 mm の増分
        g_sk4 = -g_sk4                                                 # サンプル − 参照 の向きでは減る
        out(f"  N-SK4 1 mm を参照側に足すと GDD {g_sk4:+.0f} fs²、組織 1 mm（往復）で {g_tis:+.0f} fs²")
        if abs(g_sk4) > 1e-6:
            out(f"  → 容器底までの GDD をゼロにする N-SK4 の追加厚さ: {-g_bot / g_sk4:+.2f} mm"
                f"（負なら参照側のガラスが多すぎる）")

        # 群光路長
        lg_bot, lg_foc, lg_ref = group_path_mm(opl_bot), group_path_mm(opl_foc), group_path_mm(opl_ref)
        out("")
        out("■ 光路長の一致（片道の群光路長, 840 nm）")
        out(f"  サンプル: 容器底の下面まで {lg_bot:.2f} mm、焦点面まで {lg_foc:.2f} mm ／ 参照: ミラーまで {lg_ref:.2f} mm")
        for name, lg in (("容器底の下面", lg_bot), ("焦点面", lg_foc)):
            d = lg - lg_ref
            out(f"  光路差ゼロを{name}に合わせるには ミラーを {d:+.2f} mm 動かす"
                f"（KXG ±{KXG_HALF} mm の範囲 {'内' if abs(d) <= KXG_HALF else '外 → 配置の見直し'}）")

        # ミラー傾きの許容
        w0f = MFD_UM / 2 * 1e-3
        a85 = w0f * math.sqrt(-math.log(0.85)) / (2 * col["f"]) * 1e6
        out("")
        out("■ ミラー傾きの許容（ファイバーへの結合 85 %, ガウスビームの式）")
        out(f"  ±{a85:.1f} µrad（f {col['f']} mm）。POLARIS-K1E 1 回転 ≈ 7.7 mrad → ねじ {a85 / 7700 * 360:.2f}° 分")

        path = os.path.join(OUT_DIR, f"reference_arm_{key}.zmx")
        system.SaveAs(path)
        out(describe(system, f"作成した系（{os.path.basename(path)}）"))
        out(f"保存しました: {path}")
        out("")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print(f"レポートを保存しました: {REPORT}")


if __name__ == "__main__":
    main()
