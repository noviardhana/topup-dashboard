import re

import gspread
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Dashboard Pengeluaran Game", page_icon="💸", layout="wide")

# ----------------------------------------------------------------- konfigurasi
TAB = "Hoyoverse"
GAME_ORDER = ["Genshin", "HSR"]  # urutan tabel game di tab, dari atas ke bawah
RECURRING = ["VIP IT", "VIP Spiral", "Stygian Onslaught"]  # layanan joki berulang
PALETTE = ["#6c5ce7", "#00b894", "#fdcb6e", "#e17055", "#0984e3",
           "#e84393", "#00cec9", "#636e72", "#a29bfe", "#55efc4"]
CAT_COLORS = {"Genshin": "#6c5ce7", "HSR": "#0984e3", "Joki": "#e17055"}
GOOD, BAD, ACCENT = "#00b894", "#e17055", "#6c5ce7"
PERIODS = {"Bulanan": "Bulan", "Kuartalan": "Kuartal", "Per patch": "Patch", "Tahunan": "Tahun"}
PERIODS_NOPATCH = {k: v for k, v in PERIODS.items() if v != "Patch"}

CSS = """
<style>
.hero{background:linear-gradient(120deg,#6c5ce7 0%,#0984e3 100%);color:#fff;border-radius:18px;padding:22px 26px;margin-bottom:18px}
.hero .t{font-size:1.7rem;font-weight:800}
.hero .s{opacity:.92;font-size:.9rem;margin-top:4px}
.kpi{border:1px solid rgba(128,128,128,.25);border-radius:14px;padding:14px 16px;background:rgba(128,128,128,.07);margin-bottom:10px}
.kpi .l{font-size:.72rem;opacity:.7;text-transform:uppercase;letter-spacing:.04em}
.kpi .v{font-size:1.45rem;font-weight:700;line-height:1.35}
.kpi .s{font-size:.78rem;opacity:.75;min-height:1.1em}
.kpi.good .v{color:#00b894}
.kpi.bad .v{color:#e17055}
</style>
"""


# --------------------------------------------------------------------- helper
def rp(x):
    return "Rp" + f"{x:,.0f}".replace(",", ".")


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[Rp,\s]", "", regex=True), errors="coerce")


def to_date(s):
    d = pd.to_datetime(s, format="%B %d, %Y", errors="coerce")
    return d.fillna(pd.to_datetime(s, errors="coerce"))


