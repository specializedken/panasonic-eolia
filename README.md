# Panasonic Eolia for Home Assistant

🇯🇵 日本語 | [English](#english)

パナソニックのエアコン **Eolia（エオリア）**（日本向けの「スマートアプリ」対応モデル）用の、**非公式**の
Home Assistant カスタムインテグレーションと Lovelace カードです。公式の Eolia アプリと同じ方法で
パナソニックのクラウドと通信します。

## なぜ作ったか

Eolia は ECHONET Lite でも操作できますが、ECHONET が反映するのは状態のごく一部だけです。
**AI モード / ECONAVI**、**nanoeX**、気流の向きの制御（人を避ける／人に当てる）、そして
**除湿（快適除湿）** と **冷房除湿** の区別は ECHONET では取得できません。これらはアプリが使う
クラウド API にしかないため、このインテグレーションはそれを利用しています。API は Android アプリの
解析によって調べたものです（[`docs/findings.md`](docs/findings.md) を参照）。この API 向けの
既存のオープンソースクライアント 2 つはアーカイブ済みで、すでに動作しません。

> **ステータス:** 実機 1 台（**CS-712DX2-W**、Eolia X）で動作しており、日常的に使っています。それ以外の
> 機種は未検証です。[制限事項](#制限事項)を参照してください。

## 他の Panasonic 向けインテグレーションとの違い

- **Comfort Cloud 系のインテグレーション**（例: [`panasonic_cc`](https://github.com/sockless-coding/panasonic_cc)）
  は、このエアコンでは使えません。日本の Eolia アプリは独自のクラウドと独自のアカウントで動いて
  おり、Comfort Cloud アプリは Eolia ID を受け付けません（日本の Eolia アカウントで確認済み）。
  このインテグレーションは Eolia のクラウドと直接通信します。
- **他の地域向けアプリ**（Panasonic Smart App、Panasonic IoT TW など）には、それぞれのクラウド
  向けのインテグレーションがありますが、日本の Eolia アプリを対象にしたものは見つかりませんでした。
- **ECHONET Lite**（例: [`echonetlite_homeassistant`](https://github.com/scottyphillips/echonetlite_homeassistant)）
  はローカルで動作し、代替手段として優秀ですが、設定温度は整数度単位で、モードも汎用的なものしか
  扱えません（上記参照）。
- **以前の Eolia クライアント**（[`panasonic_eolia`](https://github.com/avolmensky/panasonic_eolia)）
  はアーカイブ済みで、すでに動作しません。

このインテグレーションには Lovelace カードも同梱されており、モードごとの設定も保持します。

## できること

エアコン 1 台につき:

| プラットフォーム | エンティティ |
|---|---|
| `climate` | オン/オフ、目標温度（16–30 °C、0.5 度刻み）、風量、上下・左右のルーバー、そして**全モード**をプリセットとして提供: Auto、Dry、Cooling、Cool & Dehumidify、Moist Cooling、Heating、Double temperature、Clothes drying、Odor care、Away clean、Self-clean、Fan only（お使いの機種が対応するモードのみ表示されます） |
| `select` | AI モード（オフ / AI コンフォート / AI コンフォート + ECONAVI）、気流モード（標準 / 静か / パワフル / ロングリーチ）、気流の向き（標準 / 人を避ける / 人に当てる）、風量、上下ルーバー、左右ルーバー |
| `number` | Dry の湿度目標（50–60 %、5 % 刻み）、Double temperature の下限・上限 |
| `sensor` | 運転モード（エアコン本体が報告する、変換前のモード）、室内温度、室内湿度、室外温度 |
| `switch` | nanoeX、静音モード |

空気質関連のエンティティ（監視スイッチ 1 つとセンサー 2 つ）は、**その機能を報告する機種でのみ**
作成されます。CS-712DX2-W は報告しないため、表示されません。

対象外: 週間タイマー、AI シーン、電力履歴、通知、ファームウェア確認。

## 必要なもの

- Home Assistant。開発と運用は **2026.9** で行っています。ユニットテストは 2025.1 のテスト環境を
  使っています。それより古いバージョンは未検証です（特にカードは最近のフロントエンド内部に依存
  しています）。
- 公式アプリに Eolia を 1 台以上登録済みの Panasonic ID。

## インストール

**HACS を使う場合**（カスタムリポジトリとして）:

1. HACS で ⋮ メニュー → **Custom repositories** を開き、このリポジトリの URL をタイプ
   **Integration** で追加して、**Panasonic Eolia** をダウンロードします。
2. Home Assistant を再起動します。

**手動の場合:**

1. このリポジトリの `custom_components/eolia/` フォルダを、Home Assistant の
   `config/custom_components/` ディレクトリにコピーします。
2. Home Assistant を再起動します。

どちらの場合も、カードとそのアイコンはインテグレーションに含まれています。ほかにインストールする
ものはありません。

## セットアップ

**設定 → デバイスとサービス → インテグレーションを追加 → Panasonic Eolia**

パナソニックのログインは Eolia アプリ専用のリダイレクト先しか受け付けないため、Home Assistant
標準の OAuth ヘルパーは使えません。代わりに、ブラウザで一度ログインし、その結果をインテグレー
ションに渡します（手順はフォームに表示されます）:

1. フォームに表示されたログインリンクを開き、Panasonic ID でサインインします。
2. ログイン後、**ページの読み込みは失敗しますが、それは想定どおりです。**
3. ブラウザの開発者ツール → **Network** タブを開き、名前が `panasonic-eolia:` で始まる
   リクエストを探します。
4. その **Request URL**（`code=…` を含みます）を丸ごとコピーして、フォームに貼り付けます。

その後は、インテグレーションがトークンを自動で更新し続けます。パナソニックがトークンを拒否した
場合は、Home Assistant が再ログインを求めます。

## Lovelace カード

インテグレーションには専用のカードが付属しており、自動的に登録されます。インストールするものは
ありません。ダッシュボードに追加します:

```yaml
type: custom:eolia-card
entity: climate.your_eolia_climate
```

設定するのは climate エンティティだけで、同じデバイスにある他のものはカードが自動で見つける
ため、デバイス名が何であっても動作します。表示されるもの:

1. **円形の設定ダイヤル**（Home Assistant 標準のダイヤル）: 目標温度、Dry では湿度目標、
   Double temperature では下限/上限のペア（5° 以上離して保持）。目標のないモードでは、レイアウト
   が動かないようにグレーアウトして表示されたままになります。
2. **モードグリッド**: オフと全モード。それぞれのモードに、何をするものか説明するツールチップが
   付いています。
3. **その他のコントロール**（現在のモードに適用されるものだけ）: 風量、ルーバー、AI モード、
   気流モード、気流の向き、nanoeX、静音モード、および部屋の温度と湿度。

### アイコン

モードグリッドと設定行のうち 4 つは、公式 Eolia アプリのアイコンを使っています。モードアイコン
8 つと行アイコン 4 つです（行アイコンはアプリのものを正方形にパディングして色を変えたものです）。
`custom_components/eolia/www/icons/` にあり、インテグレーションが配信するため、カードは設定なしで
利用できます。**これらはパナソニックの著作物です**。利便性のために同梱しています。
[免責事項](#免責事項)を参照してください。

アイコンがなくてもカードは動作します（アイコンのないモードやファイルが見つからない場合は、
Home Assistant 標準のアイコンにフォールバックします）。カードの設定で変更できます:

```yaml
type: custom:eolia-card
entity: climate.your_eolia_climate
icons: false                  # アイコンを使わない
# icons: /local/my-icons      # 同じ構成（modes/、rows/）を自分のフォルダから読み込む
```

お手持ちのアプリから再生成するには: [`jadx`](https://github.com/skylot/jadx) でアプリを
`code/` にデコンパイルし、`python tools/extract_icons.py` を実行して（行アイコンには
`pip install Pillow` が必要です）、git 管理外の `icons/` 出力から、カードが使うファイルを
`custom_components/eolia/www/icons/` にコピーします。

## 制限事項

- **検証したのは 1 機種のみ。** すべて CS-712DX2-W 1 台で検証しています。他の機種は機能が異なり
  ます（インテグレーションは機種ごとの対応機能リストを読み取り、非対応のものは隠します）が、
  確認できているのはこの機種だけです。1 つのアカウントに複数のデバイスがある場合は未検証です。
- **クラウド API です。** 状態は 60 秒ごとにポーリングされ、書き込み 1 回に約 3 秒かかります。
  書き込みが重なるとパナソニック側でアカウントが数分間ロックされるため、書き込みは 1 件ずつ送信
  します。
- **サーバーが単に拒否するものもあります。** その場合、インテグレーションは黙って失敗せず、分かり
  やすい言葉で理由を伝えます: エアコンがオフのときの設定変更、または現在のモードが無視する設定
  （例: Fan only での AI モード）。
- **壊れる可能性があります。** パナソニックはこの非公開 API をいつでも変更できます。以前の
  オープンソースクライアントが動かなくなった原因もそれです。
- パナソニックのアクセストークンの有効期間は 14 日で、インテグレーションが自動で更新します。
  この更新はユニットテストでカバーされており、実機でも動作を確認しています（2026-10-06）。更新用
  トークン自体がいつまで有効かは、まだ分かっていません。

## 開発

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
python -m pytest -q          # Python テスト
node --test tests/js/        # Lovelace カードのテスト（Node 18+）
```

構成:

| パス | 内容 |
|---|---|
| `custom_components/eolia/` | インテグレーション本体 |
| `custom_components/eolia/www/eolia-card.js` | **Lovelace カード**（素の JavaScript、ビルド不要）。`frontend.py` が配信・登録します |
| `custom_components/eolia/www/icons/` | **カードが使うアイコン**（PNG 12 枚、パナソニックの著作物）。インテグレーションが配信します |
| `tests/` | Python テスト。`tests/js/` にカードのテスト、`tests/fixtures/` に実際にキャプチャした API 通信があります |
| `tools/` | 実際のクラウドとエアコンに接続するスクリプト（CLI、ファジングツール、アイコン抽出）。下の警告を参照 |
| `docs/` | API リファレンス、開発ログ、カードの設計メモ（[目次](docs/README.md)） |
| `icons/` | `tools/extract_icons.py` のローカル出力（約 100 ファイル）。git 管理外。コミットされているのはカードが使う 12 個だけで、上記の `www/icons/` にあります |

**警告:** `tools/` のスクリプト（`eolia_cli.py set`、ファジングツール、A/B ランナー）は、実際のエアコンに
コマンドを送信します。実行前に内容をよく確認してください。

## 免責事項

このプロジェクトは、パナソニックとは無関係であり、パナソニックの承認やサポートを受けたもので
はありません。*Panasonic*、*Eolia*、*nanoeX* はパナソニック ホールディングス株式会社の商標です。
このインテグレーションは、ご自身のアカウントでパナソニックのクラウドにサインインし、ご自身の
機器を制御します。利用は自己責任でお願いします。

`custom_components/eolia/www/icons/` のアイコンは、Eolia アプリから取得したパナソニックの
著作物です。利便性のために同梱しており、このプロジェクトがコードに対して付与するライセンスの
対象では**ありません**。権利はすべてパナソニックに帰属します。このリポジトリを再公開・再配布する
場合は、そのフォルダを削除してください（カードはそれがなくても動作します）。

## ライセンス

コードは [MIT License](LICENSE) で公開されています。上記のパナソニックのアイコンは対象外です。

---

<a id="english"></a>

# Panasonic Eolia for Home Assistant

An **unofficial** Home Assistant custom integration, plus a Lovelace card, for Panasonic **Eolia**
air conditioners (the Japanese "Smart App" models). It talks to Panasonic's cloud the same way the
official Eolia app does.

## Why

Eolia units can be reached over ECHONET Lite, but ECHONET only mirrors a small part of the state.
It never carries **AI mode / ECONAVI**, **nanoeX**, airflow targeting, or the difference between
**Dry** (Comfortable Dehumidify) and **Cool & Dehumidify**. Those exist only in the app's cloud
API, so that is what this integration uses. The API was reverse-engineered from the Android app
(see [`docs/findings.md`](docs/findings.md)); the two older open-source clients for it are
archived and no longer work.

> **Status:** working and in daily use on one real unit (**CS-712DX2-W**, Eolia X). Nothing else
> has been tested — see [Limitations](#limitations).

## How this differs from other Panasonic integrations

- **Comfort Cloud–based integrations** (e.g. [`panasonic_cc`](https://github.com/sockless-coding/panasonic_cc))
  can't be used with these units. The Japanese Eolia app runs on its own cloud with its own accounts,
  and the Comfort Cloud app does not accept an Eolia ID (tested with a Japanese Eolia account). This
  integration talks to the Eolia cloud directly.
- **Other regional apps** (Panasonic Smart App, Panasonic IoT TW, ...) have their own integrations
  for their own clouds; none that we found targets the Japanese Eolia app.
- **ECHONET Lite** (e.g. [`echonetlite_homeassistant`](https://github.com/scottyphillips/echonetlite_homeassistant))
  works locally and is a fine fallback, but only exposes whole-degree setpoints and generic modes
  (see above).
- **Earlier Eolia clients** ([`panasonic_eolia`](https://github.com/avolmensky/panasonic_eolia)) are
  archived and no longer work.

This integration also ships a Lovelace card and keeps per-mode settings.

## What you get

Per air conditioner:

| Platform | Entities |
|---|---|
| `climate` | On/off, target temperature (16–30 °C, 0.5 steps), fan speed, both louver axes, and the **full mode list** as presets: Auto, Dry, Cooling, Cool & Dehumidify, Moist Cooling, Heating, Double temperature, Clothes drying, Odor care, Away clean, Self-clean, Fan only (only the modes your model supports are offered) |
| `select` | AI mode (off / AI comfort / AI comfort + ECONAVI), Airflow mode (normal / quiet / powerful / long reach), Airflow targeting (normal / avoid people / aim at people), Fan speed, Vertical louver, Horizontal louver |
| `number` | Dry humidity target (50–60 %, 5 % steps), Double temperature low and high |
| `sensor` | Operation mode (the raw mode the unit reports), indoor temperature, indoor humidity, outdoor temperature |
| `switch` | nanoeX, Quiet mode |

Air-quality entities (a monitoring switch and two sensors) are created **only on models that report
the feature**; the CS-712DX2-W does not, so none appear there.

Not covered: the weekly timer, AI scenes, energy history, notifications and firmware checks.

## Requirements

- Home Assistant. Developed and run on **2026.9**; the unit tests use the 2025.1 test harness.
  Older versions are untested (the card in particular relies on recent frontend internals).
- A Panasonic ID with at least one Eolia unit registered in the official app.

## Install

**With HACS** (as a custom repository):

1. In HACS, open the ⋮ menu → **Custom repositories**, add this repository's URL with the type
   **Integration**, then download **Panasonic Eolia**.
2. Restart Home Assistant.

**Manually:**

1. Copy the `custom_components/eolia/` folder from this repo into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.

Either way the card and its icons come with the integration; there is nothing else to install.

## Set up

**Settings → Devices & services → Add integration → Panasonic Eolia.**

Panasonic's login only accepts the Eolia app's own redirect address, so Home Assistant's built-in
OAuth helper can't be used. Instead you log in once in a browser and hand the result to the
integration (the form walks you through it):

1. Open the login link shown in the form and sign in with your Panasonic ID.
2. The page will **fail to load after you log in — that is expected.**
3. Open your browser's Developer Tools → **Network** tab and find the request whose name starts
   with `panasonic-eolia:`.
4. Copy its full **Request URL** (it contains `code=…`) and paste it into the form.

The integration then keeps the token refreshed on its own. If Panasonic ever rejects it, Home
Assistant asks you to log in again.

## The Lovelace card

The integration ships its own card and registers it automatically; there is nothing to install.
Add it to a dashboard:

```yaml
type: custom:eolia-card
entity: climate.your_eolia_climate
```

Only the climate entity is configured; the card finds everything else on the same device, so it
works whatever the device is called. It shows:

1. **A round setpoint dial** (Home Assistant's own dial): target temperature, the humidity target in
   Dry, or a low/high pair in Double temperature (kept at least 5° apart). In modes with no target it
   stays on screen greyed out so the layout doesn't move.
2. **A mode grid**: Off plus every mode, each with a tooltip explaining what it does.
3. **The remaining controls**, and only the ones that apply to the current mode: fan speed, louvers,
   AI mode, airflow mode, targeting, nanoeX, quiet mode, plus the room's temperature and humidity.

### Icons

The mode grid and four of the settings rows use icons taken from the official Eolia app: 8 mode icons
and 4 row icons (the row ones are square-padded and recoloured versions of the app's). They live in
`custom_components/eolia/www/icons/`, and the integration serves them, so the card has them with no
setup. **They are Panasonic's artwork**, included for convenience; see the [disclaimer](#disclaimer).

The card works without them (a mode with no icon, or a missing file, falls back to a standard Home
Assistant icon), and you can change this in the card config:

```yaml
type: custom:eolia-card
entity: climate.your_eolia_climate
icons: false                  # don't use them
# icons: /local/my-icons      # or load the same layout (modes/, rows/) from your own folder
```

To regenerate them from your own copy of the app: decompile it with
[`jadx`](https://github.com/skylot/jadx) into `code/`, run `python tools/extract_icons.py` (the row
icons need `pip install Pillow`), and copy the files the card uses from the git-ignored `icons/`
output into `custom_components/eolia/www/icons/`.

## Limitations

- **One model tested.** Everything was validated on a single CS-712DX2-W. Other models expose
  different features (the integration reads each model's capability list and hides what it lacks),
  but that has only been proven on this one. Multiple devices on one account are untested.
- **It's a cloud API.** State is polled every 60 s, and each write takes about 3 seconds. Writes are
  sent one at a time, because overlapping writes make Panasonic lock the account out for a couple of
  minutes.
- **Some things the server simply refuses**, and the integration says so in plain language instead of
  failing silently: changing a setting while the unit is off, or one the current mode ignores
  (for example AI mode in Fan only).
- **It can break.** Panasonic can change this private API at any time — that is what killed the
  earlier open-source clients.
- Panasonic's access token lasts 14 days and the integration refreshes it itself. That refresh is
  covered by unit tests and has been seen working on a real unit (2026-10-06). How long the refresh
  token itself stays valid is not known yet.

## Development

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
python -m pytest -q          # Python tests
node --test tests/js/        # Lovelace card tests (Node 18+)
```

Where things are:

| Path | What |
|---|---|
| `custom_components/eolia/` | The integration |
| `custom_components/eolia/www/eolia-card.js` | **The Lovelace card** (plain JavaScript, no build step); `frontend.py` serves and registers it |
| `custom_components/eolia/www/icons/` | **The icons the card uses** (12 PNGs, Panasonic's artwork), served by the integration |
| `tests/` | Python tests; `tests/js/` holds the card's tests; `tests/fixtures/` holds real captured API traffic |
| `tools/` | Scripts that talk to the real cloud and unit (CLI, fuzzer, icon extractor) — see the warning below |
| `docs/` | The API reference, the development log and the card's design notes ([index](docs/README.md)) |
| `icons/` | Git-ignored local output of `tools/extract_icons.py` (~100 files); only the 12 the card uses are committed, under `www/icons/` above |

**Warning:** the scripts in `tools/` (`eolia_cli.py set`, the fuzzer, the A/B runner) send real
commands to your air conditioner. Read what they do before running them.

## Disclaimer

This project is not affiliated with, endorsed by or supported by Panasonic. *Panasonic*, *Eolia* and
*nanoeX* are trademarks of Panasonic Holdings Corporation. The integration signs in to Panasonic's
cloud with your own account and controls your own appliance; use it at your own risk.

The icons in `custom_components/eolia/www/icons/` are copyrighted Panasonic artwork taken from the
Eolia app. They are included for convenience and are **not** covered by any license this project may
grant for its code; Panasonic keeps all rights in them. If you republish or redistribute this
repository, remove that folder (the card still works without it).

## License

The code is released under the [MIT License](LICENSE). The Panasonic icons mentioned above are not part of it.
