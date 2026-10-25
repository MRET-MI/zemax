"""段階1: 0.16X 物体側テレセントリックレンズ (Edmund #26-624 模擬) の初期系を作成

目標仕様 (#26-624 公開値):
    倍率 0.16X / WD 177 mm / 物体側 NA 0.008 (f/10 相当) / 900-1700 nm
    物体高 25 mm (1/2" センサー対角 8 mm 相当) / 全長 191 mm / C マウント

構成 (初期・近軸目安):
    物体 → 前群 (正, f1≈133) → 絞り (前群の後側焦点) → 後群 (正, f2≈21) → 像
    倍率 ≈ -f2/f1 ≈ -0.16
最適化と 8 枚 6 群化は次の段階で行う。

注意: 実行すると OpticStudio で開いている系は保存されずに新規系に置き換わる。
"""
import math
import os

from specs import FIELDS_Y, OBJ_NA, PRIMARY_WAVE, WAVES, WD
from zos_connect import connect

# (コメント, 曲率半径, 厚み, 材料)  ※ 物体面と像面は除く。"STOP" 行が絞り面
SURFACES = [
    ("L1 front", 262.0, 8.0, "N-BK7"),
    ("L1 back", -262.0, 5.0, ""),
    ("L2 front", 262.0, 8.0, "N-BK7"),
    ("L2 back", -262.0, 127.3, ""),
    ("STOP", math.inf, 18.0, ""),
    ("L3 front", 42.7, 4.0, "N-BK7"),
    ("L3 back", -42.7, 2.0, ""),
    ("L4 front", 42.7, 4.0, "N-BK7"),
    ("L4 back", -42.7, 18.0, ""),  # 像距離は近軸マージナル光線高さ 0 のソルブで決定
]

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design", "step1_initial.zmx")


def setup_system_data(ZOSAPI, system):
    sd = system.SystemData
    sd.Aperture.ApertureType = ZOSAPI.SystemData.ZemaxApertureType.ObjectSpaceNA
    sd.Aperture.ApertureValue = OBJ_NA
    # 入射瞳がほぼ無限遠になるので実光線エイミングを使う
    sd.RayAiming.RayAiming = ZOSAPI.SystemData.RayAimingMethod.Real

    sd.Wavelengths.GetWavelength(1).Wavelength = WAVES[0]
    for w in WAVES[1:]:
        sd.Wavelengths.AddWavelength(w, 1.0)
    sd.Wavelengths.GetWavelength(PRIMARY_WAVE).MakePrimary()

    sd.Fields.SetFieldType(ZOSAPI.SystemData.FieldType.ObjectHeight)
    sd.Fields.GetField(1).Y = FIELDS_Y[0]
    for y in FIELDS_Y[1:]:
        sd.Fields.AddField(0.0, y, 1.0)


def build_lens(ZOSAPI, system, surfaces=SURFACES):
    lde = system.LDE
    lde.GetSurfaceAt(0).Thickness = WD

    # 新規系は OBJ(0) / STO(1) / IMA(2)。絞りの前後に面を挿入する
    stop_idx = 1 + [s[0] for s in surfaces].index("STOP")
    for _ in range(stop_idx - 1):
        lde.InsertNewSurfaceAt(1)
    for _ in range(len(surfaces) - stop_idx):
        lde.InsertNewSurfaceAt(stop_idx + 1)

    for i, (comment, radius, thickness, material) in enumerate(surfaces, start=1):
        surf = lde.GetSurfaceAt(i)
        surf.Comment = comment
        if not math.isinf(radius):
            surf.Radius = radius
        surf.Thickness = thickness
        surf.Material = material

    last = lde.GetSurfaceAt(len(surfaces))
    solve = last.ThicknessCell.CreateSolveType(ZOSAPI.Editors.SolveType.MarginalRayHeight)
    solve._S_MarginalRayHeight.Height = 0.0
    last.ThicknessCell.SetSolveData(solve)


def report(ZOSAPI, system):
    mfe = system.MFE
    op = ZOSAPI.Editors.MFE.MeritOperandType

    def val(op_type, *params):
        p = list(params) + [0] * (8 - len(params))
        return mfe.GetOperandValue(op_type, *p)

    pmag = val(op.PMAG, 0, PRIMARY_WAVE)
    effl = val(op.EFFL, 0, PRIMARY_WAVE)
    totr = val(op.TOTR)
    # 最大物体高の主光線の物体空間での角度 (0 に近いほどテレセントリック)
    chief_deg = math.degrees(val(op.RANG, 0, PRIMARY_WAVE, 0.0, 1.0, 0.0, 0.0))
    image_dist = system.LDE.GetSurfaceAt(len(SURFACES)).Thickness

    print("=== 段階1 初期系 (近軸チェック) ===")
    print(f"近軸倍率 PMAG        : {pmag:+.4f}   (目標 -0.16)")
    print(f"合成焦点距離 EFFL    : {effl:.2f} mm")
    print(f"レンズ全長 TOTR      : {totr:.2f} mm (第1面〜像面)")
    print(f"像距離 (最終面→像)   : {image_dist:.2f} mm")
    print(f"物体側主光線角 (最大視野): {chief_deg:+.3f} deg   (目標 <0.03)")


def main():
    ZOSAPI, app, system = connect()
    system.New(False)
    setup_system_data(ZOSAPI, system)
    build_lens(ZOSAPI, system)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    system.SaveAs(OUT_PATH)
    report(ZOSAPI, system)
    print(f"保存しました: {OUT_PATH}")


if __name__ == "__main__":
    main()
