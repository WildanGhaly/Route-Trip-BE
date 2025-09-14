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
        # Compute notes
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
    """Very simple HOS planner implementing the essentials:
    - 14-hour on-duty window per day
    - 11 hours max driving per day
    - 30-min break after 8h of *driving* (any >=30min non-driving breaks count)
    - 70-hour / 8-day cycle limit with an optional automatic 34-hour reset
    - 1h Pickup + 1h Drop (on-duty)
    - Fuel stop: every 1000 miles (30 min on-duty)
    """

    def __init__(self, *, distance_mi: float, duration_hr: float, current_cycle_used_hours: float, start_dt: Optional[datetime] = None):
        self.distance_mi = float(distance_mi)
        self.duration_min = int(round(duration_hr * 60))
        self.cycle_used_min = int(round(current_cycle_used_hours * 60))
        self.start_dt = start_dt or datetime.utcnow().replace(hour=8, minute=0, second=0, microsecond=0)
        self.avg_mph = (self.distance_mi / (self.duration_min/60.0)) if self.duration_min else 50.0

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

        # Helper to append a segment
        def add_segment(start: datetime, minutes: int, status: str, label: str = '') -> Segment:
            seg = Segment(start=start, end=start + timedelta(minutes=minutes), status=status, label=label)
            segments.append(seg)
            # Add stop record for special labels
            if label in { 'Pickup', 'Drop', 'Fuel', '30m Break' }:
                stops.append(Stop(type=label.lower() if label != '30m Break' else 'break', eta=seg.start, duration_min=minutes))
            return seg

        # Time trackers
        cursor = self.start_dt
        driving_left_min = self.duration_min
        driven_min = 0
        drive_since_break_min = 0
        next_fuel_thresholds = [i * self.fuel_every_miles for i in range(1, int(self.distance_mi // self.fuel_every_miles) + 1)]
        # convert fuel thresholds into driving-min thresholds to trigger fuel stops
        fuel_drive_threshold_mins = [int(round((miles / self.distance_mi) * self.duration_min)) for miles in next_fuel_thresholds]

        # Day trackers
        day_start = cursor
        day_window_used = 0
        day_drive_used = 0
        day_index = 1

        def start_new_day(current: datetime):
            return current.replace(hour=8, minute=0, second=0, microsecond=0)

        def end_day_and_reset():
            nonlocal cursor, day_start, day_window_used, day_drive_used, drive_since_break_min
            add_segment(cursor, self.OFF_DUTY_RESET_MIN, OFF, 'Off Duty (reset)')
            cursor += timedelta(minutes=self.OFF_DUTY_RESET_MIN)
            drive_since_break_min = 0
            # new day at 08:00 local time equivalent (stick with UTC in this demo)
            cursor = start_new_day(cursor)

        # Pickup (1h on-duty)
        add_segment(cursor, 60, ON_DUTY, 'Pickup')
        cursor += timedelta(minutes=60)
        day_window_used += 60
        self.cycle_used_min += 60

        while driving_left_min > 0:
            if self.cycle_used_min >= self.CYCLE_MAX_MIN:
                add_segment(cursor, self.CYCLE_RESET_MIN, OFF, '34h Restart')
                cursor += timedelta(minutes=self.CYCLE_RESET_MIN)
                self.cycle_used_min = 0
                drive_since_break_min = 0
                day_start = start_new_day(cursor)
                cursor = day_start
                day_window_used = 0
                day_drive_used = 0

            # Need a 30m break?
            if drive_since_break_min >= self.BREAK_AFTER_DRIVE_MIN:
                add_segment(cursor, self.BREAK_BLOCK_MIN, OFF, '30m Break')
                cursor += timedelta(minutes=self.BREAK_BLOCK_MIN)
                day_window_used += self.BREAK_BLOCK_MIN
                drive_since_break_min = 0
                continue

            # Day window/drive caps — start a new day if needed
            if day_window_used >= self.DAY_WINDOW_MIN or day_drive_used >= self.DAY_DRIVE_MAX_MIN:
                end_day_and_reset()
                day_start = cursor
                day_window_used = 0
                day_drive_used = 0
                continue

            drive_room_today = min(self.DAY_DRIVE_MAX_MIN - day_drive_used, self.DAY_WINDOW_MIN - day_window_used)
            drive_until_break = self.BREAK_AFTER_DRIVE_MIN - drive_since_break_min
            chunk = min(drive_room_today, drive_until_break, driving_left_min)
            chunk = max(15, int(chunk))
            chunk = min(chunk, driving_left_min)

            # Check if we should insert a fuel stop within this chunk
            # If the next threshold is within (driven_min, driven_min+chunk], split and insert fuel
            next_threshold = None
            for t in fuel_drive_threshold_mins:
                if driven_min < t <= driven_min + chunk:
                    next_threshold = t
                    break

            if next_threshold is not None:
                # Drive until threshold
                drive_first = next_threshold - driven_min
                add_segment(cursor, drive_first, DRIVING)
                cursor += timedelta(minutes=drive_first)
                day_window_used += drive_first
                day_drive_used += drive_first
                drive_since_break_min += drive_first
                driving_left_min -= drive_first
                driven_min += drive_first
                add_segment(cursor, self.fuel_block_min, ON_DUTY, 'Fuel')
                cursor += timedelta(minutes=self.fuel_block_min)
                day_window_used += self.fuel_block_min
                self.cycle_used_min += self.fuel_block_min
                continue

            # Normal driving chunk
            add_segment(cursor, chunk, DRIVING)
            cursor += timedelta(minutes=chunk)
            day_window_used += chunk
            day_drive_used += chunk
            drive_since_break_min += chunk
            driving_left_min -= chunk
            driven_min += chunk

        add_segment(cursor, 60, ON_DUTY, 'Drop')
        cursor += timedelta(minutes=60)
        day_window_used += 60
        self.cycle_used_min += 60

        # Group into days for API
        days: List[DayPlan] = []
        day_map: Dict[date, List[Segment]] = {}
        for seg in segments:
            d = seg.start.date()
            day_map.setdefault(d, []).append(seg)
        for i, d in enumerate(sorted(day_map.keys()), start=1):
            days.append(DayPlan(date=d, segments=day_map[d]))

        # Build stop list (ensure chronological)
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