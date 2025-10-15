# path: app.py
import datetime as dt
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Any
import json
import pandas as pd
import streamlit as st

PROGRAM_NAME = "Global Sales Bonus Accelerator – New Product Launch"

@dataclass
class TierRule:
    name: str
    min_value: float
    max_value: Optional[float]
    multiplier: float
    payouts: List[Tuple[int, float]]  # (months_from_sign, fraction)

@dataclass
class ProgramConfig:
    program_months: int
    min_order_usd: float
    min_term_months: int
    confirm_sla_days: int
    base_bonus_by_region_role: Dict[str, Dict[str, float]]
    push_products: List[str]
    fx_by_quarter: Dict[str, Dict[str, float]]  # USD→CCY
    tiers: List[TierRule]

@dataclass
class DealInput:
    product: str
    region: str
    role: str
    currency: str
    annual_order_value: float
    contract_term_months: int
    signing_date: dt.date
    launch_or_announce_date: dt.date
    is_new_customer: bool
    product_type_push: bool
    sfdc_po_id: str

@dataclass
class Payout:
    date: dt.date
    amount_usd: float
    amount_local: float

@dataclass
class DealResult:
    eligible: bool
    reasons: List[str]
    tier: Optional[str]
    multiplier: float
    base_bonus_usd: float
    gross_bonus_usd: float
    fx_rate: float
    currency: str
    payouts: List[Payout]
    checklist: Dict[str, bool]
    audit: Dict[str, Any]

