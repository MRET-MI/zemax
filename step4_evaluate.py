"""段階4: 設計を #26-624 の仕様と比べて評価する

入力: design/step3_8elements.zmx (引数で別の .zmx を指定可)
出力: design/step4_report.txt (画面表示と同じ内容)

評価項目:
    倍率 / 歪曲 / テレセントリック性 (全視野×全波長) / 倍率色収差 /
    MTF (20・40 lp/mm、回折限界との比較) / 被写界深度 / F値範囲 (f/6・f/10・f/22)

使い方:
    python step4_evaluate.py
    python step4_evaluate.py design/step2_optimized.zmx

注意: 評価のために作動距離や NA を一時的に変えるが、.zmx は上書きしない。
      OpticStudio で開いている系は指定ファイルに置き換わる。
"""
import math
import os
import sys

from specs import (DISTORTION_MAX_PERCENT, DOF_FREQ, DOF_HALF_RANGE, DOF_MTF_MIN, F_NUMBER_RANGE,
                   FIELDS_Y, MAG_TARGET, MTF_FREQ, MTF_MIN, OBJ_NA, PRIMARY_WAVE,
                   TELECENTRICITY_MAX_DEG, WAVES, WD)
from zos_connect import connect

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN = os.path.join(HERE, "design", "step3_8elements.zmx")
REPORT_PATH = os.path.join(HERE, "design", "step4_report.txt")

MTF_SAMPLING = 2   # 1: 32x32, 2: 64x64, 3: 128x128 ...
DOF_SHIFTS = [-DOF_HALF_RANGE, -10.0, 0.0, 10.0, DOF_HALF_RANGE]
F_NUMBERS = [F_NUMBER_RANGE[0], 10.0, F_NUMBER_RANGE[1]]

_lines = []


def out(text=""):
    print(text)
    _lines.append(text)


def ok(flag):
    return "OK" if flag else "NG"


def diffraction_mtf(nu, wl_um, fno):
    """無収差円形開口の MTF (nu: lp/mm, wl: µm)"""
    x = nu * wl_um * 1e-3 * fno
    if x >= 1:
        return 0.0
    return 2 / math.pi * (math.acos(x) - x * math.sqrt(1 - x * x))