def ver_key(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (999,)


def kpi(col, label, value, sub="", tone=""):
    col.markdown(
        f'<div class="kpi {tone}"><div class="l">{label}</div>'
        f'<div class="v">{value}</div><div class="s">{sub}</div></div>',
        unsafe_allow_html=True,
    )


def bullets(items):
    st.markdown("\n".join(f"- {t}" for t in items) if items else "_Belum ada catatan._")


def saran_text(items, limit=None):
    out = []
    for it in items[:limit]:
        t = it["text"]
        if it["est"] > 0:
            t += f" _(estimasi hemat {rp(it['est'])})_"
        out.append(t)
    return out


def fmt_table(d, money=()):
    d = d.copy()
    if "Date" in d:
        d["Date"] = d["Date"].dt.strftime("%d %b %Y")
    for c in money:
        d[c] = d[c].map(rp)
    return d


# ------------------------------------------------------------------- bersihkan
def clean_game(df):
    df = df.copy()
    df["Date"] = to_date(df["Date"])
    df["Amount"] = to_num(df["Amount"])
    df["Acuan"] = to_num(df["70% Value"])
    df["Selisih"] = df["Amount"] - df["Acuan"]
    df["Overpay"] = df["Selisih"].clip(lower=0)
    df["Hemat"] = (-df["Selisih"]).clip(lower=0)
    df["Version"] = df["Version"].astype(str).str.strip()
    return df.sort_values("Date", kind="stable").reset_index(drop=True)


def clean_joki(df):
    df = df.copy()
    df["Date"] = to_date(df["Date"])
    df["Amount"] = to_num(df["Amount"])
    df["Version"] = df["Version"].astype(str).str.strip()
    if "Keterangan" not in df:
        df["Keterangan"] = ""
    df["Hari"] = pd.to_numeric(df["Keterangan"].str.extract(r"\((\d+)\s*hari\)", flags=re.I)[0])
    return df.sort_values("Date", kind="stable").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_sheet():
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    rows = gc.open_by_key(st.secrets["sheet_id"]).worksheet(TAB).get_all_values()
    games = iter(GAME_ORDER)
    out = {}
    for i, r in enumerate(rows):
        # header tabel: kolom A = "No" / "No.", kolom B = "Date"
        if len(r) > 1 and r[0].strip() in ("No", "No.") and r[1].strip() == "Date":
            header = [h.strip() for h in r]
            while header and not header[-1]:
                header.pop()
            body = []
            for x in rows[i + 1:]:
                if not x or not x[0].strip().isdigit():  # berhenti di baris total / kosong
                    break
                body.append((x + [""] * len(header))[: len(header)])
            d = pd.DataFrame(body, columns=header)
            if "Joki" in header:
                out["Joki"] = clean_joki(d)
            elif "Item" in header:
                name = next(games, None)
                if name:
                    out[name] = clean_game(d)
    return {k: out[k] for k in ["Genshin", "HSR", "Joki"] if k in out}


# ----------------------------------------------------------------------- tren
def add_period(df, period):
    d = df.copy()
    if period == "Bulan":
        d["_p"] = d["Date"].dt.to_period("M").dt.to_timestamp()
        order = pd.date_range(d["_p"].min(), d["_p"].max(), freq="MS")
    elif period == "Kuartal":
        q = d["Date"].dt.to_period("Q")
        d["_p"] = q.map(lambda x: f"{x.year} Q{x.quarter}")
        order = pd.Index([f"{x.year} Q{x.quarter}" for x in pd.period_range(q.min(), q.max(), freq="Q")])
    elif period == "Tahun":
        d["_p"] = d["Date"].dt.year.astype(str)
        order = pd.Index(sorted(d["_p"].unique()))
    else:  # Patch
        d["_p"] = d["Version"].astype(str)
        order = pd.Index(sorted(d["_p"].unique(), key=ver_key))
    return d, order


def style_fig(fig, period, order, height=380):
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", y=-0.22, x=0, title_text=""),
                      hovermode="x unified", bargap=0.25, xaxis_title=None, yaxis_title=None)
    fig.update_yaxes(tickprefix="Rp", tickformat="~s", gridcolor="rgba(128,128,128,.2)")
    if period == "Bulan":
        fig.update_xaxes(tickformat="%b %Y")
    else:
        fig.update_xaxes(type="category", categoryorder="array", categoryarray=[str(o) for o in order])
    return fig


def spend_chart(df, period, group, cmap=None):
    d, order = add_period(df, period)
    p = (d.pivot_table(index="_p", columns=group, values="Amount", aggfunc="sum", fill_value=0)
          .reindex(order, fill_value=0))
    p.index.name = "Periode"
    p.columns.name = None
    long = p.reset_index().melt(id_vars="Periode", var_name=group, value_name="Amount")
    fig = px.bar(long, x="Periode", y="Amount", color=group,
                 color_discrete_map=cmap, color_discrete_sequence=PALETTE)
    fig.update_traces(hovertemplate="%{fullData.name}: Rp%{y:,.0f}<extra></extra>")
    if period == "Bulan":
        tot = p.sum(axis=1)
        fig.add_scatter(x=tot.index, y=tot.rolling(3, min_periods=1).mean(), mode="lines",
                        name="Rata-rata 3 bulan", line=dict(color="#d63031", width=3, dash="dot"),
                        hovertemplate="Rata-rata 3 bln: Rp%{y:,.0f}<extra></extra>")
    return style_fig(fig, period, order)


