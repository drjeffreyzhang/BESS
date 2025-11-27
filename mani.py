import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import numpy_financial as npf

# --- 1. 页面配置 ---
st.set_page_config(page_title="工商业储能 ROI (含需量)", layout="wide")

st.title("🔋 工商业储能 ROI 估算器 (Pro版)")
st.caption("支持：分时套利 + 需量管理 (Peak Shaving)")
st.markdown("---")

# --- 2. 侧边栏配置 ---
st.sidebar.header("⚙️ 参数配置")

# 2.1 电池系统
with st.sidebar.expander("1. 电池储能系统 (BESS)", expanded=True):
    batt_capacity = st.number_input("额定容量 (kWh)", value=215.0, step=10.0)
    batt_power = st.number_input("额定功率 (kW)", value=100.0, step=10.0)
    eff = st.slider("循环效率 (%)", 80, 100, 90) / 100.0
    dod = st.slider("放电深度 DOD (%)", 80, 100, 90) / 100.0
    system_cost_per_kwh = st.number_input("系统单价 (元/kWh)", value=1100.0, step=50.0)
    capex = batt_capacity * system_cost_per_kwh

# 2.2 电价策略
with st.sidebar.expander("2. 电度电价 (元/kWh)", expanded=False):
    price_peak = st.number_input("峰时电价", value=1.15)
    price_flat = st.number_input("平时电价", value=0.75)
    price_valley = st.number_input("谷时电价", value=0.32)
    st.info("🕒 默认时段：\n谷: 0-8点\n峰: 12-14, 18-22点\n平: 其他")

# 2.3 需量电价 (新增核心功能)
with st.sidebar.expander("3. 需量电价 (基本电费)", expanded=True):
    demand_price = st.number_input("需量电价 (元/kW/月)", value=40.0, help="按最大需量计算的基本电费单价")
    st.caption("注：此处假设每月都能成功削减到目标值")

# --- 3. 数据加载与模拟 ---

# 3.1 负载数据
st.subheader("📊 负荷曲线分析")
uploaded_file = st.file_uploader("上传负荷 CSV (选填)", type=["csv"])

hours = np.arange(0, 24, 1)

if uploaded_file:
    try:
        df = pd.read_csv(uploaded_file)
        # 假设第一列是时间，第二列是功率，这里做简单处理
        load_curve = df.iloc[:, 1].values[:24] 
        st.success("已加载自定义负荷数据")
    except:
        st.error("CSV格式有误，使用模拟数据")
        load_curve = np.array([50]*24)
else:
    # 模拟一个带尖峰的工厂负载 (用于演示削峰)
    # 早上8点开工，中午休息，下午有个大尖峰
    base_load = 100
    load_curve = base_load + \
                 50 * np.sin((hours - 8)/3)**2 + \
                 150 * np.exp(-((hours - 15)**2)/4) # 下午3点有个 250kW 的尖峰
    load_curve = np.maximum(load_curve, 20)

# 获取原始最大需量
original_max_demand = np.max(load_curve)

# 3.2 设定削峰目标 (阈值)
col_a, col_b = st.columns([1, 2])
with col_a:
    st.metric("原始最大需量", f"{original_max_demand:.1f} kW")
with col_b:
    # 默认削减到原本的 80% 或者 电池功率能覆盖的范围
    default_threshold = max(0, original_max_demand - batt_power * 0.8)
    threshold = st.slider("📉 设定目标需量 (削峰阈值 kW)", 
                          min_value=0.0, 
                          max_value=float(original_max_demand), 
                          value=float(default_threshold),
                          help="系统将尝试通过放电，把电网取电限制在这个值以下")

# --- 4. 核心算法：削峰 + 套利 ---

# 初始化
sim_data = pd.DataFrame(index=hours)
sim_data['Hour'] = hours
sim_data['Load'] = load_curve
sim_data['Threshold'] = threshold

# 电价函数
def get_price(h):
    if 0 <= h < 8: return price_valley
    elif (12 <= h < 14) or (18 <= h < 22): return price_peak
    else: return price_flat

sim_data['Price'] = sim_data['Hour'].apply(get_price)

# 逐小时模拟
soc = 0.0 # 初始电量
usable_cap = batt_capacity * dod
batt_actions = [] # 电池功率 (+放 -充)

