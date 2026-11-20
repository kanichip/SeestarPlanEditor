"""Offline plan validation and Stellarium integration (no telescope commands)."""
from __future__ import annotations

import copy
import json
import math
import os
import re
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import urlopen


def minutes(text: str) -> int:
    """Accept extended hours: 25:30 is 01:30 on the following day."""
    try:
        h, m = map(int, text.split(':'))
        if not (0 <= h < 48 and 0 <= m < 60):
            raise ValueError
        return h * 60 + m
    except (ValueError, AttributeError):
        raise ValueError('開始時刻は00:00〜47:59で入力してください。翌日01:30は25:30です。') from None


def clock(value: int) -> str:
    return f'{value // 60:02d}:{value % 60:02d}'


def calculate_altitude(ra_hours: float, dec_deg: float, moment: datetime,
                       lat_deg: float = 35.68, lon_deg: float = 139.76) -> float:
    """指定時刻・観測地における天体の地平高度（度）を算出"""
    # ユリウス日 (JD) の計算
    utc = moment - timedelta(hours=9)  # JST (UTC+9) 前提
    y, m, d = utc.year, utc.month, utc.day
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    day_fraction = (utc.hour + utc.minute / 60.0 + utc.second / 3600.0) / 24.0
    jd = math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + day_fraction + b - 1524.5

    # グリニッジ平均恒星時 (GMST) および 地方恒星時 (LMST) [度]
    t = (jd - 2451545.0) / 36525.0
    gmst = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t**2 - (t**3) / 38710000.0
    lmst = (gmst + lon_deg) % 360.0

    # 時角 (HA) [ラジアン]
    ra_deg = (ra_hours * 15.0) % 360.0
    ha_rad = math.radians((lmst - ra_deg) % 360.0)

    # 地平座標系への変換
    lat_rad = math.radians(lat_deg)
    dec_rad = math.radians(dec_deg)

    sin_alt = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    return math.degrees(math.asin(sin_alt))


def number(value, low, high, label):
    v = float(value)
    if not math.isfinite(v) or not low <= v <= high:
        raise ValueError(f'{label}は{low}〜{high}の範囲で指定してください。')
    return v


def coordinate_text(value, *, ra=False):
    """Display coordinates in degrees, arcminutes and integer arcseconds."""
    value = number(value, 0 if ra else -90, 24 if ra else 90, '座標')
    ticks = round(abs(value) * (15 if ra else 1) * 3600)
    if ra:
        ticks %= 360 * 3600
    major, remainder = divmod(ticks, 3600)
    minute, second = divmod(remainder, 60)
    sign = '' if ra else ('-' if math.copysign(1, value) < 0 else '+')
    return f"{sign}{major:02d}° {minute:02d}' {second:02d}''"


def parse_coordinate(text, *, ra=False):
    """Accept sexagesimal coordinates and legacy decimal hours/degrees."""
    text = text.strip().replace('−', '-')
    try:
        degrees = '°' in text
        if degrees:
            match = re.fullmatch(r'''([+-]?\d+)\s*°\s*(\d+)\s*['′]\s*(\d+(?:\.\d+)?)\s*(?:''|"|″)''', text)
            if not match:
                raise ValueError
            text = ':'.join(match.groups())
        if ':' in text:
            major, minute, second = text.split(':')
            if not major.lstrip('+-').isdigit() or not minute.isdigit():
                raise ValueError
            m = int(minute)
            s = float(second)
            if not 0 <= m < 60 or not math.isfinite(s) or not 0 <= s < 60:
                raise ValueError
            value = (abs(int(major)) + m / 60 + s / 3600) * (-1 if major.startswith('-') else 1)
        else:
            value = float(text)
        if degrees and ra:
            value /= 15
        value = number(value, 0 if ra else -90, 24 if ra else 90, '座標')
        return value % 24 if ra else value
    except ValueError:
        unit = "度・分・秒（0° 00' 00''〜359° 59' 59''）" if ra else "度・分・秒（-90° 00' 00''〜+90° 00' 00''）"
        raise ValueError(f'座標は{unit}で入力してください。') from None


