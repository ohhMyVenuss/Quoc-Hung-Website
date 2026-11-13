import math
import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from django.conf import settings
from django.utils import timezone


FIXED_SLA_HOURS: int = 12
FIXED_SLA_DURATION: timedelta = timedelta(hours=FIXED_SLA_HOURS)


SYNTHETIC_STORE_CALENDAR: Dict[str, Any] = {
    'TIMEZONE': 'Asia/Ho_Chi_Minh',
    'WORK_DAYS': [0, 1, 2, 3, 4, 5],
    'WORK_HOURS': [('08:00', '12:00'), ('13:00', '17:00')],
    'HOLIDAYS': [
        '2026-01-01',
        '2026-04-30',
        '2026-05-01',
        '2026-09-02',
    ],
    'SLA_HOURS': 12,
}


def _parse_time(t: Any) -> Optional[time]:
    if isinstance(t, time):
        return t
    if isinstance(t, str):
        parts = t.strip().split(':')
        if len(parts) in (2, 3):
            try:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) == 3 else 0
                if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
                    return time(hour, minute, second)
            except (ValueError, TypeError):
                return None
    return None


def _parse_date(d: Any) -> Optional[date]:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return date.fromisoformat(d.strip())
        except (ValueError, TypeError):
            return None
    return None


def get_store_calendar(calendar_config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    raw_cfg = calendar_config
    if raw_cfg is None:
        raw_cfg = getattr(settings, 'RECONCILIATION_SLA_CALENDAR', None)

    if not raw_cfg or not isinstance(raw_cfg, dict):
        return None

    try:
        if 'TIMEZONE' not in raw_cfg:
            return None
        tz_raw = raw_cfg['TIMEZONE']
        if isinstance(tz_raw, zoneinfo.ZoneInfo):
            tz = tz_raw
        elif isinstance(tz_raw, str) and tz_raw.strip():
            try:
                tz = zoneinfo.ZoneInfo(tz_raw.strip())
            except Exception:
                return None
        else:
            return None

        if 'WORK_DAYS' not in raw_cfg:
            return None
        raw_days = raw_cfg['WORK_DAYS']
        if not isinstance(raw_days, (list, tuple, set)) or isinstance(raw_days, (str, bytes)):
            return None
        if not raw_days:
            return None

        work_days: Set[int] = set()
        for d in raw_days:
            if isinstance(d, bool) or not isinstance(d, int):
                return None
            if not (0 <= d <= 6):
                return None
            work_days.add(d)

        if not work_days:
            return None

        if 'WORK_HOURS' not in raw_cfg:
            return None
        raw_hours = raw_cfg['WORK_HOURS']
        if not isinstance(raw_hours, (list, tuple)) or isinstance(raw_hours, (str, bytes)):
            return None
        if not raw_hours:
            return None

        parsed_shifts: List[Tuple[time, time]] = []
        for item in raw_hours:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return None
            start_t = _parse_time(item[0])
            end_t = _parse_time(item[1])
            if start_t is None or end_t is None:
                return None
            if start_t >= end_t:
                return None
            parsed_shifts.append((start_t, end_t))

        parsed_shifts.sort(key=lambda s: s[0])

        for i in range(len(parsed_shifts) - 1):
            cur_end = parsed_shifts[i][1]
            next_start = parsed_shifts[i + 1][0]
            if cur_end > next_start:
                return None

        raw_holidays = raw_cfg.get('HOLIDAYS', [])
        if raw_holidays is None:
            raw_holidays = []
        if not isinstance(raw_holidays, (list, tuple, set)) or isinstance(raw_holidays, (str, bytes)):
            return None

        holidays: Set[date] = set()
        for h in raw_holidays:
            d = _parse_date(h)
            if d is None:
                return None
            holidays.add(d)

        if 'SLA_HOURS' in raw_cfg:
            raw_sla = raw_cfg['SLA_HOURS']
            if isinstance(raw_sla, bool):
                return None
            try:
                sla_val = float(raw_sla)
                if math.isnan(sla_val) or math.isinf(sla_val):
                    return None
                if sla_val != float(FIXED_SLA_HOURS):
                    return None
            except (ValueError, TypeError):
                return None

        return {
            'TIMEZONE': tz,
            'WORK_DAYS': work_days,
            'WORK_HOURS': parsed_shifts,
            'HOLIDAYS': holidays,
            'SLA_HOURS': FIXED_SLA_HOURS,
        }
    except Exception:
        return None


def calculate_sla_due_at(
    created_at: Optional[datetime],
    calendar_config: Optional[Dict[str, Any]] = None,
) -> Optional[datetime]:
    if created_at is None:
        return None

    calendar = get_store_calendar(calendar_config)
    if calendar is None:
        return None

    tz = calendar['TIMEZONE']
    work_days = calendar['WORK_DAYS']
    work_hours = calendar['WORK_HOURS']
    holidays = calendar['HOLIDAYS']
    target_duration = FIXED_SLA_DURATION

    if target_duration <= timedelta(0):
        return None

    if timezone.is_naive(created_at):
        cur_dt = created_at.replace(tzinfo=tz)
    else:
        cur_dt = created_at.astimezone(tz)

    remaining = target_duration
    cur_date = cur_dt.date()

    for _ in range(365):
        if remaining <= timedelta(0):
            break

        if cur_date.weekday() in work_days and cur_date not in holidays:
            for start_time, end_time in work_hours:
                interval_start = datetime.combine(cur_date, start_time, tzinfo=tz)
                interval_end = datetime.combine(cur_date, end_time, tzinfo=tz)

                if cur_dt >= interval_end:
                    continue

                eff_start = max(cur_dt, interval_start)
                available = interval_end - eff_start

                if available <= timedelta(0):
                    continue

                if remaining <= available:
                    sla_due_at = eff_start + remaining
                    return sla_due_at
                else:
                    remaining -= available
                    cur_dt = interval_end

        cur_date += timedelta(days=1)
        cur_dt = datetime.combine(cur_date, time.min, tzinfo=tz)

    return None


def is_within_working_hours(
    dt: Optional[datetime],
    calendar_config: Optional[Dict[str, Any]] = None,
) -> bool:
    if dt is None:
        return False
    calendar = get_store_calendar(calendar_config)
    if calendar is None:
        return False

    tz = calendar['TIMEZONE']
    if timezone.is_naive(dt):
        local_dt = dt.replace(tzinfo=tz)
    else:
        local_dt = dt.astimezone(tz)

    cur_date = local_dt.date()
    if cur_date.weekday() not in calendar['WORK_DAYS'] or cur_date in calendar['HOLIDAYS']:
        return False

    cur_time = local_dt.time()
    for start_t, end_t in calendar['WORK_HOURS']:
        if start_t <= cur_time < end_t:
            return True
    return False
