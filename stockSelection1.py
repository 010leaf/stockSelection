import tushare as ts
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
import time
import random
import os
import pickle

# -------------------------- 核心配置（Tushare授权+筛选规则） --------------------------
CONFIG = {
    "tushare_token": "你的token",  # 替换为你的真实TOKEN！
    "limit_up_price_pct": 9.8,
    "limit_down_price_pct": -9.8,
    "trend_days": 60,
    "trend_up_pct": 30,
    "trend_volatility_pct": 25,
    "capital_flow_days": 5,
    "min_stock_price": 3.0,
    "exclude_boards": ["创业板", "科创板"],  # 排除创业板/科创板
    "batch_size": 10,
    "cache_expire_hours": 24  # 真实数据缓存24小时
}

# -------------------------- 1. Tushare初始化（核心） --------------------------
def init_tushare():
    """初始化Tushare，确保授权成功"""
    try:
        ts.set_token(CONFIG["tushare_token"])
        pro = ts.pro_api()
        # 测试连接是否正常
        pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,industry,list_date')
        st.success("✅ Tushare授权成功，可获取真实股票数据")
        return pro
    except Exception as e:
        st.error(f"❌ Tushare初始化失败：{e}")
        st.info("💡 请检查：1.TOKEN是否正确 2.网络是否正常 3.Tushare账号是否实名认证")
        return None

# -------------------------- 2. 缓存工具（减少重复请求） --------------------------
def get_cache_file_path():
    cache_dir = "tushare_stock_cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    return os.path.join(cache_dir, "tushare_stock_data.pkl")

def load_cache():
    cache_path = get_cache_file_path()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                return pickle.load(f)
        except:
            return {}
    return {}

def save_cache(cache_data):
    cache_path = get_cache_file_path()
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump(cache_data, f)
    except Exception as e:
        st.warning(f"⚠️ 保存缓存失败: {e}")