def balance_chart(df, period):
    d, order = add_period(df, period)
    g = d.groupby("_p")[["Overpay", "Hemat"]].sum().reindex(order, fill_value=0)
    cum = (g["Overpay"] - g["Hemat"]).cumsum()
    fig = go.Figure()
    fig.add_bar(x=g.index, y=g["Overpay"], name="Overpay", marker_color=BAD,
                hovertemplate="Overpay: Rp%{y:,.0f}<extra></extra>")
    fig.add_bar(x=g.index, y=-g["Hemat"], name="Hemat", marker_color=GOOD,
                hovertemplate="Hemat: Rp%{customdata:,.0f}<extra></extra>", customdata=g["Hemat"])
    fig.add_scatter(x=g.index, y=cum, name="Net kumulatif (kanan)", mode="lines+markers", yaxis="y2",
                    line=dict(color=ACCENT, width=3),
                    hovertemplate="Net kumulatif: Rp%{y:,.0f}<extra></extra>")
    fig.update_layout(barmode="relative",
                      yaxis2=dict(overlaying="y", side="right", tickprefix="Rp", tickformat="~s", showgrid=False))
    return style_fig(fig, period, order)


# ------------------------------------------------------------------- statistik
def window(df, asof, months_from, months_to=0):
    """transaksi dengan asof-months_from < Date <= asof-months_to"""
    lo = asof - pd.DateOffset(months=months_from)
    hi = asof - pd.DateOffset(months=months_to)
    return df[(df["Date"] > lo) & (df["Date"] <= hi)]


def trend_sentence(df, asof):
    last6 = window(df, asof, 6)["Amount"].sum() / 6
    prev6 = window(df, asof, 12, 6)["Amount"].sum() / 6
    if prev6 <= 0:
        return None
    ch = last6 / prev6 - 1
    arah = "naik" if ch > 0 else "turun"
    return (f"Rata-rata belanja 6 bulan terakhir **{rp(last6)}/bulan**, {arah} {abs(ch):.0%} "
            f"dibanding 6 bulan sebelumnya ({rp(prev6)}/bulan).")


def game_stats(df, asof):
    r12 = window(df, asof, 12)
    return dict(
        total=df["Amount"].sum(), n=len(df), over=df["Overpay"].sum(), hemat=df["Hemat"].sum(),
        hit=(df["Overpay"] == 0).mean(), n12=len(r12), total12=r12["Amount"].sum(),
        over12=r12["Overpay"].sum(), hit12=(r12["Overpay"] == 0).mean() if len(r12) else float("nan"),
    )


