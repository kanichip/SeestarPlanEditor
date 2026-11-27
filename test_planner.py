import copy
import io
import json
import math
import unittest
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from planner import (build_plan, calculate_altitude, clear_stellarium_frames, coordinate_text, draw_stellarium_frame,
                     frame_points, minutes, mosaic_panel_centers, parse_coordinate, schedule_targets, selected_object,
                     sky_frame_corners)
from urllib.parse import parse_qs
from urllib.error import HTTPError


class PlannerTests(unittest.TestCase):
    # 対象未選択時の案内を確認する。Stellarium が404を返したら、座標の指定を促すメッセージ付きの ValueError が発生すること。
    def test_unselected_object_404_shows_coordinate_prompt(self):
        error = HTTPError('http://localhost:8090/api/objects/info', 404, 'not found', {}, None)
        with patch('planner.urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'stellarium で座標を指定してください。'):
                selected_object('http://localhost:8090')

    # 前回の撮影枠を消去するスクリプトを確認する。既存レイヤーを列挙して個別枠と全表示枠を消去し、画像の読み込み命令を含まないこと。
    def test_clear_previous_session_frames(self):
        with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
            clear_stellarium_frames('http://localhost:8090')
        script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
        self.assertIn('StelSkyLayerMgr.getAllKeys()', script)
        self.assertIn('"seestar_planner_frame"', script)
        self.assertIn('^seestar_planner_all_[0-9]+$', script)
        self.assertIn('core.removeSkyImage(ids[i])', script)
        self.assertNotIn('loadSkyImage', script)

    # 赤経・赤緯の表示と入力変換を確認する。負のゼロ度、秒の繰り上がり、赤経24時から0時への折り返しが正しく、範囲外や不正な座標は ValueError になること。
    def test_sexagesimal_rounding_sign_and_limits(self):
        self.assertEqual(coordinate_text(12.5, ra=True), "187° 30' 00''")
        self.assertEqual(coordinate_text(23.999999999, ra=True), "00° 00' 00''")
        self.assertEqual(coordinate_text(-0.5), "-00° 30' 00''")
        self.assertEqual(coordinate_text(90), "+90° 00' 00''")
        self.assertEqual(parse_coordinate("187° 30' 00''", ra=True), 12.5)
        self.assertEqual(parse_coordinate("-00° 30' 00''"), -0.5)
        self.assertEqual(coordinate_text(12 + 59 / 60 + 59.9 / 3600), "+13° 00' 00''")
        for value in ("12° 60' 00''", "12° 00' 60''", "-90° 00' 01''"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_coordinate(value)
        self.assertEqual(parse_coordinate('-00:30:00.000'), -0.5)
        self.assertEqual(parse_coordinate('12:30:00.000', ra=True), 12.5)
        self.assertEqual(parse_coordinate('24:00:00', ra=True), 0)
        self.assertEqual(parse_coordinate('12.5', ra=True), 12.5)
        for value in ('12:60:00', '12:00:60', '-90:00:01', '00:00:nan', '1:2', '1:-2:3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_coordinate(value)
        with self.assertRaises(ValueError):
            parse_coordinate('24:00:01', ra=True)

    def target(self, ident=123456789, start=1380):
        return dict(target_id=ident, target_name='M31', target_ra_dec=[0.712, 41.27],
                    start_min=start, duration_min=30, lp_filter=False,
                    camera_mode='dual', second_camera={'enabled': True},
                    mosaic={'angle': 35, 'scale': 1.2, 'star_map_angle': 0})

    # 日付をまたぐ時刻入力を確認する。25:30は1530分になり、分が60、負の時、48時、時刻形式でない文字列は ValueError になること。
    def test_midnight_and_invalid_time(self):
        self.assertEqual(minutes('25:30'), 1530)
        for value in ('01:60', '-1:30', '48:00', 'abc'):
            with self.assertRaises(ValueError):
                minutes(value)

    # プランのJSON出力を確認する。望遠モードになり、第二カメラ設定が除かれ、回転角と星図回転角が35°で保存されること。元の対象データは保持されること。
    def test_telephoto_and_rotation_survive_json(self):
        t = self.target()
        original = copy.deepcopy(t)
        p = build_plan('test', [t])
        restored = json.loads(json.dumps(p))['list'][0]
        self.assertEqual(restored['camera_mode'], 'tele')
        self.assertNotIn('second_camera', restored)
        self.assertEqual(restored['mosaic']['angle'], 35)
        self.assertEqual(restored['mosaic']['star_map_angle'], 35)
        self.assertEqual(t, original)

    # プランの入力検証を確認する。撮影時間帯が重なるプランと、赤経が NaN の対象は、それぞれ ValueError になること。
    def test_overlap_and_nan_rejected(self):
        with self.assertRaises(ValueError):
            build_plan('test', [self.target(), self.target(123456790, 1390)])
        t = self.target()
        t['target_ra_dec'][0] = math.nan
        with self.assertRaises(ValueError):
            build_plan('test', [t])

    # 日付をまたぐ対象の並び順を確認する。入力順を逆にしても、23:00の対象が翌日01:30の対象より前に並ぶこと。
    def test_next_day_order(self):
        p = build_plan('test', [self.target(123456790, 1530), self.target()])
        self.assertEqual([t['start_min'] for t in p['list']], [1380, 1530])

    # 23:30から一覧順に連続撮影する場合、1件目の撮影時間45分を使って2件目が翌日00:15になること。元の開始時刻は保持されること。
    def test_schedule_uses_duration_and_list_order_across_midnight(self):
        targets = [self.target(123456790, 1530), self.target()]
        targets[0]['duration_min'] = 45
        p = build_plan('test', targets, '23:30')
        self.assertEqual([t['target_id'] for t in p['list']], [123456790, 123456789])
        self.assertEqual([t['start_min'] for t in p['list']], [1410, 1455])
        self.assertEqual(targets[0]['start_min'], 1530)

    # 撮影時間の積み上げが時刻の上限を超える場合を確認する。47:45から30分撮影した後に始まる2件目は ValueError になること。
    def test_schedule_rejects_accumulated_start_outside_supported_range(self):
        with self.assertRaises(ValueError):
            schedule_targets([self.target(), self.target(123456790)], '47:45')

    # Stellarium の選択天体の座標変換を確認する。M42の赤経83.82°が5.588時に換算され、天体名と赤緯−5.39°はそのまま返ること。
    def test_stellarium_degrees_to_hours(self):
        response = io.BytesIO(json.dumps({'name': 'M42', 'raJ2000': 83.82, 'decJ2000': -5.39}).encode())
        with patch('planner.urlopen', return_value=response):
            name, ra, dec = selected_object('http://localhost:8090')
        self.assertEqual(name, 'M42')
        self.assertAlmostEqual(ra, 5.588)
        self.assertEqual(dec, -5.39)

    # 撮影枠の90°回転を確認する。幅4・高さ2の枠の先頭頂点が (1, −2) に移ること。
    def test_quarter_turn(self):
        x, y = frame_points(90, width=4, height=2)[0]
        self.assertAlmostEqual(x, 1)
        self.assertAlmostEqual(y, -2)

    # 撮影枠の天球投影を確認する。赤経0°をまたぐ座標、90°回転、面積倍率4の拡大が期待値に合い、北極でも赤経・赤緯が有効範囲に収まること。
    def test_sky_frame_projection_and_poles(self):
        corners = sky_frame_corners(0, 0, 0, width=4, height=2)
        self.assertAlmostEqual(corners[0][0], 2)
        self.assertAlmostEqual(corners[1][0], 358)
        rotated = sky_frame_corners(0, 0, 90, width=4, height=2)
        self.assertAlmostEqual(rotated[0][0], 1)
        self.assertGreater(rotated[0][1], 0)
        enlarged = sky_frame_corners(0, 0, 0, scale=4, width=4, height=2)
        self.assertAlmostEqual(math.tan(math.radians(enlarged[0][0])),
                               2 * math.tan(math.radians(corners[0][0])))
        for ra, dec in sky_frame_corners(23.99, 90, 45, 4):
            self.assertTrue(0 <= ra < 360)
            self.assertTrue(-90 <= dec <= 90)

    # 既定の撮影枠の向きを確認する。回転0°では東西幅が2.25°で長辺は南北方向、回転90°では長辺が東西方向になること。
    def test_default_long_side_points_north_south(self):
        def spans(angle):
            corners = sky_frame_corners(0, 0, angle)
            east_west = [(ra + 180) % 360 - 180 for ra, _ in corners]
            north_south = [dec for _, dec in corners]
            return max(east_west)-min(east_west), max(north_south)-min(north_south)
        width, height = spans(0)
        self.assertAlmostEqual(width, 2.25)
        self.assertGreater(height, width)
        width, height = spans(90)
        self.assertGreater(width, height)

    # 出力プランの星図回転角を確認する。元の値が15°でも未設定でも、出力では撮影枠の角度35°にそろい、元の15°は書き換わらないこと。
    def test_plan_synchronizes_star_map_angle(self):
        target = self.target()
        target['mosaic']['star_map_angle'] = 15
        result = build_plan('test', [target])
        self.assertEqual(result['list'][0]['mosaic']['star_map_angle'], 35)
        self.assertEqual(result['list'][0]['mosaic']['angle'], 35)
        self.assertEqual(target['mosaic']['star_map_angle'], 15)
        del target['mosaic']['star_map_angle']
        result = build_plan('test', [target])
        self.assertEqual(result['list'][0]['mosaic']['star_map_angle'], 35)

    # 全表示の枠座標を確認する。旧データの星図回転角15°があっても、描画スクリプトには撮影枠の角度35°で計算した頂点が含まれること。
    def test_all_frames_ignore_legacy_star_map_angle(self):
        target = self.target()
        target['mosaic']['star_map_angle'] = 15
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090', None, None, 0, frames=[target])
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
        expected = sky_frame_corners(*target['target_ra_dec'], 35, 1.2)
        self.assertIn(', '.join(str(v) for point in expected for v in point), script)

    # 個別枠を差し替えるリクエストを確認する。Stellarium のスクリプト実行先に送信し、旧画像の削除が新画像の読み込みより先で、J2000座標と既定の太さ3の画像を使うこと。
    def test_sky_frame_posts_replacement_script(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090/', 5, 40, 35, 1.2)
        args, kwargs = request.call_args
        self.assertEqual(args[0], 'http://localhost:8090/api/scripts/direct')
        script = parse_qs(kwargs['data'].decode())['code'][0]
        self.assertLess(script.index('removeSkyImage'), script.index('loadSkyImage'))
        self.assertIn('EqJ2000', script)
        self.assertIn('seestar_planner_frame_3_', script)

    # 太さ8の枠画像を確認する。横・縦とも境界から7ピクセル目まで枠色で8ピクセル目は別の色になり、縦横比が画角から求めた値に合うこと。
    def test_frame_thickness_changes_texture_stroke(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')):
                draw_stellarium_frame('http://localhost:8090', 5, 40, 0, thickness=8)
            texture = next((Path(folder) / 'Stellarium/scripts').glob('seestar_planner_frame_8_*.png'))
            with Image.open(texture) as image:
                color = image.getpixel((0, 0))
                self.assertEqual(image.getpixel((7, image.height // 2)), color)
                self.assertNotEqual(image.getpixel((8, image.height // 2)), color)
                self.assertEqual(image.getpixel((image.width // 2, 7)), color)
                self.assertNotEqual(image.getpixel((image.width // 2, 8)), color)
                expected = math.tan(math.radians(2.25) / 2) / math.tan(math.radians(4) / 2)
                self.assertAlmostEqual(image.width / image.height, expected, delta=0.001)

    # Stellarium が描画スクリプトを拒否した場合を確認する。スクリプト実行中という応答に対して ValueError が発生すること。
    def test_sky_frame_reports_rejected_script(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'error: script running')):
                with self.assertRaises(ValueError):
                    draw_stellarium_frame('http://localhost:8090', 5, 40, 0)

    # 対象選択時の視点と時刻を確認する。基準日11月15日の25:30を翌日01:30の現地時刻として設定し、J2000座標の赤経5時・赤緯40°へ移動して時刻の進行を止めること。
    def test_target_selection_sets_local_time_and_j2000_position(self):
        status = io.BytesIO(json.dumps({'time': {'local': '2026-11-15T23:00:00'}}).encode())
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', side_effect=[status, io.BytesIO(b'ok')]) as request:
                base = draw_stellarium_frame('http://localhost:8090', 5, 40, 35, start_min=1530)
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
        self.assertEqual(base, date(2026, 11, 15))
        self.assertIn('core.setDate("2026-11-16T01:30:00", "local")', script)
        self.assertIn('core.moveToRaDecJ2000("5.0h", "40.0d", 0)', script)
        self.assertIn('core.setTimeRate(0)', script)

    # 翌日対象を繰り返し選択した場合を確認する。指定した基準日11月15日を使い、日付を再取得せず1回の送信で11月16日01:30を設定すること。
    def test_repeated_next_day_selection_keeps_plan_date(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090', 5, 40, 0,
                                      start_min=1530, plan_date=date(2026, 11, 15))
                self.assertEqual(request.call_count, 1)
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
        self.assertIn('2026-11-16T01:30:00', script)

    # 全表示の更新を確認する。旧レイヤーの余分な枠を消して新しい2枠を読み込み、濃さ100に対応する描画パラメーターを使い、日時変更命令を含まないこと。
    def test_all_frames_replace_old_layers_and_apply_intensity(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090', None, None, 0,
                                      frames=[self.target(), self.target(123456790)],
                                      previous_count=3, intensity=100)
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
        self.assertEqual(script.count('core.loadSkyImage'), 2)
        self.assertIn('core.removeSkyImage("seestar_planner_all_2")', script)
        self.assertIn('2.5, 8.0, true', script)
        self.assertNotIn('core.setDate', script)

    # 未選択枠の減光を確認する。選択枠には通常画像、未選択枠には減光画像が使われ、減光画像のRGBは通常の35%、アルファ値は同じであること。
    def test_unselected_frame_texture_has_dimmer_rgb(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090', 5, 40, 0,
                                      frames=[self.target(), self.target(123456790)],
                                      selected_index=0, dim_unselected=True)
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
            textures = Path(folder) / 'Stellarium' / 'scripts'
            dimmed = next(textures.glob('*_dim.png'))
            normal = dimmed.with_name(dimmed.name.replace('_dim.png', '.png'))
            self.assertIn(f'"seestar_planner_all_0", "{normal.name}"', script)
            self.assertIn(f'"seestar_planner_all_1", "{dimmed.name}"', script)
            with Image.open(normal) as normal_image, Image.open(dimmed) as dimmed_image:
                normal_pixel = normal_image.getpixel((0, 0))
                dimmed_pixel = dimmed_image.getpixel((0, 0))
                self.assertGreater(max(normal_pixel[:3]), 0)
                self.assertEqual(dimmed_pixel[:3], tuple(int(v * 0.35) for v in normal_pixel[:3]))
                self.assertEqual(dimmed_pixel[3], normal_pixel[3])

    # 濃さ0での描画を確認する。旧枠の消去命令があり、新しい画像の読み込み命令を含まないこと。
    def test_zero_intensity_removes_frames_without_loading(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090', 5, 40, 0,
                                      frames=[self.target()], previous_count=1, intensity=0)
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
        self.assertNotIn('core.loadSkyImage', script)
        self.assertIn('core.removeSkyImage("seestar_planner_all_0")', script)

    # パネル番号の画像描画を確認する。明示した番号1と対象名から読み取った番号2・3の画像が生成され、スクリプトで使われ、各画像の中央に複数色があること。
    def test_panel_numbers_are_rendered_in_stellarium_textures(self):
        from PIL import Image
        frames = [dict(self.target(), panel_number=1),
                  dict(self.target(), target_name='M1 P2'),
                  dict(self.target(), target_name='M1 パネル3')]
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', APPDATA=folder):
            with patch('planner.urlopen', return_value=io.BytesIO(b'ok')) as request:
                draw_stellarium_frame('http://localhost:8090', None, None, 0, frames=frames)
                script = parse_qs(request.call_args.kwargs['data'].decode())['code'][0]
            for panel in (1, 2, 3):
                texture = next((Path(folder) / 'Stellarium/scripts').glob(f'*_panel_{panel}.png'))
                self.assertIn(texture.name, script)
                with Image.open(texture) as image:
                    middle = image.crop((image.width // 4, image.height // 4,
                                         3 * image.width // 4, 3 * image.height // 4))
                    self.assertGreater(len(middle.getcolors()), 1)

    # 2行3列のモザイクで撮影順を確認する。各列では同じ赤経で南側から北側へ進み、列は赤経の小さい西側から東側へ並ぶこと。
    def test_mosaic_shoots_north_in_each_column_from_west_to_east(self):
        panels = list(mosaic_panel_centers(5, 0, 0, 1, 1, 2, rows=2, columns=3))
        columns = [panels[i:i + 2] for i in range(0, len(panels), 2)]
        self.assertEqual(len(columns), 3)
        for ((south_ra, south_dec), (north_ra, north_dec)) in columns:
            self.assertAlmostEqual(south_ra, north_ra)
            self.assertLess(south_dec, 0)
            self.assertGreater(north_dec, 0)
        self.assertLess(columns[0][0][0], columns[1][0][0])
        self.assertLess(columns[1][0][0], columns[2][0][0])

    # 東京から見た天体の高度計算を確認する。指定日時の北極星は34°より高く37°より低く、赤緯−80°の天体は地平線より下になること。
    def test_calculate_altitude(self):
        moment = datetime(2026, 11, 16, 21, 0)
        # Polaris (Dec ~ 89.26, RA ~ 2.5) from Tokyo (Lat 35.68) should always be around 35-36 degrees
        polaris_alt = calculate_altitude(2.5, 89.26, moment, lat_deg=35.68, lon_deg=139.76)
        self.assertGreater(polaris_alt, 34)
        self.assertLess(polaris_alt, 37)

        # Deep southern object (Dec -80) from Tokyo should be below horizon (< 0)
        south_alt = calculate_altitude(12.0, -80.0, moment, lat_deg=35.68, lon_deg=139.76)
        self.assertLess(south_alt, 0)

        # Altitude range is within [-90, 90]
        self.assertGreaterEqual(polaris_alt, -90)
        self.assertLessEqual(polaris_alt, 90)


if __name__ == '__main__':
    unittest.main()
