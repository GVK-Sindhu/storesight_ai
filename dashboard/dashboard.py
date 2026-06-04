
import streamlit as st
import pandas as pd
import requests
import time
import plotly.express as px
import plotly.graph_objects as go
import os

# ----------------- INSTRUMENTATION SETUP -----------------
if "session_start_time" not in st.session_state:
    st.session_state.session_start_time = time.perf_counter()
if "run_count" not in st.session_state:
    st.session_state.run_count = 0
st.session_state.run_count += 1

run_start_time = time.perf_counter()
# ---------------------------------------------------------

# Configure page layout
st.set_page_config(
    page_title="StoreSight AI - Retail Intelligence Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# API Endpoint configuration
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

# Styling improvements for premium retail aesthetic with dark mode compatibility
st.markdown("""
<style>
    .main { background-color: var(--background-color, #f8f9fa); }
    .stMetric { 
        background-color: var(--secondary-background-color, #ffffff) !important; 
        padding: 15px; 
        border-radius: 8px; 
        box-shadow: 0 2px 4px rgba(0,0,0,0.05); 
        border: 1px solid var(--border-color, #e2e8f0);
    }
    .stMetric * {
        color: var(--text-color, #1e293b) !important;
    }
    h1, h2, h3 { color: var(--text-color, #1e293b); font-family: 'Inter', sans-serif; }
    .stAlert { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# Title & Refresh controls
st.title("📊 StoreSight AI — Retail Intelligence Dashboard")
st.subheader("Real-Time Store Performance & Customer Behavior Analytics")

# Sidebar configurations
st.sidebar.header("Configuration")

# Fetch unique stores from the API
try:
    store_list = requests.get(f"{API_URL}/stores", timeout=2).json()
except Exception:
    store_list = ["ST1008"]

store_id = st.sidebar.selectbox("Select Store ID", options=store_list, index=0)

# Sidebar metadata
if store_id == "ST1008":
    st.sidebar.write("**Location:** Brigade Road, Bangalore")
elif store_id == "ST1076":
    st.sidebar.write("**Location:** Kurla Mall, Mumbai")
else:
    st.sidebar.write(f"**Location:** Retail Outlet ({store_id})")

refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", min_value=2, max_value=30, value=5)
auto_refresh = st.sidebar.checkbox("Enable Auto-Refresh", value=True)

# Health Status bar
try:
    health_resp = requests.get(f"{API_URL}/health", timeout=2)
    if health_resp.status_code == 200:
        health_data = health_resp.json()
        status_color = "green" if health_data["status"] == "UP" else "red"
        feed_status = health_data.get("feed_status", "UNKNOWN")
        st.sidebar.markdown(f"**API Status:** :green[UP]")
        st.sidebar.markdown(f"**Camera Ingestion Feed:** :{status_color}[{feed_status}]")
        if health_data.get("stale_feed_warning"):
            st.sidebar.warning("⚠️ Warning: Camera Feed lag exceeds 10 minutes!")
    else:
        st.sidebar.markdown("**API Status:** :red[OFFLINE]")
except Exception:
    st.sidebar.markdown("**API Status:** :red[DISCONNECTED]")

# Detect fragment support and fall back if not available
HAS_FRAGMENT = hasattr(st, "fragment") or hasattr(st, "experimental_fragment")

if hasattr(st, "fragment"):
    fragment_decorator = st.fragment
elif hasattr(st, "experimental_fragment"):
    fragment_decorator = st.experimental_fragment
else:
    # Fallback to no-op decorator if fragments aren't supported on old Streamlit
    def fragment_decorator(run_every=None):
        def decorator(func):
            return func
        return decorator

# Main content loader
def load_data(store_id):
    api_timings = {}
    try:
        t_api_start = time.perf_counter()
        
        t0 = time.perf_counter()
        metrics = requests.get(f"{API_URL}/stores/{store_id}/metrics", timeout=2).json()
        api_timings["metrics"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        funnel = requests.get(f"{API_URL}/stores/{store_id}/funnel", timeout=2).json()
        api_timings["funnel"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        heatmap = requests.get(f"{API_URL}/stores/{store_id}/heatmap", timeout=2).json()
        api_timings["heatmap"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        anomalies = requests.get(f"{API_URL}/stores/{store_id}/anomalies", timeout=2).json()
        api_timings["anomalies"] = (time.perf_counter() - t0) * 1000

        try:
            t0 = time.perf_counter()
            demographics = requests.get(f"{API_URL}/stores/{store_id}/demographics", timeout=2).json()
            api_timings["demographics"] = (time.perf_counter() - t0) * 1000
        except Exception:
            demographics = None
            api_timings["demographics"] = 0.0
            
        api_timings["total_api"] = (time.perf_counter() - t_api_start) * 1000
        return metrics, funnel, heatmap, anomalies, demographics, api_timings
    except Exception as e:
        st.error(f"Failed to fetch data from API: {e}. Is the backend API running?")
        return None, None, None, None, None, {}

# Wrap dashboard contents in a fragment to refresh it in isolation without full-page fading
@fragment_decorator(run_every=refresh_rate if auto_refresh else None)
def render_dashboard_content(store_id):
    frag_start = time.perf_counter()
    plotly_durations = []

    metrics, funnel, heatmap, anomalies, demographics, api_timings = load_data(store_id)
    t_after_load = time.perf_counter()

    if metrics and funnel and heatmap is not None:
        # 1. Main KPIs row
        t_kpi_start = time.perf_counter()
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(label="Unique Visitors Today", value=metrics["unique_visitors"], delta=None)
        with col2:
            st.metric(label="Conversion Rate", value=f"{metrics['conversion_rate']}%", delta=None)
        with col3:
            st.metric(label="Billing Queue Depth", value=metrics["queue_depth"], delta=None)
        with col4:
            st.metric(label="Queue Abandonment Rate", value=f"{metrics['abandonment_rate']}%", delta=None)
        t_kpi_end = time.perf_counter()
            
        st.markdown("---")

        # 2. Charts and Funnel row
        t_funnel_start = time.perf_counter()
        col_left, col_right = st.columns([3, 2])
        
        with col_left:
            st.subheader("Conversion Funnel Analysis")
            # Plotly horizontal bar chart for funnel
            stages = [s["stage"] for s in funnel["stages"]]
            counts = [s["count"] for s in funnel["stages"]]
            
            fig = go.Figure(go.Funnel(
                y=stages,
                x=counts,
                textinfo="value+percent initial",
                connector={"fillcolor": "#e2e8f0"},
                marker={"color": ["#3b82f6", "#60a5fa", "#93c5fd", "#bfdbfe"]}
            ))
            fig.update_layout(
                margin=dict(l=100, r=20, t=20, b=20),
                height=320,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)"
            )
            t_plotly_funnel_start = time.perf_counter()
            st.plotly_chart(fig, use_container_width=True)
            plotly_durations.append(("funnel", (time.perf_counter() - t_plotly_funnel_start) * 1000))

        with col_right:
            st.subheader("Active Operational Anomalies")
            if anomalies:
                for anom in anomalies:
                    severity = anom["severity"]
                    if severity == "CRITICAL":
                        st.error(f"🚨 **CRITICAL: {anom['anomaly_type']}**\n\n{anom['message']}\n\n**Action Required:** {anom['suggested_action']}")
                    elif severity == "WARN":
                        st.warning(f"⚠️ **WARNING: {anom['anomaly_type']}**\n\n{anom['message']}\n\n**Action Required:** {anom['suggested_action']}")
                    else:
                        st.info(f"ℹ️ **INFO: {anom['anomaly_type']}**\n\n{anom['message']}\n\n**Action:** {anom['suggested_action']}")
            else:
                st.success("✅ No operational anomalies detected. All store operations are normal.")
        t_funnel_end = time.perf_counter()

        # 3. Heatmap and Dwell Times row
        st.markdown("---")
        t_heatmap_start = time.perf_counter()
        col_h1, col_h2 = st.columns([3, 2])
        
        with col_h1:
            st.subheader("Store Zone Spatial Heatmap")
            zones = heatmap["zones"]
            if zones:
                # Flatten zone data for rendering
                zone_list = []
                for name, details in zones.items():
                    zone_list.append({
                        "Zone ID": name,
                        "Visits": details["frequency"],
                        "Avg Dwell (sec)": details["avg_dwell_sec"],
                        "Normalized Traffic (0-100)": details["norm_frequency"],
                        "Normalized Dwell (0-100)": details["norm_dwell"]
                    })
                df_zones = pd.DataFrame(zone_list)
                
                # Draw normalized frequency heatmap chart
                fig_heat = px.bar(
                    df_zones,
                    x="Normalized Traffic (0-100)",
                    y="Zone ID",
                    orientation="h",
                    color="Avg Dwell (sec)",
                    color_continuous_scale="Viridis",
                    labels={"Normalized Traffic (0-100)": "Traffic Index (0-100)"},
                    title="Zone Traffic & Dwell Intensity Map"
                )
                fig_heat.update_layout(height=300, margin=dict(t=30, b=10, l=10, r=10))
                
                t_plotly_heatmap_start = time.perf_counter()
                st.plotly_chart(fig_heat, use_container_width=True)
                plotly_durations.append(("heatmap", (time.perf_counter() - t_plotly_heatmap_start) * 1000))
            else:
                st.info("No zone event data available yet.")

        with col_h2:
            st.subheader("Layout Zone Performance")
            if zones:
                st.dataframe(
                    df_zones[["Zone ID", "Visits", "Avg Dwell (sec)"]].set_index("Zone ID"),
                    use_container_width=True
                )
                confidence_str = "✅ HIGH (>= 20 sessions)" if heatmap["data_confidence"] else "⚠️ LOW (< 20 sessions)"
                st.write(f"**Data Confidence Rating:** {confidence_str}")
            else:
                st.info("No performance stats available.")
        t_heatmap_end = time.perf_counter()

        # 4. Customer Demographics Section (New Schema)
        t_demographics_start = time.perf_counter()
        if demographics and demographics.get("total_demographics_sampled", 0) > 0:
            st.markdown("---")
            st.subheader("👥 Customer Demographics & Shopping Group Dynamics")
            
            d_col1, d_col2, d_col3 = st.columns(3)
            
            with d_col1:
                st.markdown("<div style='text-align: center;'><strong>Gender Distribution</strong></div>", unsafe_allow_html=True)
                g_data = demographics["genders"]
                if sum(g_data.values()) > 0:
                    fig_gender = px.pie(
                        names=list(g_data.keys()),
                        values=list(g_data.values()),
                        color_discrete_sequence=["#ec4899", "#3b82f6"],
                        hole=0.4
                    )
                    fig_gender.update_layout(height=240, margin=dict(t=10, b=10, l=10, r=10))
                    
                    t_plotly_gender_start = time.perf_counter()
                    st.plotly_chart(fig_gender, use_container_width=True)
                    plotly_durations.append(("gender", (time.perf_counter() - t_plotly_gender_start) * 1000))
                else:
                    st.info("No gender demographics data captured.")
                    
            with d_col2:
                st.markdown("<div style='text-align: center;'><strong>Age Bracket Distribution</strong></div>", unsafe_allow_html=True)
                a_data = demographics["age_buckets"]
                if a_data:
                    # Sort keys chronologically
                    age_order = ["Under 18", "18-24", "25-34", "35-44", "45+"]
                    sorted_keys = [k for k in age_order if k in a_data] + [k for k in a_data if k not in age_order]
                    sorted_vals = [a_data[k] for k in sorted_keys]
                    
                    fig_age = px.bar(
                        x=sorted_keys,
                        y=sorted_vals,
                        labels={"x": "Age Bracket", "y": "Visitors Count"},
                        color_discrete_sequence=["#10b981"]
                    )
                    fig_age.update_layout(height=240, margin=dict(t=10, b=10, l=10, r=10))
                    
                    t_plotly_age_start = time.perf_counter()
                    st.plotly_chart(fig_age, use_container_width=True)
                    plotly_durations.append(("age", (time.perf_counter() - t_plotly_age_start) * 1000))
                else:
                    st.info("No age demographics data captured.")
                    
            with d_col3:
                st.markdown("<div style='text-align: center;'><strong>Group shopping dynamics</strong></div>", unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div style='background-color: var(--secondary-background-color, #ffffff); border: 1px solid var(--border-color, #e2e8f0); padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; margin-top: 15px;'>
                        <span style='color: var(--text-color, #6b7280); opacity: 0.8; font-size: 14px; font-weight: bold;'>AVERAGE GROUP SIZE</span>
                        <h1 style='color: #6366f1; font-size: 52px; margin: 10px 0;'>{demographics['avg_group_size']}</h1>
                        <p style='color: var(--text-color, #9ca3af); opacity: 0.6; margin: 0;'>customers per checkout group</p>
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
        t_demographics_end = time.perf_counter()

        # Print all timings
        frag_end = time.perf_counter()
        total_frag_ms = (frag_end - frag_start) * 1000
        
        # Calculate time relative to the session start (opening localhost:8501)
        session_elapsed_kpis = (t_kpi_end - st.session_state.session_start_time) * 1000
        session_elapsed_funnel = (t_funnel_end - st.session_state.session_start_time) * 1000
        session_elapsed_heatmap = (t_heatmap_end - st.session_state.session_start_time) * 1000
        session_elapsed_demographics = (t_demographics_end - st.session_state.session_start_time) * 1000
        session_elapsed_total = (frag_end - st.session_state.session_start_time) * 1000
        
        # Output timings dictionary to st.session_state to show in sidebar
        run_timings = {
            "run_count": st.session_state.run_count,
            "total_frag_ms": total_frag_ms,
            "api_timings": api_timings,
            "plotly_durations": plotly_durations,
            "kpis_render_time_ms": (t_kpi_end - t_kpi_start) * 1000,
            "funnel_render_time_ms": (t_funnel_end - t_funnel_start) * 1000,
            "heatmap_render_time_ms": (t_heatmap_end - t_heatmap_start) * 1000,
            "demographics_render_time_ms": (t_demographics_end - t_demographics_start) * 1000,
            "session_elapsed_kpis": session_elapsed_kpis,
            "session_elapsed_funnel": session_elapsed_funnel,
            "session_elapsed_heatmap": session_elapsed_heatmap,
            "session_elapsed_demographics": session_elapsed_demographics,
            "session_elapsed_total": session_elapsed_total,
        }
        st.session_state.last_run_timings = run_timings
        
        # Log to server console
        print(f"\n--- PERF_MONITOR (Run {st.session_state.run_count}) ---")
        print(f"Total Fragment Run Time: {total_frag_ms:.2f} ms")
        print(f"API Fetch Times: {api_timings}")
        print(f"Plotly Render Times: {dict(plotly_durations)}")
        print(f"Component Prep + Plotly Render Times (Individual):")
        print(f"  KPIs: {run_timings['kpis_render_time_ms']:.2f} ms")
        print(f"  Funnel: {run_timings['funnel_render_time_ms']:.2f} ms")
        print(f"  Heatmap: {run_timings['heatmap_render_time_ms']:.2f} ms")
        print(f"  Demographics: {run_timings['demographics_render_time_ms']:.2f} ms")
        print(f"Time from Session Init (Page Opened) to Render:")
        print(f"  Until KPIs: {session_elapsed_kpis:.2f} ms")
        print(f"  Until Funnel: {session_elapsed_funnel:.2f} ms")
        print(f"  Until Heatmap: {session_elapsed_heatmap:.2f} ms")
        print(f"  Until Demographics: {session_elapsed_demographics:.2f} ms")
        print(f"  Until Usable (Total): {session_elapsed_total:.2f} ms")
        print(f"-----------------------------------------\n")

# Render the fragment-wrapped dashboard contents
render_dashboard_content(store_id)

# Fallback auto-refresh loop (only executed if Streamlit version doesn't support fragments)
if auto_refresh and not HAS_FRAGMENT:
    time.sleep(refresh_rate)
    st.rerun()

# Render the sidebar performance audit if available
if "last_run_timings" in st.session_state:
    t = st.session_state.last_run_timings
    st.sidebar.markdown("---")
    st.sidebar.subheader("⏱️ Real-Time Performance Audit")
    st.sidebar.write(f"**Run Number:** {t['run_count']}")
    st.sidebar.write(f"**Total Script Execution:** {t['total_frag_ms']:.1f} ms")
    
    st.sidebar.markdown("**API Calls Latency:**")
    for k_val, v_val in t['api_timings'].items():
        if k_val != 'total_api':
            st.sidebar.write(f"- `{k_val}`: {v_val:.1f} ms")
    st.sidebar.write(f"- **Total API Fetch**: {t['api_timings'].get('total_api', 0.0):.1f} ms")
    
    st.sidebar.markdown("**Component Prep & Plotly:**")
    st.sidebar.write(f"- **KPIs Row**: {t['kpis_render_time_ms']:.1f} ms")
    st.sidebar.write(f"- **Funnel Row**: {t['funnel_render_time_ms']:.1f} ms")
    st.sidebar.write(f"- **Heatmap Row**: {t['heatmap_render_time_ms']:.1f} ms")
    st.sidebar.write(f"- **Demographics**: {t['demographics_render_time_ms']:.1f} ms")
    
    st.sidebar.markdown("**Cumulative Time since Load:**")
    st.sidebar.write(f"- **Time to KPIs**: {t['session_elapsed_kpis']:.1f} ms")
    st.sidebar.write(f"- **Time to Funnel**: {t['session_elapsed_funnel']:.1f} ms")
    st.sidebar.write(f"- **Time to Heatmap**: {t['session_elapsed_heatmap']:.1f} ms")
    st.sidebar.write(f"- **Time to Demographics**: {t['session_elapsed_demographics']:.1f} ms")
    st.sidebar.write(f"- **Fully Usable Dashboard**: {t['session_elapsed_total']:.1f} ms")
