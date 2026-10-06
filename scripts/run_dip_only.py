"""Dip-only strategy: money market until a sleeve's paper cycle (the regime book's put-wing in corr-hi, call wing
in corr-lo, event selling) is down the threshold, then enter that sleeve's position at that day's marks, hold to
expiry. The tranche P&L is exactly (add-on run) - (base run) from run_regime_dipbuy.py. Leverage L = tranche
notional / equity. Cash yield on all equity."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from dispersion.backtest import metrics
from dispersion.data.universe import load_universe
from dispersion.data import rates as R
from dispersion.data.paths import RESULTS
uni = load_universe()
legs = pd.read_csv(RESULTS / "regime_dipbuy_legs_VOLVUE.csv", index_col=0, parse_dates=True); days = legs.index
rf = R.fedfunds_daily(days); cash_acc = (1 + rf / 252.0).cumprod()
ent = list(pd.read_csv(RESULTS / "regime_sleeves_VOLVUE.csv", index_col=0, parse_dates=True).index) + [days[-1]]
PM1 = {"PW": 0.06, "CO": 0.06, "EV": 0.03}
def tranche(sl, var):
    return legs[f"{sl}|{var}"] - legs[f"{sl}|base"]
D = {
    "PW dip 1.0x at -1%": tranche("PW", "add 1.0x at -1%"), "PW dip 1.0x at -2%": tranche("PW", "add 1.0x at -2%"),
    "PW dips 0.5x at -1% and -2%": tranche("PW", "add 0.5x at -1% and 0.5x at -2%"), "PW dips 1.0x at -1% and -2%": tranche("PW", "add 1.0x at -1% and 1.0x at -2%"),
    "CO dip 1.0x at -1%": tranche("CO", "add 1.0x at -1%"),
    "EV dip 1.0x at -0.5%": tranche("EV", "add 1.0x at -0.5%"), "EV dip 1.0x at -1%": tranche("EV", "add 1.0x at -1%"), "EV dips 0.5x at -0.5% and -1%": tranche("EV", "add 0.5x at -0.5% and 0.5x at -1%"),
}
D["all dips 1.0x (PW -1%, CO -1%, EV -1%)"] = D["PW dip 1.0x at -1%"] + D["CO dip 1.0x at -1%"] + D["EV dip 1.0x at -1%"]
D["all dips 2 tranches 0.5x"] = D["PW dips 0.5x at -1% and -2%"] + tranche("CO", "add 0.5x at -1% and 0.5x at -2%") + D["EV dips 0.5x at -0.5% and -1%"]
# cycle stats of the tranche itself
cyc = pd.DataFrame({k: [float(v.loc[b] - v.loc[a]) / 1e6 for a, b in zip(ent[:-1], ent[1:])] for k, v in D.items()}, index=ent[:-1])
print("=== dip tranche per cycle, % of 1x tranche notional (only cycles where it fired) ===")
for k in D:
    x = cyc[k][cyc[k] != 0]
    print(f"{k:42s} fired {len(x):3d}/{len(cyc)} cycles | mean={x.mean():+.2%} median={x.median():+.2%} hit={(x>0).mean():.0%} worst={x.min():+.1%} best={x.max():+.1%} | sum/yr={cyc[k].sum()/19.6:+.2%}")
def equity(L, p):
    E = 1e6; out = pd.Series(np.nan, index=days); out.iloc[0] = E
    for a, b in zip(ent[:-1], ent[1:]):
        c = cash_acc.loc[a:b]; q = p.loc[a:b]
        seg = E * (c / c.iloc[0]) + L * E / 1e6 * (q - q.iloc[0]); out.loc[a:b] = seg.values; E = float(seg.iloc[-1])
        if E <= 0: out.loc[b:] = 0.0; break
    return out.ffill()
def rep(name, e, extra=""):
    e = e.where(e > 0).dropna(); st = metrics.summary(e, uni.spy); y = metrics.yearly(e); cr = metrics.crisis_table(e, uni.spy)
    print(f"{name:48s} cagr={st['cagr']:+.1%} vol={st['vol']:.1%} sharpe={st['sharpe']:.2f} sortino={st['sortino']:.2f} maxdd={st['maxdd']:+.1%} calmar={st['calmar']:.2f} worst_m={st['worst_month']:+.1%} cvar={st['cvar95_m']:+.1%} beta={st.get('beta',np.nan):.2f} sh2h={metrics.second_half(e)['sharpe']:.2f} | 2008={y.get(2008, np.nan):+.0%} 2020={y.get(2020, np.nan):+.0%} 2022={y.get(2022, np.nan):+.0%} covid={cr.loc['Covid_2020','strategy'] if 'Covid_2020' in cr.index else np.nan:+.0%}{extra}", flush=True)
    return st, y
rows = []
print("\n=== dip-only strategy: money market, enter the tranche at L x equity when it fires, cash yield on all equity ===")
rep("money market only", equity(0, D["PW dip 1.0x at -1%"]))
for k in ("PW dip 1.0x at -1%", "PW dips 0.5x at -1% and -2%", "EV dip 1.0x at -1%", "all dips 1.0x (PW -1%, CO -1%, EV -1%)", "all dips 2 tranches 0.5x"):
    for L in (1, 3, 5, 10):
        st, y = rep(f"{k} L={L}", equity(L, D[k])); rows.append(dict(strategy=k, L=L, **{c: st[c] for c in ("cagr", "vol", "sharpe", "sortino", "maxdd", "calmar", "worst_month", "cvar95_m")}, y2008=y.get(2008), y2020=y.get(2020), y2022=y.get(2022)))
        if L == 5: print("   yearly: " + " ".join(f"{kk}={v:+.1%}" for kk, v in y.items()))
pd.DataFrame(rows).to_csv(RESULTS / "dip_only_VOLVUE.csv", index=False); pd.DataFrame(D).to_csv(RESULTS / "dip_only_legs_VOLVUE.csv")
print("DONE")
