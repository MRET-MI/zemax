"""SD-OCT の Zemax モデルで使う設計値（sdoct_design_calc.xlsx と同じ値。2026-11-24 時点）

単位: 長さ mm、波長 µm、角度 °
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(HERE, "vendor")       # Thorlabs からダウンロードした .zmx を置く
OUT_DIR = os.path.join(HERE, "out")             # 作成した .zmx とレポート

# ---- 光源・ファイバー ----
CWL_UM = 0.840
MFD_UM = 5.0                                    # 780HP @850 nm（Excel p_mfd）
DIV_RAD = CWL_UM / (math.pi * MFD_UM / 2)       # 1/e² 半角（tan）= 0.107
NA_FIBER = math.sin(math.atan(DIV_RAD))         # 物体側 NA（1/e²）。ガウスのアポダイゼーション係数 1 と組み合わせる

# ---- 分光器（Excel「計算」「分光器構造」） ----
G_LINES_PER_UM = 1.2                            # GP1012L 1200 本/mm
THETA_C_DEG = math.degrees(math.asin(CWL_UM * G_LINES_PER_UM / 2))   # 30.265°（対称配置）
GRATING_T = 3.0                                 # GP1012L 厚さ（販売店値, 要確認）
GRATING_GLASS = "N-BK7"
D_COL_G = 40.0                                  # コリメータ → グレーティング（Excel p_d_col_g）
F_LSM05 = 110.0                                 # LSM05-BB の EFL（ベンダーファイルが無いときの理想レンズ）
LAM_LO_UM, LAM_HI_UM = 0.7715, 0.9049           # センサ端の波長（Excel c_lam_lo / c_lam_hi）
WAVES_SPEC = [LAM_LO_UM, 0.805, CWL_UM, 0.8725, LAM_HI_UM]
PRIMARY_SPEC = 3                                # 840 nm
SENSOR_HALF = 10.24                             # 受光長の半分（2048 × 10 µm / 2）
PITCH_UM = 10.0
ZR_SENSOR = 0.229                               # センサ側の焦点深度（片側, Excel c_zr_s）
TILT_TOL_DEG = 1.28                             # センサのあおり許容（Excel c_tol_tilt）
KS1RS_CA = 22.9                                 # KS1RS 有効径

# ---- コリメータの候補 ----
#   rc08 / rc04: Thorlabs 反射型（軸外し放物面鏡）。軸上の点光源には無収差・色収差なし → 理想レンズで表す
#   ac254 / ac080: Thorlabs 色消しダブレット。vendor/ の .zmx を前後反転して入れる
COLLIMATORS = {
    "rc08": dict(kind="paraxial", f=33.0, label="RC08APC-P01（反射型, f 33）"),
    "ac254": dict(kind="vendor", file="AC254-035-B.zmx", f=35.0, label="AC254-035-B（色消し, f 35）"),
    "rc04": dict(kind="paraxial", f=15.0, label="RC04APC-P01（反射型, f 15）"),
    "ac080": dict(kind="vendor", file="AC080-020-B.zmx", f=20.0, label="AC080-020-B（色消し, f 20）"),
}
SCAN_LENS_FILES = {"LSM05-BB": "LSM05-BB.zmx", "LSM03-BB": "LSM03-BB.zmx"}
