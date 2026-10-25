"""Edmund #26-624 (0.16X SilverTL SWIR) 模擬の設計条件と目標値 (全段階で共通)"""

# --- 系の条件 ---
WD = 177.0                          # 作動距離 [mm]
OBJ_NA = 0.008                      # 物体側 NA (像側 f/10 相当)
WAVES = [0.9, 1.3, 1.7]             # [µm]
PRIMARY_WAVE = 2                    # WAVES の番号 (1 始まり) → 1.3 µm
FIELDS_Y = [0.0, 12.5, 17.5, 25.0]  # 物体高 [mm] (1/2" 対角 8 mm / 0.16)

# --- 目標値 ---
MAG_TARGET = -0.16
TOTAL_TRACK_TARGET = 205.0          # 第1面〜像面 [mm] (全長 191 + C マウント 17.526 から概算)
MIN_BACK_DISTANCE = 12.0            # 最終面〜像面の下限 [mm] (C マウントのセンサー側の余裕)
MAX_SEMI_DIAMETER = 31.0            # レンズ有効半径の上限 [mm] (M62 フィルタ・外径 65 mm)
TELECENTRICITY_MAX_DEG = 0.03       # 物体側主光線角の上限 [deg]
DISTORTION_MAX_PERCENT = 0.015
MTF_FREQ = 40.0                     # [lp/mm]
MTF_MIN = 0.40                      # f/10 で
DOF_HALF_RANGE = 19.74              # 被写界深度 ± [mm]
DOF_FREQ = 20.0                     # [lp/mm]
DOF_MTF_MIN = 0.20
F_NUMBER_RANGE = (6.0, 22.0)