for i in range(24):
    h = sim_data.iloc[i]['Hour']
    load = sim_data.iloc[i]['Load']
    price = sim_data.iloc[i]['Price']
    
    power = 0.0
    
    # ------------------------------------------------
    # 策略优先级 1: 削峰 (Peak Shaving) - 必须动作
    # ------------------------------------------------
    if load > threshold:
        # 需要削减的功率
        needed_shave = load - threshold
        # 电池能提供的最大功率 (受限于额定功率 和 剩余电量)
        max_discharge_by_soc = soc 
        actual_shave = min(needed_shave, batt_power, max_discharge_by_soc)
        
        power = actual_shave # 正数为放电
        soc -= actual_shave # 扣减电量
        
    # ------------------------------------------------
    # 策略优先级 2: 套利 (Arbitrage) - 可选动作
    # 只有在不需要削峰的时候，才考虑价格套利
    # ------------------------------------------------
    else:
        # 谷价 -> 充电
        if price == price_valley:
            # 尽可能充，但不能超过容量限制
            max_charge = min(batt_power, usable_cap - soc)
            power = -max_charge # 负数为充电
            soc += max_charge * eff # 计入充电效率
            
        # 峰价 -> 放电 (但要保留一部分电量给未来的削峰吗？)
        # 简化逻辑：如果是峰价，且不需要削峰，就放电赚钱
        # (高级逻辑需要预测未来负载，这里做简化处理)
        elif price == price_peak:
            # 尽可能放
            max_discharge = min(batt_power, soc)
            power = max_discharge
            soc -= max_discharge
            
        else:
            power = 0 # 平价待机

    batt_actions.append(power)

sim_data['Battery_kW'] = batt_actions
# 计算实际电网取电 = 负载 - 电池放电 (如果是充电，则是 负载 - (-充电) = 负载 + 充电)
sim_data['Grid_kW'] = sim_data['Load'] - sim_data['Battery_kW'] 

# --- 5. 财务计算 ---

# 5.1 需量收益计算
new_max_demand = sim_data['Grid_kW'].max()
demand_reduction = original_max_demand - new_max_demand
# 每月节省 = 削减的功率 * 单价
monthly_demand_savings = demand_reduction * demand_price
annual_demand_savings = monthly_demand_savings * 12

# 5.2 电度收益计算 (套利)
# 收益 = 放电收入 - 充电成本
sim_data['Elec_Cost_Savings'] = sim_data.apply(
    lambda x: (x['Battery_kW'] * x['Price']), axis=1
)
daily_elec_savings = sim_data['Elec_Cost_Savings'].sum()
annual_elec_savings = daily_elec_savings * 330 # 假设运行330天

# 5.3 总收益
total_annual_savings = annual_demand_savings + annual_elec_savings
payback = capex / total_annual_savings if total_annual_savings > 0 else 99

# --- 6. 结果展示 ---

st.subheader("💰 收益分析")

# 指标卡片
c1, c2, c3, c4 = st.columns(4)
c1.metric("1. 需量电费节省 (年)", f"¥ {annual_demand_savings:,.0f}", 
          delta=f"需量降低 {demand_reduction:.1f} kW")
c2.metric("2. 峰谷套利收益 (年)", f"¥ {annual_elec_savings:,.0f}")
c3.metric("🔥 总年化收益", f"¥ {total_annual_savings:,.0f}")
c4.metric("静态回收期", f"{payback:.2f} 年", delta_color="inverse")

# 可视化图表
fig = go.Figure()

# 1. 原始负荷 (灰色填充)
fig.add_trace(go.Scatter(
    x=sim_data['Hour'], y=sim_data['Load'],
    name='原始负荷',
    fill='tozeroy', line=dict(color='gray', width=0), opacity=0.2
))

# 2. 削峰后电网负荷 (粗线)
fig.add_trace(go.Scatter(
    x=sim_data['Hour'], y=sim_data['Grid_kW'],
    name='削峰后电网取电',
    line=dict(color='#2563eb', width=3)
))

# 3. 需量红线 (虚线)
fig.add_trace(go.Scatter(
    x=[0, 23], y=[threshold, threshold],
    name=f'目标需量 ({threshold:.0f}kW)',
    line=dict(color='red', dash='dash', width=2)
))

# 4. 电池动作 (柱状图)
fig.add_trace(go.Bar(
    x=sim_data['Hour'], y=sim_data['Battery_kW'],
    name='电池动作 (+放 -充)',
    marker_color=sim_data['Battery_kW'].apply(lambda x: '#ef4444' if x > 0 else '#10b981'),
    opacity=0.8,
    yaxis='y2'
))

fig.update_layout(
    title="削峰填谷策略模拟 (24小时)",
    xaxis_title="时间 (小时)",
    yaxis=dict(title="功率 (kW)", side="left"),
    yaxis2=dict(title="电池功率", side="right", overlaying="y", showgrid=False),
    legend=dict(orientation="h", y=1.1),
    hovermode="x unified"
)

st.plotly_chart(fig, use_container_width=True)

# 底部数据表
with st.expander("查看详细数据表"):
    st.dataframe(sim_data.style.format("{:.2f}").background_gradient(subset=['Battery_kW'], cmap='RdYlGn_r'))

# 营销钩子
st.sidebar.markdown("---")
st.sidebar.info("💡 **提示：** 需量管理策略非常依赖准确的负荷预测。如需定制**AI预测控制算法**，请联系专家团队。")