# -------------------------- 3. 获取真实股票基础数据（核心） --------------------------
def get_all_qualified_stocks(pro):
    """从Tushare获取：非创业板/非科创板+非ETF+股价≥3元的真实股票数据"""
    cache_data = load_cache()
    cache_key = "qualified_stocks"
    
    # 优先读取缓存（24小时内有效）
    if cache_key in cache_data:
        cached_time, stock_df = cache_data[cache_key]
        if (datetime.now() - datetime.fromisoformat(cached_time)).total_seconds() < CONFIG["cache_expire_hours"] * 3600:
            st.info("📦 使用缓存的真实股票数据（24小时内）")
            return stock_df
    
    try:
        st.info("🔍 从Tushare获取全市场A股基础数据...")
        # 1. 获取所有上市A股基础信息
        stock_basic = pro.stock_basic(
            exchange='',
            list_status='L',  # 仅上市状态
            fields='ts_code,symbol,name,industry,market,list_date,exchange'
        )
        
        # 2. 核心筛选：排除创业板/科创板
        # Tushare的market字段对应：主板/创业板/科创板/中小板/北交所
        stock_basic = stock_basic[~stock_basic["market"].isin(["创业板", "科创板"])]
        
        # 3. 排除ETF（名称含ETF/etf）
        stock_basic = stock_basic[~stock_basic["name"].str.contains("ETF|etf", na=False, regex=True)]
        
        # 4. 获取最新股价（真实行情）
        st.info("📡 获取最新股价数据...")
        # 获取当日行情（Tushare的trade_date为YYYYMMDD）
        trade_date = pro.trade_cal(exchange='', start_date=datetime.now().strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"), is_open=1)
        if not trade_date.empty:
            trade_date = trade_date.iloc[0]["cal_date"]
        else:
            # 非交易日取最近一个交易日
            trade_date = pro.trade_cal(exchange='', start_date=(datetime.now()-timedelta(days=7)).strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"), is_open=1).iloc[-1]["cal_date"]
        
        # 批量获取行情数据
        ts_codes = stock_basic["ts_code"].tolist()
        price_df = pd.DataFrame()
        # 分批请求（避免单次请求过多）
        batch_size = 500
        for i in range(0, len(ts_codes), batch_size):
            batch_codes = ts_codes[i:i+batch_size]
            batch_price = pro.daily(ts_code=','.join(batch_codes), trade_date=trade_date)
            price_df = pd.concat([price_df, batch_price], ignore_index=True)
            time.sleep(0.5)  # Tushare免费版限速
        
        # 合并股价数据
        price_df.rename(columns={"close": "最新价格(元)", "pct_chg": "涨跌幅(%)", "vol": "成交量(手)", "amount": "成交额(万元)"}, inplace=True)
        # 成交量单位转换：Tushare的vol是手，无需转换；amount是元→转万元
        price_df["成交额(万元)"] = price_df["成交额(万元)"] / 10000
        
        # 合并基础信息和股价
        stock_df = pd.merge(
            stock_basic,
            price_df[["ts_code", "最新价格(元)", "涨跌幅(%)", "成交量(手)", "成交额(万元)"]],
            on="ts_code",
            how="left"
        )
        
        # 5. 筛选股价≥3元（剔除无股价数据的股票）
        stock_df = stock_df[stock_df["最新价格(元)"].notna()]
        stock_df = stock_df[stock_df["最新价格(元)"] >= CONFIG["min_stock_price"]]
        
        # 6. 字段重命名/整理（适配展示）
        stock_df.rename(
            columns={
                "symbol": "股票代码",
                "name": "股票名称",
                "market": "所属板块",
                "industry": "所属行业",
                "exchange": "交易所",
                "ts_code": "TS代码"
            },
            inplace=True
        )
        # 交易所名称标准化
        stock_df["交易所"] = stock_df["交易所"].map({"SSE": "上海证券交易所", "SZSE": "深圳证券交易所", "BSE": "北京证券交易所"})
        
        # 7. 缓存数据
        cache_data[cache_key] = (datetime.now().isoformat(), stock_df)
        save_cache(cache_data)
        
        st.success(f"✅ 成功获取 {len(stock_df)} 只符合条件的真实股票数据（非创业板/非科创板+非ETF+股价≥{CONFIG['min_stock_price']}元）")
        return stock_df
    
    except Exception as e:
        st.error(f"❌ 获取真实股票数据失败：{e}")
        st.info("💡 Tushare免费版限制：1.每分钟最多60次请求 2.部分字段需升级权限")
        return pd.DataFrame()

# -------------------------- 4. 获取真实日线数据（用于进阶筛选） --------------------------
def get_real_daily_data(pro, ts_code, start_date, end_date):
    """从Tushare获取单只股票的真实日线数据"""
    cache_data = load_cache()
    cache_key = f"daily_{ts_code}_{start_date}_{end_date}"
    
    # 缓存优先
    if cache_key in cache_data:
        cached_time, daily_df = cache_data[cache_key]
        if (datetime.now() - datetime.fromisoformat(cached_time)).total_seconds() < CONFIG["cache_expire_hours"] * 3600:
            return daily_df
    
    try:
        # 转换日期格式（Tushare为YYYYMMDD）
        start = start_date.replace("-", "") if "-" in start_date else start_date
        end = end_date.replace("-", "") if "-" in end_date else end_date
        
        daily_df = pro.daily(
            ts_code=ts_code,
            start_date=start,
            end_date=end
        )
        
        if not daily_df.empty:
            # 字段整理
            daily_df.rename(columns={"close": "close", "open": "open", "high": "high", "low": "low", "amount": "amount"}, inplace=True)
            daily_df["pct_change"] = daily_df["pct_chg"]  # 涨跌幅
            daily_df["trade_date"] = daily_df["trade_date"]
            
            # 缓存数据
            cache_data[cache_key] = (datetime.now().isoformat(), daily_df)
            save_cache(cache_data)
            
            return daily_df
        else:
            st.warning(f"⚠️ {ts_code} 无日线数据（{start_date}至{end_date}）")
            return pd.DataFrame()
    
    except Exception as e:
        st.warning(f"⚠️ 获取 {ts_code} 日线数据失败：{e}")
        return pd.DataFrame()

# -------------------------- 5. 进阶筛选逻辑（基于真实数据） --------------------------
def calculate_limit_up_status(pro, ts_code, trade_date, 连续涨停天数=2):
    """计算连板状态（真实数据）"""
    try:
        # 获取最近N个交易日的日线数据
        start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=连续涨停天数+2)).strftime("%Y%m%d")
        daily_df = get_real_daily_data(pro, ts_code, start_date, trade_date)
        
        if daily_df.empty or len(daily_df) < 连续涨停天数:
            return False, 0
        
        # 按交易日期降序排列
        daily_df = daily_df.sort_values("trade_date", ascending=False).reset_index(drop=True)
        涨停天数 = 0
        
        for i in range(连续涨停天数):
            if i >= len(daily_df):
                break
            # 涨停判断：涨跌幅≥9.8%（主板/中小板/北交所涨停板10%）
            if daily_df.iloc[i]["pct_chg"] >= CONFIG["limit_up_price_pct"]:
                涨停天数 += 1
            else:
                break
        
        return 涨停天数 >= 连续涨停天数, 涨停天数
    except Exception as e:
        st.warning(f"⚠️ 计算 {ts_code} 连板状态失败：{e}")
        return False, 0