def game_insights(df, asof):
    s = game_stats(df, asof)
    ks, ss = [], []
    net = s["over"] - s["hemat"]
    pct_over = s["over"] / s["total"] if s["total"] else 0
    ks.append(f"Total belanja **{rp(s['total'])}** dari {s['n']} transaksi; overpay terhadap acuan 70% "
              f"**{rp(s['over'])}** ({pct_over:.1%} dari belanja).")
    if net > 0:
        ks.append(f"Secara bersih masih **boros {rp(net)}**: penghematan ({rp(s['hemat'])}) belum menutup overpay.")
    else:
        ks.append(f"Secara bersih sudah **hemat {rp(-net)}**: penghematan ({rp(s['hemat'])}) melebihi overpay.")
    hit_txt = f"{s['hit']:.0%} transaksi berada dibawah acuan"
    if s["n12"]:
        hit_txt += f"; dalam 12 bulan terakhir {s['hit12']:.0%} ({s['n12']} transaksi)"
    ks.append(hit_txt + ".")
    t = trend_sentence(df, asof)
    if t:
        ks.append(t)

    cut12 = asof - pd.DateOffset(months=12)
    sh = df.groupby("Toko").agg(n=("Amount", "size"), over=("Overpay", "sum"), hemat=("Hemat", "sum"),
                                last=("Date", "max"),
                                n_over=("Overpay", lambda x: int((x > 0).sum()))).reset_index()
    sh["net"] = sh["over"] - sh["hemat"]
    pool = sh[sh["n"] >= 3] if (sh["n"] >= 3).any() else sh
    best = pool.nsmallest(1, "net").iloc[0]
    if best["net"] < 0:
        ks.append(f"Toko paling menguntungkan: **{best['Toko']}** (net hemat {rp(-best['net'])} dari {best['n']} transaksi).")
    worst = sh.nlargest(1, "net").iloc[0]
    if worst["net"] > 0:
        ks.append(f"Toko paling boros: **{worst['Toko']}** (net overpay {rp(worst['net'])} dari {worst['n']} transaksi).")
    old = sh[(sh["over"] > 0) & (sh["last"] <= cut12)]
    if len(old):
        ks.append("Toko dengan riwayat overpay yang sudah tidak dipakai 12 bulan terakhir: "
                  + ", ".join(f"**{x}**" for x in old["Toko"]) + " (pertahankan tidak dipakai).")
    r12 = window(df, asof, 12)
    if len(r12) and r12["Overpay"].max() > 0:
        r = r12.loc[r12["Overpay"].idxmax()]
        ks.append(f"Overpay terbesar 12 bulan terakhir: **{r['Item']}** di {r['Toko']} "
                  f"({r['Date']:%d %b %Y}), {rp(r['Overpay'])} di atas acuan.")

    # ----- saran
    top = df.groupby("Item").agg(total=("Amount", "sum"), acuan=("Acuan", "last")).nlargest(3, "total")
    lim = ", ".join(f"{i} {rp(r['acuan'])}" for i, r in top.iterrows())
    if s["over12"] > 0:
        ss.append(dict(text=f"Pasang **batas harga maksimal** sebelum membeli: {lim}. Overpay 12 bulan terakhir "
                            f"{rp(s['over12'])}.", est=s["over12"]))
    else:
        ss.append(dict(text=f"Pertahankan disiplin harga (batas maksimal: {lim}); tidak ada overpay dalam 12 bulan terakhir.",
                       est=0))
    risky = sh[(sh["over"] > 0) & (sh["last"] > cut12)].nlargest(2, "over")
    for _, r in risky.iterrows():
        ss.append(dict(text=f"Beli di **{r['Toko']}** hanya jika harga ≤ acuan: {r['n_over']} dari {r['n']} transaksi "
                            f"overpay (total {rp(r['over'])}), terakhir dipakai {r['last']:%d %b %Y}.", est=0))
    promo = df[df["Hemat"] >= 0.2 * df["Acuan"]]
    if len(promo):
        pg = promo.groupby("Item").agg(k=("Amount", "size"), med=("Amount", "median"), last=("Date", "max"),
                                       toko=("Toko", lambda x: x.mode().iloc[0])).nlargest(1, "k")
        for item, r in pg.iterrows():
            if r["k"] >= 2:
                ss.append(dict(text=f"**{item}** beberapa kali promo: {r['k']}x dibeli ≥20% di bawah acuan "
                                    f"(median {rp(r['med'])}, paling sering di {r['toko']}, terakhir {r['last']:%d %b %Y}). "
                                    "Jika stok aman, tunda pembelian sampai promo muncul.", est=0))
    return ks, ss


