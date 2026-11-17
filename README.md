# Seestar 撮影プラン

Windows / Python / PySide6 / デスクトップ版Stellarium用。望遠カメラ専用です。

左上のドロップダウンで S30 / S50 / S30 Pro / S50 Pro を選択します。初期選択は S50 Pro です。機種変更時は、その機種の望遠画角で選択対象・全表示の撮影枠を再計算します。本体に接続すると、取得した機種名に対応する項目を自動選択します。接続に失敗した場合は選択機種を維持します。

撮影枠の基準サイズ（横×縦、概算）は S30：1.20°×2.13°、S50：0.72°×1.28°、S30 Pro：2.24°×3.99°、S50 Pro：1.38°×2.45°です。S50とPro各機種は[公式比較表](https://us.seestar.com/blogs/review/s50-pro-s30-pro-vs-s50-has-a-new-star-king-been-born)の値、S30はS50との焦点距離比からの概算を使用します。回転0°では長辺が南北方向です。画角は表示・フレーミング用の設定で、プランJSONには保存しません。

## 起動

PyCharmでこのフォルダーを開き、プロジェクトのPython環境へ `requirements.txt` をインストールし、`main.py` を実行します。

PowerShellの場合（プロジェクトフォルダー内）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## Windows実行ファイルのビルド

Python 3.12（64bit）で動作確認しています。初回はプロジェクトフォルダー内で、ビルド専用の環境を作成して依存パッケージをインストールします。

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

以降は次のバッチでビルドできます。

```powershell
.\build_onedir.bat
```

`SeestarPlanEditor.spec` を使用した標準の **onedir** ビルドです。PySide6・Qt・shiboken6の特別な除外・再配置処理は行わず、PyInstallerの標準フックで収集します。依存ファイルは `contents_directory='_internal'` により、次の構成にまとめます。

```text
dist/
└── SeestarPlanEditor/
    ├── SeestarPlanEditor.exe
    ├── licenses/
    │   ├── LGPL-3.0.txt
    │   └── GPL-3.0.txt
    └── _internal/
        ├── PySide6/
        ├── shiboken6/
        ├── frame.png
        └── その他の実行時依存ファイル
```

配布するときは **`dist/SeestarPlanEditor` フォルダー全体** をコピーしてください。実行先にPythonをインストールする必要はありません。ビルド元に `seestar.pem` がある場合は `_internal` に同梱し、実行時に参照します。

再ビルド前に、配布先のEXEを終了してください。`dist/SeestarPlanEditor` は自動で削除・作り直されるため、プランJSONなどの保存先は別の場所にしてください。手動での削除は不要です。

バッチを使わず直接ビルドする場合は、次のコマンドを実行します。

```powershell
.\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm SeestarPlanEditor.spec
```

## Stellariumの設定

1. F2 → プラグイン → Remote Control（リモートコントロール）を開き、「起動時に実行」を有効にしてStellariumを再起動します。
2. プラグインの設定からサーバーを有効化します。標準ポートは8090です。このアプリは認証なしのローカル接続を想定しています。
3. Stellariumで天体をクリックして選択し、このアプリの「選択天体を取得」を押します。
4. 取得した天体を中心に撮影範囲の枠をStellariumへ描画します。角度・倍率を変更すると回転フレーミングが自動で有効になり、操作が止まって約0.25秒後に枠を更新します。

Stellariumとこのアプリは同じPCで実行してください。描画には同梱の `frame.png` とRemote Controlのスクリプト実行機能を使い、画像を `%APPDATA%/Stellarium/scripts/` に自動配置します。横・縦画角は左上で選択した機種に応じて設定します。

取得するのはJ2000座標です。Stellariumの赤経（度）をSeestar用の時単位へ変換します。移動天体の追尾・座標更新には対応しません。

## 撮影プラン

- プラン全体の開始時刻を指定し、「選択天体を取得」で開くダイアログで対象ごとの撮影時間（分）、LPフィルターなどを設定して「反映」を押します。
- リスト順に撮影時間を積算して各対象の開始時刻を自動計算します。開始時刻の変更、対象の追加・時間変更・削除でも再計算します。翌日01:30は `25:30` と表示・入力します。導入・フォーカスの時間は自動加算しません。
- 行をドラッグして順序を入れ替えられます。並べ替えた順で開始時刻を再計算し、保存・共有にも反映します。
- 行をダブルクリックすると設定ダイアログで再編集できます。行を選ぶとStellariumも対象の位置・撮影枠・その行の開始時刻へ移動します。時刻はStellariumの現地時刻として設定し、時間の進行を停止します。
- 基準日は最初に行を選んだときのStellariumの表示日付です。24時以降は翌日として扱い、行を切り替えても日付が繰り上がり続けないよう同じ基準日を使います。別の日付で確認する場合は、Stellariumの日付を変更してプランを開き直すかアプリを再起動してください。
- 「ファイル」→「保存」（Ctrl+S）／「開く」（Ctrl+O）でJSONファイルを保存・再読み込みできます。読み込み時は最初の対象の開始時刻をプラン開始時刻とし、撮影時間を積算して再計算します。カメラは望遠に固定されます。
- 「Seestar」→「共有QR」で、内容を確認した後でZWOクラウドへアップロードします。リンクを知る人はプランを取得でき、サービス側に削除機能はありません。本体との接続は不要です。
- スマホのSeestar V3.4.1以降でQRコードまたは共有リンクを読み込み、時刻・構図を確認して本体へ設定できます。
- 「Seestar」→「接続」で本体のIPv4アドレスを入力すると、機器情報を取得して接続を確認します。PCを本体と同じネットワークへ接続してください。初期値は `10.0.0.1` です。
- 接続確認後、「Seestar」→「プラン転送」で接続先・プラン名・対象数・開始時刻を確認して送信します。使用する `set_view_plan` APIは転送と実行開始を兼ねます。開始時刻が過去の場合は即座に撮影が始まる可能性があります。確認画面で了承した場合だけ送信します。転送結果を確認できなかった場合は、本体側の状態を確認してから再接続してください。

## 回転フレーミング

「回転フレーミングを有効化」で回転角・画角倍率を指定します。センサーを物理的に回す操作ではなく、モザイク撮影範囲の設定です。撮影枠はStellariumのJ2000座標上に表示します。回転0°で長辺が南北を向き、回転角はスライダーで1°刻みに変更できます。手入力とスライダーは連動します。倍率は辺の長さを基準に1〜2倍で指定し、JSONの `mosaic.scale` には面積倍率の1〜4を保存します。無効時は回転0°・倍率1で描画します。

「撮影枠」→「枠の濃さ・太さを調整」で、濃さ（50〜100%）と太さ（1〜10）を変更できます。変更はStellariumへ随時反映し、キャンセルすると元の値へ戻します。リスト下の「全表示」（「選択対象を削除」の左隣）をONにすると、登録済みの撮影枠をすべて表示します。OFFでは編集中の対象の枠だけを表示します。全表示中も選択行の角度・倍率変更と枠の濃さを反映します。枠の濃さ・太さと全表示の状態は表示用で、プランには保存されません。

赤経・赤緯は度・分・秒（例 `82° 30' 00''`、`-12° 20' 44''`）で表示・入力します。秒と回転角は整数表示です。手入力は入力欄からフォーカスを外すと整形します。従来の時:分:秒や小数による入力（赤経は時、赤緯は度）も使用できます。取得・読込時の元の数値精度とJSON内の単位は維持します。

リストの「回転°」の前にLPフィルターのON/OFFを表示します。対象のLPフィルターを変更して「追加 / 選択行を更新」を押すと、列の表示も更新します。

`mosaic.scale` / `mosaic.angle` を保存し、seestarpyの共有機能へ渡します。`mosaic.star_map_angle` も `angle` と同じ値で保存します。**S30 Pro実機での反映、角度の符号・基準方向は未検証です。** 公式アプリへ取り込んだ後、必ずフレーミング画面で確認・調整してください。その他の追加mosaic設定は編集後も維持します。

## 検証

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

時刻の日またぎ、重複検出、座標変換、回転パラメーターの保持をオフラインで検証します。共有サービスや本体との通信には実環境での確認が必要です。

## ライセンスについて

本ソフトウェアは、GUIライブラリとして PySide6（Qt for Python）を使用しています。  
PySide6およびQtの関連ライブラリは、LGPL v3などのオープンソースライセンスに基づいて提供されています。  
本ソフトウェアでは、Qt関連ライブラリを外部ファイルとして配置し、利用者が互換性のあるライブラリに交換できる構成としています。  
PySide6公式サイト：https://doc.qt.io/qtforpython-6/  
Qtライセンス情報：https://www.qt.io/development/open-source-lgpl-obligations  
LGPL v3全文：https://www.gnu.org/licenses/lgpl-3.0.html  

本ソフトウェアは、Seestar望遠鏡との通信および制御に seestarpy 0.8.0 を使用しています。  
seestarpyは GNU General Public License version 3（GPL v3） に基づいて公開されているオープンソースライブラリです。  
seestarpy公式サイト：https://github.com/astronomyk/seestarpy  
ライセンス：GPL v3  
ライセンス全文：https://www.gnu.org/licenses/gpl-3.0.html  
seestarpyの著作権は、各権利者に帰属します。

## 参照

- [seestarpy プラン共有](https://seestarpy.readthedocs.io/en/latest/examples/plan_sharing.html)
- [seestarpy プラン形式・mosaic](https://github.com/astronomyk/seestarpy/blob/master/claude_found_the_plan_methods.md)（古い資料の赤経・時刻説明より、現行のplan APIを優先）
- [Stellarium Remote Control API](https://stellarium.org/doc/head/remoteControlApi.html)