def schedule_targets(targets, start):
    """Assign consecutive starts in list order without changing the input."""
    current = minutes(start)
    result = copy.deepcopy(targets)
    for t in result:
        duration = t['duration_min']
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError('撮影時間は正の整数（分）にしてください。')
        if current >= 2880:
            raise ValueError('積算した開始時刻が47:59を超えています。')
        t['start_min'] = current
        current += duration
    return result


def build_plan(name, targets, start=None):
    if not name.strip() or not targets:
        raise ValueError('プラン名と1件以上の対象が必要です。')
    result = copy.deepcopy(targets) if start is None else schedule_targets(targets, start)
    ids = set()
    for t in result:
        if not t['target_name'].strip():
            raise ValueError('天体名を入力してください。')
        ra, dec = t['target_ra_dec']
        number(ra, 0, 24 - 1e-10, '赤経(h)')
        number(dec, -90, 90, '赤緯(°)')
        if t['target_id'] in ids or not isinstance(t['target_id'], int) or t['target_id'] <= 0:
            raise ValueError('対象IDが不正または重複しています。')
        ids.add(t['target_id'])
        if not isinstance(t['duration_min'], int) or t['duration_min'] <= 0:
            raise ValueError('撮影時間は正の整数（分）にしてください。')
        if not isinstance(t['start_min'], int) or not 0 <= t['start_min'] < 2880:
            raise ValueError('開始時刻が範囲外です。')
        if not isinstance(t.get('lp_filter'), bool):
            raise ValueError('フィルターの設定が不正です。')
        t['camera_mode'] = 'tele'
        t.pop('second_camera', None)
        if t.get('mosaic'):
            m = t['mosaic']
            number(m['scale'], 1, 4, '画角倍率')
            number(m['angle'], -360, 360, '回転角')
            m['star_map_angle'] = m['angle']
    result.sort(key=lambda t: t['start_min'])
    for a, b in zip(result, result[1:]):
        if a['start_min'] + a['duration_min'] > b['start_min']:
            raise ValueError(f"撮影時間が重複しています：{a['target_name']} / {b['target_name']}")
    return {'plan_name': name.strip(), 'update_time_seestar': date.today().strftime('%Y.%m.%d'), 'list': result}


def selected_object(base_url):
    parsed = urlsplit(base_url)
    if parsed.scheme != 'http' or not parsed.hostname:
        raise ValueError('StellariumのURLは http://localhost:8090 の形式で指定してください。')
    url = base_url.rstrip('/') + '/api/objects/info?' + urlencode({'format': 'json'})
    try:
        with urlopen(url, timeout=8) as response:
            info = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError('stellarium で座標を指定してください。') from None
        raise
    if not isinstance(info, dict) or 'raJ2000' not in info or 'decJ2000' not in info:
        raise ValueError('stellarium で座標を指定してください。')
    # Stellarium may return signed longitudes (e.g. NGC 7000: -45 degrees).
    # Wrap finite degrees before converting to Seestar's RA hours.
    ra_degrees = float(info['raJ2000'])
    if not math.isfinite(ra_degrees):
        raise ValueError('Stellarium赤経が有効な数値ではありません。')
    ra = (ra_degrees % 360) / 15
    dec = number(info['decJ2000'], -90, 90, 'Stellarium赤緯')
    name = info.get('name') or info.get('localized-name') or info.get('designation') or info.get('english-name')
    if not name:
        name = f'RA{ra:.5f} DEC{dec:+.5f}'
    return str(name), ra, dec


def frame_points(angle, scale=1, width=240, height=135):
    """A schematic rectangle, not a sensor orientation prediction."""
    a = math.radians(angle)
    return [(scale*(x*math.cos(a)-y*math.sin(a)),
             scale*(x*math.sin(a)+y*math.cos(a)))
            for x, y in [(-width/2, -height/2), (width/2, -height/2),
                         (width/2, height/2), (-width/2, height/2)]]


def sky_offset(ra, dec, x, y):
    """Project east/north tangent-plane offsets onto the celestial sphere."""
    norm = math.sqrt(1 + x*x + y*y)
    lon = ra + math.atan2(x, math.cos(dec) - y * math.sin(dec))
    lat = math.asin(max(-1, min(1, (math.sin(dec) + y*math.cos(dec)) / norm)))
    return math.degrees(lon) % 360, math.degrees(lat)