def joki_insights(df, asof):
    ks, ss = [], []
    total, n = df["Amount"].sum(), len(df)
    byj = df.groupby("Joki")["Amount"].agg(["sum", "size"]).sort_values("sum", ascending=False)
    byc = df.groupby("Content")["Amount"].agg(["sum", "size"]).sort_values("sum", ascending=False)
    singles = int((byj["size"] == 1).sum())
    ks.append(f"Total jasa joki **{rp(total)}** dari {n} transaksi ke {len(byj)} joki; rata-rata {rp(total / n)} per transaksi.")
    ks.append(f"Joki terbesar: **{byj.index[0]}** ({byj['sum'].iloc[0] / total:.0%} dari total); "
              f"{singles} dari {len(byj)} joki hanya dipakai 1x.")
    if len(byc) >= 2:
        ks.append(f"Konten terbesar: **{byc.index[0]}** ({byc['sum'].iloc[0] / total:.0%}) dan "
                  f"**{byc.index[1]}** ({byc['sum'].iloc[1] / total:.0%}).")
    t = trend_sentence(df, asof)
    if t:
        ks.append(t)

    cut12 = asof - pd.DateOffset(months=12)
    ra = df[(df["Content"] == "Rawat Akun") & df["Hari"].notna()].copy()
    if len(ra) >= 2:
        ra["PerHari"] = ra["Amount"] / ra["Hari"]
        mn, mx = ra["PerHari"].min(), ra["PerHari"].max()
        cheap = ra.loc[ra["PerHari"].idxmin(), "Joki"]
        ks.append(f"Rawat Akun: tarif per hari berkisar {rp(mn)} sampai {rp(mx)} ({cheap} termurah).")
        r12 = ra[ra["Date"] > cut12]
        est = ((r12["PerHari"] - mn) * r12["Hari"]).sum()
        if est > 0:
            ss.append(dict(text=f"Untuk **Rawat Akun**, bandingkan tarif per hari sebelum memesan. Memakai tarif "
                                f"termurah ({rp(mn)}/hari, {cheap}) pada pemesanan 12 bulan terakhir.", est=est))
    est_rec, notes = 0.0, []
    for c in RECURRING:
        d = df[df["Content"] == c]
        if len(d) < 3:
            continue
        med = d["Amount"].median()
        est_rec += (d[d["Date"] > cut12]["Amount"] - med).clip(lower=0).sum()
        g = d.groupby("Joki")["Amount"].agg(["median", "size"])
        g = g[g["size"] >= 2]
        if len(g) >= 2:
            notes.append(f"{c}: {g['median'].idxmin()} (median {rp(g['median'].min())})")
    if est_rec > 0 or notes:
        txt = ("Untuk layanan berulang (" + ", ".join(RECURRING) + "), anggap **median** sebagai harga wajar dan "
               "tolak harga jauh di atasnya.")
        if notes:
            txt += " Joki termurah (≥2 transaksi): " + "; ".join(notes) + "."
        ss.append(dict(text=txt, est=est_rec))
    if singles >= 5:
        ss.append(dict(text=f"Kurangi joki sekali pakai ({singles} joki hanya 1 transaksi). Pilih 2-3 joki langganan "
                            "agar harga mudah dibandingkan.", est=0))
    return ks, ss


# --------------------------------------------------------------------- render
def render_summary(data, asof):
    frames = [d[["Date", "Amount"]].assign(Kategori=k) for k, d in data.items()]
    allx = pd.concat(frames, ignore_index=True)
    total = allx["Amount"].sum()
    y = asof.year
    ytd = allx[allx["Date"].dt.year == y]["Amount"].sum()
    prev_ytd = allx[(allx["Date"].dt.year == y - 1) & (allx["Date"] <= asof - pd.DateOffset(years=1))]["Amount"].sum()
    avg12 = window(allx, asof, 12)["Amount"].sum() / 12
    avg_prev = window(allx, asof, 24, 12)["Amount"].sum() / 12

    ins = {k: (game_insights(d, asof) if k != "Joki" else joki_insights(d, asof)) for k, d in data.items()}
    est_total = sum(it["est"] for _, ss in ins.values() for it in ss)
    over12 = sum(window(d, asof, 12)["Overpay"].sum() for k, d in data.items() if k != "Joki")
    topup12 = sum(window(d, asof, 12)["Amount"].sum() for k, d in data.items() if k != "Joki")
    joki_total = data["Joki"]["Amount"].sum() if "Joki" in data else 0

    c = st.columns(3)
    kpi(c[0], "Total pengeluaran", rp(total), f"{allx['Date'].min():%b %Y} - {allx['Date'].max():%b %Y} · {len(allx)} transaksi")
    if prev_ytd > 0:
        ch = ytd / prev_ytd - 1
        kpi(c[1], f"Tahun {y} (sampai {asof:%d %b})", rp(ytd),
            f"{'naik' if ch > 0 else 'turun'} {abs(ch):.0%} vs periode sama {y - 1} ({rp(prev_ytd)})",
            "bad" if ch > 0 else "good")
    else:
        kpi(c[1], f"Tahun {y} (sampai {asof:%d %b})", rp(ytd), "")
    kpi(c[2], "Rata-rata per bulan (12 bln)", rp(avg12),
        f"12 bulan sebelumnya {rp(avg_prev)}", "bad" if avg12 > avg_prev > 0 else "good")
    c = st.columns(3)
    kpi(c[0], "Overpay top-up (12 bln)", rp(over12),
        f"{over12 / topup12:.1%} dari belanja top-up" if topup12 else "", "bad" if over12 > 0 else "good")
    kpi(c[1], "Porsi joki", f"{joki_total / total:.0%}" if total else "-", f"{rp(joki_total)} sepanjang waktu")
    kpi(c[2], "Estimasi potensi hemat (12 bln)", rp(est_total), "dari saran di bawah", "good" if est_total > 0 else "")

    st.subheader("Tren pengeluaran")
    left, right = st.columns([3, 1])
    label = left.radio("Periode", list(PERIODS_NOPATCH), horizontal=True, key="p_sum", label_visibility="collapsed")
    left.plotly_chart(spend_chart(allx, PERIODS_NOPATCH[label], "Kategori", CAT_COLORS), key="sp_sum")
    share = allx.groupby("Kategori")["Amount"].sum().reset_index()
    fig = px.pie(share, names="Kategori", values="Amount", hole=0.6, color="Kategori", color_discrete_map=CAT_COLORS)
    fig.update_traces(textinfo="percent", hovertemplate="%{label}: Rp%{value:,.0f}<extra></extra>")
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h", y=-0.1))
    right.plotly_chart(fig, key="pie_sum")

    st.subheader("Kesimpulan dan saran")
    shares = " · ".join(f"{k} {d['Amount'].sum() / total:.0%}" for k, d in data.items())
    top_lines = [f"Pengeluaran total **{rp(total)}** ({shares})."]
    if prev_ytd > 0:
        top_lines.append(f"Tahun {y} sampai {asof:%d %b}: **{rp(ytd)}**, {'naik' if ytd > prev_ytd else 'turun'} "
                         f"{abs(ytd / prev_ytd - 1):.0%} dibanding periode sama {y - 1}.")
    if topup12:
        top_lines.append(f"Overpay top-up 12 bulan terakhir **{rp(over12)}** ({over12 / topup12:.1%} dari belanja top-up).")
    bullets(top_lines)

    kc, sc = st.columns(2)
    kc.markdown("#### Kesimpulan")
    sc.markdown("#### Saran penghematan berikutnya")
    for k, (ks, ss) in ins.items():
        with kc:
            st.markdown(f"**{k}**")
            bullets(ks[:3])
        with sc:
            st.markdown(f"**{k}**")
            bullets(saran_text(ss, 3))
    st.caption("Acuan top-up = kolom '70% Value' di sheet. Estimasi hemat dihitung dari transaksi 12 bulan terakhir "
               "dan hanya perkiraan, bukan jaminan.")