def calculate_trend_status(pro, ts_code, trade_date):
    """计算趋势状态（真实数据）"""
    try:
        # 获取60天前的日期
        start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=CONFIG["trend_days"])).strftime("%Y%m%d")
        daily_df = get_real_daily_data(pro, ts_code, start_date, trade_date)
        
        if daily_df.empty or len(daily_df) < CONFIG["trend_days"]:
            return False, 0, 0
        
        daily_df = daily_df.sort_values("trade_date").reset_index(drop=True)
        start_close = daily_df.iloc[0]["close"]
        end_close = daily_df.iloc[-1]["close"]
        
        # 60日涨幅
        trend_up_pct = (end_close - start_close) / start_close * 100 if start_close != 0 else 0
        
        # 计算均线
        daily_df["ma5"] = daily_df["close"].rolling(window=5).mean().fillna(0)
        daily_df["ma20"] = daily_df["close"].rolling(window=20).mean().fillna(0)
        last_ma5 = daily_df.iloc[-1]["ma5"]
        last_ma20 = daily_df.iloc[-1]["ma20"]
        
        # 计算波动率
        daily_df["volatility"] = daily_df.apply(
            lambda row: (row["high"] - row["low"]) / row["open"] * 100 if row["open"] != 0 else 0,
            axis=1
        )
        avg_volatility = daily_df.tail(20)["volatility"].mean()
        
        # 趋势判断
        is_trend = (trend_up_pct >= CONFIG["trend_up_pct"]) and \
                   (last_ma5 > last_ma20) and \
                   (avg_volatility <= CONFIG["trend_volatility_pct"])
        
        return is_trend, round(trend_up_pct, 2), round(avg_volatility, 2)
    except Exception as e:
        st.warning(f"⚠️ 计算 {ts_code} 趋势状态失败：{e}")
        return False, 0, 0

