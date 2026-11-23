"""公差解析（OpticStudio 標準）用の共通処理

- 公差の値: design/sdoct_design_calc.xlsx の「公差」シート（ID → 公差 ±）。読めないときは下の既定値
- 公差データエディタ（TDE）・メリットファンクション（MFE）・マルチコンフィグ（MCE）の設定
これまでの Zemax スクリプト（../step1〜3）は変えずに、その組立処理だけを呼んで公称系を作る。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ZEMAX_DIR = os.path.dirname(HERE)
sys.path.insert(0, ZEMAX_DIR)                    # ../oct_specs, ../zos_util, ../step1〜3 を使う
OUT = os.path.join(HERE, "out")
XLSX = os.path.join(os.path.dirname(ZEMAX_DIR), "sdoct_design_calc.xlsx")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Excel「公差」シートと同じ既定値（シートが無いとき・ID が欠けているときに使う）
DEFAULTS = {
    "SP_FIB_DZ": 0.010, "SP_FIB_DXY": 0.10, "SP_COL_TILT": 0.10, "SP_GR_YAW": 0.50, "SP_GR_PITCH": 0.20,
    "SP_GR_ROLL": 0.20, "SP_PUPIL": 0.50, "SP_LSM_DXY": 0.05, "SP_LSM_TILT": 0.10, "SP_SEN_TILT": 0.30,
    "SP_COMP_FOCUS": 12.5, "SP_COMP_HEIGHT": 1.0, "SP_COMP_ROLL": 2.0,
    "SA_FIB_DZ": 0.010, "SA_FIB_DXY": 0.10, "SA_COL_TILT": 0.10, "SA_M1M2": 0.30, "SA_PUPIL": 0.50,
    "SA_LSM_DXY": 0.05, "SA_LSM_TILT": 0.10, "SA_BOT_T": 0.10, "SA_BOT_N": 0.010, "SA_DISH_TILT": 0.50,
    "SA_COMP_Z": 5.0,
    "RF_FIB_DZ": 0.010, "RF_MIRROR_TILT": 0.0056, "RF_ND_TILT": 1.0, "RF_ND_T": 0.10, "RF_DC_L": 0.10,
}


def load_tolerances():
    """(公差の dict, 出どころの説明) を返す"""
    tol = dict(DEFAULTS)
    try:
        import openpyxl
        wb = openpyxl.load_workbook(XLSX, data_only=True, read_only=True)
        ws = wb["公差"]
        n = 0
        for row in ws.iter_rows(min_row=5, values_only=True):
            if row and isinstance(row[0], str) and row[0] in tol and isinstance(row[4], (int, float)):
                tol[row[0]] = float(row[4])
                n += 1
        wb.close()
        return tol, f"Excel「公差」シート（{n} 項目）"
    except Exception as e:
        return tol, f"既定値（Excel を読めませんでした: {e}）"


def _set(row, col_enum, name, value, integer):
    """行のプロパティ（Param1・Min など）を設定する。プロパティが無ければセルで設定する"""
    try:
        setattr(row, name, int(value) if integer else float(value))
        return
    except Exception:
        pass
    cell = row.GetOperandCell(getattr(col_enum, name))
    if integer:
        cell.IntegerValue = int(value)
    else:
        cell.DoubleValue = float(value)


class Tde:
    """公差データエディタ。最初に空にしてから 1 行ずつ足す"""

    def __init__(self, ZOSAPI, system):
        self.ZOSAPI = ZOSAPI
        self.tde = system.TDE
        self.col = getattr(ZOSAPI.Editors.TDE, "ToleranceColumn", None)
        while self.tde.NumberOfOperands > 1:
            self.tde.RemoveOperandAt(self.tde.NumberOfOperands)
        self.first = True
        self.rows = []

    def add(self, type_name, p1=0, p2=0, lo=None, hi=None, comment=""):
        row = self.tde.GetOperandAt(1) if self.first else self.tde.AddOperand()
        self.first = False
        row.ChangeType(getattr(self.ZOSAPI.Editors.TDE.ToleranceOperandType, type_name))
        _set(row, self.col, "Param1", p1, True)
        _set(row, self.col, "Param2", p2, True)
        if lo is not None:
            _set(row, self.col, "Min", lo, False)
            _set(row, self.col, "Max", hi, False)
        if comment:
            try:
                row.Comment = comment
            except Exception:
                pass
        self.rows.append((type_name, p1, p2, lo, hi, comment))
        return row

    def pm(self, type_name, p1, p2, t, comment):
        return self.add(type_name, p1, p2, -t, t, comment)

    def element(self, first, last, dxy, tilt, label):
        """要素（first〜last 面）の偏心 X・Y と傾き X・Y"""
        if dxy:
            self.pm("TEDX", first, last, dxy, f"{label} 偏心 X")
            self.pm("TEDY", first, last, dxy, f"{label} 偏心 Y")
        if tilt:
            self.pm("TETX", first, last, tilt, f"{label} 傾き X")
            self.pm("TETY", first, last, tilt, f"{label} 傾き Y")

    def comp_thickness(self, surf, rng, label):
        return self.add("COMP", surf, 0, -rng, rng, f"補正: {label}")

    def comp_param(self, surf, par, rng, label):
        return self.add("CPAR", surf, par, -rng, rng, f"補正: {label}")

    def table(self):
        lines = [f"  {'種類':<5} {'P1':>4} {'P2':>4} {'最小':>10} {'最大':>10}  内容"]
        for t, p1, p2, lo, hi, c in self.rows:
            los = "" if lo is None else f"{lo:+10.4f}"
            his = "" if hi is None else f"{hi:+10.4f}"
            lines.append(f"  {t:<5} {p1:>4} {p2:>4} {los:>10} {his:>10}  {c}")
        return "\n".join(lines)


class Merit:
    """メリットファンクション（公差解析の評価基準 = Criterion: Merit Function）"""

    def __init__(self, ZOSAPI, system):
        self.ZOSAPI = ZOSAPI
        self.system = system
        self.mfe = system.MFE
        self.col = ZOSAPI.Editors.MFE.MeritColumn
        while self.mfe.NumberOfOperands > 1:
            self.mfe.RemoveOperandAt(self.mfe.NumberOfOperands)
        self.first = True

    def add(self, type_name, params=(), target=0.0, weight=1.0, comment=""):
        row = self.mfe.GetOperandAt(1) if self.first else self.mfe.AddOperand()
        self.first = False
        row.ChangeType(getattr(self.ZOSAPI.Editors.MFE.MeritOperandType, type_name))
        for i, v in enumerate(params, start=1):
            cell = row.GetOperandCell(getattr(self.col, f"Param{i}"))
            if i <= 2:
                cell.IntegerValue = int(v)
            else:
                cell.DoubleValue = float(v)
        if type_name not in ("CONF", "BLNK"):
            row.Target = float(target)
            row.Weight = float(weight)
        if comment:
            try:
                row.GetOperandCell(self.col.Comment).Value = comment
            except Exception:
                pass
        return row

    def value(self):
        return self.mfe.CalculateMeritFunction()


def multiconfig(ZOSAPI, system, pram_list, n_config):
    """マルチコンフィグ。pram_list = [(面, パラメータ番号, [各コンフィグの値]), ...]"""
    mce = system.MCE
    while mce.NumberOfConfigurations < n_config:
        mce.AddConfiguration(False)
    first = True
    for surf, par, values in pram_list:
        op = mce.GetOperandAt(1) if first else mce.AddOperand()
        first = False
        op.ChangeType(ZOSAPI.Editors.MCE.MultiConfigOperandType.PRAM)
        op.Param1 = int(surf)
        op.Param2 = int(par)
        for k, v in enumerate(values, start=1):
            op.GetOperandCell(k).DoubleValue = float(v)
    mce.SetCurrentConfiguration(1)


GUIDE = """OpticStudio での実行手順:
  1. このファイルを開く（File > Open）
  2. Tolerance タブ > Tolerancing を開く
     Set-Up: Mode = Sensitivity、Criterion = Merit Function、Comp = Optimize All (DLS)（補正なしのファイルでは関係なし）
     Monte Carlo: # Monte Carlo Runs = 200 程度、Statistics = Normal
  3. OK で実行 → 感度の大きい順の表と、モンテカルロの累積確率（90 % の値など）を見る
  ※ Criterion の値 = メリットファンクションの値（公称値は下に表示）。公差の値は Excel「公差」シートで変えて作り直す"""