def mosaic_panel_centers(ra, dec, angle, scale, width, height, rows, columns):
    """Yield J2000 panel centers in shooting order (RA in hours)."""
    origin_ra, origin_dec = math.radians(ra * 15), math.radians(dec)
    rotation = math.radians(angle)
    cos_angle, sin_angle = math.cos(rotation), math.sin(rotation)
    # Use the same 20% overlap as the Mosaic Planner default.
    step_x = 0.8 * 2 * math.tan(math.radians(width) / 2) * math.sqrt(scale)
    step_y = 0.8 * 2 * math.tan(math.radians(height) / 2) * math.sqrt(scale)
    for column in range(columns):
        x = (column - (columns - 1) / 2) * step_x
        for row in range(rows):
            y = (row - (rows - 1) / 2) * step_y
            lon, lat = sky_offset(origin_ra, origin_dec,
                                  x*cos_angle - y*sin_angle, x*sin_angle + y*cos_angle)
            yield min(lon / 15, 24 - 1e-10), lat


def mosaic_panel_number(target):
    match = re.search(r' (?:P|パネル)([1-9][0-9]{0,2})$', target.get('target_name', ''))
    return int(match[1]) if match else None


def sky_frame_corners(ra, dec, angle, scale=1, width=2.25, height=4.0):
    """Inverse gnomonic projection; RA in hours, sizes in degrees, scale is area.

    Default telephoto field is approximate; at zero angle the long side is north-south.
    Return texture corners: bottom left, bottom right, top right, top left.
    """
    ra = math.radians(number(ra, 0, 24, '赤経') * 15)
    dec = math.radians(number(dec, -90, 90, '赤緯'))
    angle = number(angle, -720, 720, '回転角')
    scale = math.sqrt(number(scale, 1, 4, '面積倍率'))
    w = 2 * math.tan(math.radians(number(width, 0.01, 30, '横画角')) / 2) * scale
    h = 2 * math.tan(math.radians(number(height, 0.01, 30, '縦画角')) / 2) * scale
    corners = [sky_offset(ra, dec, x, y) for x, y in frame_points(angle, width=w, height=h)]
    # Viewed from inside the celestial sphere, east is left on the sky.
    # Stellarium needs this winding to render the front of the image.
    return [corners[i] for i in (1, 0, 3, 2)]


def clear_stellarium_frames(base_url):
    """Remove only this application's layers, including earlier sessions."""
    parsed = urlsplit(base_url)
    if parsed.scheme != 'http' or not parsed.hostname:
        raise ValueError('StellariumのURLは http://localhost:8090 の形式で指定してください。')
    script = '''var ids = StelSkyLayerMgr.getAllKeys();
for (var i = 0; i < ids.length; ++i) {
    if (ids[i] === "seestar_planner_frame" || /^seestar_planner_all_[0-9]+$/.test(ids[i])) {
        core.removeSkyImage(ids[i]);
    }
}'''
    data = urlencode({'code': script}).encode('utf-8')
    with urlopen(base_url.rstrip('/') + '/api/scripts/direct', data=data, timeout=8) as response:
        reply = response.read().decode('utf-8').strip()
    if reply != 'ok':
        raise ValueError(f'Stellariumの撮影枠消去に失敗しました: {reply}')


