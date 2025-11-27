import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import numpy_financial as npf

# --- 1. 页面基础配置 ---
st.set_page_config(page_title="工商业储能 ROI 计算器", layout="wide")

st.title("🔋 工商业储能项目 ROI 估算器")
st.markdown("---")

# --- 2. 侧边栏：输入参数 ---
st.sidebar.header("⚙️ 系统参数配置")

# 2.1 电池参数
st.sidebar.subheader("1. 电池储能系统 (BESS)")
batt_capacity = st.sidebar.number_input("额定容量 (kWh)", value=200.0, step=10.0)
batt_power = st.sidebar.number_input("额定功率 (kW)", value=100.0, step=10.0)
eff = st.sidebar.slider("充放电循环效率 (%)", 80, 100, 90) / 100.0
dod = st.sidebar.slider("放电深度 DOD (%)", 80, 100, 90) / 100.0
system_cost_per_kwh = st.sidebar.number_input("系统单价 (元/kWh)", value=1200.0, step=50.0)

# 计算总投资 (CAPEX)
capex = batt_capacity * system_cost_per_kwh

# 2.2 电价策略 (简化版：定义时段电价)
st.sidebar.subheader("2. 电价策略 (元/kWh)")
# 默认值：模拟典型的峰谷价差
price_peak = st.sidebar.number_input("峰时电价 (Peak)", value=1.2)
price_flat = st.sidebar.number_input("平时电价 (Flat)", value=0.7)
price_valley = st.sidebar.number_input("谷时电价 (Valley)", value=0.3)

# 简单的时段定义 (Demo用途，实际可做成更复杂的交互)
st.sidebar.markdown("📅 **时段设置 (默认)**")
st.sidebar.info(
    """
    - 谷时 (充电): 00:00 - 08:00
    - 平时 (待机): 08:00 - 12:00, 14:00 - 18:00
    - 峰时 (放电): 12:00 - 14:00, 18:00 - 22:00
    """
)

# --- 3. 主界面：数据加载与模拟 ---

# 3.1 生成或上传负载数据
st.subheader("📊 负荷曲线与策略模拟")

uploaded_file = st.file_uploader("上传负荷数据 CSV (格式：时间, 功率)", type=["csv"])

# 构建 24小时 时间轴
hours = np.arange(0, 24, 1)

if uploaded_file is not None:
    # 这里预留读取 CSV 的逻辑，为演示方便，我们主要通过模拟数据
    df = pd.read_csv(uploaded_file)
    st.success("文件上传成功！(演示版将继续使用模拟逻辑进行计算)")
else:
    # 生成模拟的工厂负载曲线 (双峰形态)
    load_curve = 50 + 30 * np.sin((hours - 8) / 4) + 20 * np.sin((hours - 16) / 2)
    # 保证负载不为负数
    load_curve = np.maximum(load_curve, 10)

# 3.2 核心算法：构建 24小时 策略表
# 创建一个 DataFrame 来存储每小时的状态
sim_data = pd.DataFrame(index=hours)
sim_data['Hour'] = hours
sim_data['Load_kW'] = load_curve if uploaded_file is None else [100]*24 # 简化处理

# 定义每小时的电价
def get_price(h):
    # 谷时：0-8点
    if 0 <= h < 8: return price_valley
    # 峰时：12-14点 或 18-22点
    elif (12 <= h < 14) or (18 <= h < 22): return price_peak
    # 其他为平时
    else: return price_flat

sim_data['Price'] = sim_data['Hour'].apply(get_price)

# 模拟充放电逻辑
# 规则：谷时充满，峰时放空。
# 注意：这里是简化的策略，实际策略会更复杂(需量控制等)
actions = []
battery_flow = [] # 正数为放电，负数为充电

current_soc = 0.0 # 初始电量
usable_capacity = batt_capacity * dod # 可用容量

for i in range(24):
    h = sim_data.iloc[i]['Hour']
    p = sim_data.iloc[i]['Price']
    
    flow = 0
    
    # 策略逻辑
    if p == price_valley: 
        # 充电逻辑：尽可能充满
        charge_energy = min(batt_power, usable_capacity - current_soc)
        flow = -charge_energy # 充电为负
        # 计入效率损耗 (充进去 10度，实际电池里增加 10 * sqrt(eff))
        # 为简化，我们假设损耗发生在充电侧
        current_soc += charge_energy * eff 
        
    elif p == price_peak:
        # 放电逻辑：尽可能放空
        discharge_energy = min(batt_power, current_soc)
        flow = discharge_energy
        current_soc -= discharge_energy
    
    else:
        # 平时：待机
        flow = 0
        
    battery_flow.append(flow)

sim_data['Battery_kW'] = battery_flow

# --- 4. 财务计算 ---

# 计算每日收益
# 收益 = 放电电量 * 电价 (收入) - 充电电量 * 电价 (成本)
# 注意：Battery_kW 正数为放，负数为充
sim_data['Cash_Flow'] = sim_data.apply(
    lambda x: (x['Battery_kW'] * x['Price']) if x['Battery_kW'] > 0 else (x['Battery_kW'] * x['Price']), 
    axis=1
)

daily_profit = sim_data['Cash_Flow'].sum()
days_per_year = 330 # 假设每年运行 330 天
annual_profit = daily_profit * days_per_year

# 回收期
payback_period = capex / annual_profit if annual_profit > 0 else 99.9

# IRR 计算 (简化版：假设运行10年)
cash_flows = [-capex] + [annual_profit] * 10
irr = npf.irr(cash_flows) * 100

# --- 5. 结果展示 ---

# 5.1 关键指标卡片
c1, c2, c3, c4 = st.columns(4)
c1.metric("项目总投资 (CAPEX)", f"¥ {capex:,.0f}")
c2.metric("预估年收益", f"¥ {annual_profit:,.0f}", delta_color="normal")
c3.metric("静态回收期", f"{payback_period:.2f} 年", delta_color="inverse")
c4.metric("IRR (10年期)", f"{irr:.2f} %")

# 5.2 图表可视化 (Plotly)
fig = go.Figure()

# 轴1：功率 (负载 & 电池)
fig.add_trace(go.Scatter(
    x=sim_data['Hour'], y=sim_data['Load_kW'],
    name='工厂原有负荷 (kW)',
    fill='tozeroy', line=dict(color='gray', width=1), opacity=0.3
))

fig.add_trace(go.Bar(
    x=sim_data['Hour'], y=sim_data['Battery_kW'],
    name='电池充放电功率 (kW)',
    marker_color=sim_data['Battery_kW'].apply(lambda x: '#ef553b' if x > 0 else '#00cc96')
))

# 轴2：电价
fig.add_trace(go.Scatter(
    x=sim_data['Hour'], y=sim_data['Price'],
    name='电价 (元/kWh)',
    line=dict(color='orange', dash='dot', width=2),
    yaxis='y2'
))

# 布局设置
fig.update_layout(
    title="24小时 功率运行模拟 & 电价曲线",
    xaxis=dict(title="时间 (小时)"),
    yaxis=dict(title="功率 (kW)", side="left"),
    yaxis2=dict(title="电价 (元)", side="right", overlaying="y", showgrid=False),
    legend=dict(orientation="h", y=1.1),
    template="plotly_white"
)

st.plotly_chart(fig, use_container_width=True)

# 5.3 数据明细
with st.expander("查看详细运行数据表"):
    st.dataframe(sim_data.style.format("{:.2f}"))

# 底部声明
st.caption("注：本工具仅为估算模型，未包含电池衰减曲线、运维成本及复杂的需量管理策略。")