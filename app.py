import gspread
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Dashboard Top-up", layout="wide")

TAB = "Hoyoverse"
GAME_ORDER = ["Genshin", "HSR"]  # urutan tabel di tab, dari atas ke bawah


def rp(x):
    return f"Rp{x:,.0f}".replace(",", ".")


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[Rp,\s]", "", regex=True), errors="coerce")


def clean(df):
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], format="%B %d, %Y")
    df["Amount"] = to_num(df["Amount"])
    df["Acuan"] = to_num(df["70% Value"])
    df["Selisih"] = df["Amount"] - df["Acuan"]
    df["Overpay"] = df["Selisih"].clip(lower=0)
    df["Hemat"] = (-df["Selisih"]).clip(lower=0)
    df["Bulan"] = df["Date"].dt.to_period("M").dt.to_timestamp()
    return df.sort_values("Date").reset_index(drop=True)


def streak(df):
    n = 0
    for v in df["Overpay"][::-1]:
        if v == 0:
            n += 1
        else:
            break
    return n


def render(df, game):
    # ---------- 1. Status cepat ----------
    last = df.iloc[-1]
    net = df["Selisih"].sum()
    over = df["Overpay"].sum()
    total = df["Amount"].sum()
    c = st.columns(5)
    c[0].metric("Pembelian terakhir", rp(last["Amount"]), f"{last['Item']} @ {last['Toko']}")
    c[1].metric("Status terakhir", "OK" if last["Overpay"] == 0 else "Overpay",
                rp(-last["Selisih"]) + " vs acuan", delta_color="normal")
    c[2].metric("Streak tanpa overpay", f"{streak(df)}x")
    c[3].metric("Net hemat kumulatif", rp(-net))
    c[4].metric("Overpay (gross)", rp(over), f"{over / total:.1%} dari belanja", delta_color="off")

    # ---------- 2. Aturan belanja ----------
    st.subheader("Aturan belanja")
    left, right = st.columns(2)
    rules = (df.groupby("Item")
               .agg(Batas_maks=("Acuan", "last"), Terakhir=("Amount", "last"),
                    Termurah=("Amount", "min"), Beli=("Amount", "size"))
               .reset_index())
    rules["Status"] = (rules["Terakhir"] <= rules["Batas_maks"]).map({True: "OK", False: "Mahal"})
    left.markdown("**Batas harga maksimal per item**")
    left.dataframe(rules, hide_index=True, use_container_width=True)

    shop = (df.groupby("Toko")
              .agg(Transaksi=("Amount", "size"), Overpay=("Overpay", "sum"), Hemat=("Hemat", "sum"))
              .reset_index())
    shop["Net_overpay"] = shop["Overpay"] - shop["Hemat"]
    shop = shop.sort_values("Net_overpay")
    fig = px.bar(shop, x="Net_overpay", y="Toko", orientation="h",
                 color=shop["Net_overpay"] > 0,
                 color_discrete_map={True: "#d9534f", False: "#2e9d5b"},
                 labels={"Net_overpay": "Net (+ = boros, - = hemat)"})
    fig.update_layout(showlegend=False, height=350, margin=dict(l=0, r=0, t=10, b=0))
    right.markdown("**Net per toko**")
    right.plotly_chart(fig, use_container_width=True, key=f"shop_{game}")

    # ---------- 3. Tren ----------
    st.subheader("Tren bulanan")
    m = df.groupby("Bulan").agg(Belanja=("Amount", "sum"), Overpay=("Overpay", "sum"),
                                Hemat=("Hemat", "sum")).reset_index()
    m["Net_kumulatif"] = (m["Overpay"] - m["Hemat"]).cumsum()
    t1, t2 = st.columns(2)
    t1.plotly_chart(px.bar(m, x="Bulan", y=["Overpay", "Hemat"], barmode="group",
                           color_discrete_map={"Overpay": "#d9534f", "Hemat": "#2e9d5b"}),
                    use_container_width=True, key=f"bulan_{game}")
    t2.plotly_chart(px.line(m, x="Bulan", y="Net_kumulatif", markers=True,
                            title="Net kumulatif (naik = makin boros)"),
                    use_container_width=True, key=f"kum_{game}")

    # ---------- 4. Promo & overpay terbesar ----------
    p1, p2 = st.columns(2)
    p1.subheader("Pola promo (hemat besar)")
    promo = df[df["Hemat"] >= 0.2 * df["Acuan"]][["Date", "Item", "Toko", "Amount", "Hemat"]]
    p1.dataframe(promo.sort_values("Date", ascending=False), hide_index=True, use_container_width=True)
    p2.subheader("Overpay terbesar")
    top = df.nlargest(10, "Overpay")[["Date", "Item", "Toko", "Amount", "Overpay"]]
    p2.dataframe(top, hide_index=True, use_container_width=True)

    # ---------- 5. Anggaran ----------
    st.subheader("Anggaran")
    a1, a2 = st.columns(2)
    per_bln = df.groupby("Bulan")["Amount"].sum().mean()
    per_patch = df.groupby("Version")["Amount"].sum()
    a1.metric("Rata-rata belanja / bulan", rp(per_bln))
    a1.metric("Rata-rata belanja / patch", rp(per_patch.mean()))
    a2.plotly_chart(px.pie(df, names="Item", values="Amount", hole=0.5),
                    use_container_width=True, key=f"porsi_{game}")

    # ---------- 6. Detail ----------
    with st.expander("Detail transaksi"):
        f = st.multiselect("Toko", sorted(df["Toko"].unique()), key=f"f_{game}")
        st.dataframe(df[df["Toko"].isin(f)] if f else df, hide_index=True, use_container_width=True)


@st.cache_data(ttl=300)
def load_sheet():
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    rows = gc.open_by_key(st.secrets["sheet_id"]).worksheet(TAB).get_all_values()
    # tabel dikenali dari baris header: kolom A = "No.", kolom B = "Date"
    starts = [i for i, r in enumerate(rows)
              if len(r) > 1 and r[0].strip() == "No." and r[1].strip() == "Date"]
    out = {}
    for game, s in zip(GAME_ORDER, starts):
        header = rows[s][:10]
        body = []
        for r in rows[s + 1:]:
            if not r or not r[0].strip().isdigit():  # berhenti di baris total / kosong
                break
            body.append(r[:10])
        out[game] = clean(pd.DataFrame(body, columns=header))
    return out


st.title("Dashboard Top-up Pribadi")
if st.sidebar.button("Refresh data"):
    st.cache_data.clear()

try:
    data = load_sheet()
except Exception as e:
    st.error(f"Gagal membaca Google Sheets: {e}")
    st.stop()

tabs = st.tabs(list(data))
for tab, (game, df) in zip(tabs, data.items()):
    with tab:
        render(df, game)
