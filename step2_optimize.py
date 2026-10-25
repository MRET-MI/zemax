"""段階2: step1 の初期系にメリットファンクションを設定して局所最適化

入力: design/step1_initial.zmx (step1_build_initial.py で作成)
出力: design/step2_optimized.zmx

メリットファンクション:
    先頭に仕様の行 (倍率・像高・テレセントリック性・全長・C マウント余裕・外径) を置き、
    その後ろにデフォルトの RMS スポット (重心基準) と厚みの境界条件を置く。
    歪曲は理想像高 (MAG_TARGET × 物体高) を REAY で直接狙うことで抑える。

注意: 実行すると OpticStudio で開いている系は保存されずに step1 の系に置き換わる。
"""
import math
import os

from specs import (FIELDS_Y, MAG_TARGET, MAX_SEMI_DIAMETER, MIN_BACK_DISTANCE,
                   PRIMARY_WAVE, TOTAL_TRACK_TARGET)
from zos_connect import connect

DESIGN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design")
IN_PATH = os.path.join(DESIGN_DIR, "step1_initial.zmx")
OUT_PATH = os.path.join(DESIGN_DIR, "step2_optimized.zmx")

# 仕様の行の重み (結果を見て調整する)
W_PMAG = 1.0
W_IMAGE_HEIGHT = 10.0
W_TELECENTRIC = 100.0   # RANG はラジアンで値が小さいので重めにする
W_TRACK = 0.1
W_BACK = 1.0
W_SEMI_DIA = 1.0

# デフォルトメリットファンクションの厚み境界 [mm]
GLASS_MIN, GLASS_MAX, GLASS_EDGE = 2.0, 15.0, 1.5
AIR_MIN, AIR_MAX, AIR_EDGE = 0.5, 1000.0, 0.5


def set_variables(system):
    """レンズ面の曲率半径と、物体面・最終面以外の厚みを変数にする"""
    lde = system.LDE
    last = lde.NumberOfSurfaces - 2  # 像面の1つ手前 (厚みは像距離ソルブなので触らない)
    count = 0
    for i in range(1, last + 1):
        surf = lde.GetSurfaceAt(i)
        if not surf.IsStop:
            surf.RadiusCell.MakeSolveVariable()
            count += 1
        if i < last:
            surf.ThicknessCell.MakeSolveVariable()
            count += 1
    return count


def build_default_merit(ZOSAPI, system):
    wizard = system.MFE.SEQOptimizationWizard
    wizard.Type = 0          # RMS
    wizard.Data = 1          # Spot Radius
    wizard.Reference = 0     # Centroid
    wizard.Ring = 2          # 3 Rings
    wizard.Arm = 0           # 6 Arms
    wizard.IsGlassUsed = True
    wizard.GlassMin, wizard.GlassMax, wizard.GlassEdgeThickness = GLASS_MIN, GLASS_MAX, GLASS_EDGE
    wizard.IsAirUsed = True
    wizard.AirMin, wizard.AirMax, wizard.AirEdgeThickness = AIR_MIN, AIR_MAX, AIR_EDGE
    # バージョンにより OK() の場所が異なる
    if hasattr(wizard, "CommonSettings"):
        wizard.CommonSettings.OK()
    else:
        wizard.OK()


def add_spec_operands(ZOSAPI, system):
    """仕様の行を MFE の先頭に挿入し、(説明, 行オブジェクト, 表示用の変換関数) のリストを返す"""
    mfe = system.MFE
    op = ZOSAPI.Editors.MFE.MeritOperandType
    col = ZOSAPI.Editors.MFE.MeritColumn
    params = [col.Param1, col.Param2, col.Param3, col.Param4,
              col.Param5, col.Param6, col.Param7, col.Param8]
    image_surf = system.LDE.NumberOfSurfaces - 1
    last_lens = image_surf - 1
    h_max = FIELDS_Y[-1]
    rows = []

    def add(label, op_type, target, weight, ints=(), floats=(), fmt=lambda v: f"{v:+.5f}"):
        row = mfe.InsertNewOperandAt(len(rows) + 1)
        row.ChangeType(op_type)
        for c, v in zip(params, ints):
            row.GetOperandCell(c).IntegerValue = v
        for c, v in zip(params[len(ints):], floats):
            row.GetOperandCell(c).DoubleValue = v
        row.Target = target
        row.Weight = weight
        rows.append((label, row, fmt))

    add("近軸倍率 PMAG", op.PMAG, MAG_TARGET, W_PMAG, ints=(0, PRIMARY_WAVE))

    for y in FIELDS_Y[1:]:
        hy = y / h_max
        add(f"像高 REAY (物体高 {y:g} mm)", op.REAY, MAG_TARGET * y, W_IMAGE_HEIGHT,
            ints=(image_surf, PRIMARY_WAVE), floats=(0.0, hy, 0.0, 0.0),
            fmt=lambda v: f"{v:+.4f} mm")

    for y in FIELDS_Y[1:]:
        hy = y / h_max
        add(f"物体側主光線角 RANG (物体高 {y:g} mm)", op.RANG, 0.0, W_TELECENTRIC,
            ints=(0, PRIMARY_WAVE), floats=(0.0, hy, 0.0, 0.0),
            fmt=lambda v: f"{math.degrees(v):+.4f} deg")

    add("第1面〜像面 TTHI", op.TTHI, TOTAL_TRACK_TARGET, W_TRACK,
        ints=(1, image_surf - 1), fmt=lambda v: f"{v:.2f} mm")
    add("最終面〜像面 CTGT (下限)", op.CTGT, MIN_BACK_DISTANCE, W_BACK,
        ints=(last_lens,), fmt=lambda v: f"{v:.3f}")
    add("最大有効半径 MXSD (上限)", op.MXSD, MAX_SEMI_DIAMETER, W_SEMI_DIA,
        ints=(1, last_lens), fmt=lambda v: f"{v:.3f}")
    return rows


def print_spec_rows(title, rows):
    print(f"--- {title} ---")
    for label, row, fmt in rows:
        print(f"  {label:36s}: {fmt(row.Value)}")


def run_local_optimization(ZOSAPI, system):
    opt = system.Tools.OpenLocalOptimization()
    opt.Algorithm = ZOSAPI.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares
    opt.Cycles = ZOSAPI.Tools.Optimization.OptimizationCycles.Automatic
    opt.NumberOfCores = os.cpu_count() or 1
    opt.RunAndWaitForCompletion()
    opt.Close()


def main():
    if not os.path.exists(IN_PATH):
        raise FileNotFoundError(f"{IN_PATH} がありません。先に step1_build_initial.py を実行してください")

    ZOSAPI, app, system = connect()
    if not system.LoadFile(IN_PATH, False):
        raise RuntimeError(f"読み込みに失敗しました: {IN_PATH}")

    n_var = set_variables(system)
    build_default_merit(ZOSAPI, system)
    rows = add_spec_operands(ZOSAPI, system)
    print(f"変数の数: {n_var} / メリットファンクションの行数: {system.MFE.NumberOfOperands}")

    mf_before = system.MFE.CalculateMeritFunction()
    print_spec_rows(f"最適化前 (メリット関数 {mf_before:.6f})", rows)

    print("局所最適化 (DLS) を実行中...")
    run_local_optimization(ZOSAPI, system)

    mf_after = system.MFE.CalculateMeritFunction()
    print_spec_rows(f"最適化後 (メリット関数 {mf_after:.6f})", rows)

    system.SaveAs(OUT_PATH)
    print(f"保存しました: {OUT_PATH}")


if __name__ == "__main__":
    main()