def render_game(df, name, asof):
    s = game_stats(df, asof)
    net = s["over"] - s["hemat"]
    c = st.columns(6)
    kpi(c[0], "Total belanja", rp(s["total"]), f"{s['n']} transaksi")
    kpi(c[1], "Rata-rata per patch", rp(df.groupby("Version")["Amount"].sum().mean()), f"{df['Version'].nunique()} patch")
    kpi(c[2], "Overpay", rp(s["over"]), f"{s['over'] / s['total']:.1%} dari belanja", "bad")
    kpi(c[3], "Hemat", rp(s["hemat"]), "di bawah acuan", "good")
    kpi(c[4], "Net", ("Boros " if net > 0 else "Hemat ") + rp(abs(net)), "overpay dikurangi hemat",
        "bad" if net > 0 else "good")
    kpi(c[5], "Hit rate ≤ acuan", f"{s['hit']:.0%}", f"12 bln terakhir: {s['hit12']:.0%}" if s["n12"] else "")

    st.subheader("Tren")
    label = st.radio("Periode", list(PERIODS), horizontal=True, key=f"p_{name}", label_visibility="collapsed")
    period = PERIODS[label]
    a, b = st.columns([3, 2])
    a.markdown("**Belanja per item**")
    a.plotly_chart(spend_chart(df, period, "Item"), key=f"sp_{name}")
    b.markdown("**Overpay vs hemat**")
    b.plotly_chart(balance_chart(df, period), key=f"bal_{name}")

    st.subheader("Toko dan item")
    a, b = st.columns([2, 3])
    sh = df.groupby("Toko").agg(n=("Amount", "size"), Overpay=("Overpay", "sum"), Hemat=("Hemat", "sum")).reset_index()
    sh["Net"] = sh["Overpay"] - sh["Hemat"]
    sh["Status"] = sh["Net"].map(lambda x: "Boros" if x > 0 else "Hemat")
    sh = sh.sort_values("Net")
    fig = px.bar(sh, x="Net", y="Toko", orientation="h", color="Status",
                 color_discrete_map={"Boros": BAD, "Hemat": GOOD}, text=sh["n"].map(lambda v: f"{v}x"))
    fig.update_layout(height=max(300, 34 * len(sh) + 80), margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", y=-0.15, title_text=""), xaxis_title="Net (+ boros, - hemat)",
                      yaxis_title=None)
    fig.update_xaxes(tickprefix="Rp", tickformat="~s")
    a.markdown("**Net per toko**")
    a.plotly_chart(fig, key=f"shop_{name}")

    it = df.groupby("Item").agg(Transaksi=("Amount", "size"), Total=("Amount", "sum"), Rata=("Amount", "mean"),
                                Acuan=("Acuan", "last"), HitRate=("Overpay", lambda x: (x == 0).mean() * 100),
                                Overpay=("Overpay", "sum"), Hemat=("Hemat", "sum")).reset_index()
    it = it.sort_values("Total", ascending=False)
    disp = fmt_table(it, money=["Total", "Rata", "Acuan", "Overpay", "Hemat"])
    b.markdown("**Per item (Acuan = batas harga maksimal)**")
    b.dataframe(disp, hide_index=True, column_config={
        "Rata": st.column_config.Column("Rata-rata"),
        "HitRate": st.column_config.ProgressColumn("Hit rate", min_value=0, max_value=100, format="%.0f%%"),
    })

    st.subheader("Promo dan overpay")
    a, b = st.columns(2)
    promo = df[df["Hemat"] >= 0.2 * df["Acuan"]][["Date", "Item", "Toko", "Amount", "Hemat"]]
    a.markdown("**Promo besar (≥20% di bawah acuan)**")
    a.dataframe(fmt_table(promo.sort_values("Date", ascending=False), ["Amount", "Hemat"]), hide_index=True)
    top = df[df["Overpay"] > 0].nlargest(10, "Overpay")[["Date", "Item", "Toko", "Amount", "Overpay"]]
    b.markdown("**10 overpay terbesar**")
    b.dataframe(fmt_table(top, ["Amount", "Overpay"]), hide_index=True)

    ks, ss = game_insights(df, asof)
    st.subheader("Kesimpulan dan saran")
    k, sa = st.columns(2)
    k.markdown("#### Kesimpulan")
    with k:
        bullets(ks)
    sa.markdown("#### Saran penghematan")
    with sa:
        bullets(saran_text(ss))

    with st.expander("Detail transaksi"):
        f1, f2 = st.columns(2)
        ft = f1.multiselect("Toko", sorted(df["Toko"].unique()), key=f"ft_{name}")
        fi = f2.multiselect("Item", sorted(df["Item"].unique()), key=f"fi_{name}")
        mask = pd.Series(True, index=df.index)
        if ft:
            mask &= df["Toko"].isin(ft)
        if fi:
            mask &= df["Item"].isin(fi)
        v = df[mask]
        st.dataframe(fmt_table(v.drop(columns=["No"], errors="ignore"),
                               ["Amount", "Acuan", "Selisih", "Overpay", "Hemat"]), hide_index=True)