def filter_all_stocks(pro, stock_basic, stock_type, board_filter="全部"):
    """进阶筛选（基于真实数据）"""
    if stock_basic.empty:
        st.error("❌ 无符合条件的股票数据")
        return pd.DataFrame()
    
    # 获取最新交易日
    trade_date = pro.trade_cal(exchange='', start_date=datetime.now().strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"), is_open=1)
    trade_date = trade_date.iloc[0]["cal_date"] if not trade_date.empty else datetime.now().strftime("%Y%m%d")
    
    st.write(f"📅 筛选日期：{trade_date} | 📈 筛选类型：{stock_type} | 🎯 板块过滤：{board_filter}")
    
    result_list = []
    total = len(stock_basic)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # 分批处理
    batch_size = CONFIG["batch_size"]
    for batch_idx in range(0, total, batch_size):
        batch = stock_basic.iloc[batch_idx:batch_idx+batch_size]
        status_text.write(f"🔄 处理第{batch_idx//batch_size + 1}批 / 共{total//batch_size + 1}批（{batch_idx+1}-{min(batch_idx+batch_size, total)}/{total}）")
        
        for idx, row in batch.iterrows():
            ts_code = row["TS代码"]
            symbol = row["股票代码"]
            try:
                res_row = row.copy()
                
                if stock_type in ["全部", "连板票"]:
                    is_lianban, days = calculate_limit_up_status(pro, ts_code, trade_date)
                    res_row["连板天数"] = days
                    if is_lianban:
                        res_row["股票类型"] = "连板票"
                        result_list.append(res_row)
                
                if stock_type in ["全部", "趋势票"]:
                    is_trend, up_pct, vol = calculate_trend_status(pro, ts_code, trade_date)
                    res_row["60日涨幅(%)"] = up_pct
                    res_row["20日波动率(%)"] = vol
                    if is_trend:
                        res_row["股票类型"] = "趋势票"
                        result_list.append(res_row)
                
                if stock_type == "全部" and res_row.name not in [r.name for r in result_list]:
                    res_row["股票类型"] = "未匹配"
                    result_list.append(res_row)
            
            except Exception as e:
                st.warning(f"⚠️ 处理 {symbol} 失败：{e}")
                continue
        
        progress_bar.progress(min((batch_idx+batch_size)/total, 1.0))
        time.sleep(1)  # Tushare限速
    
    progress_bar.empty()
    status_text.empty()
    
    result_df = pd.DataFrame(result_list)
    if not result_df.empty:
        result_df = result_df.fillna("")
        # 板块过滤
        if board_filter != "全部" and "所属板块" in result_df.columns:
            result_df = result_df[result_df["所属板块"] == board_filter]
        result_df = result_df.drop_duplicates(subset=["股票代码"], keep="first")
    
    return result_df

# -------------------------- 6. Web页面展示（真实数据） --------------------------
def main():
    st.set_page_config(page_title="股票选股系统（Tushare真实数据版）", page_icon="📊", layout="wide")
    st.title("📊 股票选股系统（Tushare真实数据版）")
    st.subheader("✅ 非创业板/非科创板+非ETF+股价≥3元 | 基于Tushare真实市场数据")
    st.divider()
    
    # 1. 初始化Tushare
    pro = init_tushare()
    if pro is None:
        return
    
    # 2. 获取并展示符合条件的真实股票列表
    st.header("📋 符合条件的股票列表（真实数据）")
    with st.spinner("⌛ 加载真实股票数据..."):
        stock_basic = get_all_qualified_stocks(pro)
    
    if not stock_basic.empty:
        # 展示核心字段：股票代码、名称、所属板块、交易所、最新价格、涨跌幅、成交量、所属行业
        display_cols = ["股票代码", "股票名称", "所属板块", "交易所", "最新价格(元)", "涨跌幅(%)", "成交量(手)", "所属行业"]
        st.dataframe(
            stock_basic[display_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "最新价格(元)": st.column_config.NumberColumn("最新价格(元)", format="%.2f"),
                "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅(%)", format="%.2f"),
                "成交量(手)": st.column_config.NumberColumn("成交量(手)", format="%d"),
                "股票代码": st.column_config.TextColumn("股票代码", width="small"),
                "股票名称": st.column_config.TextColumn("股票名称", width="medium")
            },
            height=500
        )
        st.info(f"📊 共展示 {len(stock_basic)} 只符合条件的股票（非创业板/非科创板+非ETF+股价≥{CONFIG['min_stock_price']}元）")
    else:
        st.error("❌ 无法获取符合条件的真实股票数据")
        return
    
    st.divider()
    
    # 3. 进阶筛选功能
    st.header("🔧 进阶筛选（基于真实日线数据）")
    col1, col2 = st.columns(2)
    with col1:
        stock_type = st.selectbox("筛选类型", ["全部", "连板票", "趋势票"], index=0)
    with col2:
        # 按所属板块筛选
        all_boards = ["全部"] + list(set(stock_basic["所属板块"].tolist()))
        board_filter = st.selectbox("所属板块", all_boards, index=0)
    
    # 筛选按钮
    if st.button("🚀 开始进阶筛选", type="primary"):
        with st.spinner(f"⌛ 正在筛选 {len(stock_basic)} 只股票..."):
            result_df = filter_all_stocks(pro, stock_basic, stock_type, board_filter)
        
        st.success(f"✅ 筛选完成！共找到 {len(result_df)} 只符合条件的股票")
        st.divider()
        
        # 展示筛选结果
        st.header("🎯 筛选结果（真实数据）")
        if not result_df.empty:
            display_cols = ["股票代码", "股票名称", "所属板块", "交易所", "最新价格(元)", "涨跌幅(%)", "所属行业"]
            # 补充筛选维度字段
            if stock_type == "连板票":
                display_cols.append("连板天数")
            elif stock_type == "趋势票":
                display_cols.extend(["60日涨幅(%)", "20日波动率(%)"])
            
            st.dataframe(
                result_df[display_cols],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "最新价格(元)": st.column_config.NumberColumn("最新价格(元)", format="%.2f"),
                    "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅(%)", format="%.2f"),
                    "60日涨幅(%)": st.column_config.NumberColumn("60日涨幅(%)", format="%.2f"),
                    "20日波动率(%)": st.column_config.NumberColumn("20日波动率(%)", format="%.2f"),
                    "连板天数": st.column_config.NumberColumn("连板天数", format="%d")
                }
            )
            
            # 导出真实数据
            csv_data = result_df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="📥 导出筛选结果（真实数据）",
                data=csv_data,
                file_name=f"真实股票筛选结果_{stock_type}_{board_filter}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
        else:
            st.warning("⚠️ 未找到符合条件的股票")
    
    # 重要提示
    st.divider()
    st.info("""
    🛡️ Tushare真实数据使用说明：
    1. ✅ 所有数据均来自Tushare，为证券交易所真实交易数据；
    2. ⚠️ 免费版Tushare有请求频率限制（每分钟≤60次），批量筛选时请耐心等待；
    3. ⚠️ 数据有15-30分钟延迟，非实时行情；
    4. ❗ 数据仅作学习/测试使用，不构成任何投资建议。
    """)

if __name__ == "__main__":

    main()
