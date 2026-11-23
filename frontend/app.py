"""Streamlit frontend for the room booking service.

The interface talks to the project's own FastAPI endpoints only - it never
imports SQLAlchemy, never opens a database connection and never reads the
database file.  Every failure path of the API is surfaced to the user.

Run with::

    streamlit run frontend/app.py
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import httpx
import pandas as pd
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "10"))

st.set_page_config(page_title="会议室预订系统", page_icon="🗓", layout="wide")


# --------------------------------------------------------------------------- api
def api_request(method: str, path: str, **kwargs) -> tuple[dict | list | None, dict | None]:
    """Call the API and return ``(payload, error)`` - exactly one of them is set."""
    try:
        response = httpx.request(
            method, f"{API_BASE_URL}{path}", timeout=REQUEST_TIMEOUT, **kwargs
        )
    except httpx.TimeoutException:
        return None, {"status": None, "message": f"请求超时（>{REQUEST_TIMEOUT:.0f}s）。后端可能正在重启，请稍后重试。"}
    except httpx.HTTPError as exc:
        return None, {"status": None, "message": f"无法连接后端 {API_BASE_URL}：{exc.__class__.__name__}"}

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        if isinstance(detail, dict):
            return None, {
                "status": response.status_code,
                "code": detail.get("code"),
                "message": detail.get("message") or str(detail),
                "extra": detail,
            }
        return None, {"status": response.status_code, "message": str(detail)}
    try:
        return response.json(), None
    except ValueError:
        return None, {"status": response.status_code, "message": "后端返回了非 JSON 响应。"}


def show_error(error: dict) -> None:
    """Render an API error, including the documented conflict details."""
    message = error.get("message", "未知错误")
    code = error.get("code")
    status_code = error.get("status")
    if status_code == 409:
        st.warning(f"该时段与已有预订冲突：{message}")
        extra = error.get("extra") or {}
        conflicting = extra.get("conflicting_booking_ids") or []
        if conflicting:
            st.caption("冲突的预订编号：" + "、".join(f"#{item}" for item in conflicting))
        with st.expander("技术细节"):
            st.json(error)
        return
    if status_code == 503:
        st.info(f"系统正在维护，暂时只读：{message}")
        return
    st.error(f"请求失败{f'（{code}）' if code else ''}：{message}")
    with st.expander("技术细节"):
        st.json(error)


# ---------------------------------------------------------------------- sidebar
st.sidebar.title("会议室预订系统")
st.sidebar.caption(f"API: {API_BASE_URL}")
health, health_error = api_request("GET", "/health")
if health_error:
    st.sidebar.error("后端不可用")
    st.sidebar.caption(health_error["message"])
elif health:
    maintenance = health.get("maintenance_mode", False)
    if maintenance:
        st.sidebar.warning("维护模式：当前只能查询")
    else:
        st.sidebar.success(f"后端正常 · v{health.get('version', '?')}")
st.sidebar.divider()
st.sidebar.markdown(
    "**使用说明**\n"
    "1. 新建预订：同一房间时间重叠会被拒绝（首尾相接允许）\n"
    "2. 预订查询：可按房间和日期过滤\n"
    "3. 利用率：按营业时段（08:00–20:00）计算"
)

create_tab, list_tab, stats_tab = st.tabs(["新建预订", "预订查询", "利用率分析"])


# ------------------------------------------------------------------- create tab
with create_tab:
    st.subheader("新建预订")
    rooms_payload, rooms_error = api_request("GET", "/rooms")
    existing_rooms = [] if (rooms_error or not rooms_payload) else rooms_payload.get("items", [])

    with st.form("create_booking_form", clear_on_submit=False):
        col_a, col_b = st.columns(2)
        with col_a:
            room = st.text_input("房间 *", placeholder="例如 Room A", help="已有房间：" + ("、".join(existing_rooms) if existing_rooms else "暂无"))
            booker = st.text_input("预订人 *", placeholder="例如 陶禹豪")
        with col_b:
            purpose = st.text_input("用途", placeholder="可选，例如 项目周会")
            st.caption("时间以 `HH:MM` 表示，结束必须晚于开始。")

        col_c, col_d, col_e = st.columns(3)
        day = col_c.date_input("日期 *", value=date.today(), min_value=date(2000, 1, 1))
        start_time = col_d.time_input("开始时间 *", value=time(9, 0), step=timedelta(minutes=15))
        end_time = col_e.time_input("结束时间 *", value=time(10, 0), step=timedelta(minutes=15))
        submitted = st.form_submit_button("提交预订", type="primary")

    if submitted:
        if not room.strip() or not booker.strip():
            st.error("房间和预订人是必填项。")
        elif end_time <= start_time:
            st.error("结束时间必须晚于开始时间。")
        else:
            start_at = datetime.combine(day, start_time)
            end_at = datetime.combine(day, end_time)
            payload, error = api_request(
                "POST",
                "/bookings",
                json={
                    "room": room.strip(),
                    "booker": booker.strip(),
                    "purpose": purpose.strip() or None,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                },
            )
            if error:
                show_error(error)
            elif payload:
                st.success(
                    f"预订成功 #{payload['id']}：{payload['room']} "
                    f"{payload['start_at'][11:16]}–{payload['end_at'][11:16]}（{payload['booker']}）"
                )


# --------------------------------------------------------------------- list tab
with list_tab:
    st.subheader("预订查询")
    rooms_payload, _ = api_request("GET", "/rooms")
    filter_rooms = [] if not rooms_payload else rooms_payload.get("items", [])
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    room_choice = col_f1.selectbox("房间", ["全部"] + filter_rooms)
    date_from = col_f2.date_input("起始日期", value=date.today())
    date_to = col_f3.date_input("结束日期", value=date.today() + timedelta(days=7))

    if date_to < date_from:
        st.error("结束日期不能早于起始日期。")
    else:
        params: dict[str, str] = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "limit": "200"}
        if room_choice != "全部":
            params["room"] = room_choice
        payload, error = api_request("GET", "/bookings", params=params)
        if error:
            show_error(error)
        elif not payload or payload.get("total", 0) == 0:
            st.info("这个时间范围内还没有预订。")
        else:
            st.caption(f"共 {payload['total']} 条预订（最多显示 200 条）")
            frame = pd.DataFrame(payload["items"])
            frame["时间段"] = frame.apply(
                lambda row: f"{row['start_at'][5:10]} {row['start_at'][11:16]}–{row['end_at'][11:16]}", axis=1
            )
            st.dataframe(
                frame[["id", "时间段", "room", "booker", "purpose"]].rename(
                    columns={"id": "编号", "room": "房间", "booker": "预订人", "purpose": "用途"}
                ),
                hide_index=True,
            )


# -------------------------------------------------------------------- stats tab
with stats_tab:
    st.subheader("利用率分析")
    col_s1, col_s2 = st.columns(2)
    stats_from = col_s1.date_input("统计起始日期", value=date.today())
    stats_to = col_s2.date_input("统计结束日期", value=date.today() + timedelta(days=6))

    if stats_to < stats_from:
        st.error("结束日期不能早于起始日期。")
    else:
        payload, error = api_request(
            "GET",
            "/stats/utilization",
            params={"date_from": stats_from.isoformat(), "date_to": stats_to.isoformat()},
        )
        if error:
            show_error(error)
        elif not payload or not payload.get("rooms"):
            st.info("这个区间内没有可统计的预订。")
        else:
            window = payload["window"]
            st.caption(
                f"{window['date_from']} → {window['date_to']}，营业时段 "
                f"{window['open_hour']:02d}:00–{window['close_hour']:02d}:00，"
                f"可用总时长 {window['open_minutes'] / 60:.0f} 小时。利用率 = 已预订时长 / 可用时长。"
            )
            frame = pd.DataFrame(payload["rooms"]).sort_values("utilization", ascending=False)
            frame["利用率(%)"] = (frame["utilization"] * 100).round(1)
            frame["已预订(小时)"] = (frame["booked_minutes"] / 60).round(2)
            st.bar_chart(frame, x="room", y="利用率(%)")
            st.dataframe(
                frame[["room", "booking_count", "已预订(小时)", "利用率(%)"]].rename(
                    columns={"room": "房间", "booking_count": "预订数"}
                ),
                hide_index=True,
            )