def render_joki(df, asof):
    total, n = df["Amount"].sum(), len(df)
    ra = df[(df["Content"] == "Rawat Akun") & df["Hari"].notna()].copy()
    ra["PerHari"] = ra["Amount"] / ra["Hari"]
    c = st.columns(5)
    kpi(c[0], "Total joki", rp(total), f"{n} transaksi")
    kpi(c[1], "Joki berbeda", f"{df['Joki'].nunique()}", f"{int((df['Joki'].value_counts() == 1).sum())} hanya 1x")
    kpi(c[2], "Rata-rata / transaksi", rp(total / n), f"median {rp(df['Amount'].median())}")
    kpi(c[3], "12 bulan terakhir", rp(window(df, asof, 12)["Amount"].sum()),
        f"{len(window(df, asof, 12))} transaksi")
    kpi(c[4], "Rawat Akun / hari (median)", rp(ra["PerHari"].median()) if len(ra) else "-",
        f"{len(ra)} pemesanan" if len(ra) else "")

    st.subheader("Tren")
    label = st.radio("Periode", list(PERIODS), horizontal=True, key="p_Joki", label_visibility="collapsed")
    a, b = st.columns([3, 2])
    a.markdown("**Pengeluaran per konten**")
    a.plotly_chart(spend_chart(df, PERIODS[label], "Content"), key="sp_Joki")
    bj = df.groupby("Joki")["Amount"].agg(Total="sum", N="size").sort_values("Total").tail(10).reset_index()
    fig = px.bar(bj, x="Total", y="Joki", orientation="h", text=bj["N"].map(lambda v: f"{v}x"),
                 color_discrete_sequence=[ACCENT])
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), xaxis_title=None, yaxis_title=None)
    fig.update_xaxes(tickprefix="Rp", tickformat="~s")
    b.markdown("**10 joki terbesar (jumlah transaksi di ujung bar)**")
    b.plotly_chart(fig, key="joki_top")

    st.subheader("Harga per konten")
    a, b = st.columns([3, 2])
    ct = df.groupby("Content")["Amount"].agg(Total="sum", N="size", Median="median", Rata="mean", Maks="max")
    ct = ct.sort_values("Total", ascending=False).reset_index()
    a.dataframe(fmt_table(ct, ["Total", "Median", "Rata", "Maks"]), hide_index=True,
                column_config={"Rata": st.column_config.Column("Rata-rata")})
    if len(ra):
        fig = px.scatter(ra, x="Date", y="PerHari", color="Joki", size="Hari", size_max=22,
                         color_discrete_sequence=PALETTE)
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), xaxis_title=None, yaxis_title=None,
                          legend=dict(orientation="h", y=-0.25, title_text=""))
        fig.update_yaxes(tickprefix="Rp", tickformat="~s")
        b.markdown("**Rawat Akun: tarif per hari (ukuran = jumlah hari)**")
        b.plotly_chart(fig, key="ra_scatter")

    ks, ss = joki_insights(df, asof)
    st.subheader("Kesimpulan dan saran")
    k, sa = st.columns(2)
    k.markdown("#### Kesimpulan")
    with k:
        bullets(ks)
    sa.markdown("#### Saran penghematan")
    with sa:
        bullets(saran_text(ss))

    with st.expander("Detail transaksi"):
        f1, f2 = st.columns(2)
        fj = f1.multiselect("Joki", sorted(df["Joki"].unique()), key="fj_Joki")
        fc = f2.multiselect("Content", sorted(df["Content"].unique()), key="fc_Joki")
        mask = pd.Series(True, index=df.index)
        if fj:
            mask &= df["Joki"].isin(fj)
        if fc:
            mask &= df["Content"].isin(fc)
        v = df[mask]
        st.dataframe(fmt_table(v.drop(columns=["No"], errors="ignore"), ["Amount"]), hide_index=True)


# ----------------------------------------------------------------------- main
st.markdown(CSS, unsafe_allow_html=True)
if st.sidebar.button("Refresh data"):
    st.cache_data.clear()

try:
    data = load_sheet()
except Exception as e:
    st.error(f"Gagal membaca Google Sheets: {e}")
    st.stop()

if not data:
    st.warning(f"Tidak ada tabel yang dikenali di tab '{TAB}'. Header tabel harus diawali kolom 'No' dan 'Date'.")
    st.stop()

asof = max(d["Date"].max() for d in data.values())
st.markdown(
    f'<div class="hero"><div class="t">💸 Dashboard Pengeluaran Game</div>'
    f'<div class="s">Transaksi terakhir {asof:%d %B %Y} · tabel terbaca: {", ".join(data)}</div></div>',
    unsafe_allow_html=True,
)

tabs = st.tabs(["📊 Ringkasan"] + list(data))
with tabs[0]:
    render_summary(data, asof)
for tab, (name, df) in zip(tabs[1:], data.items()):
    with tab:
        if name == "Joki":
            render_joki(df, asof)
        else:
            render_game(df, name, asof)