def draw_stellarium_frame(base_url, ra, dec, angle, scale=1, width=2.25, height=4.0,
                          *, start_min=None, plan_date=None, intensity=50,
                          frames=None, previous_count=0, selected_index=None, thickness=3,
                          panel_number=None):
    """Replace this application's sky image via Stellarium Remote Control."""
    parsed = urlsplit(base_url)
    if parsed.scheme != 'http' or not parsed.hostname:
        raise ValueError('StellariumのURLは http://localhost:8090 の形式で指定してください。')
    intensity = number(intensity, 0, 100, '枠の濃さ')
    thickness = round(number(thickness, 1, 10, '枠の太さ'))
    width = number(width, 0.01, 30, '横画角')
    height = number(height, 0.01, 30, '縦画角')
    # Match the texture aspect to the gnomonic plane so both axes have
    # the same angular pixel scale. Bound the size for extreme fields.
    aspect = math.tan(math.radians(width) / 2) / math.tan(math.radians(height) / 2)
    texture_width = max(2, round(1024 * min(1, aspect)))
    texture_height = max(2, round(1024 * min(1, 1 / aspect)))
    # loadSkyImage always prefixes "scripts/", even for absolute Windows paths.
    # Install only our texture into Stellarium's standard user script directory.
    texture = f'seestar_planner_frame_{thickness}_{texture_width}x{texture_height}'
    scripts = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')) / 'Stellarium' / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    # Keep the outer field of view fixed as the stroke grows inward.
    with Image.open(Path(__file__).with_name('frame.png')) as reference:
        reference = reference.convert('RGBA')
        color = reference.getpixel((0, 0))
        background = reference.getpixel((reference.width // 2, reference.height // 2))
        image = Image.new('RGBA', (texture_width, texture_height), background)
    ImageDraw.Draw(image).rectangle((0, 0, image.width - 1, image.height - 1),
                                   outline=color, width=thickness)
    textures = {}

    def frame_texture(label):
        if label not in textures:
            filename = f'{texture}.png'
            rendered = image
            if label is not None:
                filename = f'{texture}_panel_{label}.png'
                rendered = image.copy()
                font = ImageFont.load_default(size=max(1, round(min(image.size) * 0.14)))
                ImageDraw.Draw(rendered).text((image.width / 2, image.height / 2), str(label),
                                              fill=color, font=font, anchor='mm')
            buffer = BytesIO()
            rendered.save(buffer, format='PNG')
            source = buffer.getvalue()
            destination = scripts / filename
            if not destination.exists() or destination.read_bytes() != source:
                destination.write_bytes(source)
            textures[label] = filename
        return textures[label]

    commands = ['core.removeSkyImage("seestar_planner_frame");']
    commands.extend(f'core.removeSkyImage("seestar_planner_all_{i}");'
                    for i in range(previous_count))
    def add_frame(ident, frame_ra, frame_dec, frame_angle, frame_scale, label=None):
        corners = sky_frame_corners(frame_ra, frame_dec, frame_angle, frame_scale, width, height)
        coordinates = ', '.join(str(v) for point in corners for v in point)
        if intensity > 0:
            brightness = 20 - intensity * 0.12
            commands.append(f'core.loadSkyImage({json.dumps(ident)}, {json.dumps(frame_texture(label))}, '
                            f'{coordinates}, 2.5, {brightness}, true, "EqJ2000");')
    if frames is not None:
        for i, target in enumerate(frames):
            label = target.get('panel_number') or mosaic_panel_number(target)
            if i == selected_index and ra is not None:
                add_frame(f'seestar_planner_all_{i}', ra, dec, angle, scale, label)
            else:
                mosaic = target.get('mosaic') or {}
                add_frame(f'seestar_planner_all_{i}', *target['target_ra_dec'],
                          mosaic.get('angle', 0),
                          mosaic.get('scale', 1), label)
    if ra is not None and (frames is None or selected_index is None):
        add_frame('seestar_planner_frame', ra, dec, angle, scale, panel_number)
    script = '\n'.join(commands)
    if start_min is not None:
        if not isinstance(start_min, int) or not 0 <= start_min < 2880:
            raise ValueError('開始時刻が範囲外です。')
        if plan_date is None:
            with urlopen(base_url.rstrip('/') + '/api/main/status', timeout=8) as response:
                status = json.load(response)
            plan_date = date.fromisoformat(status['time']['local'].split('T')[0])
        moment = datetime.combine(plan_date, datetime.min.time()) + timedelta(minutes=start_min)
        script = (f'core.setDate({json.dumps(moment.isoformat())}, "local");\n'
                  'core.setTimeRate(0);\n'
                  f'core.moveToRaDecJ2000("{float(ra)}h", "{float(dec)}d", 0);\n' + script)
    data = urlencode({'code': script}).encode('utf-8')
    with urlopen(base_url.rstrip('/') + '/api/scripts/direct', data=data, timeout=8) as response:
        reply = response.read().decode('utf-8').strip()
    if reply != 'ok':
        raise ValueError(f'Stellariumの撮影枠描画に失敗しました: {reply}')
    return plan_date
