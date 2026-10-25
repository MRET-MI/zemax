"""Zemax なしで step1 の初期系を近軸計算するチェック用スクリプト (標準ライブラリのみ)

確認すること:
    - 近軸倍率 (目標 -0.16)
    - 絞りが前群の後側焦点にあるか (物体側テレセントリック性)
    - 像距離・全長・各面の必要有効半径
    - 波長ごとの像距離のずれ (軸上色収差の目安)

使い方:
    python paraxial_check.py         (step1 の4枚系)
    python paraxial_check.py step3   (step3 の8枚6群の初期系)
"""
import math
import sys

from specs import (FIELDS_Y, MAG_TARGET, MAX_SEMI_DIAMETER, MIN_BACK_DISTANCE, OBJ_NA,
                   PRIMARY_WAVE, TELECENTRICITY_MAX_DEG, TOTAL_TRACK_TARGET, WAVES, WD)

# Schott Sellmeier 係数 (B1, B2, B3, C1, C2, C3)  波長 µm
# ※ 参考値。正確な値は OpticStudio の SCHOTT.AGF で確認すること
SELLMEIER = {
    "N-BK7": (1.03961212, 0.231792344, 1.01046945,
              0.00600069867, 0.0200179144, 103.560653),
    "N-FK51A": (0.971247817, 0.216901417, 0.904651666,
                0.00472301995, 0.0153575612, 168.68133),
    "N-SF11": (1.73759695, 0.313747346, 1.89878101,
               0.013188707, 0.0623068142, 155.23629),
}


def refractive_index(material, wl_um):
    if not material:
        return 1.0
    b1, b2, b3, c1, c2, c3 = SELLMEIER[material]
    l2 = wl_um ** 2
    return math.sqrt(1 + b1 * l2 / (l2 - c1) + b2 * l2 / (l2 - c2) + b3 * l2 / (l2 - c3))


def surface_rows(wl_um, surfaces):
    """[(コメント, 曲率, 厚み, 面の後ろの屈折率)]"""
    return [(name, 0.0 if math.isinf(r) else 1.0 / r, t, refractive_index(mat, wl_um))
            for name, r, t, mat in surfaces]


def trace(rows, y, u, gap_before=0.0, last=None):
    """近軸光線追跡 (y-nu 法)。

    y, u: 第1面の gap_before 手前での高さと傾き。
    rows[:last] を通し、各面での高さのリストと最終面直後の (y, u) を返す。
    """
    rows = rows[:last]
    n = 1.0
    y += gap_before * u
    heights = []
    for i, (_, c, t, n2) in enumerate(rows):
        heights.append(y)
        u = (n * u - y * c * (n2 - n)) / n2
        n = n2
        if i < len(rows) - 1:
            y += t * u
    return heights, y, u


def analyze(wl_um, surfaces):
    rows = surface_rows(wl_um, surfaces)
    stop = [r[0] for r in rows].index("STOP")

    # マージナル光線 (軸上物点から NA の角度)
    m_heights, m_y, m_u = trace(rows, 0.0, OBJ_NA, WD)
    image_dist = -m_y / m_u
    mag = OBJ_NA / m_u  # 物体・像空間とも n=1 なのでラグランジュ不変量から

    # 最大物体高の主光線: 絞り中心を通る物体空間の傾きを求める
    h = FIELDS_Y[-1]
    ya = trace(rows, h, 0.0, WD)[0][stop]
    yb = trace(rows, 0.0, 1.0, WD)[0][stop]
    u_chief = -ya / yb
    c_heights, c_y, c_u = trace(rows, h, u_chief, WD)

    # 前群 (絞りより前) の後側焦点位置 = 物体側テレセントリックにするべき絞り位置
    _, fy, fu = trace(rows, 1.0, 0.0, last=stop)
    front_efl = -1.0 / fu
    front_bfl = -fy / fu

    _, sy, su = trace(rows, 1.0, 0.0)
    return {
        "rows": rows,
        "stop": stop,
        "image_dist": image_dist,
        "mag": mag,
        "efl": -1.0 / su,
        "front_efl": front_efl,
        "front_bfl": front_bfl,
        "chief_obj_deg": math.degrees(math.atan(u_chief)),
        "chief_img_deg": math.degrees(math.atan(c_u)),
        "image_height": c_y + image_dist * c_u,
        "semi_dia": [abs(a) + abs(b) for a, b in zip(m_heights, c_heights)],
        "track": sum(t for _, _, t, _ in rows[:-1]) + image_dist,
    }


def ok(flag):
    return "OK" if flag else "NG"


def load_surfaces(name):
    if name == "step3":
        from step3_expand_8elements import SURFACES
    else:
        from step1_build_initial import SURFACES
    return SURFACES


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "step1"
    surfaces = load_surfaces(name)
    p = analyze(WAVES[PRIMARY_WAVE - 1], surfaces)
    stop_gap = p["rows"][p["stop"] - 1][2]

    print(f"=== 近軸チェック [{name}] (主波長 {WAVES[PRIMARY_WAVE - 1]} µm) ===")
    print(f"近軸倍率            : {p['mag']:+.4f}  (目標 {MAG_TARGET})  "
          f"{ok(abs(p['mag'] - MAG_TARGET) < 0.005)}")
    print(f"系の焦点距離        : {p['efl']:.2f} mm")
    print(f"前群 焦点距離 f1    : {p['front_efl']:.2f} mm")
    print(f"前群 後側焦点距離   : {p['front_bfl']:.2f} mm  ← 絞りまでの距離 {stop_gap:.2f} mm "
          f"(差 {stop_gap - p['front_bfl']:+.2f})")
    print(f"物体側主光線角      : {p['chief_obj_deg']:+.4f} deg  (目標 <{TELECENTRICITY_MAX_DEG})  "
          f"{ok(abs(p['chief_obj_deg']) < TELECENTRICITY_MAX_DEG)}")
    print(f"像側主光線角 (参考) : {p['chief_img_deg']:+.3f} deg")
    print(f"像高 (物体高 {FIELDS_Y[-1]} mm) : {p['image_height']:+.3f} mm")
    print(f"像距離 (最終面→像)  : {p['image_dist']:.2f} mm  (下限 {MIN_BACK_DISTANCE})  "
          f"{ok(p['image_dist'] >= MIN_BACK_DISTANCE)}")
    print(f"第1面〜像面         : {p['track']:.2f} mm  (目安 {TOTAL_TRACK_TARGET})")

    print("\n--- 各面の必要有効半径 (近軸: |マージナル| + |主光線|) ---")
    for (name, *_), sd in zip(p["rows"], p["semi_dia"]):
        print(f"  {name:10s} {sd:7.2f} mm  {ok(sd <= MAX_SEMI_DIAMETER)}")

    print("\n--- 波長ごとの像距離 (軸上色収差の目安) ---")
    for wl in WAVES:
        q = analyze(wl, surfaces)
        print(f"  {wl:.1f} µm : 像距離 {q['image_dist']:8.3f} mm  "
              f"(主波長との差 {q['image_dist'] - p['image_dist']:+.3f})  倍率 {q['mag']:+.4f}")


if __name__ == "__main__":
    main()
