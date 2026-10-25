"""段階3: 8枚6群の初期系を作り、DLS → Hammer (ガラス置換) → DLS で最適化

構成 (#26-624 は 8 枚 6 群):
    前群 3枚2群 : G1 貼り合わせ (N-FK51A + N-SF11, f≈200) + G2 単レンズ (N-FK51A, f≈400)
                  → 合成 f1≈136 mm
    絞り        : 前群の後側焦点付近 (最適化で RANG により詰める)
    後群 5枚4群 : G3 単 (FK51A) / G4 貼り合わせ (FK51A + SF11) / G5 単 (FK51A) /
                  G6 弱い負メニスカス (SF11, 像面平坦化)
ガラスは SWIR での色消し配分 (V_SWIR: FK51A≈76, SF11≈40) から選んだ初期値。
初期の曲率は薄肉の屈折力配分からの概算で、最適化で詰める。

出力: design/step3_8elements.zmx
注意: 実行すると OpticStudio で開いている系は保存されずに新規系に置き換わる。
"""
import math
import os

from step1_build_initial import build_lens, setup_system_data
from step2_optimize import (add_spec_operands, build_default_merit, print_spec_rows,
                            run_local_optimization, set_variables)
from zos_connect import connect

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design", "step3_8elements.zmx")

HAMMER_SECONDS = 300          # Hammer 最適化の実行時間 [s] (0 で省略)
USE_GLASS_SUBSTITUTION = True  # Hammer でガラスを置き換えるか
GLASS_CATALOG = "SCHOTT"

# (コメント, 曲率半径, 厚み, 材料)  ※ 物体面と像面は除く。"STOP" 行が絞り面
SURFACES = [
    # --- 前群 ---
    ("G1 L1 front", 90.0, 12.0, "N-FK51A"),
    ("G1 L1/L2 cement", -93.8, 4.0, "N-SF11"),
    ("G1 L2 back", -290.7, 3.0, ""),
    ("G2 L3 front", 200.0, 7.0, "N-FK51A"),
    ("G2 L3 back", -4000.0, 124.0, ""),
    ("STOP", math.inf, 15.0, ""),
    # --- 後群 ---
    ("G3 L4 front", 47.8, 4.0, "N-FK51A"),
    ("G3 L4 back", -47.8, 2.0, ""),
    ("G4 L5 front", 23.0, 4.5, "N-FK51A"),
    ("G4 L5/L6 cement", -22.9, 2.0, "N-SF11"),
    ("G4 L6 back", -67.8, 2.0, ""),
    ("G5 L7 front", 57.3, 3.5, "N-FK51A"),
    ("G5 L7 back", -57.3, 4.0, ""),
    ("G6 L8 front", -50.0, 2.5, "N-SF11"),
    ("G6 L8 back", -90.2, 15.0, ""),  # 像距離は近軸マージナル光線高さ 0 のソルブで決定
]


def glass_surfaces(system):
    """材料が入っている面の番号リスト"""
    lde = system.LDE
    return [i for i in range(1, lde.NumberOfSurfaces - 1) if lde.GetSurfaceAt(i).Material]


def set_material_substitution(ZOSAPI, system, enable):
    """ガラス面の材料を Substitute ソルブ (enable=True) か固定 (False) にする"""
    lde = system.LDE
    for i in glass_surfaces(system):
        cell = lde.GetSurfaceAt(i).MaterialCell
        if enable:
            solve = cell.CreateSolveType(ZOSAPI.Editors.SolveType.MaterialSubstitute)
            solve._S_MaterialSubstitute.Catalog = GLASS_CATALOG
            cell.SetSolveData(solve)
        else:
            cell.MakeSolveFixed()


def run_hammer(ZOSAPI, system, seconds):
    hammer = system.Tools.OpenHammerOptimization()
    hammer.Algorithm = ZOSAPI.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares
    hammer.NumberOfCores = os.cpu_count() or 1
    hammer.RunAndWaitWithTimeout(seconds)
    hammer.Cancel()
    hammer.WaitForCompletion()
    hammer.Close()


def print_lens_data(system):
    lde = system.LDE
    print("--- レンズデータ ---")
    print(f"  {'面':>3} {'コメント':18s} {'曲率半径':>10} {'厚み':>9} {'材料':10s} {'有効半径':>8}")
    for i in range(lde.NumberOfSurfaces):
        s = lde.GetSurfaceAt(i)
        r = "inf" if s.Radius == 0 or abs(s.Radius) > 1e10 else f"{s.Radius:.3f}"
        t = "" if i == lde.NumberOfSurfaces - 1 else f"{s.Thickness:.3f}"
        print(f"  {i:>3} {s.Comment:18s} {r:>10} {t:>9} {s.Material:10s} {s.SemiDiameter:8.3f}")


def count_elements_groups(system):
    """(レンズ枚数, 群数) を数える。空気から入る面で群、材料のある面で1枚"""
    lde = system.LDE
    elements = groups = 0
    prev_glass = False
    for i in range(1, lde.NumberOfSurfaces - 1):
        glass = bool(lde.GetSurfaceAt(i).Material)
        if glass:
            elements += 1
            if not prev_glass:
                groups += 1
        prev_glass = glass
    return elements, groups


def main():
    ZOSAPI, app, system = connect()
    system.New(False)
    setup_system_data(ZOSAPI, system)
    build_lens(ZOSAPI, system, SURFACES)

    n_el, n_gr = count_elements_groups(system)
    print(f"構成: {n_el} 枚 {n_gr} 群 (目標 8 枚 6 群)")

    n_var = set_variables(system)
    build_default_merit(ZOSAPI, system)
    rows = add_spec_operands(ZOSAPI, system)
    print(f"変数の数: {n_var} / メリットファンクションの行数: {system.MFE.NumberOfOperands}")
    print_spec_rows(f"初期 (メリット関数 {system.MFE.CalculateMeritFunction():.6f})", rows)

    print("[1/3] 局所最適化 (DLS) ...")
    run_local_optimization(ZOSAPI, system)
    print_spec_rows(f"DLS 後 (メリット関数 {system.MFE.CalculateMeritFunction():.6f})", rows)

    if HAMMER_SECONDS > 0:
        if USE_GLASS_SUBSTITUTION:
            set_material_substitution(ZOSAPI, system, True)
        print(f"[2/3] Hammer 最適化 ({HAMMER_SECONDS} 秒, ガラス置換 "
              f"{'あり' if USE_GLASS_SUBSTITUTION else 'なし'}) ...")
        run_hammer(ZOSAPI, system, HAMMER_SECONDS)
        if USE_GLASS_SUBSTITUTION:
            set_material_substitution(ZOSAPI, system, False)
        print_spec_rows(f"Hammer 後 (メリット関数 {system.MFE.CalculateMeritFunction():.6f})", rows)

    print("[3/3] 仕上げの局所最適化 (DLS) ...")
    run_local_optimization(ZOSAPI, system)
    print_spec_rows(f"最終 (メリット関数 {system.MFE.CalculateMeritFunction():.6f})", rows)

    print_lens_data(system)
    print("※ Hammer で置き換わったガラスは、1.7 µm 付近の内部透過率をデータシートで確認すること")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    system.SaveAs(OUT_PATH)
    print(f"保存しました: {OUT_PATH}")


if __name__ == "__main__":
    main()
