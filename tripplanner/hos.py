from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional

# Duty status constants for the log grid
OFF = 'off'
SLEEPER = 'sleeper'
DRIVING = 'driving'
ON_DUTY = 'on_duty'

@dataclass
class Segment:
    start: datetime
    end: datetime
    status: str
    label: str = ''

    def to_api(self):
        return {
            't0': self.start.strftime('%H:%M'),
            't1': self.end.strftime('%H:%M'),
            'status': self.status,
            'label': self.label or ''
        }

@dataclass
class DayPlan:
    date: date
    segments: List[Segment]

    def to_api(self, index: int):
        drive_minutes = sum(int((s.end - s.start).total_seconds()//60) for s in self.segments if s.status == DRIVING)
        window_minutes = sum(int((s.end - s.start).total_seconds()//60) for s in self.segments if s.status in {DRIVING, ON_DUTY, OFF, SLEEPER})
        return {
            'index': index,
            'date': self.date.isoformat(),
            'segments': [s.to_api() for s in self.segments],
            'notes': f'Day total: {drive_minutes/60:.2f}h driving; window used: {window_minutes/60:.2f}h'
        }

@dataclass
class Stop:
    type: str
    eta: datetime
    duration_min: int

    def to_api(self):
        return {
            'type': self.type,
            'eta': self.eta.isoformat(),
            'duration_min': self.duration_min,
        }

class HOSPlanner:
    """Simplified HOS planner (14h window, 11h driving/day, 30m break after 8h driving,
    70h/8d cycle with 34h restart, 1h Pickup + 1h Drop, Fuel every 1000mi (30m))."""

    def __init__(self, *, distance_mi: float, duration_hr: float, current_cycle_used_hours: float,
                 start_dt: Optional[datetime] = None, pre_pickup_drive_min: int = 0):
        self.distance_mi = float(distance_mi)
        self.duration_min = int(round(duration_hr * 60))
        self.cycle_used_min = int(round(current_cycle_used_hours * 60))
        self.start_dt = start_dt or datetime.utcnow().replace(hour=8, minute=0, second=0, microsecond=0)
        self.avg_mph = (self.distance_mi / (self.duration_min/60.0)) if self.duration_min else 50.0
        self.pre_pickup_drive_min = max(0, int(pre_pickup_drive_min))

        # HOS constants (minutes)
        self.DAY_WINDOW_MIN = 14 * 60
        self.DAY_DRIVE_MAX_MIN = 11 * 60
        self.BREAK_AFTER_DRIVE_MIN = 8 * 60
        self.BREAK_BLOCK_MIN = 30
        self.OFF_DUTY_RESET_MIN = 10 * 60
        self.CYCLE_MAX_MIN = 70 * 60
        self.CYCLE_RESET_MIN = 34 * 60

        # Operational
        self.fuel_every_miles = 1000.0
        self.fuel_block_min = 30

    def plan(self) -> Dict:
        segments: List[Segment] = []
        stops: List[Stop] = []

        def add_segment(start: datetime, minutes: int, status: str, label: str = '') -> Segment:
            seg = Segment(start=start, end=start + timedelta(minutes=minutes), status=status, label=label)
            segments.append(seg)
            # Count toward 70h cycle if on-duty or driving
            if status in {DRIVING, ON_DUTY}:
                self.cycle_used_min += minutes
            if label in {'Pickup', 'Drop', 'Fuel', '30m Break'}:
                stops.append(Stop(type=label.lower() if label != '30m Break' else 'break', eta=seg.start, duration_min=minutes))
            return seg

        cursor = self.start_dt
        driving_left_min = self.duration_min     # total driving minutes for entire route
        driven_min = 0                           # how many minutes of driving we have logged
        drive_since_break_min = 0

        # Fuel thresholds based on trip proportion (convert miles→minutes via total duration proportion)
        next_fuel_thresholds_miles = [i * self.fuel_every_miles for i in range(1, int(self.distance_mi // self.fuel_every_miles) + 1)]
        fuel_drive_threshold_mins = [int(round((miles / self.distance_mi) * self.duration_min)) for miles in next_fuel_thresholds_miles]

        # Day trackers
        day_window_used = 0
        day_drive_used = 0
        pickup_done = (self.pre_pickup_drive_min == 0)

        # If already at pickup, do the pickup now
        if pickup_done:
            add_segment(cursor, 60, ON_DUTY, 'Pickup')
            cursor += timedelta(minutes=60)
            day_window_used += 60

        def start_new_day(current: datetime):
            return current.replace(hour=8, minute=0, second=0, microsecond=0)

        def end_day_and_reset():
            nonlocal cursor, day_window_used, day_drive_used, drive_since_break_min
            add_segment(cursor, self.OFF_DUTY_RESET_MIN, OFF, 'Off Duty (reset)')
            cursor += timedelta(minutes=self.OFF_DUTY_RESET_MIN)
            drive_since_break_min = 0
            cursor = start_new_day(cursor)
            day_window_used = 0
            day_drive_used = 0

        while driving_left_min > 0:
            # Insert pickup exactly when reaching the boundary of the first leg
            if (not pickup_done) and driven_min >= self.pre_pickup_drive_min:
                add_segment(cursor, 60, ON_DUTY, 'Pickup')
                cursor += timedelta(minutes=60)
                day_window_used += 60
                pickup_done = True
                continue

            # 70h/8d cycle
            if self.cycle_used_min >= self.CYCLE_MAX_MIN:
                add_segment(cursor, self.CYCLE_RESET_MIN, OFF, '34h Restart')
                cursor += timedelta(minutes=self.CYCLE_RESET_MIN)
                self.cycle_used_min = 0
                drive_since_break_min = 0
                cursor = start_new_day(cursor)
                day_window_used = 0
                day_drive_used = 0
                continue

            # 30-min break after 8h driving
            if drive_since_break_min >= self.BREAK_AFTER_DRIVE_MIN:
                add_segment(cursor, self.BREAK_BLOCK_MIN, OFF, '30m Break')
                cursor += timedelta(minutes=self.BREAK_BLOCK_MIN)
                day_window_used += self.BREAK_BLOCK_MIN
                drive_since_break_min = 0
                continue

            # Day limits
            if day_window_used >= self.DAY_WINDOW_MIN or day_drive_used >= self.DAY_DRIVE_MAX_MIN:
                end_day_and_reset()
                continue

            drive_room_today = min(self.DAY_DRIVE_MAX_MIN - day_drive_used, self.DAY_WINDOW_MIN - day_window_used)
            drive_until_break = self.BREAK_AFTER_DRIVE_MIN - drive_since_break_min
            chunk = min(drive_room_today, drive_until_break, driving_left_min)
            chunk = max(15, int(chunk))
            chunk = min(chunk, driving_left_min)

            # Split for fuel or pickup boundary inside this chunk
            next_threshold = None
            for t in fuel_drive_threshold_mins:
                if driven_min < t <= driven_min + chunk:
                    next_threshold = t
                    break

            pickup_threshold = None
            if not pickup_done and driven_min < self.pre_pickup_drive_min <= driven_min + chunk:
                pickup_threshold = self.pre_pickup_drive_min

            split_at = min([x for x in [next_threshold, pickup_threshold] if x is not None], default=None)
            if split_at is not None:
                drive_first = split_at - driven_min
                add_segment(cursor, drive_first, DRIVING)
                cursor += timedelta(minutes=drive_first)
                day_window_used += drive_first
                day_drive_used += drive_first
                drive_since_break_min += drive_first
                driving_left_min -= drive_first
                driven_min += drive_first
                if pickup_threshold is not None and split_at == pickup_threshold:
                    add_segment(cursor, 60, ON_DUTY, 'Pickup')
                    cursor += timedelta(minutes=60)
                    day_window_used += 60
                    pickup_done = True
                else:
                    add_segment(cursor, self.fuel_block_min, ON_DUTY, 'Fuel')
                    cursor += timedelta(minutes=self.fuel_block_min)
                    day_window_used += self.fuel_block_min
                continue

            # Normal driving chunk
            add_segment(cursor, chunk, DRIVING)
            cursor += timedelta(minutes=chunk)
            day_window_used += chunk
            day_drive_used += chunk
            drive_since_break_min += chunk
            driving_left_min -= chunk
            driven_min += chunk

        # Drop at the end
        add_segment(cursor, 60, ON_DUTY, 'Drop')
        cursor += timedelta(minutes=60)
        day_window_used += 60

        # Group into days for API
        days: List[DayPlan] = []
        day_map: Dict[date, List[Segment]] = {}
        for seg in segments:
            d = seg.start.date()
            day_map.setdefault(d, []).append(seg)
        for i, d in enumerate(sorted(day_map.keys()), start=1):
            days.append(DayPlan(date=d, segments=day_map[d]))

        stops_sorted = sorted(stops, key=lambda s: s.eta)

        return {
            'route': {
                'distance_mi': round(self.distance_mi, 1),
                'duration_hr': round(self.duration_min/60.0, 2),
                'polyline': None,
            },
            'stops': [s.to_api() for s in stops_sorted],
            'days': [dp.to_api(i) for i, dp in enumerate(days, start=1)],
        }
