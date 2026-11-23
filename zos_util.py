"""ZOS-API の共通処理（面の種類・パラメータ、ベンダーファイルの取り込み、色消しレンズの反転、オペランド値）"""
import math
import os

from oct_specs import VENDOR_DIR


class Lens:
    """系を前から順に組み立てる（常に像面の直前に面を足す）"""

    def __init__(self, ZOSAPI, system):
        self.ZOSAPI = ZOSAPI
        self.system = system
        self.lde = system.LDE
        self.used_first = False     # 新規系の 1 面（STO）を最初の面として使う

    def add(self, comment="", thickness=0.0, radius=None, material=""):
        if not self.used_first:
            s = self.lde.GetSurfaceAt(1)
            self.used_first = True
        else:
            s = self.lde.InsertNewSurfaceAt(self.lde.NumberOfSurfaces - 1)
        s.Comment = comment
        s.Thickness = float(thickness)
        if radius is not None and not math.isinf(radius) and radius != 0:
            s.Radius = float(radius)
        if material:
            s.Material = material
        return s

    def index(self, surf):
        return surf.SurfaceNumber

    # ---- 面の種類とパラメータ ----
    def set_type(self, surf, type_name):
        st = getattr(self.ZOSAPI.Editors.LDE.SurfaceType, type_name)
        surf.ChangeType(surf.GetSurfaceTypeSettings(st))

    def par(self, surf, n, value):
        col = getattr(self.ZOSAPI.Editors.LDE.SurfaceColumn, f"Par{n}")
        surf.GetSurfaceCell(col).DoubleValue = float(value)

    def coord_break(self, comment, tilt_x=0.0, thickness=0.0):
        s = self.add(comment, thickness)
        self.set_type(s, "CoordinateBreak")
        self.par(s, 3, tilt_x)             # Par3 = Tilt About X
        return s

    def paraxial(self, comment, focal, thickness):
        s = self.add(comment, thickness)
        self.set_type(s, "Paraxial")
        self.par(s, 1, focal)              # Par1 = Focal Length
        return s

    def grating(self, comment, lines_per_um, order, material, thickness):
        s = self.add(comment, thickness, material=material)
        self.set_type(s, "DiffractionGrating")
        self.par(s, 1, lines_per_um)       # Par1 = Lines/µm
        self.par(s, 2, order)              # Par2 = Diffraction Order
        return s

    # ---- オペランド ----
    def val(self, op_name, *params):
        op = getattr(self.ZOSAPI.Editors.MFE.MeritOperandType, op_name)
        p = list(params) + [0] * (8 - len(params))
        return self.system.MFE.GetOperandValue(op, *p)


def vendor_path(name):
    p = os.path.join(VENDOR_DIR, name)
    return p if os.path.exists(p) else None


def open_vendor(ZOSAPI, app, path):
    """ベンダーの .zmx を別の系として開く（呼び出し側で Close する）"""
    src = app.CreateNewSystem(ZOSAPI.SystemType.Sequential)
    if not src.LoadFile(path, False):
        raise RuntimeError(f"読み込みに失敗しました: {path}")
    return src


def describe(system, title):
    """面の一覧（確認用）"""
    lde = system.LDE
    lines = [f"--- {title}: {lde.NumberOfSurfaces} 面, 絞り = {lde.StopSurface} ---"]
    for i in range(lde.NumberOfSurfaces):
        s = lde.GetSurfaceAt(i)
        r = s.Radius
        rs = "inf" if math.isinf(r) else f"{r:.4f}"
        tname = getattr(s, "TypeName", None) or getattr(s, "Type", "")
        lines.append(f"  {i:2d} {str(tname):<20} R {rs:>11}  t {s.Thickness:9.4f}  {str(s.Material):<10} {s.Comment}")
    return "\n".join(lines)


def copy_scan_lens(lens, src, first=None):
    """走査レンズ（ベンダー）の絞り面〜像面の手前を、組立中の系の末尾（像面の前）へ写す。戻り値: 写した最初の面"""
    slde = src.LDE
    first = slde.StopSurface if first is None else first
    last = slde.NumberOfSurfaces - 2
    n = last - first + 1
    if not lens.used_first:                       # 1 面目がまだ空いていれば、写す前に埋めておく
        lens.add("（ダミー）", 0.0)
    at = lens.lde.NumberOfSurfaces - 1            # 像面の位置に挿入 → 像面の前に入る
    lens.lde.CopySurfacesFrom(slde, first, n, at)
    return lens.lde.GetSurfaceAt(at), n


def lens_block(src):
    """色消しレンズのベンダーファイルから、ガラスのある面のまとまり [(R, t, material), ...] を取り出す。
    最後の要素の t は最終面 → 焦点（BFL）"""
    slde = src.LDE
    rows = [(slde.GetSurfaceAt(i).Radius, slde.GetSurfaceAt(i).Thickness, str(slde.GetSurfaceAt(i).Material))
            for i in range(1, slde.NumberOfSurfaces - 1)]
    glass = [i for i, r in enumerate(rows) if r[2].strip()]
    if not glass:
        raise RuntimeError("ベンダーファイルにガラスの面が見つかりません")
    a, b = glass[0], glass[-1] + 1
    return rows[a:b + 1]


def add_reversed(lens, block, label, last_thickness):
    """色消しレンズを前後反転して入れる（点光源 → 平行光の向き）。block は lens_block() の戻り値"""
    n = len(block)
    first = None
    for k in range(n):
        i = n - 1 - k
        R = block[i][0]
        mat = block[i - 1][2] if i >= 1 else ""
        t = block[i - 1][1] if k < n - 1 else last_thickness
        s = lens.add(f"{label} 面{k + 1}", t, radius=(-R if not math.isinf(R) else None), material=mat)
        first = first or s
    return first, s