class Evaluator:
    def __init__(self, ZOSAPI, system):
        self.ZOSAPI = ZOSAPI
        self.system = system
        self.op = ZOSAPI.Editors.MFE.MeritOperandType
        self.image_surf = system.LDE.NumberOfSurfaces - 1
        self.h_max = FIELDS_Y[-1]

    def val(self, op_type, *params):
        p = list(params) + [0] * (8 - len(params))
        return self.system.MFE.GetOperandValue(op_type, *p)

    # --- 個別の量 ---
    def pmag(self):
        return self.val(self.op.PMAG, 0, PRIMARY_WAVE)

    def wfno(self):
        return self.val(self.op.WFNO)

    def image_height(self, y, wave=PRIMARY_WAVE):
        return self.val(self.op.REAY, self.image_surf, wave, 0.0, y / self.h_max, 0.0, 0.0)

    def chief_angle_deg(self, y, wave):
        return math.degrees(self.val(self.op.RANG, 0, wave, 0.0, y / self.h_max, 0.0, 0.0))

    def mtf(self, field_no, freq):
        """多色 FFT MTF の (タンジェンシャル, サジタル)"""
        t = self.val(self.op.MTFT, MTF_SAMPLING, 0, float(field_no), freq)
        s = self.val(self.op.MTFS, MTF_SAMPLING, 0, float(field_no), freq)
        return t, s

    def mtf_min(self, freq):
        return min(min(self.mtf(i, freq)) for i in range(1, len(FIELDS_Y) + 1))

    def diffraction_poly(self, freq, fno):
        wl = self.system.SystemData.Wavelengths
        num = den = 0.0
        for i in range(1, wl.NumberOfWavelengths + 1):
            w = wl.GetWavelength(i)
            num += w.Weight * diffraction_mtf(freq, w.Wavelength, fno)
            den += w.Weight
        return num / den

    # --- 評価セクション ---
    def magnification_and_distortion(self):
        out("■ 倍率・歪曲")
        pmag = self.pmag()
        real_mag = self.image_height(self.h_max) / self.h_max
        out(f"  近軸倍率 PMAG       : {pmag:+.5f}  (目標 {MAG_TARGET}, 差 {pmag - MAG_TARGET:+.5f})")
        out(f"  実倍率 (最大視野)   : {real_mag:+.5f}")
        worst = 0.0
        for y in FIELDS_Y[1:]:
            y_real = self.image_height(y)
            y_par = pmag * y
            dist = (y_real - y_par) / y_par * 100
            worst = max(worst, abs(dist))
            out(f"  物体高 {y:5.1f} mm : 像高 {y_real:+.5f} mm  歪曲 {dist:+.4f} %")
        out(f"  最大歪曲 {worst:.4f} %  (仕様 <{DISTORTION_MAX_PERCENT})  {ok(worst < DISTORTION_MAX_PERCENT)}")
        out()

    def telecentricity(self):
        out("■ テレセントリック性 (物体空間の主光線角)")
        header = "  物体高 [mm] " + "".join(f"{w:>10.1f}µm" for w in WAVES)
        out(header)
        worst = 0.0
        for y in FIELDS_Y[1:]:
            angles = [self.chief_angle_deg(y, i) for i in range(1, len(WAVES) + 1)]
            worst = max(worst, max(abs(a) for a in angles))
            out(f"  {y:10.1f}  " + "".join(f"{a:+12.4f}" for a in angles))
        out(f"  最大 {worst:.4f} deg  (仕様 <{TELECENTRICITY_MAX_DEG})  {ok(worst < TELECENTRICITY_MAX_DEG)}")
        out()

    def lateral_color(self):
        out("■ 倍率色収差 (最大視野の像高差)")
        heights = [self.image_height(self.h_max, i) for i in range(1, len(WAVES) + 1)]
        for w, h in zip(WAVES, heights):
            out(f"  {w:.1f} µm : 像高 {h:+.5f} mm")
        out(f"  {WAVES[0]}–{WAVES[-1]} µm の差: {(heights[0] - heights[-1]) * 1000:+.2f} µm")
        out()

    def mtf_table(self):
        fno = self.wfno()
        out(f"■ MTF (多色 FFT, 実効 F値 {fno:.2f})")
        out(f"  {'物体高':>8} {'T@20':>7} {'S@20':>7} {'T@40':>7} {'S@40':>7}")
        worst40 = 1.0
        for i, y in enumerate(FIELDS_Y, start=1):
            t20, s20 = self.mtf(i, DOF_FREQ)
            t40, s40 = self.mtf(i, MTF_FREQ)
            worst40 = min(worst40, t40, s40)
            out(f"  {y:6.1f}mm {t20:7.3f} {s20:7.3f} {t40:7.3f} {s40:7.3f}")
        dl = self.diffraction_poly(MTF_FREQ, fno)
        out(f"  回折限界 (多色, {MTF_FREQ:g} lp/mm): {dl:.3f}  → 最小値は回折限界の {worst40 / dl * 100:.0f} %")
        out(f"  40 lp/mm 最小 {worst40:.3f}  (仕様 >{MTF_MIN})  {ok(worst40 > MTF_MIN)}")
        out()

    def depth_of_field(self):
        out(f"■ 被写界深度 (像面固定, {DOF_FREQ:g} lp/mm の全視野最小 MTF)")
        lde = self.system.LDE
        last = lde.GetSurfaceAt(self.image_surf - 1)
        last.ThicknessCell.MakeSolveFixed()  # 像面を固定 (ピントを追従させない)
        obj = lde.GetSurfaceAt(0)
        y0 = self.image_height(self.h_max)
        passed = True
        for dz in DOF_SHIFTS:
            obj.Thickness = WD + dz
            m = self.mtf_min(DOF_FREQ)
            dmag = (self.image_height(self.h_max) - y0) / y0 * 100
            edge = abs(abs(dz) - DOF_HALF_RANGE) < 1e-9
            if edge:
                passed &= m >= DOF_MTF_MIN
            mark = f"  {ok(m >= DOF_MTF_MIN)}" if edge else ""
            out(f"  WD {WD + dz:7.2f} mm (Δ {dz:+6.2f}) : MTF {m:.3f}  倍率変化 {dmag:+.4f} %{mark}")
        obj.Thickness = WD
        out(f"  ±{DOF_HALF_RANGE} mm で {DOF_MTF_MIN} 以上  {ok(passed)}")
        out()

    def f_number_range(self):
        out(f"■ F値範囲 (物体側 NA を切り替え, {MTF_FREQ:g} lp/mm)")
        ap = self.system.SystemData.Aperture
        out(f"  {'F値':>5} {'物体側NA':>9} {'実効F値':>8} {'MTF最小':>8} {'回折限界':>8}")
        for fno in F_NUMBERS:
            ap.ApertureValue = abs(MAG_TARGET) / (2 * fno)
            w = self.wfno()
            out(f"  f/{fno:<4g} {ap.ApertureValue:9.5f} {w:8.2f} {self.mtf_min(MTF_FREQ):8.3f} "
                f"{self.diffraction_poly(MTF_FREQ, w):8.3f}")
        ap.ApertureValue = OBJ_NA
        out()


def main():
    in_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    if not os.path.exists(in_path):
        raise FileNotFoundError(f"{in_path} がありません。先に step3_expand_8elements.py を実行してください")

    ZOSAPI, app, system = connect()
    if not system.LoadFile(in_path, False):
        raise RuntimeError(f"読み込みに失敗しました: {in_path}")

    out(f"=== 評価: {os.path.basename(in_path)} ===")
    out()
    ev = Evaluator(ZOSAPI, system)
    ev.magnification_and_distortion()
    ev.telecentricity()
    ev.lateral_color()
    ev.mtf_table()
    ev.depth_of_field()
    ev.f_number_range()

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    print(f"レポートを保存しました: {REPORT_PATH}")


if __name__ == "__main__":
    main()