def quarter_key(d: dt.date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"

def end_of_month(d: dt.date) -> dt.date:
    nm = d.replace(day=28) + dt.timedelta(days=4)
    return nm - dt.timedelta(days=nm.day)

def check_eligibility(deal: DealInput, cfg: ProgramConfig) -> Tuple[bool, List[str]]:
    reasons = []
    if not deal.is_new_customer: reasons.append("Customer is not new.")
    if not deal.product_type_push: reasons.append("Product is not a designated push product.")
    if deal.annual_order_value < cfg.min_order_usd: reasons.append(f"Order value below ${cfg.min_order_usd:,.0f} USD minimum.")
    if deal.contract_term_months < cfg.min_term_months: reasons.append(f"Contract term below {cfg.min_term_months} months.")
    window_end = deal.launch_or_announce_date + dt.timedelta(days=round(cfg.program_months * 30.4375))
    if not (deal.launch_or_announce_date <= deal.signing_date <= window_end):
        reasons.append("Signing date outside program window (18 months post launch/announce).")
    return (len(reasons) == 0), reasons

def determine_tier(order_value_usd: float, tiers: List[TierRule]) -> TierRule:
    for t in tiers:
        if (order_value_usd >= t.min_value) and (t.max_value is None or order_value_usd <= t.max_value):
            return t
    return sorted(tiers, key=lambda x: x.min_value)[0]

def get_base_bonus(region: str, role: str, cfg: ProgramConfig) -> float:
    return float(cfg.base_bonus_by_region_role.get(region, {}).get(role, 0.0))

def get_fx_rate(ccy: str, signing_date: dt.date, cfg: ProgramConfig) -> float:
    if ccy.upper() == "USD": return 1.0
    qk = quarter_key(signing_date)
    return float(cfg.fx_by_quarter.get(qk, {}).get(ccy.upper(), 1.0))

def build_payout_schedule(tier: TierRule, signing_date: dt.date, gross_bonus_usd: float) -> List[Tuple[dt.date, float]]:
    schedule = []
    for months, frac in tier.payouts:
        when = end_of_month(signing_date + dt.timedelta(days=round(months * 30.4375)))
        schedule.append((when, round(gross_bonus_usd * frac, 2)))
    return schedule

def compute_deal(deal: DealInput, cfg: ProgramConfig) -> DealResult:
    eligible, reasons = check_eligibility(deal, cfg)
    tier = determine_tier(deal.annual_order_value, cfg.tiers)
    base_bonus = get_base_bonus(deal.region, deal.role, cfg)
    multiplier = tier.multiplier if eligible else 0.0
    gross_bonus_usd = round(base_bonus * multiplier, 2)
    fx = get_fx_rate(deal.currency, deal.signing_date, cfg)
    payouts: List[Payout] = []
    if eligible:
        for when, usd in build_payout_schedule(tier, deal.signing_date, gross_bonus_usd):
            payouts.append(Payout(when, usd, round(usd * fx, 2)))
    if fx == 1.0 and deal.currency.upper() != "USD":
        reasons.append(f"No FX rate found for {deal.currency} in {quarter_key(deal.signing_date)}; using 1.0.")
    checklist = {
        "SFDC_PO_Submitted": bool(deal.sfdc_po_id.strip()),
        "SalesOps_Validated": False,
        "ProductMgmt_Validated": False,
        "Bonus_Confirmed_Within_30_Days": False,
    }
    return DealResult(
        eligible=eligible,
        reasons=reasons,
        tier=tier.name if eligible else None,
        multiplier=multiplier,
        base_bonus_usd=base_bonus,
        gross_bonus_usd=gross_bonus_usd,
        fx_rate=fx,
        currency=deal.currency.upper(),
        payouts=payouts,
        checklist=checklist,
        audit={
            "program": PROGRAM_NAME,
            "computed_at": dt.datetime.utcnow().isoformat() + "Z",
            "input": asdict(deal),
            "config_version": "session",
            "tier_rule": asdict(tier),
        },
    )

def default_config() -> ProgramConfig:
    return ProgramConfig(
        program_months=18,
        min_order_usd=100_000.0,
        min_term_months=12,
        confirm_sla_days=30,
        base_bonus_by_region_role={
            "NA": {"AE": 5000.0, "SE": 5000.0, "AM": 6000.0},
            "EMEA": {"AE": 4500.0, "SE": 4500.0, "AM": 5500.0},
            "APAC": {"AE": 4000.0, "SE": 4000.0, "AM": 5000.0},
        },
        push_products=["NovaEdge", "QuantumSync", "DataPulse"],
        fx_by_quarter={
            "2025Q3": {"EUR": 0.92, "GBP": 0.78, "JPY": 140.0, "INR": 83.0},
            "2025Q4": {"EUR": 0.91, "GBP": 0.79, "JPY": 141.0, "INR": 83.5},
        },
        tiers=[
            TierRule("Tier 1", 100_000.0, 249_999.999, 1.0, [(0, 1.0)]),
            TierRule("Tier 2", 250_000.0, 499_999.999, 1.5, [(0, 0.5), (6, 0.5)]),
            TierRule("Tier 3", 500_000.0, None, 2.0, [(0, 0.5), (12, 0.5)]),
        ],
    )

st.set_page_config(
    page_title="Global Sales Bonus Accelerator Dashboard",
    page_icon="logo.png", 
    layout="wide",
)

if "config" not in st.session_state:
    st.session_state.config = default_config()
if "recent" not in st.session_state:
    st.session_state.recent = []

cfg: ProgramConfig = st.session_state.config

LOGO_PATH = "logo.png"

c1, c2 = st.columns([1, 14])
with c1:
    st.image(LOGO_PATH, width=48) 
with c2:
    st.markdown(
        "<h1 style='margin:0;'>Global Sales Bonus Accelerator Dashboard</h1>",
        unsafe_allow_html=True,
    )
st.caption("Create a Better Tomorrow....")


tab_calc, tab_config, tab_recent = st.tabs(["Calculator", "Configuration", "Recent Results"])

with tab_config:
    st.subheader("Push Products")
    products_csv = st.text_area(
        "Designated push products (comma-separated)",
        value=",".join(cfg.push_products),
        help="Simple editor to avoid custom components."
    )
    push_products = [p.strip() for p in products_csv.split(",") if p.strip()]

    st.subheader("Base Bonus by Region & Role (USD, Tier-1 base)")
    regions = sorted(cfg.base_bonus_by_region_role.keys())
    roles = sorted({r for m in cfg.base_bonus_by_region_role.values() for r in m.keys()})
    rows = []
    for region in regions:
        row = {"Region": region}
        for role in roles:
            row[role] = cfg.base_bonus_by_region_role.get(region, {}).get(role, 0.0)
        rows.append(row)
    df_bb = pd.DataFrame(rows)
    edited = st.data_editor(df_bb, num_rows="dynamic", use_container_width=True)
    new_bb: Dict[str, Dict[str, float]] = {}
    for _, r in edited.iterrows():
        region = r["Region"]
        new_bb[region] = {}
        for role in roles:
            new_bb[region][role] = float(r.get(role, 0.0) or 0.0)

    st.subheader("Quarterly FX (USD→CCY)")
    fx_rows = []
    for qk, mp in cfg.fx_by_quarter.items():
        for ccy, rate in mp.items():
            fx_rows.append({"Quarter": qk, "Currency": ccy, "Rate": rate})
    df_fx = pd.DataFrame(fx_rows or [{"Quarter": quarter_key(dt.date.today()), "Currency": "EUR", "Rate": 0.9}])
    edited_fx = st.data_editor(df_fx, num_rows="dynamic", use_container_width=True)
    new_fx: Dict[str, Dict[str, float]] = {}
    for _, r in edited_fx.iterrows():
        q = str(r["Quarter"]).strip()
        c = str(r["Currency"]).upper().strip()
        if q and c:
            new_fx.setdefault(q, {})[c] = float(r["Rate"])

    c1, c2, c3 = st.columns(3)
    cfg.program_months = int(c1.number_input("Program Duration (months)", 1, 60, cfg.program_months))
    cfg.min_order_usd = float(c2.number_input("Min Annual Order (USD)", 0, 10_000_000, int(cfg.min_order_usd), step=50_000))
    cfg.min_term_months = int(c3.number_input("Min Contract Term (months)", 1, 60, cfg.min_term_months))

    cfg.push_products = push_products
    cfg.base_bonus_by_region_role = new_bb
    cfg.fx_by_quarter = new_fx

with tab_calc:
    st.subheader("Deal Calculator")
    c1, c2, c3, c4 = st.columns(4)
    product = c1.selectbox("Product", sorted(cfg.push_products + ["Other"]))
    region = c2.selectbox("Region", sorted(cfg.base_bonus_by_region_role.keys()))
    role = c3.selectbox("Role", sorted({r for m in cfg.base_bonus_by_region_role.values() for r in m.keys()}))
    currency = c4.selectbox("Currency", ["USD", "EUR", "GBP", "JPY", "INR", "CNY", "AUD"])

    d1, d2, d3 = st.columns(3)
    aov = d1.number_input("Annual Order Value (USD)", 0, 50_000_000, 100_000, step=50_000)
    term = d2.number_input("Contract Term (months)", 1, 60, 12)
    signing_date = d3.date_input("Contract Signing Date", value=dt.date.today())

    d4, d5, d6 = st.columns(3)
    launch_date = d4.date_input("Product Launch/Announcement Date", value=dt.date.today() - dt.timedelta(days=30))
    is_new = d5.toggle("New Customer", value=True)
    push_flag = d6.toggle("Push Product", value=(product in cfg.push_products))

    sfdc_po_id = st.text_input("SFDC PO / Opportunity ID", placeholder="006xx00000ABC123")

    if st.button("Compute Bonus"):
        deal = DealInput(
            product=product, region=region, role=role, currency=currency,
            annual_order_value=float(aov), contract_term_months=int(term),
            signing_date=signing_date, launch_or_announce_date=launch_date,
            is_new_customer=is_new, product_type_push=push_flag, sfdc_po_id=sfdc_po_id
        )
        res = compute_deal(deal, cfg)

        st.markdown(f"### {'✅ Eligible' if res.eligible else '⛔ Ineligible'}")
        if res.reasons: st.warning(" / ".join(res.reasons))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tier", res.tier or "—")
        m2.metric("Multiplier", f"{res.multiplier:.1f}×")
        m3.metric("Base Bonus (USD)", f"${res.base_bonus_usd:,.2f}")
        m4.metric("Gross Bonus (USD)", f"${res.gross_bonus_usd:,.2f}")
        st.caption(f"FX ({res.currency} per USD) for {quarter_key(signing_date)}: {res.fx_rate:.4f}")

        if res.payouts:
            dfp = pd.DataFrame(
                [{"Payout Date": p.date, "Amount (USD)": p.amount_usd, f"Amount ({res.currency})": p.amount_local} for p in res.payouts]
            )
            st.dataframe(dfp, use_container_width=True)
            total_local = sum(p.amount_local for p in res.payouts)
            st.metric(f"Total ({res.currency})", f"{total_local:,.2f}")
        else:
            st.info("No payouts scheduled.")

        st.subheader("Validation Checklist")
        cols = st.columns(4)
        keys = list(res.checklist.keys())
        for i, k in enumerate(keys):
            res.checklist[k] = cols[i].checkbox(k.replace("_", " "), value=res.checklist[k])

        record = {
            **asdict(deal),
            "eligible": res.eligible,
            "tier": res.tier,
            "multiplier": res.multiplier,
            "base_bonus_usd": res.base_bonus_usd,
            "gross_bonus_usd": res.gross_bonus_usd,
            "fx": res.fx_rate,
            "currency": res.currency,
            "payouts": [{"date": p.date.isoformat(), "usd": p.amount_usd, "local": p.amount_local} for p in res.payouts],
            "checklist": res.checklist,
            "audit": res.audit,
        }
        st.session_state.recent.insert(0, record)
        st.session_state.recent = st.session_state.recent[:25]

        colx1, colx2 = st.columns(2)
        with colx1:
            st.download_button(
                "Download Result (JSON)",
                data=json.dumps(record, indent=2, default=str),
                file_name=f"bonus_{sfdc_po_id or 'deal'}.json",
                mime="application/json",
            )
        with colx2:
            df_export = pd.DataFrame([{
                "SFDC_PO_ID": sfdc_po_id, "Product": product, "Region": region, "Role": role,
                "Currency": res.currency, "Eligible": res.eligible, "Tier": res.tier,
                "Multiplier": res.multiplier, "BaseBonusUSD": res.base_bonus_usd,
                "GrossBonusUSD": res.gross_bonus_usd, "SigningDate": signing_date.isoformat(),
                "LaunchDate": launch_date.isoformat(),
            }])
            st.download_button(
                "Download Summary (CSV)",
                data=df_export.to_csv(index=False),
                file_name=f"bonus_{sfdc_po_id or 'deal'}.csv",
                mime="text/csv",
            )

with tab_recent:
    st.subheader("Recent Calculations (session)")
    if st.session_state.recent:
        df_recent = pd.DataFrame(st.session_state.recent)
        st.dataframe(df_recent, use_container_width=True, height=400)
    else:
        st.info("No results yet.")

st.divider()
st.caption("Notes: Regional taxes/compliance apply. Local leaders may adjust base bonuses. Currency conversion handled by quarterly FX.")
